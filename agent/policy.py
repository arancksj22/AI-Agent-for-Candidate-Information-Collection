"""Deterministic outcome policy and notes builder. The LLM never sets collection_status."""
from __future__ import annotations

import json
import re

from . import schema
from .config import now_ist
from .diagnose import STATE_MARKER, diagnose
from .session import Session


def compute_status(s: Session) -> str:
    if s.identity == "denied" or s.escalations or s.conflicts or s.write_errors:
        return "needs_human"
    if diagnose(s.record).complete:
        return "completed"
    if not s.candidate_replied:
        return "no_answer"
    if s.refused and not s.remaining:
        return "refused"
    if not s.saved and not s.refused and not s.followup:
        return "no_answer"          # replied, but left before giving anything
    return "in_progress"


def build_notes(s: Session, status: str, previous_notes: str | None) -> str:
    stamp = now_ist().strftime("%Y-%m-%d %H:%M IST")
    parts = [f"[{stamp}] Agent chat → {status}."]
    if s.identity == "denied":
        parts.append("The person who replied said they are NOT the candidate; nothing was collected.")
    if s.saved:
        parts.append("Collected: " + ", ".join(f"{k}={v}" for k, v in s.saved.items()) + ".")
    if s.refused:
        parts.append("Refused: " + "; ".join(f"{k} (\"{v}\")" for k, v in s.refused.items()) + ".")
    remaining = [f for f in s.remaining]
    if remaining and status != "completed":
        parts.append("Still needed: " + ", ".join(remaining) + ".")
    unresolved = [i for i in s.diagnosis.invalid if i.field not in s.saved]
    for i in unresolved:
        parts.append(f"Existing {i.field}={i.current!r} is invalid and was not changed.")
    if s.followup:
        parts.append(f"Candidate asked to continue later: \"{s.followup['text']}\""
                     + (f" (≈ {s.followup['iso']})" if s.followup.get("iso") else "") + ".")
    for e in s.escalations:
        parts.append(f"Needs human: {e}.")
    for k, c in s.conflicts.items():
        parts.append(f"Write conflict on {k}: someone else set {c['theirs']!r}; we did not overwrite "
                     f"(candidate said {c['ours']!r}).")
    for e in s.write_errors:
        parts.append(f"Write failed: {e}.")
    if s.llm_failed:
        parts.append("Chat ended early because the language model was unavailable.")
    if s.end_reason:
        parts.append(f"End reason: {s.end_reason}.")
    for n in s.team_notes:
        parts.append(f"For the team: {n}.")

    state = {
        "remaining": remaining,
        "refused": list(s.refused),
        "callback": (s.followup or {}).get("iso"),
        "status": status,
    }
    entry = " ".join(parts) + f"\n{STATE_MARKER}\n{json.dumps(state, separators=(',', ':'))}"

    # Keep earlier human-readable history (minus its old machine block), newest first.
    old = re.sub(rf"\n?{re.escape(STATE_MARKER)}\n\{{[^\n]*\}}", "", previous_notes or "").strip()
    notes = entry if not old else f"{entry}\n\n--- earlier ---\n{old}"
    if len(notes) > schema.NOTES_MAX:
        keep = schema.NOTES_MAX - len(entry) - 20
        notes = entry if keep < 50 else f"{entry}\n\n--- earlier ---\n{old[:keep]}…"
    return notes[: schema.NOTES_MAX]
