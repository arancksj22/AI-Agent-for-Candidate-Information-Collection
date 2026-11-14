"""Deterministic guards around the LLM: grounding, hedging, cross-field rules, outgoing-message check.

None of these call the LLM. They turn "the model said so" into "the candidate said so and the
rules allow it", and they clean the agent's own messages before the candidate sees them.
"""
from __future__ import annotations

import re
from datetime import date

from rapidfuzz import fuzz

from agent.domain import schema

# ---------------------------------------------------------------- grounding

_DIGIT_WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
_DIGIT_WORD_RE = "|".join(_DIGIT_WORDS)


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def words_to_digits(text: str) -> str:
    """'eight point five' → '8.5', 'nine' → '9', so spelled-out answers still count as grounded."""
    def point(m: re.Match) -> str:
        frac = "".join(_DIGIT_WORDS[w] for w in m.group(2).split())
        return f"{_DIGIT_WORDS[m.group(1)]}.{frac}"
    text = re.sub(rf"\b({_DIGIT_WORD_RE}) point ((?:(?:{_DIGIT_WORD_RE})\s?)+)\b", point, text)
    return re.sub(rf"\b({_DIGIT_WORD_RE})\b", lambda m: _DIGIT_WORDS[m.group(1)], text)


def grounded(evidence: str, candidate_messages: list[str]) -> bool:
    """The evidence must be (close to) something the candidate actually wrote."""
    ev = squash(evidence).strip(" .!,\"'")
    if not ev:
        return False
    for m in candidate_messages:
        mm = squash(m)
        if ev in mm or fuzz.partial_ratio(ev, mm) >= 90:
            return True
    return False


def value_traceable(field: str, value, candidate_messages: list[str], last_agent_msg: str,
                    confirmed: bool) -> bool:
    """For values that are easy to hallucinate, the candidate's words (or a value they explicitly
    confirmed from our last message) must contain them."""
    pool = words_to_digits(squash(" ".join(candidate_messages)))
    if confirmed:
        pool += " " + squash(last_agent_msg)
    if field == "email":
        return str(value).split("@")[0] in pool.replace(" ", "")
    if field == "cgpa":
        return any(abs(float(n) - float(value)) < 0.005 for n in re.findall(r"\d+(?:\.\d+)?", pool))
    if field == "graduation_year":
        y = str(value)
        return bool(re.search(rf"\b{y}\b|\b'?{y[2:]}\b", pool))
    if field in ("linkedin_url", "github_url"):
        return str(value).rstrip("/").rsplit("/", 1)[-1].lower() in pool
    return True


# ---------------------------------------------------------------- hedging (value lifecycle)

HEDGE_RE = re.compile(
    r"\b(around|about|approx\w*|roughly|maybe|probably|perhaps|i think|i guess|not sure|"
    r"something like|or so|ish|lagbhag|shayad|kareeb)\b|~|\?\s*$",
    re.I,
)
HEDGE_SENSITIVE = {"cgpa", "graduation_year", "available_from", "internship_duration_months"}


SCALE_RE = re.compile(r"(/\s*10\b|out of 10\b|\b10[- ]?point|\bten[- ]point|\bon 10\b|\b10 (?:ke|me|mein) se\b|"
                      r"\b10 scale|\bscale of 10\b|\bcgpa of 10\b)", re.I)


def scale_stated(messages: list[str]) -> bool:
    """'8.4/10', '8.4 out of 10', 'on a 10 point scale' → the CGPA's scale is explicit."""
    return any(SCALE_RE.search(m or "") for m in messages)


def is_hedged(field: str, evidence: str) -> bool:
    """'around 8', '2027 I think' → the value is only *proposed* until the candidate confirms it."""
    return field in HEDGE_SENSITIVE and bool(HEDGE_RE.search(evidence or ""))


# ---------------------------------------------------------------- cross-field soft rules

def cross_field_issues(field: str, value, known: dict) -> list[tuple[str, str]]:
    """Soft consistency rules. Each returns (rule_id, message). They're raised once per chat; if the
    candidate confirms (or we already asked), the value is accepted."""
    f = dict(known)
    f[field] = value
    issues = []
    start = f.get("available_from")
    start_d = date.fromisoformat(start) if isinstance(start, str) and len(start) == 10 else None
    grad = f.get("graduation_year")

    # An internship normally starts before the student graduates. A start in a later calendar year
    # than graduation is legal but more likely a typo in one of the two.
    if start_d and isinstance(grad, int) and start_d.year > grad:
        issues.append(("start_after_graduation",
                       f"The start date {start} is after their graduation year {grad}. Mention it once and "
                       f"confirm both are right."))
    return [(rid, msg) for rid, msg in issues if field in _RULE_FIELDS[rid]]


_RULE_FIELDS = {
    "start_after_graduation": {"available_from", "graduation_year"},
}


# ---------------------------------------------------------------- outgoing-message check

_ASKS_FOR_ID = re.compile(
    r"(candidate|application|applicant|record)\s*(id|number|no\.?)|\bOTP\b|"
    r"(phone|mobile|contact)\s*(number|no\.?)",
    re.I,
)
_RECORD_ID = re.compile(r"\bc\d{3}\b", re.I)
_MARKDOWN = re.compile(r"(\*\*|__|`|^#+\s*|^\s*[-*•]\s+)", re.M)


def check_outgoing(reply: str) -> tuple[str, list[str]]:
    """Clean the agent's message before the candidate sees it. Returns (clean_text, problems).

    Sentences that ask for an ID/OTP/phone number or leak a record id are dropped (rule: identity
    is confirmed by name only; the phone is already verified). Markdown is stripped (it's a chat).
    """
    problems = []
    text = _MARKDOWN.sub("", reply)
    if text != reply:
        problems.append("markdown removed")
    sentences = re.split(r"(?<=[.!?])\s+", text.replace("\n", " "))
    kept = []
    for sent in sentences:
        if (_ASKS_FOR_ID.search(sent) and "?" in sent) or _RECORD_ID.search(sent):
            problems.append(f"dropped: {sent!r}")
            continue
        kept.append(sent)
    return " ".join(k for k in kept if k).strip(), problems


# ---------------------------------------------------------------- plain confirmation

_STRONG_YES = {"yes", "yeah", "yea", "yeh", "yep", "yup", "ya", "yaa", "yas", "yess", "yesss", "ye",
               "correct", "right", "perfect", "ok", "okay", "okk", "okie", "k", "kk", "sure", "haan", "han",
               "haa", "haanji", "hanji", "ji", "bilkul", "sahi", "theek", "thik", "absolutely", "exactly",
               "good", "affirmative", "confirmed", "confirm", "accurate", "alright", "allright", "fine",
               "👍", "✅", "👌"}
_YES_WORDS = _STRONG_YES | {"done", "great", "thanks", "thank", "you", "thx", "ty", "all", "looks", "look",
                            "everything", "is", "it", "that", "s", "its", "thats", "hai", "he", "h", "sab",
                            "ekdum", "bhai", "sir", "mam", "maam", "totally", "true", "spot", "on", "very",
                            "much", "so", "dhanyavad", "shukriya", "cool", "nice", "awesome", "seems", "to",
                            "me", "be", "am", "are", "details", "info", "every", "thing", "ha"}


def is_plain_yes(message: str) -> bool:
    """'yes', 'yep all good', 'haan ji', '👍' → True. Anything with new content → False (goes to the LLM)."""
    words = re.findall(r"[a-z]+|👍|✅|👌", message.lower())
    return bool(words) and all(w in _YES_WORDS for w in words) and any(w in _STRONG_YES for w in words)


def known_values(record: dict, saved: dict) -> dict:
    return {f: saved.get(f, record.get(f)) for f in schema.MANDATORY_FIELDS}
