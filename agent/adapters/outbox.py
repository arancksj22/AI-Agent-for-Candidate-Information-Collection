"""Write-ahead outbox and dead-letter queue for PATCHes to the Candidate DB.

Every write intent is appended to state/outbox.jsonl *before* it's sent and marked done after a
2xx, so a crash mid-write is recovered on the next run (the intent is replayed with the same
content-derived Idempotency-Key, so it can never apply twice). A write that still fails after
retries is dead-lettered to state/dlq.jsonl and, if SQS_QUEUE_URL is set, to SQS, where a Lambda
replays it (with an SQS DLQ behind that).
"""
from __future__ import annotations

import json
from pathlib import Path

from agent.config import Config, now_ist


class Outbox:
    def __init__(self, cfg: Config):
        self.outbox_path = cfg.state_dir / "outbox.jsonl"
        self.dlq_path = cfg.state_dir / "dlq.jsonl"
        self.sqs_queue_url = cfg.sqs_queue_url
        self.aws_region = cfg.aws_region

    # ------------------------------------------------------------ outbox (write-ahead)

    def begin(self, intent: dict) -> None:
        _append(self.outbox_path, {**intent, "status": "pending", "at": now_ist().isoformat()})

    def done(self, key: str, status: str = "done") -> None:
        _append(self.outbox_path, {"key": key, "status": status, "at": now_ist().isoformat()})

    def pending(self, candidate_id: str | None = None) -> list[dict]:
        """Intents that were started but never finished (the process died mid-write)."""
        return [i for i in _open_intents(self.outbox_path)
                if candidate_id is None or i["candidate_id"] == candidate_id]

    # ------------------------------------------------------------ DLQ

    def dead_letter(self, intent: dict, error: str) -> str:
        entry = {**intent, "error": error, "status": "dead", "at": now_ist().isoformat()}
        _append(self.dlq_path, entry)
        self.done(intent["key"], status="dead")
        where = str(self.dlq_path)
        if self.sqs_queue_url:
            try:
                import boto3
                boto3.client("sqs", region_name=self.aws_region).send_message(
                    QueueUrl=self.sqs_queue_url, MessageBody=json.dumps(intent, default=str))
                where += " + SQS"
            except Exception as e:           # SQS is best-effort; the local DLQ already has it
                where += f" (SQS send failed: {e!r})"
        return where

    def dlq_open(self) -> list[dict]:
        return _open_intents(self.dlq_path, open_status="dead")

    def dlq_resolve(self, key: str, status: str) -> None:
        _append(self.dlq_path, {"key": key, "status": status, "at": now_ist().isoformat()})


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def _open_intents(path: Path, open_status: str = "pending") -> list[dict]:
    if not path.exists():
        return []
    latest: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("status") == open_status and "body" in row:
            latest[row["key"]] = row
        else:
            latest.pop(row.get("key"), None)
    return list(latest.values())
