# Initial Development Plan

> Two parts. **Part 1** is a complete, correct, demo-ready agent. **Part 2** adds hardening and
> polish on top without rewriting Part 1. Constraints: use the live candidate API and keys from
> `.env`, no unit-test suite, and **make as few Gemini calls as possible**.

---

## 0. Guiding principle: the LLM only does what only an LLM can do

| Job | Who does it | Why |
|---|---|---|
| Find missing or invalid fields | **Code** (schema rules) | deterministic, free |
| Pull out emails, URLs, years, numbers, dates | **Code** (regex + `dateparser`) as *hints* | free, precise, and grounds the LLM |
| Map "btech", "blr", "wfh" to enum values | **Code** (alias table + `rapidfuzz`) | free, deterministic |
| Validate and normalise every value | **Code** (Pydantic) | the API accepts garbage; we can't trust the LLM |
| Decide the final status | **Code** (outcome policy) | resists prompt injection |
| Greeting, nudge, timeout and goodbye messages | **Code** (templates) | no judgement needed, so no LLM call |
| Understand meaning, intent and tone; decide what to ask next; write a natural reply | **LLM** | the only part that needs language understanding |

### LLM call budget
- **0 calls** for: complete records (c003/c006), diagnosis, the opening greeting, silence/timeout,
  `/quit`, Ctrl+C, and finalising.
- **1 call per candidate message** in the normal case (single-shot turn, §1.5).
- **+1 call only when** a proposed value fails validation (the reply has to change).
- A typical chat of about 6–10 candidate messages costs about **6–12 calls**.
- Dev rule: no Gemini call for anything I can check without one. Live runs happen only when you ask.

---

## 1. NLP and model choices: what we use, what we don't

### Used (lightweight, pure Python, no GPU, no model downloads)
| Library / technique | Purpose |
|---|---|
| **Regex extractors** | email, linkedin/github URLs, 4-digit years, decimal numbers ("8.4", "8.4/10"), durations ("6 months") |
| **`dateparser`** | resolves "next month", "1st Jan", "jan 2027", including Hindi month names, relative to today in IST |
| **`rapidfuzz`** | fuzzy matching: degree/location aliases ("b tech", "bengalore"), identity name matching ("Aarav" vs "Aarav Mehta"), email domain typos (`gmial.com` → `gmail.com`) |
| **`email-validator`** | email syntax and domain format (catches `kabir.shah@gmail`) |
| **Alias tables** | small hand-written maps: `{"btech","b.tech","b tech","bachelor of technology"} → B.Tech`, `{"blr","bangalore","bengaluru"} → Bengaluru`, `{"wfh","remote","work from home"} → Remote`, `{"anywhere","any","dont mind"} → No preference` |

These run on **every** candidate message before the LLM call. Their output is injected as **hints**
("detected: email=…, number=8.4, date=2027-01-04") so a Flash model extracts more accurately. The
same hints are reused afterwards to **ground** the LLM's values: a value the LLM claims must be
traceable to the candidate's text.

### Considered and rejected
| Option | Why not |
|---|---|
| **Hugging Face NER** (e.g. `dslim/bert-base-NER`) | Generic PER/ORG/LOC tags don't map to our fields. Weak on Indian college names ("MPSTME", "DJ Sanghvi") and Hinglish. Pulls in torch (~1–2 GB) and slows startup during a live demo. The LLM already reads every message, so it would add risk with no gain. |
| **spaCy NER / pipelines** | Same problem: generic entities, extra model download, no gain over regex + LLM for our 11 fields. |
| **GLiNER** (zero-shot NER) | Better fit (custom labels), but still torch and model download. Gains over regex + LLM for our narrow fields are marginal. A possible Part 2+ idea only if extraction quality turns out poor (it won't for these fields). |
| **Zero-shot intent classifier** (`bart-large-mnli`) for refusal / later / upset | Heavy, slow on CPU, worse than the LLM on Hinglish and sarcasm. Intent is handled inside the same single LLM call at no extra cost. |
| **HF sentiment model** for detecting an upset candidate | Same: the LLM call already returns a `sentiment` field in its structured output. |
| **Embeddings / RAG** | Nothing to retrieve; the schema fits in the prompt. |
| **`word2number`** | Spelled-out numbers ("eight point four") are rare and the LLM handles them. |

**Summary:** cheap deterministic NLP *around* the LLM (pre-extraction hints, normalisation,
grounding), one LLM call *for* language understanding, and no heavy models.

---

## Part 1: Core agent (complete and correct)

**Goal:** run `python -m agent --candidate-id <id>` on any of the 8 candidates and get the correct
conversation, the correct DB writes and the correct status for all the main paths.

### 1.1 Project layout
```
agent/
  __main__.py        CLI entry: --candidate-id, --timeout, --diagnose-all
  config.py          loads .env (CANDIDATE_API_*, GEMINI_API_KEY, GEMINI_MODEL, timeouts)
  api_client.py      CandidateRepo: get, patch (If-Match, Idempotency-Key, retry, 409 handling)
  schema.py          field rules, enums, alias tables
  normalize.py       normalisers + validators (one function per field) → ok/value or error+hint
  extract.py         regex/dateparser/rapidfuzz pre-extraction → hints
  diagnose.py        record → {missing, invalid(with repair suggestion), ok}
  session.py         session state: field states, refused, flags, turn count, transcript
  policy.py          deterministic outcome + notes builder
  llm.py             Gemini wrapper: structured output, retry on 429/5xx, call counter
  prompts.py         system prompt + per-turn state card renderer
  agent.py           the agent loop (turn handling, action execution)
  chat_io.py         terminal I/O with an inactivity timeout (works on Windows)
  runlog.py          writes runs/<id>-<timestamp>.json
runs/                sample transcripts (deliverable)
README.md
requirements.txt     google-genai, httpx, pydantic, python-dotenv, dateparser, rapidfuzz, email-validator
.env / .env.example
```

### 1.2 API client (`api_client.py`)
- `get(id)` returns the record, including `version`.
- `patch(id, fields, version)`:
  - **Diff first:** drop fields whose normalised value equals the stored value. An empty diff means no request.
  - Headers: `If-Match: "<version>"`, `Idempotency-Key: <uuid>`, generated once per logical write and
    **reused on retries**.
  - Retries: timeouts, 429 and 5xx with exponential backoff (3 tries). **422 is never retried**; it's
    logged as a bug.
  - **409:** re-GET the record. For each field we're writing, if the fresh value equals what we read
    earlier, re-apply it on the new version; if someone else changed it → don't overwrite, flag a conflict
    (→ `needs_human`).
  - The new `version` from the response updates session state.
- Every request and response is appended to the run log.

### 1.3 Diagnosis (`diagnose.py`, no LLM)
- Runs every mandatory field through its validator: empty → `missing`; present but failing → `invalid`,
  with a **repair suggestion** where one exists:
  - email with no TLD (`kabir.shah@gmail`) → suggest `kabir.shah@gmail.com`; domain typos via rapidfuzz.
  - graduation year out of range → suggest nothing and ask for the actual year.
  - an enum value that's an alias → normalise silently? **No.** It's flagged and the candidate is asked to
    confirm, because we never change existing data without the candidate.
- Optional fields that are present but invalid (e.g. a non-linkedin URL) → noted, never pushed.
- Reads `notes` for a previous `---agent-state---` block (Part 2 uses it fully; Part 1 just keeps it).
- **Nothing missing or invalid** → no chat. If `collection_status != completed`, write
  `{collection_status: completed}` once; otherwise write nothing. Exit.
- `--diagnose-all` prints the diagnosis for all 8 candidates (our no-LLM sanity check).

### 1.4 Validators and normalisers (`normalize.py`)
One function per field, `normalize_<field>(raw) → Ok(value) | Err(message, hint)`:

| Field | Normalisation | Example error message for the LLM |
|---|---|---|
| email | trim, lowercase, `email-validator` | "missing domain ending; maybe @gmail.com?" |
| college | trim, collapse spaces, 2–150 chars | "too short, ask for the full college name" |
| degree | alias table → rapidfuzz ≥ 85 → enum; unknown → `Other` (keep detail in notes) | — |
| branch | trim, 2–100 chars, title-case only if it's all lowercase | — |
| graduation_year | int, 2026–2028 | "2031 is outside 2026–2028; confirm the year" |
| cgpa | parse "8.4", "8.4/10"; reject > 10 (hint: percentage?) or "x/4" (hint: other scale); round to 2 decimals | "85 looks like a percentage; ask for the 10-point CGPA, don't convert" |
| preferred_location | alias table → enum | "Pune isn't an option; offer Mumbai, Bengaluru, Remote, No preference" |
| available_from | `dateparser` (prefer future dates, IST) → YYYY-MM-DD, 2026-01-01 to 2027-12-31 | "vague date; ask for a specific date" |
| internship_duration_months | words or digits → {2, 3, 6} | "4 isn't allowed; offer 2, 3 or 6" |
| linkedin_url / github_url | must be on the linkedin.com / github.com domain | — |
| skills | split on commas, trim, dedupe, ≤ 30 items, each 1–50 chars | — |

### 1.5 The agent loop: a single-shot structured turn (`agent.py`, `llm.py`)
Each candidate message makes **one** Gemini call with a JSON response schema:
```json
{
  "identity": "unknown | confirmed | denied",
  "actions": [
    {"type": "save",      "field": "cgpa", "value": "8.4", "evidence": "my cgpa is 8.4"},
    {"type": "refuse",    "field": "cgpa", "evidence": "rather not say"},
    {"type": "followup",  "when_text": "tomorrow 6pm", "evidence": "..."},
    {"type": "escalate",  "reason": "wrong person / upset / contradiction"},
    {"type": "end",       "reason": "candidate leaving / done"}
  ],
  "sentiment": "ok | frustrated | angry",
  "reply": "message to send to the candidate"
}
```
**Why this design:** the actions are the agent's tool calls. The LLM decides which to make and in what
order, so this is still a tool-using agent, but the reply and the actions come back in **one** call
instead of the 2–3 round-trips of a standard function-calling loop. This is the main quota saving.

Turn handling:
1. Run `extract.py` on the message → hints.
2. Build the prompt: static system prompt + **state card** (§1.6) + hints + the last ~8 turns.
3. One Gemini call → structured output.
4. Execute actions in code:
   - `save` → normalise/validate → check grounding (the value must be derivable from the evidence, and the
     evidence must appear in a candidate message) → batch the valid saves → **one PATCH per turn**.
   - `refuse` → mark the field refused. `followup` → parse the time with dateparser and store it.
     `escalate` → set the flag. `end` → finalise after sending the reply.
5. **If any save was rejected**, the reply is now wrong (it might say "got it"). Make **one** repair call
   with the rejection messages, which returns only a corrected `reply`. Otherwise send the reply as is.
6. Identity `denied` → no saves allowed, escalate, end.
7. Saves are ignored until identity is `confirmed` (they wait in the session and are committed once confirmed).

### 1.6 Prompt and state card (`prompts.py`)
- **System prompt (static):** role (the ACME internship team on WhatsApp), goals, rules (confirm the name
  first, never ask for an ID or phone number, never invent values, never convert percentages, no
  markdown, short messages, at most 2–3 questions per message, easy fields → logistics → CGPA last,
  explain briefly when asked why, one gentle push on a refusal and then respect it, don't answer stipend
  or role questions, reply in the candidate's language), field rules and enum values, and the action schema.
- **State card (fresh each turn, built by code):**
  ```
  Candidate: Priya Nair | identity: confirmed | today: 2026-11-14 (IST)
  Saved this chat: graduation_year=2027
  Still needed: available_from, internship_duration_months
  Existing data to fix: —
  Refused: —
  Attempts: available_from=1
  Turn 4 of 30
  ```
  The LLM reasons from this rather than from memory of the history, so it doesn't re-ask fields,
  forget answers, or claim false saves.

### 1.7 Terminal I/O and endings (`chat_io.py`)
- The agent's opening message is a **template** (0 calls):
  "Hi! This is the ACME Corp internship team about your application. Am I speaking with Priya Nair?"
- Input with an **inactivity timeout** (a thread-based reader that works on Windows):
  no reply for `NUDGE_AFTER` seconds → template nudge; then `GIVE_UP_AFTER` → finalise. Both configurable
  (demo defaults 60s and 120s).
- `/quit`, Ctrl+C and EOF → finalise. The evaluator can type normally because the terminal input is plain.
- Goodbye messages are templates matched to the outcome.

### 1.8 Outcome policy and notes (`policy.py`, deterministic)
```
denied identity / escalated / unresolved contradiction / write conflict → needs_human
all mandatory fields valid (existing + saved)                          → completed
any field refused (and nothing else outstanding)                       → refused
no candidate reply at all, or left before giving anything              → no_answer, attempt_count+1
otherwise                                                              → in_progress
```
- `refused` vs `in_progress` when both apply: refused fields **and** fields still outstanding →
  `in_progress` (more can still be collected), with the refused fields listed in the notes.
- Final PATCH: `collection_status`, `last_contacted_at` (ISO with `+05:30`), `attempt_count` (incremented
  on `no_answer`; we also increment it on every contacted conversation, documented in the README),
  `notes`.
- **Notes:** a human summary (what was collected, what's left, refusals and reasons, callback time,
  escalation reason, off-topic questions to pass on) + a compact `---agent-state v1---` JSON block.
  Capped at 4000 chars, existing notes kept (prefixed with the new entry, oldest trimmed).

### 1.9 Run logs and deliverables
- `runs/<id>-<timestamp>.json`: transcript, every LLM request/response (structured), action execution
  results, every API request/response, LLM call count, final status.
- Generate samples for at least 2 candidates (we'll do c001 and c008, plus c007 if quota allows).
- README: setup in ≤ 5 commands, architecture, decisions (from the edge-case table), honest status.

### 1.10 Part 1 done when
- `--diagnose-all` gives the correct result for all 8 (no LLM).
- Live chats (a few, on request): c001 → `completed`; c003 → no chat; c008 → 2031 not written;
  quitting midway → `in_progress`; silence → `no_answer`.
- Commit.

---

## Part 2: Hardening and quality (on top of Part 1, no rewrites)

### 2.1 Data correctness
- **Value lifecycle** `proposed → confirmed → committed`: hedged answers ("around 8", "I think") are held
  until the exact value is confirmed (detected from hedge words by regex plus an LLM flag).
- **Repair confirmations** for existing bad data (yes/no question with the suggested value).
- **Cross-field soft checks:** start date in the past or long after graduation; a 6-month internship that
  runs past 2027-12-31; asked once and then accepted.
- **Read-back** of the values collected in this chat before finishing (one turn, catches extraction mistakes).

### 2.2 Agent robustness
- **Per-field attempt cap** (2), **turn budget** (30), and a **no-progress detector** (offer to continue later).
- **Outgoing-message check** (code): no IDs, no request for a phone number or ID, no "saved" claims that
  don't match the state card, no markdown. One regeneration on failure.
- **Resumable conversations:** parse the `---agent-state---` block → "welcome back, I just need X"; earlier
  refusals respected.
- **Prompt-history compression:** in long chats, keep the state card + the last 6 turns only (fewer tokens).

### 2.3 Write path resilience
- **Field-level three-way merge** on 409 (base/ours/theirs).
- **Content-derived idempotency keys:** `sha256(id + base_version + canonical_body)`.
- **Local outbox** (write-ahead JSONL) + flush on startup for crash recovery.
- **Local DLQ:** after the retries run out → DLQ entry, `needs_human`, and a `replay-dlq` command.
- Client-side **rate limiter** (120/min).

### 2.4 LLM resilience
- 429/quota from Gemini → backoff → second Gemini model (e.g. a lighter Flash variant) → if still failing:
  template apology, commit what's confirmed, finalise `in_progress` with "technical issue" in the notes.
- A **response cache** keyed on the exact prompt during development (re-running a scenario costs no quota).

### 2.5 Infra: SQS + DLQ + Lambda
- An `Outbox` interface with local JSONL and SQS adapters (config switch).
- `candidate-writes` queue → `write-replayer` Lambda (replays with the stored idempotency key + If-Match
  merge) → redrive after 3 receives to `candidate-writes-dlq`.
- IaC script to deploy and tear down. The API key goes to the Lambda as an environment variable. Least-privilege IAM user.
- The live demo stays local; cloud mode is optional.

### 2.6 Optional, if wanted
- A scripted-persona runner (fixed candidate replies, no LLM on the candidate side) to replay the main
  scenarios after `/reset`.
- A FastAPI wrapper over the same core.

---

## 3. Mapping to the scoring

| Criterion (weight) | Where it's earned |
|---|---|
| Agent architecture and decisions (20) | structured actions, state card, LLM chooses order and actions, recovery from tool errors |
| Conversation quality (15) | prompt rules, batching, language matching, templates only for fixed moments |
| Edge cases (15) | edge-case table, policy, endings, Part 2 checks |
| Extraction and validation (15) | regex/dateparser/rapidfuzz hints, per-field validators, grounding |
| Error handling and state (15) | retries, 409 handling, finalize on every exit path, outbox/DLQ |
| DB reliability (10) | diff-only PATCH, no-op suppression, code-computed status |
| Autonomy and engineering (10) | clean modules, run logs, honest README |
