"""mem0 OSS backend, exposing the same interface as StateCoreBackend.

Both systems are driven by THIS runner rather than each project's own, so the
question sample, the answerer prompt, the context assembly and the judges are
identical by construction. The only thing that differs is the memory system.

mem0 OSS REST surface (from mem0ai/memory-benchmarks common/mem0_client.py):
  POST   /memories   {messages, user_id, timestamp?}  -> {results:[...]}  (sync)
  POST   /search     {query, user_id, limit}          -> {results:[{memory,score,id}]}
  DELETE /memories?user_id=...
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .statecore import parse_lme_date


class Mem0Backend:
    name = "mem0-oss"

    def __init__(self, base_url="http://localhost:8888", ingest_concurrency=8, timeout=300):
        self.base_url = base_url.rstrip("/")
        self.ingest_concurrency = ingest_concurrency
        self.timeout = timeout

    # ---- transport -------------------------------------------------------
    def _call(self, method, path, body=None, timeout=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
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
    def create_scope(self, name):
        # mem0 has no scope object; the user_id IS the namespace. Deleting it
        # before the run guarantees isolation even if a previous run crashed.
        self.delete_scope(name)
        return name

    def delete_scope(self, scope_id):
        try:
            self._call("DELETE", "/memories?" + urllib.parse.urlencode({"user_id": scope_id}))
        except Exception:
            pass

    # ---- ingest ----------------------------------------------------------
    def ingest_sessions(self, scope_id, sessions, dates, granularity="message",
                        send_occurred_at=True):
        payloads = []
        for sess, date in zip(sessions, dates):
            iso = parse_lme_date(date) if send_occurred_at else None
            ts = None
            if iso:
                ts = int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp())
            if granularity == "session":
                payloads.append(([{"role": m["role"], "content": m["content"]} for m in sess], ts))
            else:
                for m in sess:
                    payloads.append(([{"role": m["role"], "content": m["content"]}], ts))

        def send(item):
            messages, ts = item
            body = {"messages": messages, "user_id": scope_id}
            if ts is not None:
                body["timestamp"] = ts
            return self._call_retry("POST", "/memories", body)

        # The previous run silently lost 483 of 10,094 ingests — 4.8% of mem0's
        # corpus — because the exception was swallowed with no detail and no
        # retry. A comparison where one side quietly ingested less than the other
        # is not a comparison.
        errs = 0
        error_detail = []

        def send_with_retry(item, attempts=3):
            last = None
            for attempt in range(attempts):
                try:
                    return send(item)
                except Exception as exc:
                    last = exc
                    if attempt < attempts - 1:
                        time.sleep(2 ** attempt)
            raise last

        with ThreadPoolExecutor(max_workers=self.ingest_concurrency) as pool:
            for i in range(0, len(payloads), self.ingest_concurrency):
                chunk = payloads[i:i + self.ingest_concurrency]
                for fut in [pool.submit(send_with_retry, c) for c in chunk]:
                    try:
                        fut.result()
                    except Exception as exc:
                        errs += 1
                        if len(error_detail) < 20:
                            error_detail.append("%s: %s" % (type(exc).__name__, str(exc)[:200]))
        return {"events": len(payloads), "errors": errs, "error_detail": error_detail}

    # ---- digest ----------------------------------------------------------
    def run_digest(self, scope_id, wait_s=180, poll_s=3):
        # mem0 extracts facts during ingest and has no separate consolidation
        # pass, so there is nothing to trigger or wait for.
        return None, "not_applicable"

    # ---- retrieve --------------------------------------------------------
    def search(self, scope_id, query, top_k=20):
        # Ask for far more than any budget can use, so what comes back is mem0's
        # whole store rather than a number this harness chose. The previous run
        # asked for 50 and got exactly 20 every time, and it was never
        # established whether that was a cap or simply all mem0 held.
        data = self._call_retry("POST", "/search", {
            "query": query, "user_id": scope_id, "limit": max(top_k, 1000)}) or {}
        results = data.get("results", data if isinstance(data, list) else []) or []
        # Map into the shape the shared answer-prompt builder consumes. mem0's
        # units are extracted facts; StateCore additionally returns a digest and
        # fact registry. Each system contributes what it actually produces --
        # that asymmetry is the architectural difference under test, not a bias.
        return {
            "digest": None,
            "factRegistry": [],
            "events": [{"content": r.get("memory") or r.get("data") or "",
                        "createdAt": r.get("created_at") or ""} for r in results],
            "retrieval": {"mode": "mem0-oss", "returnedCount": len(results)},
        }

    # ---- queue drain -----------------------------------------------------
    def wait_for_embeddings(self, expected_scope_events, redis_check, wait_s=600, poll_s=2):
        # /memories is synchronous: extraction and indexing finish before it
        # returns, so there is no async backlog to drain.
        return True
