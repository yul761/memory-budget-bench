"""StateCore HTTP backend for memory benchmarks.

Talks only to the frozen /v1 contract. Verified against StateCore HEAD de983b5:
  POST   /v1/scopes                 -> {id, name, ...}
  DELETE /v1/scopes/:id             -> {ok:true}
  POST   /v1/memory/events          -> {scopeId, type, source, key?, content}
  POST   /v1/memory/digest          -> {jobId}   (async, BullMQ)
  GET    /memory/state?scopeId=     -> {digestId, state, consistency, createdAt}
  POST   /v1/memory/retrieve        -> {digest, events[], factRegistry[], retrieval}

Note: /v1/memory/events has NO timestamp field (contracts/src/index.ts:49) --
createdAt is server-assigned. Historical time is carried inside `content`.
"""
import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

_LME_DATE = re.compile(r"^(\d{4})/(\d{2})/(\d{2})\s+\([A-Za-z]{3}\)\s+(\d{2}):(\d{2})$")


def parse_lme_date(date_str):
    """'2023/05/20 (Sat) 05:50' -> '2023-05-20T05:50:00+00:00'. None if unparseable."""
    m = _LME_DATE.match((date_str or "").strip())
    if not m:
        return None
    y, mo, d, h, mi = (int(x) for x in m.groups())
    try:
        return datetime(y, mo, d, h, mi).isoformat() + "+00:00"
    except ValueError:
        return None


class StateCoreBackend:
    name = "statecore"

    def __init__(self, base_url="http://localhost:3002", user_id="local-dev-user",
                 ingest_concurrency=8, timeout=120):
        self.base_url = base_url.rstrip("/")
        self.user_id = user_id
        self.ingest_concurrency = ingest_concurrency
        self.timeout = timeout

    # ---- transport -------------------------------------------------------
    def _call(self, method, path, body=None, timeout=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base_url + path, data=data, method=method,
            headers={"Content-Type": "application/json", "x-user-id": self.user_id})
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw else None

    def _call_retry(self, method, path, body=None, attempts=4):
        last = None
        for i in range(attempts):
            try:
                return self._call(method, path, body)
            except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
                last = e
                if isinstance(e, urllib.error.HTTPError) and 400 <= e.code < 500 and e.code != 429:
                    raise
                time.sleep(1.5 * (i + 1))
        raise last

    # ---- lifecycle -------------------------------------------------------
    def create_scope(self, name, template="personal"):
        # LongMemEval is personal conversation. Without an explicit template a
        # scope resolves to the "project" domain, whose facets are decisions and
        # constraints — so identity/goals/relationship facts would be rejected as
        # unregistered and the run would measure a project ontology applied to
        # personal chat.
        return self._call_retry("POST", "/v1/scopes", {"name": name[:120], "template": template})["id"]

    def delete_scope(self, scope_id):
        try:
            self._call_retry("DELETE", "/v1/scopes/" + scope_id, attempts=2)
        except Exception:
            pass

    # ---- ingest ----------------------------------------------------------
    def ingest_sessions(self, scope_id, sessions, dates, granularity="message",
                        send_occurred_at=True):
        """sessions: list[list[{role, content}]]; dates: list[str] aligned to sessions.

        The date is written into `content` (always) and, when the server supports
        it, also sent as `occurredAt` so createdAt reflects when the conversation
        actually happened rather than when the benchmark replayed it.
        """
        payloads = []
        for sess, date in zip(sessions, dates):
            iso = parse_lme_date(date) if send_occurred_at else None
            if granularity == "session":
                body = "\n".join("%s: %s" % (m["role"], m["content"]) for m in sess)
                payloads.append(("[%s]\n%s" % (date, body), iso))
            else:
                for m in sess:
                    payloads.append(("[%s] %s: %s" % (date, m["role"], m["content"]), iso))

        def send(item):
            content, iso = item
            body = {"scopeId": scope_id, "type": "stream", "source": "api", "content": content}
            if iso:
                body["occurredAt"] = iso
            return self._call_retry("POST", "/v1/memory/events", body)

        # Ingest order matters (recency scoring), so keep sessions ordered by
        # chunking: parallel within a chunk, sequential across chunks.
        errs = 0
        with ThreadPoolExecutor(max_workers=self.ingest_concurrency) as pool:
            for i in range(0, len(payloads), self.ingest_concurrency):
                chunk = payloads[i:i + self.ingest_concurrency]
                for fut in [pool.submit(send, c) for c in chunk]:
                    try:
                        fut.result()
                    except Exception:
                        errs += 1
        return {"events": len(payloads), "errors": errs}

    # ---- digest ----------------------------------------------------------
    def run_digest(self, scope_id, wait_s=180, poll_s=3):
        """Returns (ok, detail). digest is async and may fail its consistency gate."""
        try:
            self._call_retry("POST", "/v1/memory/digest", {"scopeId": scope_id})
        except Exception as e:
            return False, "enqueue_failed:%s" % e
        deadline = time.time() + wait_s
        while time.time() < deadline:
            time.sleep(poll_s)
            try:
                st = self._call("GET", "/memory/state?scopeId=" + scope_id)
            except Exception:
                continue
            if st and st.get("digestId"):
                return True, st
        return False, "timeout_or_consistency_failure"

    # ---- retrieve --------------------------------------------------------
    def search(self, scope_id, query, top_k=20):
        return self._call_retry("POST", "/v1/memory/retrieve", {
            "scopeId": scope_id, "query": query, "limit": top_k})

    # ---- queue drain -----------------------------------------------------
    def wait_for_embeddings(self, expected_scope_events, redis_check, wait_s=600, poll_s=2):
        """Async embed/classify jobs must drain before retrieve, or hybrid search
        silently degrades to heuristic-only. redis_check() -> pending job count."""
        deadline = time.time() + wait_s
        stable = 0
        while time.time() < deadline:
            pending = redis_check()
            if pending == 0:
                stable += 1
                if stable >= 2:
                    return True
            else:
                stable = 0
            time.sleep(poll_s)
        return False
