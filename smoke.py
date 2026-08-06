#!/usr/bin/env python3
"""Single-question smoke test: validate the StateCore /v1 flow and measure throughput."""
import json, time, sys, urllib.request, urllib.error

BASE = "http://localhost:3002"
USER = "local-dev-user"

def call(method, path, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", "x-user-id": USER})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]

d = json.load(open("data/longmemeval_s.json"))
q = d[0]
print("question_id :", q["question_id"])
print("type        :", q["question_type"])
print("question    :", q["question"])
print("answer      :", q["answer"])
print("sessions    :", len(q["haystack_sessions"]))
msgs = sum(len(s) for s in q["haystack_sessions"])
print("messages    :", msgs)

st, scope = call("POST", "/v1/scopes", {"name": "smoke-" + q["question_id"]})
print("\ncreate scope ->", st, scope)
if st >= 300:
    sys.exit(1)
sid = scope["id"]

# ingest first 30 messages, timed
t0 = time.time()
n = 0
for sess, date in zip(q["haystack_sessions"], q["haystack_dates"]):
    for m in sess:
        if n >= 30:
            break
        content = "[%s] %s: %s" % (date, m["role"], m["content"])
        st, resp = call("POST", "/v1/memory/events",
                        {"scopeId": sid, "type": "stream", "source": "api", "content": content})
        if st >= 300:
            print("ingest FAILED", st, resp)
            sys.exit(1)
        n += 1
    if n >= 30:
        break
dt = time.time() - t0
print("\ningested %d events in %.1fs -> %.1f ev/s (serial)" % (n, dt, n / dt))
print("   projected serial time for %d msgs: %.1f min" % (msgs, msgs / (n / dt) / 60))

st, r = call("POST", "/v1/memory/digest", {"scopeId": sid})
print("\ndigest enqueue ->", st, r)

t0 = time.time()
for _ in range(60):
    time.sleep(3)
    st, state = call("GET", "/memory/state?scopeId=" + sid)
    if st < 300 and state and state.get("digestId"):
        print("digest ready after %.0fs" % (time.time() - t0))
        print("state keys:", list(state.keys()))
        print(json.dumps(state, indent=1)[:1200])
        break
else:
    print("digest NOT ready after 180s; last:", st, str(state)[:300])

st, ret = call("POST", "/v1/memory/retrieve", {"scopeId": sid, "query": q["question"], "limit": 20})
print("\nretrieve ->", st)
if st < 300:
    print("  digest      :", (ret.get("digest") or "")[:300])
    print("  events      :", len(ret.get("events") or []))
    print("  factRegistry:", len(ret.get("factRegistry") or []))
    print("  retrieval   :", json.dumps(ret.get("retrieval"))[:300])
    for e in (ret.get("events") or [])[:3]:
        print("   -", e["createdAt"], e["content"][:120])
else:
    print(ret)

print("\ncleanup ->", call("DELETE", "/v1/scopes/" + sid)[0])
