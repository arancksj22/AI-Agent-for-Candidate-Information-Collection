"""Work out what a record needs before any conversation. Pure code, no LLM."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from agent.domain import schema
from agent.nlp.normalize import is_empty, normalize

STATE_MARKER = "---agent-state v1---"


@dataclass
class Issue:
    field: str
    current: object
    problem: str
    suggestion: object = None


@dataclass
class Diagnosis:
    missing: list[str] = field(default_factory=list)
    invalid: list[Issue] = field(default_factory=list)
    optional_invalid: list[Issue] = field(default_factory=list)
    prior_state: dict | None = None

    @property
    def needed(self) -> list[str]:
        """Fields to collect, in the suggested asking order."""
        fields = set(self.missing) | {i.field for i in self.invalid}
        return [f for f in schema.ASK_PRIORITY if f in fields]

    @property
    def complete(self) -> bool:
        return not self.missing and not self.invalid


def diagnose(record: dict) -> Diagnosis:
    d = Diagnosis()
    for f in schema.MANDATORY_FIELDS:
        if f == "phone":
            continue                     # read-only and OTP-verified
        value = record.get(f)
        if is_empty(value):
            d.missing.append(f)
            continue
        res = normalize(f, value)
        if not res.ok and not res.soft:
            # Soft failures (a past start date, a likely email typo) are legal values: they're worth
            # confirming when a candidate gives them, but not worth starting a chat over.
            d.invalid.append(Issue(f, value, res.error, res.suggestion))
        elif f in ("degree", "preferred_location", "internship_duration_months") and res.value != value:
            # Not a valid enum value as stored, even if we can guess what was meant.
            d.invalid.append(Issue(f, value, f"'{value}' isn't an exact allowed value", res.value))
    for f in schema.OPTIONAL_FIELDS:
        value = record.get(f)
        if not is_empty(value):
            res = normalize(f, value)
            if not res.ok:
                d.optional_invalid.append(Issue(f, value, res.error))
    d.prior_state = parse_state_block(record.get("notes"))
    return d


def parse_state_block(notes: str | None) -> dict | None:
    if not notes or STATE_MARKER not in notes:
        return None
    tail = notes.split(STATE_MARKER, 1)[1]
    m = re.search(r"\{.*?\}(?=\s*(?:\n|$))", tail, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return None


def describe(record: dict, d: Diagnosis) -> str:
    lines = [f"{record['id']}  {record.get('full_name')}  (status={record.get('collection_status')}, "
             f"v{record.get('version')})"]
    if d.complete:
        lines.append("  ✓ complete: no conversation needed")
    if d.missing:
        lines.append(f"  missing : {', '.join(d.missing)}")
    for i in d.invalid:
        sug = f"  → suggest {i.suggestion!r}" if i.suggestion is not None else ""
        lines.append(f"  invalid : {i.field}={i.current!r}: {i.problem}{sug}")
    for i in d.optional_invalid:
        lines.append(f"  optional: {i.field}={i.current!r}: {i.problem}")
    if d.prior_state:
        lines.append(f"  prior   : {d.prior_state}")
    return "\n".join(lines)
