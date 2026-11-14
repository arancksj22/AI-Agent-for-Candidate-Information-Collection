"""Cheap deterministic pre-extraction run on every candidate message before the LLM call.

The output is a list of hints ("detected email x: valid", "85 looks like a percentage") that go
into the prompt. They help a Flash-class model extract accurately and, because the validator's
verdict is included, let it ask back about a bad value in the same call instead of needing a
second "repair" call.
"""
from __future__ import annotations

import re

from dateparser.search import search_dates

from agent.domain import schema
from agent.config import now_ist
from agent.nlp.normalize import _match_alias, match_college, normalize

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*", re.I)
URL_RE = re.compile(r"(?:https?://)?(?:www\.)?(?:linkedin\.com|github\.com)/[^\s,]+", re.I)
NUM_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(%|percent|/\s*\d+|out of \d+)?", re.I)
DATE_WORDS = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b|\b(today|tomorrow|tonight|"
    r"next|week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday|kal|parso)\b|"
    r"\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?",
    re.I,
)
# Alias keys too common in ordinary sentences to be a useful hint on their own.
GENERIC_WORDS = {"any", "either", "online", "other", "others", "flexible", "fromhome", "be", "home",
                 "open", "both", "anyone", "whatever", "wherever", "virtual", "bang", "mum", "bom",
                 "auto", "none", "bs", "chalega", "remotely", "athome"}


def _verdict(field: str, raw: str) -> str:
    res = normalize(field, raw)
    if res.ok:
        return f"valid {field}={res.value!r}"
    if res.soft:
        return f"{field}={res.value!r} needs confirmation: {res.error}"
    return f"NOT valid as {field}: {res.error}"


COLLEGE_CONTEXT = re.compile(r"college|colg|clg|university|uni\b|institute|studying|study at|student|from|"
                             r"pursuing|padh|engg|engineering", re.I)
# Aliases that are also everyday words or very short: only trusted with college context or a short reply.
_RISKY_ALIASES = {"christ", "jain", "lovely", "alliance", "heritage", "flame", "ashoka", "amrita", "manipal",
                  "amity", "reva", "pes", "bits", "vit", "jamia", "sastra", "psg", "ssn", "bms", "nirma"}


def _college_hints(text: str, words: list[str]) -> list[str]:
    short_reply = len(words) <= 5
    has_context = bool(COLLEGE_CONTEXT.search(text))
    found: dict[str, str] = {}
    for n in (5, 4, 3, 2, 1):                          # longest phrase first
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i:i + n])
            key = phrase.replace(" ", "")
            if key in _RISKY_ALIASES and not has_context:      # "lovely", "christ": real words first
                continue
            if len(key) <= 3 and not (short_reply or has_context):
                continue
            if n == 1 and key in GENERIC_WORDS:
                continue
            official = match_college(phrase)
            if official and official not in found.values() and not any(phrase in p for p in found):
                found[phrase] = official
    return [f"college mention '{p}' → likely '{o}'. If this is their college, offer the full name as a yes/no "
            f"question; save the full name with candidate_confirmed=true only if they say yes, otherwise save "
            f"their own words" for p, o in list(found.items())[:2]]


def extract_hints(message: str) -> list[str]:
    hints: list[str] = []
    text = message.strip()
    if not text:
        return hints

    for email in EMAIL_RE.findall(text):
        hints.append(f"email '{email}': {_verdict('email', email)}")

    for url in URL_RE.findall(text):
        field = "linkedin_url" if "linkedin" in url.lower() else "github_url"
        hints.append(f"url '{url}': {_verdict(field, url)}")

    text_wo = URL_RE.sub(" ", EMAIL_RE.sub(" ", text))
    for num, suffix in NUM_RE.findall(text_wo):
        value = float(num)
        raw = f"{num}{suffix or ''}"
        if suffix or ("." in num and value <= 10) or (10 < value <= 100 and "." in num):
            hints.append(f"number '{raw}': if this is the CGPA → {_verdict('cgpa', raw)}")
        elif 1900 <= value <= 2100:
            hints.append(f"year '{num}': if this is the graduation year → {_verdict('graduation_year', num)}")
        elif 1 <= value <= 24 and re.search(rf"{re.escape(num)}\s*(months?|mo|mahine|mahina)\b", text_wo, re.I):
            hints.append(f"duration '{num} months': {_verdict('internship_duration_months', num)}")
        elif 10 < value <= 100 and re.search(r"cgpa|gpa|pointer|percent|marks|score|grade", text, re.I):
            hints.append(f"number '{num}': if this is the CGPA → {_verdict('cgpa', num)}")
        elif 10 <= value <= 99 and "." not in num and re.search(r"pass|grad|batch|year|saal", text, re.I):
            hints.append(f"'{num}' may be a short graduation year → {_verdict('graduation_year', num)} "
                         f"(confirm the full year with the candidate)")

    words = re.findall(r"[a-z]+", text.lower())
    grams = words + [a + b for a, b in zip(words, words[1:])] + \
        [a + b + c for a, b, c in zip(words, words[1:], words[2:])]
    seen_loc = set()
    for g in grams:
        if len(g) < 3 or g in GENERIC_WORDS:
            continue
        loc = _match_alias(g, schema.LOCATION_ALIASES, cutoff=90)
        if loc and loc not in seen_loc:
            seen_loc.add(loc)
            hints.append(f"location mention '{g}' → preferred_location could be '{loc}'")
        deg = _match_alias(g, schema.DEGREE_ALIASES, cutoff=95) if len(g) >= 4 or g in ("bca", "mca", "bsc") else None
        if deg and deg != "Other":
            hints.append(f"degree mention '{g}' → degree could be '{deg}'")

    hints += _college_hints(text, words)

    if DATE_WORDS.search(text):
        try:
            found = search_dates(
                text, languages=["en", "hi"],
                settings={"PREFER_DATES_FROM": "future", "PREFER_DAY_OF_MONTH": "first",
                          "RELATIVE_BASE": now_ist().replace(tzinfo=None), "DATE_ORDER": "DMY"},
            ) or []
        except Exception:                      # dateparser can raise on odd input; hints are best-effort
            found = []
        for phrase, dt in found[:3]:
            p = phrase.strip()
            if (re.fullmatch(r"\d+(\.\d+)?", p)                      # bare numbers
                    or re.fullmatch(r"\d+\s*(months?|mo|weeks?)", p, re.I)  # durations
                    or re.search(rf"\d\.{re.escape(p)}|{re.escape(p)}\s*(%|cgpa|gpa)", text, re.I)
                    or re.fullmatch(r"\d{1,2}\s*/\s*(10|4)", p)):              # scores like 8.4/10
                continue
            hints.append(f"date phrase '{phrase}' → {dt.strftime('%Y-%m-%d %H:%M')} (IST). If used as "
                         f"available_from → {_verdict('available_from', dt.date().isoformat())}")
    return list(dict.fromkeys(hints))
