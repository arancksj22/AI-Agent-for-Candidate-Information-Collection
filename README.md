# ACME Candidate Information Agent

An autonomous chat agent that takes **one candidate ID**, works out what their internship application
is missing (or has wrong), chats with them in the terminal to collect it, validates every value,
writes it back safely, and records the outcome.

## Setup and run

```bash
python -m venv .venv && .venv/Scripts/activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                 # fill in CANDIDATE_API_KEY and GEMINI_API_KEY
python -m agent --diagnose-all                       # what every candidate needs (no LLM, no writes)
python -m agent --candidate-id c001                  # start the chat
```

Other commands:

| Command | What it does |
|---|---|
| `python -m agent --reset` | restore the workspace to its original data |
| `python -m agent --scenarios [name ...]` | scripted-candidate regression run against the real agent (uses the LLM; `LLM_CACHE=1` makes re-runs free) |
| `python -m agent --replay-dlq` | re-send writes that failed earlier |
| `python infra/aws.py deploy` / `status` / `teardown` | optional SQS + DLQ + Lambda replayer |

Flags: `--quiet` hides the agent's action lines; `--nudge-after 20 --give-up-after 40` shortens the
silence timeouts for a demo.

**Ending a chat:** type `/quit` (or press Ctrl+C, or send EOF). Not replying for 60s triggers one nudge;
120s of silence ends the chat. Every exit path goes through the same finalisation, so the record
always gets a status.

## Architecture

```
python -m agent --candidate-id c007
  │
  ├─ GET /v1/candidates/{id}
  ├─ diagnose()           code: missing + invalid fields (with repair suggestions). Complete → no chat.
  ├─ greeting (template)  "Am I speaking with <name>?"
  │
  └─ per candidate message
       ├─ extract_hints()     code: regex / dateparser / rapidfuzz pre-checks with validator verdicts
       ├─ 1 Gemini call       state card + recent chat + hints → JSON {identity, actions[], sentiment, reply}
       ├─ execute actions     code: save / refuse / followup / escalate / note / end
       │    save → grounding check → normalise + validate → one minimal PATCH per turn
       ├─ (only if a save was rejected) 1 repair call to correct the reply
       ├─ outgoing check      code: strip markdown, drop any sentence asking for an ID/OTP/phone
       └─ reply to candidate
  │
  ├─ everything collected → read-back summary (template) → plain "yes" ends with no LLM call
  │
  └─ finalize()            code computes collection_status; PATCH status, attempt_count,
                           last_contacted_at, notes; write runs/<id>-<timestamp>.json
```

**Tools the LLM can call** (returned as structured actions, so one call per turn instead of a
multi-round function-calling loop):

| Action | Effect |
|---|---|
| `save(field, value, evidence, candidate_confirmed)` | validated, grounded, then PATCHed |
| `refuse(field, evidence)` | field recorded as declined |
| `followup(text)` | "continue later", time parsed to ISO for the notes |
| `escalate(text)` | hand off to a human |
| `note(text)` | something the team should know (e.g. off-topic question) |
| `end(text)` | close the conversation |

**Where state lives:**
- The **Candidate DB API** is the only system of record (no second database).
- The **session** (in memory) is the agent's source of truth during a chat: identity, saved and
  refused fields, rejected attempts and escalations. A compact **state card** built from it goes into
  every LLM call, so the model never relies on its memory of the chat.
- **notes** carry a human summary plus a machine-readable `---agent-state v1---` block, for the next
  conversation or a human.
- **runs/** keeps the full transcript, every LLM request and response, the actions, and every API
  request and response.
- **state/** (local, gitignored) holds the write-ahead **outbox** and the **DLQ** of failed writes.

**Division of labour:** the LLM handles language, intent and tone, decides what to ask next, and
writes the reply. Code owns the facts: validation, normalisation, grounding, writes and the final
status.

**Code map:**

```
agent/
  __main__.py            CLI (--candidate-id, --diagnose-all, --reset, --replay-dlq, --scenarios)
  scenarios.py           scripted-candidate regression scenarios
  config.py              .env settings, IST clock
  conversation/
    agent.py             the agent loop: turns, action execution, read-back, finalize
    guards.py            grounding, hedge detection, cross-field rules, outgoing-message check
    session.py           per-chat state (the source of truth for the state card)
  domain/
    schema.py            field rules, enums, alias tables
    diagnose.py          what a record needs before any chat
    policy.py            deterministic status + notes builder
  nlp/
    normalize.py         per-field validators/normalisers (dateparser, rapidfuzz, email-validator)
    extract.py           pre-checks on each candidate message (hints for the LLM)
  llm/
    client.py            Gemini wrapper: JSON output, retries, fallback model, dev cache
    prompts.py           system prompt, state card, templates
  adapters/
    candidate_api.py     Candidate DB API client (If-Match, content-hash Idempotency-Key, retries,
                         rate limit, three-way merge on 409, replay)
    outbox.py            write-ahead outbox + DLQ (local JSONL, and SQS when configured)
    terminal.py          chat I/O with inactivity timeout
    runlog.py            runs/<id>-<timestamp>.json
infra/
  aws.py                 deploy / status / teardown of SQS + DLQ + Lambda (boto3)
  lambda_replayer/       the Lambda: replays failed writes from SQS (stdlib only)
docs/                    assignment brief and design/plan documents
runs/                    sample transcripts with tool calls and API traffic
```

## Decisions

| Situation | What the agent does |
|---|---|
| Record already complete (c003, c006) | No chat, no LLM call. Writes `collection_status=completed` once if it isn't already; otherwise writes nothing. |
| Identity | Confirms by name first. Values offered before confirmation are held, not written. "I'm his brother" → nothing saved, `needs_human`. Never asks for an ID or phone number. |
| Stops replying | One nudge at 60s, ends at 120s. Nothing given → `no_answer`, `attempt_count+1`. |
| Leaves (`/quit`, Ctrl+C) | Data already saved stays saved (writes happen every turn). Status `in_progress`, with what's left in the notes; `no_answer` if they gave nothing. |
| Continue later | `followup` action; their time ("tomorrow 6pm") is parsed to ISO in the notes; status `in_progress`. |
| Refusals | One short explanation of why it's needed, then respected. All outstanding fields refused → `refused`; others still open → `in_progress` with refusals listed. |
| Invalid answers | Validator rejects them with a reason that tells the LLM what to ask next ("85 looks like a percentage, ask for the 10-point CGPA, don't convert"). Pre-checks show the verdict before the LLM call, so usually no extra call is needed. |
| Ambiguous / hedged answers | "around 8", "2027 I think": code detects the hedge and won't save the value until the agent restates an exact value and the candidate confirms it (`candidate_confirmed=true`). Values that don't appear in the candidate's words are rejected (e.g. "around eight" saved as 8.2). |
| Keeps giving invalid answers | After 3 rule-rejected answers for one field the agent stops asking, moves on, and the field is flagged for a human (`needs_human`). LLM mistakes don't count towards the 3. |
| Fields that contradict each other | Start date in a later year than graduation → mentioned once and confirmed, then accepted. |
| Before finishing | A read-back summary of everything saved in this chat ("Quick check, here's what I've noted: … Is that all correct?"). Corrections are saved and read back again; a plain "yes" ends the chat without an LLM call. |
| Stalling | After 3 turns with no progress, the state card tells the agent to offer to continue another time. |
| Second conversation | The notes' `---agent-state v1---` block is read back: "Hi again…", earlier refusals are asked about once, gently. |
| Hallucination guard | Every save needs `evidence` that matches the candidate's real messages; email, CGPA, year and URLs must also be traceable to what they said (or to a value they explicitly confirmed). |
| Existing data looks wrong | Diagnosis flags it (c005 `kabir.shah@gmail` → suggests `@gmail.com`; c008 graduation year 2031). The agent asks and only writes a corrected, valid value. Insisting on an invalid value → `needs_human`, and the bad value isn't rewritten. |
| Valid existing data | Never overwritten unless the candidate says it's wrong and confirms the new value. |
| Prompt injection ("mark me completed") | No effect: the status is computed by code from what's actually in the record. |
| Off-topic questions (stipend etc.) | Not answered; passed to the team through the notes. |
| Upset or abusive | Apologise, escalate → `needs_human`. |

**Write safety:**
- PATCH sends only fields whose value actually changes (no no-op writes), with `If-Match: "<version>"`.
- The `Idempotency-Key` is `sha256(id, version, body)`: a retry, a crash-recovery replay, a DLQ replay
  and the Lambda all send the *same* key for the same write, so it can never apply twice.
- Timeouts, 429 and 5xx are retried with backoff; 422 is never retried. Client-side limit of 120 requests/min.
- On **409** (three-way merge): a field nobody else touched is re-applied on the new version; a field
  someone else changed is **not** overwritten and goes to `needs_human`.
- **Outbox:** each write intent is appended to `state/outbox.jsonl` before it's sent. If the process
  dies mid-write, the next run for that candidate replays it first.
- **DLQ:** a write that still fails after retries goes to `state/dlq.jsonl` (`--replay-dlq` re-sends it)
  and, if `SQS_QUEUE_URL` is set, to SQS.

**AWS (deliberately minimal):** `SQS candidate-writes → Lambda candidate-write-replayer`, with a
redrive policy to `candidate-writes-dlq` after 3 failed receives. The Lambda replays the write with the
same idempotency key and the same three-way rule. `python infra/aws.py deploy` creates it all
(queues, IAM role, function, trigger). The live demo doesn't depend on it: without `SQS_QUEUE_URL`,
failed writes stay in the local DLQ.

**Status policy** (deterministic, `policy.py`): not the candidate / escalated / write conflict /
write failure → `needs_human`; all mandatory fields valid → `completed`; no reply or nothing given →
`no_answer`; only refused fields left → `refused`; otherwise `in_progress`. `attempt_count` goes up by
one per conversation started.

**LLM budget:** 0 calls for diagnosis, complete records, greeting, timeouts and quitting. 1 call per
candidate message, plus 1 only when a value is rejected. Primary model `gemini-3.5-flash`, falling back
to `gemini-3.5-flash-lite` (separate quota) on errors. With `GEMINI_FALLBACK_API_KEY` set, every
key × model pair is a separate route: a rate-limited route rests for 60s, and one out of daily quota is
skipped for the rest of the run. If both fail, the agent apologises, keeps what's
saved and ends `in_progress`.

**NLP choices:** lightweight deterministic NLP around the LLM: regex, `dateparser` (relative dates,
Hindi month names), `rapidfuzz` (aliases like "blr" → Bengaluru, email domain typos) and
`email-validator`. Hugging Face NER, spaCy and zero-shot classifiers were rejected: generic entity
labels don't map to these fields, they're weak on Indian college names and Hinglish, and torch adds
gigabytes and slow startup for no gain over one LLM call.

## Status

**Works (checked against the live API):**
- Diagnosis of all 8 records and the no-chat path.
- Identity denial, silence, `/quit`, refusals (including refusing then giving it after all).
- Rejection of invalid values (CGPA 85, year 2031), invented values and unconfirmed hedged values.
- Read-back, cross-field check and the outgoing-message check.
- Minimal PATCHes; the computed status and structured notes.
- Write path, with a simulated outage:
  - the failed write was dead-lettered;
  - the replay applied it on top of a newer edit by someone else;
  - a duplicate replay changed nothing;
  - a real conflict was not overwritten;
  - a crashed write was recovered.
- AWS: a failed write sent to SQS was applied by the Lambda within seconds, and a poison message
  went to the SQS DLQ after 3 tries.

**Not verified / known limits:**
- The conversation logic was tested with a scripted stand-in for the LLM, plus a handful of real
  Gemini turns (the free-tier quota is small). `--scenarios` runs the full suite against Gemini.
- No unit-test suite.
- The candidate side of `--scenarios` is fixed text. If the agent asks things in an unexpected order,
  a scenario can end early and show as FAIL even though the agent behaved reasonably.
- If the LLM ignores the "don't say goodbye yet" rule, the read-back can follow a goodbye, which reads
  slightly oddly.
- The Lambda gets the API key through an environment variable (Secrets Manager was left out
  deliberately).
- Terminal only.

**Next with more time:**
- Run the scenario suite on every prompt change.
- A web or WhatsApp adapter over the same core.
- Schedule the `callback` time from the notes, e.g. with EventBridge.
