"""The agent loop: one structured LLM call per candidate message; code executes and validates the actions."""
from __future__ import annotations

import re

import dateparser

from agent.adapters.candidate_api import ApiError, CandidateRepo
from agent.adapters.runlog import RunLog
from agent.adapters.terminal import EOF, Terminal
from agent.config import IST, Config, now_ist
from agent.conversation import guards
from agent.conversation.session import Session
from agent.domain import schema
from agent.domain.diagnose import diagnose
from agent.domain.policy import build_notes, compute_status
from agent.llm import prompts
from agent.llm.client import LLM, LLMUnavailable
from agent.nlp.extract import extract_hints
from agent.nlp.normalize import normalize, parse_date

QUIT_WORDS = {"/quit", "/exit", "/bye"}
MAX_REJECTIONS_PER_FIELD = 3


class Agent:
    def __init__(self, cfg: Config, repo: CandidateRepo, term: Terminal, log: RunLog):
        self.cfg, self.repo, self.term, self.log = cfg, repo, term, log
        self._llm: LLM | None = None
        self.last_results: list[str] = []

    @property
    def llm(self) -> LLM:
        # Created lazily: complete records never touch the LLM at all.
        if self._llm is None:
            self._llm = LLM(self.cfg, prompts.SYSTEM_PROMPT, log=self.log)
        return self._llm

    # ================================================================ entry point

    def run(self, candidate_id: str) -> str:
        self._recover_unfinished_writes(candidate_id)
        record = self.repo.get(candidate_id)
        d = diagnose(record)
        self.log("diagnosis", {"missing": d.missing, "invalid": [vars(i) for i in d.invalid],
                               "optional_invalid": [vars(i) for i in d.optional_invalid],
                               "prior_state": d.prior_state})

        if d.complete:
            return self._already_complete(record)
        if not d.needed:
            return self._not_collectable(record, d)

        s = Session(candidate_id=candidate_id, record=record, diagnosis=d)
        self.term.info(f"{record['full_name']}: needs {', '.join(s.needed)}")
        try:
            self._converse(s)
        except KeyboardInterrupt:
            s.end_reason = s.end_reason or "chat interrupted (Ctrl+C)"
            print()
        return self._finalize(s)

    def _recover_unfinished_writes(self, candidate_id: str) -> None:
        """A previous run died mid-PATCH: replay it (same Idempotency-Key, so it can't double-apply)."""
        outbox = self.repo.outbox
        if not outbox:
            return
        for intent in outbox.pending(candidate_id):
            self.term.info(f"recovering an unfinished write from a previous run: {', '.join(intent['body'])}")
            try:
                res = self.repo.replay(intent)
                self.log("recovered_write", {"key": intent["key"], "applied": res.applied,
                                             "conflicts": res.conflicts})
            except ApiError as e:
                self.log("recovered_write_failed", {"key": intent["key"], "error": str(e)})

    def _already_complete(self, record: dict) -> str:
        self.term.info(f"{record['id']} {record['full_name']}: all mandatory fields are present and valid. "
                       f"No conversation needed.")
        if record.get("collection_status") != "completed":
            res = self.repo.patch(record, {"collection_status": "completed"})
            self.term.info(f"collection_status → completed (v{res.record.get('version')})")
        else:
            self.term.info("Already marked completed; nothing written.")
        self.log.save(status="completed", chat=False, llm_calls=0)
        return "completed"

    def _not_collectable(self, record: dict, d) -> str:
        """The only problems are in fields a chat can't fix (e.g. full_name). A human has to look."""
        problems = "; ".join(f"{i.field}={i.current!r}: {i.problem}" for i in d.invalid)
        self.term.info(f"Only non-collectable fields are invalid ({problems}). No chat; flagging for a human.")
        stamp = now_ist().strftime("%Y-%m-%d %H:%M IST")
        note = f"[{stamp}] Agent: no chat started. Invalid data the agent can't collect: {problems}."
        old = record.get("notes")
        res = self.repo.patch(record, {"collection_status": "needs_human",
                                       "notes": (note + (f"\n\n--- earlier ---\n{old}" if old else ""))[:4000]})
        self.log.save(status="needs_human", chat=False, llm_calls=0, final_record=res.record)
        return "needs_human"

    # ================================================================ conversation

    def _converse(self, s: Session) -> None:
        self._say(s, prompts.greeting(s))

        while True:
            msg = self._wait_for_reply(s)
            if msg is None:
                s.end_reason = "no reply (timed out)"
                self._say(s, prompts.TIMEOUT_BYE)
                return
            if msg is EOF or msg.strip().lower() in QUIT_WORDS:
                s.end_reason = "candidate left the chat (/quit)"
                self._say(s, prompts.QUIT_BYE)
                return
            if not msg.strip():
                continue
            s.add("candidate", msg.strip())
            s.turns += 1
            if s.turns > self.cfg.max_turns:
                s.end_reason = "turn limit reached"
                self._say(s, prompts.QUIT_BYE)
                return
            if s.readback_sent and not s.remaining and guards.is_plain_yes(msg):
                # Plain "yes" to the read-back summary: nothing to interpret, so no LLM call.
                s.end_reason = "candidate confirmed the summary"
                self._say(s, prompts.CONFIRMED_BYE)
                return
            if self._turn(s, msg.strip()):
                return

    def _wait_for_reply(self, s: Session):
        msg = self.term.ask(self.cfg.nudge_after_s)
        if msg is not None:
            return msg
        self._say(s, prompts.NUDGE)
        return self.term.ask(max(self.cfg.give_up_after_s - self.cfg.nudge_after_s, 1))

    def _say(self, s: Session, text: str) -> None:
        self.term.say(text)
        s.add("agent", text)

    def _turn(self, s: Session, msg: str) -> bool:
        """Handle one candidate message. Returns True when the conversation should end."""
        before = s.progress_signature()
        saves_before = s.save_events

        hints = extract_hints(msg)
        prompt = prompts.turn_prompt(s, hints, self.last_results, self.cfg.max_turns)
        try:
            out = self.llm.json_call(prompt, prompts.TURN_SCHEMA, "turn")
        except LLMUnavailable as e:
            s.llm_failed = True
            s.end_reason = f"language model unavailable ({str(e)[:120]})"
            self._say(s, prompts.LLM_DOWN_BYE)
            return True
        finally:
            s.llm_calls = self._llm.calls if self._llm else 0

        results, rejected, should_end = self._execute(s, out)
        reply = (out.get("reply") or "").strip()
        if rejected and not should_end:          # a closing message doesn't need a corrected re-ask
            try:
                fixed = self.llm.json_call(prompts.repair_prompt(prompt, reply, results),
                                           prompts.REPAIR_SCHEMA, "repair_reply")
                reply = (fixed.get("reply") or "").strip() or reply
            except LLMUnavailable:
                reply = "Sorry, I couldn't record that. Could you check it and send it again?"
            s.llm_calls = self.llm.calls

        reply, problems = guards.check_outgoing(reply)
        if problems:
            self.log("outgoing_guard", {"problems": problems})
            self.term.info(f"outgoing message cleaned: {'; '.join(problems)}")
        self._say(s, reply or "Sorry, could you say that again?")
        self.last_results = results
        s.idle_turns = 0 if s.progress_signature() != before else s.idle_turns + 1

        if should_end and (s.escalations or s.identity == "denied" or s.refused or s.followup
                           or s.remaining or s.gave_up or s.readback_sent):
            return True
        if s.identity == "confirmed" and not s.remaining and not s.refused and not s.gave_up \
                and not s.escalations:
            if not s.readback_sent or s.save_events != saves_before:
                # Everything is in. Read it back once (template, no LLM call) to catch extraction
                # mistakes; a correction re-triggers the read-back.
                s.readback_sent = True
                self._say(s, prompts.readback(s))
                return False
        return should_end

    # ================================================================ action execution

    def _execute(self, s: Session, out: dict) -> tuple[list[str], bool, bool]:
        results: list[str] = []
        rejected = False
        should_end = False
        actions = out.get("actions") or []
        self.log("actions", {"identity": out.get("identity"), "sentiment": out.get("sentiment"),
                             "actions": actions})

        identity = out.get("identity", "unknown")
        if identity == "denied" and s.identity != "denied":
            s.identity = "denied"
            s.held.clear()
            s.escalations.append("person on the chat says they are not the candidate")
            self.term.info("identity DENIED → nothing will be saved; escalating")
            return ["identity denied: nothing saved, conversation ends"], False, True
        if identity == "confirmed" and s.identity == "unknown":
            s.identity = "confirmed"
            self.term.info("identity confirmed")
            actions = s.held + actions          # saves offered before confirmation are processed now
            s.held = []

        batch: dict = {}
        for a in actions:
            kind = a.get("type")
            field = a.get("field") or ""
            if kind == "save":
                if s.identity != "confirmed":
                    s.held.append(a)
                    results.append(f"save {field}: held until the candidate confirms their identity")
                    continue
                ok, value, msg = self._check_save(s, a, batch)
                if ok:
                    batch[field] = value
                else:
                    rejected = True
                    results.append(f"save {field}={a.get('value')!r}: REJECTED. {msg}")
                    self.term.info(f"rejected {field}={a.get('value')!r}: {msg}")
                    if not msg.startswith("INVALID: "):
                        continue                 # a confirmation step or an LLM slip, not a wrong answer
                    s.failed_attempts[field] = s.failed_attempts.get(field, 0) + 1
                    if s.failed_attempts[field] >= MAX_REJECTIONS_PER_FIELD and field in s.needed:
                        s.gave_up[field] = f"{s.failed_attempts[field]} answers were rejected by the rules"
                        results.append(f"{field}: STOP asking for it; it's flagged for a human")
                        self.term.info(f"giving up on {field} after {s.failed_attempts[field]} rejected answers")
            elif kind == "refuse":
                if field in s.needed and field not in s.saved:
                    s.refused[field] = a.get("evidence") or "declined"
                    results.append(f"refuse {field}: recorded")
                    self.term.info(f"refused: {field}")
            elif kind == "followup":
                text = (a.get("text") or a.get("evidence") or "").strip()
                when = parse_date_time(text)
                s.followup = {"text": text or "later (no time given)", "iso": when}
                results.append(f"followup: recorded ({when or 'no specific time'})")
                self.term.info(f"follow-up requested: {text or 'no time given'}" + (f" → {when}" if when else ""))
            elif kind == "escalate":
                reason = (a.get("text") or "agent escalated").strip()
                s.escalations.append(reason)
                results.append("escalate: recorded")
                self.term.info(f"escalated: {reason}")
            elif kind == "note":
                if a.get("text"):
                    s.team_notes.append(a["text"].strip())
            elif kind == "end":
                should_end = True
                s.end_reason = (a.get("text") or "conversation finished").strip()

        if batch:
            results += self._write(s, batch)
            if any("NOT saved" in r for r in results):
                rejected = True
        return results, rejected, should_end

    def _check_save(self, s: Session, a: dict, batch: dict) -> tuple[bool, object, str]:
        field = a.get("field") or ""
        raw = (a.get("value") or "").strip()
        evidence = (a.get("evidence") or "").strip()
        confirmed = bool(a.get("candidate_confirmed"))

        if field not in schema.COLLECTABLE_FIELDS:
            return False, None, f"'{field}' isn't a field we collect."
        if field in s.gave_up:
            return False, None, f"{field} is flagged for a human; don't ask for it again."
        if field not in s.needed and field not in s.saved and field in schema.MANDATORY_FIELDS:
            on_file = s.record.get(field)
            if not confirmed:
                return False, None, (f"CONFIRM: {field} is already on file as {on_file!r} and is valid. Only "
                                     f"change it if the candidate says it's wrong; confirm the new value first.")

        if not guards.grounded(evidence, s.candidate_messages):
            return False, None, ("The evidence isn't the candidate's words. Only save what the candidate actually "
                                 "said; ask them directly.")
        if guards.is_hedged(field, evidence) and not confirmed:
            return False, None, (f"CONFIRM: the candidate hedged ({evidence!r}). Restate one exact value and ask "
                                 f"them to confirm it; save it with candidate_confirmed=true once they say yes.")

        res = normalize(field, raw)
        if not res.ok:
            if res.soft and confirmed:
                value = res.value
            else:
                hint = f" Suggested value to offer: {res.suggestion}." if res.suggestion else ""
                prefix = "CONFIRM: " if res.soft else "INVALID: "
                return False, None, prefix + res.error + hint
        else:
            value = res.value

        if not guards.value_traceable(field, value, s.candidate_messages, s.last_agent_message, confirmed):
            return False, None, (f"{value!r} doesn't appear in what the candidate said. Don't infer values: "
                                 f"ask them to confirm the exact {schema.FIELD_LABELS.get(field, field)}.")

        known = guards.known_values(s.record, {**s.saved, **batch})
        for rule, message in guards.cross_field_issues(field, value, known):
            if rule not in s.soft_warned and not confirmed:
                s.soft_warned.add(rule)        # raised once; accepted next time
                return False, None, f"CHECK: {message}"
        return True, value, ""

    def _write(self, s: Session, batch: dict) -> list[str]:
        try:
            res = self.repo.patch(s.record, batch)
        except ApiError as e:
            s.write_errors.append(f"could not save {', '.join(batch)} ({e}); queued in the DLQ for replay")
            self.term.info(f"WRITE FAILED for {', '.join(batch)}: {e} → dead-lettered for replay")
            return [f"save {', '.join(batch)}: NOT saved due to a system error; tell the candidate we couldn't "
                    f"save it right now and a team member will follow up"]
        s.record = res.record
        results = []
        for k in res.applied:
            s.saved[k] = res.applied[k]
            s.refused.pop(k, None)             # they refused earlier but gave it after all
            s.save_events += 1
            results.append(f"save {k}={res.applied[k]!r}: saved")
        for k in res.skipped_noop:
            s.saved[k] = batch[k]
            s.refused.pop(k, None)
            results.append(f"save {k}={batch[k]!r}: already on file, nothing to change")
        for k, c in res.conflicts.items():
            s.conflicts[k] = c
            results.append(f"save {k}: NOT saved, the record was changed by someone else meanwhile "
                           f"({c['theirs']!r}); a team member will review")
        if res.applied:
            self.term.info("saved " + ", ".join(f"{k}={v!r}" for k, v in res.applied.items())
                           + f" (v{s.record.get('version')})")
        if res.conflicts:
            self.term.info(f"CONFLICT on {', '.join(res.conflicts)}: not overwritten")
        return results

    # ================================================================ finalize

    def _finalize(self, s: Session) -> str:
        status = compute_status(s)
        try:
            fresh = s.record
            notes = build_notes(s, status, fresh.get("notes"))
            changes = {
                "collection_status": status,
                "last_contacted_at": now_ist().isoformat(),
                "attempt_count": min((fresh.get("attempt_count") or 0) + 1, 50),
                "notes": notes,
            }
            res = self.repo.patch(fresh, changes)
            if res.conflicts:
                # Tracking fields are ours to maintain; rebuild on the newest copy and write again.
                fresh = res.record
                changes["notes"] = build_notes(s, status, fresh.get("notes"))
                changes["attempt_count"] = min((fresh.get("attempt_count") or 0) + 1, 50)
                res = self.repo.patch(fresh, changes)
            s.record = res.record
            write_ok = True
        except ApiError as e:
            write_ok = False
            self.log("final_write_failed", {"error": str(e), "status": status})
            print(f"\n!! Could not write the final status ({e}). It's in the DLQ for replay.")

        print("\n" + "-" * 60)
        print(f"Outcome: {status}" + ("" if write_ok else "  (NOT written yet: API error, dead-lettered)"))
        if s.saved:
            print("Saved: " + ", ".join(f"{k}={v}" for k, v in s.saved.items()))
        if s.remaining:
            print("Still needed: " + ", ".join(s.remaining))
        if s.refused:
            print("Refused: " + ", ".join(s.refused))
        if s.gave_up:
            print("Flagged for a human: " + ", ".join(s.gave_up))
        print(f"LLM calls: {s.llm_calls}")
        path = self.log.save(status=status, chat=True, llm_calls=s.llm_calls, end_reason=s.end_reason,
                             saved=s.saved, refused=s.refused, remaining=s.remaining, gave_up=s.gave_up,
                             escalations=s.escalations, followup=s.followup, transcript=s.transcript,
                             final_record=s.record)
        print(f"Run log: {path}")
        return status


def parse_date_time(text: str) -> str | None:
    """'tomorrow 6pm' → '2026-11-15T18:00:00+05:30'. None if there's no recognisable time."""
    if not text:
        return None
    # dateparser reads "IST" as Israel Standard Time; all our times are India time anyway.
    text = re.sub(r"\bIST\b", "", text, flags=re.I).strip()
    dt = dateparser.parse(text, languages=["en", "hi"], settings={
        "PREFER_DATES_FROM": "future", "RELATIVE_BASE": now_ist().replace(tzinfo=None),
        "DATE_ORDER": "DMY"})
    if dt is None:
        d = parse_date(text)
        return d.isoformat() if d else None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.replace(microsecond=0).isoformat()
