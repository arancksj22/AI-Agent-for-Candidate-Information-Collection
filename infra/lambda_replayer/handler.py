"""Lambda: replays failed Candidate DB writes from SQS.

Message body = the agent's write intent:
  {"key", "candidate_id", "base_version", "was": {...}, "body": {...}}
Uses the same Idempotency-Key (so a write that actually landed isn't applied twice) and the same
three-way rule on 409 (never overwrite a field someone else changed). Raising makes SQS retry;
after maxReceiveCount the message moves to the DLQ. Standard library only, no packaging needed.
"""
import hashlib
import json
import os
import urllib.error
import urllib.request

BASE = os.environ["CANDIDATE_API_BASE_URL"].rstrip("/")
KEY = os.environ["CANDIDATE_API_KEY"]


def _key(cid, version, body):
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{cid}|{version}|{canonical}".encode()).hexdigest()[:32]


def _call(method, path, body=None, headers=None):
    req = urllib.request.Request(f"{BASE}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", f"Bearer {KEY}")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def replay(intent):
    cid, version, body, was = intent["candidate_id"], intent["base_version"], intent["body"], intent["was"]
    for _ in range(3):
        status, resp = _call("PATCH", f"/v1/candidates/{cid}", body,
                             {"If-Match": f'"{version}"', "Idempotency-Key": _key(cid, version, body)})
        if status in (200, 201):
            return {"result": "applied", "fields": list(body)}
        if status != 409:
            raise RuntimeError(f"PATCH {cid} failed: {status} {resp}")
        _, fresh = _call("GET", f"/v1/candidates/{cid}")
        rebased = {k: v for k, v in body.items() if fresh.get(k) == was.get(k) and fresh.get(k) != v}
        conflicts = [k for k, v in body.items() if fresh.get(k) not in (v, was.get(k))]
        if conflicts:
            print(json.dumps({"conflict_not_overwritten": conflicts, "candidate_id": cid}))
        if not rebased:
            return {"result": "nothing_to_apply", "conflicts": conflicts}
        body, version = rebased, fresh["version"]
    raise RuntimeError(f"{cid}: record kept changing")


def handler(event, context):
    for record in event.get("Records", []):
        intent = json.loads(record["body"])
        print(json.dumps({"replaying": intent.get("key"), "candidate_id": intent.get("candidate_id"),
                          "result": replay(intent)}))
    return {"ok": True}
