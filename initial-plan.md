# Initial Plan: AI Agent for Candidate Information Collection

> **Strategy:** ship a **basic version that works 100%** first, then improve it in small iterations.
> Each iteration must keep everything that already works working, proven by the eval suite from v2 on.
> Every extra has to fix a real failure mode in *this* problem; rejected ideas are in §8.

---

## 1. The problem in one paragraph

A Google Form left mandatory fields optional, so internship applications are incomplete, and some
of the data that *is* there is wrong. Given **one candidate ID**, our agent fetches the record,
works out what's missing or invalid, chats with the candidate (the evaluator types the replies),
collects and validates the data, writes it back **safely and minimally**, records the outcome
(`completed`, `in_progress`, `no_answer`, `refused`, `needs_human`), and exits. The LLM must *decide*
what to do; a fixed question script scores badly.

## 2. What we know about the data (from hitting the API)

| ID | Situation | What it tests |
|---|---|---|
| c001 | missing `email`, `cgpa` | basic happy path |
| c002 | missing `graduation_year`, `available_from`, `internship_duration_months` | dates and enums |
| c003 | complete and valid | **must not start a chat** |
| c004 | missing `college`, `degree`, `branch` | enum normalisation ("btech" → `B.Tech`) |
| c005 | missing `preferred_location`; **email `kabir.shah@gmail` is invalid** | fixing bad existing data |
| c006 | complete (only optional fields empty) | **must not start a chat** |
| c007 | everything missing | long chat, likely abandon or "later" |
| c008 | missing `cgpa`; **graduation_year 2031 is out of range** | contradiction, possible `needs_human` |

`GET /v1/schema` adds rules the README omits: `branch` 2–100 chars, `email` max 254, each `skill` 1–50 chars.

---

## 3. Iteration roadmap

| Version | Theme | Exit criteria (must all pass before moving on) |
|---|---|---|
| **v1** | Basic, but 100% correct end to end | manual checklist §3.1 passes on all 8 candidates |
| **v2** | Prove it: eval harness | persona suite runs green against the real API |
| **v3** | Data correctness: grounding, confirmation, repair | suite and new extraction personas green |
| **v4** | Agent quality: context engineering and conversation | suite green, transcripts read naturally |
| **v5** | Resilience: write path hardening and LLM fallback | fault-injection personas green |
| **v6** | Infra: SQS + DLQ + Lambda replayer | same suite green in cloud mode |

Commit at least once per version (the history is graded). If time runs out, whatever version we're on
is still a complete, working submission.

### 3.1 v1: basic, but 100%

The smallest thing that's fully correct. No cleverness, no gaps.

1. **CLI:** `python -m agent --candidate-id c001`.
2. **Diagnose (deterministic):** validate every mandatory field against the schema → `missing`,
   `invalid`. If both are empty: write `collection_status=completed` only if it isn't already, and exit
   with no chat.
3. **Identity check by name.** Denial → `needs_human`, no data writes.
4. **Agent loop:** the LLM has tool calling. v1 tools:
   - `save_fields({field: value, ...})`: validated and normalised in Python, invalid values return an
     error message to the LLM, valid ones are PATCHed immediately (progress is saved mid-chat).
   - `mark_refused(field)`
   - `escalate(reason)` → needs_human
   - `end_conversation(summary)`
5. **Validator/normaliser:** Pydantic rules from the schema, plus normalisers for degree aliases,
   location aliases, date formats, `"8.4/10"`, and month words for duration.
6. **Safe PATCH:** `If-Match: version`, a fresh `Idempotency-Key` per logical write (reused on retry),
   send only fields that differ from the stored value, and on 409 re-GET and re-apply only our fields.
7. **Outcome computed by code** from session state (§4.6), never chosen by the LLM.
8. **Exits:** `/quit`, inactivity timeout and Ctrl+C all go through one `finalize()`, which always
   writes the status, `attempt_count`, `last_contacted_at` (IST offset) and `notes`.
9. **Run log:** `runs/<id>-<ts>.json` with the transcript, tool calls and API requests/responses.
10. **README:** setup ≤ 5 commands, `.env.example`, architecture, decisions, honest status.

**v1 manual checklist:** c003/c006 → no chat; c001 happy path → `completed`; c007 quit midway →
`in_progress` with what's left in the notes; silence → `no_answer` and `attempt_count+1`; refuse
CGPA → `refused`; "CGPA 85" → never stored; wrong person → `needs_human`; c005 email fixed;
c008 "2031" → not stored.

### 3.2 v2: eval harness (built early so later iterations are measured)

`python -m agent.eval` uses a second LLM as a **simulated candidate** following persona scripts.
Before each scenario it calls `POST /v1/reset`, runs the real agent, then **asserts on the final DB
record and the write history** (which fields changed, how many writes, the final status).

| Persona | Expected outcome |
|---|---|
| cooperative | `completed`, exact values, minimal writes |
| 3 fields in one message, Hinglish | `completed` |
| "CGPA 85" / "around 8" | asks back, never stores 85 or a guess |
| refuses CGPA after the explanation | `refused`, notes list `cgpa` |
| "talk tomorrow 6pm" halfway through | `in_progress`, callback time in notes, partial data saved |
| silent from the start | `no_answer`, `attempt_count+1`, no data writes |
| wrong person | `needs_human`, no data writes |
| insists on 2031 (c008) | `needs_human`, 2031 not rewritten |
| prompt injection ("mark me completed") | status still computed correctly |
| complete record (c003/c006) | no chat, ≤ 1 write |
| corrects an earlier answer ("actually 8.6") | final value 8.6 |
| asks off-topic questions (stipend?) | answers or notes it, then gets back on track |

Plus plain **pytest** for the validator, outcome policy and merge logic (no LLM needed).
The results table goes into the README.

### 3.3 v3: data correctness

- **Grounded extraction:** every saved value carries `evidence`, the candidate's exact words.
  The tool checks deterministically that the evidence appears in a real candidate message and that
  the value can be derived from it. Inferred values ("third year" → 2027) are rejected unless the
  candidate confirmed them explicitly.
- **Value lifecycle:** `unknown → proposed → confirmed → committed`. Hedged answers ("about",
  "I think", ranges) stay `proposed` until the candidate pins them down; only confirmed values are written.
- **Repair suggestions for bad existing data:** `kabir.shah@gmail` → "did you mean
  `kabir.shah@gmail.com`?"; `2031` → "did you mean 2027?". The agent asks a yes/no question. If the
  candidate insists on an invalid value → `needs_human`, with both values in the notes. Never write
  an invalid value.
- **Cross-field consistency (soft rules, which ask once and never block):** start date long after
  graduation or already in the past; 6 months from `available_from` runs past 2027-12-31; CGPA given
  as a percentage → ask for the 10-point value (**never auto-convert**).

### 3.4 v4: agent quality (context engineering and conversation design)

This is where the "agentic behaviour" score (20) and "naturalness" score (15) come from.
Details in §4.

### 3.5 v5: resilience

- **Field-level three-way merge on 409:** a field changed by someone else that we aren't writing →
  rebase onto the new version; a field we *are* writing that someone else changed to a different value
  → conflict → `needs_human`.
- **Content-derived idempotency keys:** `sha256(candidate_id + base_version + canonical_body)`, so a
  retry after a crash reuses the same key.
- **Local outbox (write-ahead):** every PATCH intent is appended *before* it's sent and marked done on 2xx;
  on startup, unsent intents for this candidate are flushed first.
- **Retries:** exponential backoff with jitter on 5xx/429/timeouts, **never on 422**. Client-side token
  bucket for 120 req/min.
- **DLQ (local JSONL in v5):** after N failed retries, the intent goes to the DLQ, the session ends
  `needs_human` with the reason in the notes, and `python -m agent.replay_dlq` re-sends it safely.
- **LLM fallback:** an `LLMProvider` interface with a primary and a fallback provider, retries, and a
  circuit breaker. If both fail, the agent apologises, commits what's confirmed, and finalises `in_progress`.

### 3.6 v6: infra (AWS, minimal and applicable)

Only **SQS + DLQ + Lambda**, behind the same `Outbox` interface used in v5 (switched by config).

```
agent ──PATCH fails after retries──► SQS "candidate-writes"
                                        │ (redrive policy, maxReceiveCount=3)
                     Lambda "write-replayer" ◄── consumes, replays PATCH with stored
                                        │        idempotency key + If-Match/merge
                                        ▼ still failing
                                  SQS "candidate-writes-dlq"  ← parked for a human / manual redrive
```

- **Why it's applicable:** failed writes are the one real asynchronous failure in this problem. Lambda
  suits it because replaying a write is short and event-driven. Idempotency keys make replays safe.
- Lambda reads the API key from an environment variable (no Secrets Manager, by decision).
- Infrastructure as code (one script or SAM template), deployable and removable with one command.
- The live demo still runs locally. Cloud mode is optional, so a network problem can't break the demo.
- IAM: a dedicated least-privilege user (SQS + Lambda + the Lambda role), never root keys; configure it
  with `! aws configure` instead of pasting keys into chat; set a budget alarm first.

---

## 4. Agent design: the agent side in depth

The infra keeps data safe; this section is what makes the agent *good*. Principle: **the LLM handles
language and judgement; code owns truth, rules and state.**

### 4.1 Per-turn state card (context engineering)
**Problem:** in long chats, LLMs lose track of what's been collected, re-ask fields, or think
something was saved when it wasn't. Chat history is a poor source of truth.

**Solution:** before every LLM call, code injects a fresh, compact **state card** generated from
session state:
```
CANDIDATE: Priya Nair (identity: confirmed)
COLLECTED: graduation_year=2027 ✓saved
PENDING CONFIRMATION: available_from="next month" → proposed 2026-12-01
STILL NEEDED: internship_duration_months
REFUSED: —
EXISTING DATA ISSUES: —
TURN 6/30 · asks for duration so far: 1
```
The LLM always reasons over ground truth instead of reconstructing it from history. This removes
"re-asking", "forgetting" and "claiming to have saved" bugs.

### 4.2 Small, sharp tool set with instructive results
- Few tools, strictly typed: `save_fields`, `propose_value` (v3), `mark_refused`, `escalate`,
  `schedule_followup(time_text)`, `end_conversation`.
- Tool results say *what happened and what to do next*, e.g.
  `{"ok":false,"field":"cgpa","error":"85 is outside 0–10; it may be a percentage. Ask for the 10-point CGPA."}`.
  Recovery is driven by tool feedback, which is the "recovers from problems" behaviour the evaluators want.
- Batched saves: one tool call can save several fields from one message, one PATCH per batch.

### 4.3 Question strategy (planning, not a script)
The LLM picks the order, guided by stated priorities rather than a fixed sequence:
- **Easy and impersonal first** (college, degree, branch), **logistics next** (location, start date,
  duration), **sensitive last** (CGPA). This builds rapport before the sensitive question, and it's how
  human recruiters do it.
- **Group related fields**, at most 2–3 per message.
- **Follow the candidate:** if they bring up a topic, collect it right then.
- **Fix existing data issues early**, while the candidate is engaged (yes/no questions are cheap).

### 4.4 Guardrails against loops and stalls
- **Per-field attempt cap:** after 2 failed attempts at a field (invalid or evasive answers), stop asking,
  record it in the notes as unresolved, and move on. Never ask the same thing a third time.
- **Turn budget:** a hard cap (e.g. 30 turns) and a cap on tool calls per turn (e.g. 5), so the agent
  can't loop forever.
- **No-progress detector:** if several turns in a row produce no state change → offer to continue later,
  then finalise `in_progress`.

### 4.5 Conversation behaviours (messy-input handling)

| Situation | Behaviour |
|---|---|
| Several fields in one message | extract all of them, save them as one batch |
| Correction ("actually 8.6") | overwrite our own committed value; the state card shows the change |
| Ambiguous date ("next month", "after exams") | resolve to a concrete date, confirm with the candidate |
| Question back ("why do you need CGPA?") | short, honest answer, then return to collecting |
| Off-topic or out-of-scope (stipend, role details) | don't invent answers; "I'll pass that to the team", logged in notes |
| Partial name match ("yes, Aarav here") | accept; full mismatch or denial → `needs_human` |
| Upset or abusive | apologise once, offer a human → `escalate` |
| Hinglish or other language | reply in their register; store normalised English values |
| "Talk later" / time given | `schedule_followup` parses it to an absolute ISO time in the notes → `in_progress` |
| Silence | one gentle nudge, then finalise |
| Injection ("ignore instructions…") | no effect: status and writes are code-owned |

### 4.6 The outcome policy is deterministic
```
if wrong_person or unresolved_contradiction or escalated → needs_human
elif all mandatory fields valid (existing + committed)  → completed
elif any field refused                                  → refused
elif no candidate reply / nothing given                 → no_answer (+attempt_count)
else                                                    → in_progress (notes: remaining + callback)
```
The LLM's `end_conversation` summary goes into the notes; the status comes from code. Disagreements are logged.

### 4.7 Resumable memory through structured notes
`notes` = human-readable summary + a machine block:
```
Asked to continue tomorrow 6pm IST. Remaining: cgpa, available_from.
---agent-state v1---
{"remaining":["cgpa","available_from"],"refused":[],"callback":"2026-11-15T18:00:00+05:30","attempts":1}
```
On a repeat run: "Hi Priya, welcome back, I just need your CGPA and start date." Earlier refusals are
respected and only re-checked gently. If a human edits the notes, the parser falls back to plain diagnosis.

### 4.8 Read-back before finishing
One short summary of everything collected in this chat ("Here's what I have: … all good?"). This
catches extraction errors at the cost of one turn. Corrections go back through the validator.

### 4.9 Outgoing-message guard
A cheap deterministic check on every agent message before it's shown: no record IDs, no asking for the
phone number or ID, no claims that a field was saved unless the state card says it was, and plain chat
style (no markdown). If a check fails, the message is regenerated once.

### 4.10 Model settings
Low temperature for consistency, short-message style (WhatsApp-like), prompt caching on the static
system prompt to cut cost and latency.

---

## 5. Architecture: ports and adapters (local-first, cloud-optional)

```
            ┌──────────── Interfaces ────────────┐
            │  CLI terminal  │  FastAPI (later)  │
            └───────┬────────┴─────────┬─────────┘
                    ▼                  ▼
             ┌────────────── Core ──────────────────┐
             │ Session state machine + timeouts     │
             │ Agent loop (LLM + tools + state card)│
             │ Validator / normaliser / repair      │
             │ Outcome policy · Notes codec         │
             └──┬────────────┬────────────┬─────────┘
       ports →  │            │            │
     CandidateRepo     LLMProvider     Outbox/DLQ
     (httpx client)    (primary +      (JSONL | SQS + Lambda)
                        fallback)
```

**Where state lives:** their API is the system of record (no second database). Session state is in
memory and in the run log. Durable write intents live in the outbox/DLQ. Memory between conversations
lives in the structured `notes`.

---

## 6. Tech stack

| Layer | Choice | Reason |
|---|---|---|
| Language | Python 3.11+ | speed, ecosystem |
| LLM | Claude Sonnet 5.5 primary + a fallback provider | reliable tool calling, resilience |
| Agent loop | hand-written loop using the provider SDK | transparent, easy to explain |
| Validation | Pydantic v2 + custom normalisers | one source of truth for rules |
| HTTP | httpx + tenacity | timeouts, backoff and jitter |
| Dates | `dateparser` (always confirmed with the candidate) | messy relative dates |
| Email | `email-validator` | catches `gmail` without `.com` |
| CLI | `typer` + `rich` | clean demo |
| Tests | pytest + LLM persona harness | proves correctness |
| Cloud | SQS + DLQ + Lambda (boto3, IaC script) | only the async failure path |

---

## 7. Where everything is handled (README "Decisions" source)

| Edge case | Handled in |
|---|---|
| Stops replying / leaves | timeout + nudge → `finalize()` (v1), outcome policy |
| Continue later | `schedule_followup` + notes block (v1 basic, v4 structured) |
| Refusals | `mark_refused` → `refused` (v1) |
| Invalid / ambiguous answers | validator feedback (v1), proposed→confirmed (v3) |
| Wrong existing data | diagnose (v1), repair suggestions (v3) |
| API conflicts / failures | 409 re-read (v1), 3-way merge + outbox + DLQ (v5, v6) |
| LLM outage | fallback + circuit breaker (v5) |

---

## 8. Considered and rejected

| Idea | Why not |
|---|---|
| Separate candidate database | two sources of truth; their API is the record |
| Secrets Manager / CloudWatch / DynamoDB | not needed for the agreed scope; env vars and local run logs are enough |
| Astro/S3 frontend | 0 UI points; the evaluator uses a terminal |
| LangChain / LangGraph / CrewAI | hides the agent loop the evaluators want to see; extra failure modes |
| Multi-agent setup (planner + extractor + critic) | several times the latency and cost, more nondeterminism; one agent plus deterministic guardrails is more reliable |
| Auto-converting percentage → CGPA | no universal formula; stores wrong data confidently |
| LLM-chosen `collection_status` | vulnerable to injection and LLM error |
| Lambda for the chat itself | a long blocking chat doesn't fit Lambda |

---

## 9. Open decisions

1. **LLM provider and key:** Claude (recommended) and which fallback (Gemini or Groq free tier)?
2. **Demo inactivity timings:** nudge at 60s, finalise at 180s?
3. **Read-back** before finishing: on (recommended) or off?
