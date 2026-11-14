"""Deterministic validators and normalisers, one per field.

The API stores whatever it is sent, so every value goes through here before it's written,
whether it came from the LLM or from existing data. Errors are worded for the LLM: they say
what's wrong and what to ask next.

Two kinds of failure:
  * hard: the value breaks a schema rule and can never be written.
  * soft: the value is legal but suspicious (a date in the past, a likely email typo). It's
    written only when the candidate has explicitly confirmed it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable
from urllib.parse import urlparse

import dateparser
from email_validator import EmailNotValidError, validate_email
from rapidfuzz import fuzz, process

from agent.domain import schema
from agent.config import now_ist


@dataclass
class Result:
    ok: bool
    value: Any = None
    error: str = ""
    soft: bool = False          # True → allowed if the candidate explicitly confirmed it
    suggestion: Any = None      # a likely intended value, to offer to the candidate


def ok(value: Any) -> Result:
    return Result(True, value)


def hard(error: str, suggestion: Any = None) -> Result:
    return Result(False, error=error, suggestion=suggestion)


def soft(value: Any, error: str, suggestion: Any = None) -> Result:
    return Result(False, value=value, error=error, soft=True, suggestion=suggestion)


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def _clean_text(raw: Any) -> str:
    return re.sub(r"\s+", " ", str(raw)).strip().strip("\"'")


# ---------------------------------------------------------------- email

def suggest_email_domain(domain: str) -> str | None:
    """'gmail' → 'gmail.com', 'gmial.com' → 'gmail.com'. None if nothing is close."""
    domain = domain.lower().strip(".")
    for known in schema.COMMON_EMAIL_DOMAINS:
        if domain == known.split(".")[0]:
            return known
    match = process.extractOne(domain, schema.COMMON_EMAIL_DOMAINS, scorer=fuzz.ratio)
    if match and match[1] >= 80 and match[0] != domain:
        return match[0]
    return None


def normalize_email(raw: Any) -> Result:
    text = _clean_text(raw).lower().replace(" ", "")
    if len(text) > 254:
        return hard("Email is longer than 254 characters.")
    if text.count("@") != 1:
        return hard("That isn't an email address (it needs exactly one '@'). Ask for the full email.")
    local, domain = text.split("@")
    try:
        validate_email(text, check_deliverability=False)
    except EmailNotValidError as e:
        fix = suggest_email_domain(domain)
        if fix and local:
            return hard(f"Invalid email ({e}). Ask whether they meant {local}@{fix}.",
                        suggestion=f"{local}@{fix}")
        return hard(f"Invalid email ({e}). Ask for the full, correct address.")
    fix = suggest_email_domain(domain)
    if fix and fix != domain:
        return soft(text, f"'{domain}' looks like a typo of '{fix}'. Confirm the exact spelling with the "
                          f"candidate before saving.", suggestion=f"{local}@{fix}")
    return ok(text)


# ---------------------------------------------------------------- simple strings

def _length_checked(raw: Any, lo: int, hi: int, label: str) -> Result:
    text = _clean_text(raw)
    if len(text) < lo:
        return hard(f"{label} is too short. Ask for the full {label.lower()}.")
    if len(text) > hi:
        return hard(f"{label} is longer than {hi} characters. Ask for a shorter form.")
    if not re.search(r"[A-Za-z]", text):
        return hard(f"{label} must contain letters. Ask again.")
    # Stored as the candidate wrote it (trimmed). We don't "fix" proper nouns like college names.
    return ok(text)


def normalize_college(raw: Any) -> Result:
    return _length_checked(raw, 2, 150, "College name")


def normalize_branch(raw: Any) -> Result:
    res = _length_checked(raw, 2, 100, "Branch")
    if res.ok:
        key = _key(re.sub(r"\b(engineering|engg|eng|branch|dept|department)\b\.?", "", str(res.value), flags=re.I)) \
            if _key(res.value) not in schema.BRANCH_ALIASES else _key(res.value)
        if key in schema.BRANCH_ALIASES:
            return ok(schema.BRANCH_ALIASES[key])      # exact short forms only: no fuzzy guessing
    return res


# ---------------------------------------------------------------- enums

def _match_alias(raw: Any, aliases: dict[str, str], cutoff: int = 85) -> str | None:
    key = _key(raw)
    if not key:
        return None
    if key in aliases:
        return aliases[key]
    match = process.extractOne(key, list(aliases), scorer=fuzz.ratio)
    if match and match[1] >= cutoff:
        return aliases[match[0]]
    return None


def normalize_degree(raw: Any) -> Result:
    text = _clean_text(raw)
    if text in schema.DEGREES:
        return ok(text)
    found = _match_alias(text, {**schema.DEGREE_ALIASES, **{_key(d): d for d in schema.DEGREES}})
    if found:
        return ok(found)
    return hard(f"'{text}' isn't one of {', '.join(schema.DEGREES)}. If their degree really isn't in the "
                f"list, save the value 'Other' and add a note with their actual degree.")


def normalize_location(raw: Any) -> Result:
    text = _clean_text(raw)
    if text in schema.LOCATIONS:
        return ok(text)
    found = _match_alias(text, {**schema.LOCATION_ALIASES, **{_key(l): l for l in schema.LOCATIONS}})
    if found:
        return ok(found)
    return hard(f"'{text}' isn't an option. The only options are {', '.join(schema.LOCATIONS)}. "
                f"Offer these choices.")


def normalize_duration(raw: Any) -> Result:
    text = _clean_text(raw).lower()
    n = None
    m = re.search(r"\d+", text)
    if m:
        n = int(m.group())
    else:
        for word, val in sorted(schema.DURATION_WORDS.items(), key=lambda kv: -len(kv[0])):
            if re.search(rf"\b{word}\b", text):
                n = val
                break
    if n is None:
        return hard("Couldn't read a number of months. Ask: 2, 3 or 6 months?")
    if "week" in text:
        return hard("Duration must be in months: 2, 3 or 6. Ask which one.")
    if "year" in text and n == 1:
        n = 12
    if n not in schema.DURATIONS:
        return hard(f"{n} months isn't offered. The only options are 2, 3 or 6 months. Ask which one works.")
    return ok(n)


# ---------------------------------------------------------------- numbers

def normalize_graduation_year(raw: Any) -> Result:
    text = _clean_text(raw)
    m = re.search(r"\b(20\d{2})\b", text)
    if not m:
        m2 = re.fullmatch(r"'?(\d{2})", text)
        if not m2:
            return hard("Couldn't read a year. Ask for the 4-digit graduation year.")
        year = 2000 + int(m2.group(1))
    else:
        year = int(m.group(1))
    if not (schema.GRAD_YEAR_MIN <= year <= schema.GRAD_YEAR_MAX):
        return hard(f"{year} is outside the accepted range {schema.GRAD_YEAR_MIN}-{schema.GRAD_YEAR_MAX}. "
                    f"Do not save it. Confirm the year with the candidate; if they insist it's {year}, "
                    f"escalate to a human.")
    return ok(year)


def normalize_cgpa(raw: Any) -> Result:
    text = _clean_text(raw).lower()
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    if not nums:
        return hard("Couldn't read a CGPA. Ask for the CGPA on the 10-point scale, e.g. 8.4.")
    value = float(nums[0])
    scale = float(nums[1]) if len(nums) > 1 and re.search(r"/|out of", text) else None
    if "%" in text or "percent" in text:
        return hard("That's a percentage, not a CGPA. Don't convert it (conversion formulas differ). "
                    "Ask for the CGPA on the 10-point scale.")
    if scale is not None and scale != 10:
        return hard(f"That's on a {scale:g}-point scale. Don't convert it. Ask for the CGPA on the 10-point "
                    f"scale (it's usually on the marksheet).")
    if value > 10:
        hint = " It looks like a percentage." if value <= 100 else ""
        return hard(f"{value:g} is outside 0-10.{hint} Don't convert it. Ask for the CGPA on the "
                    f"10-point scale.")
    if value < 0:
        return hard("CGPA can't be negative. Ask again.")
    return ok(round(value, 2))


# ---------------------------------------------------------------- dates

def parse_date(text: str) -> date | None:
    text = _clean_text(text)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    parsed = dateparser.parse(
        text,
        languages=["en", "hi"],
        settings={
            "PREFER_DATES_FROM": "future",
            "PREFER_DAY_OF_MONTH": "first",
            "RELATIVE_BASE": now_ist().replace(tzinfo=None),
            "DATE_ORDER": "DMY",
        },
    )
    return parsed.date() if parsed else None


def normalize_available_from(raw: Any) -> Result:
    d = parse_date(str(raw))
    if d is None:
        return hard("Couldn't turn that into a date. Ask for a specific start date (day and month).")
    lo, hi = date.fromisoformat(schema.AVAILABLE_MIN), date.fromisoformat(schema.AVAILABLE_MAX)
    if not (lo <= d <= hi):
        return hard(f"{d.isoformat()} is outside the accepted window {schema.AVAILABLE_MIN} to "
                    f"{schema.AVAILABLE_MAX}. Ask for a start date inside that window.")
    if d < now_ist().date():
        return soft(d.isoformat(), f"{d.isoformat()} is in the past. Confirm the start date with the candidate.")
    return ok(d.isoformat())


# ---------------------------------------------------------------- optional fields

def _normalize_url(raw: Any, host: str, label: str) -> Result:
    text = _clean_text(raw)
    if not re.match(r"https?://", text, re.I):
        text = "https://" + text
    try:
        parsed = urlparse(text)
    except ValueError:
        return hard(f"That isn't a valid {label} URL.")
    netloc = parsed.netloc.lower()
    if not (netloc == host or netloc.endswith("." + host)):
        return hard(f"That isn't a {host} URL. Ask for their {label} profile link, or skip it (it's optional).")
    path = parsed.path.rstrip("/")
    if not path:
        return hard(f"That's the {label} home page, not a profile. Ask for the profile link, or skip it.")
    return ok(f"https://{'www.' if host == 'linkedin.com' else ''}{host}{path}")


def normalize_linkedin_url(raw: Any) -> Result:
    return _normalize_url(raw, "linkedin.com", "LinkedIn")


def normalize_github_url(raw: Any) -> Result:
    res = _normalize_url(raw, "github.com", "GitHub")
    if res.ok and res.value.count("/") != 3:
        return hard("That's a repository link, not a GitHub profile. Ask for the profile link (github.com/username).")
    return res


def normalize_skills(raw: Any) -> Result:
    items = raw if isinstance(raw, list) else re.split(r",|;|\band\b|\n", str(raw))
    seen, out = set(), []
    for item in items:
        s = _clean_text(item)
        if not s or s.lower() in seen:
            continue
        if len(s) > 50:
            return hard(f"The skill '{s[:20]}…' is longer than 50 characters. Ask for a shorter name.")
        seen.add(s.lower())
        out.append(s)
    if not out:
        return hard("No skills found. Ask again, or skip it (it's optional).")
    if len(out) > 30:
        return hard("At most 30 skills are allowed. Ask for their top ones.")
    return ok(out)


def normalize_full_name(raw: Any) -> Result:
    text = _clean_text(raw)
    if not (2 <= len(text) <= 100):
        return hard("Name must be 2-100 characters.")
    return ok(text)


NORMALIZERS: dict[str, Callable[[Any], Result]] = {
    "full_name": normalize_full_name,
    "email": normalize_email,
    "college": normalize_college,
    "degree": normalize_degree,
    "branch": normalize_branch,
    "graduation_year": normalize_graduation_year,
    "cgpa": normalize_cgpa,
    "preferred_location": normalize_location,
    "available_from": normalize_available_from,
    "internship_duration_months": normalize_duration,
    "linkedin_url": normalize_linkedin_url,
    "github_url": normalize_github_url,
    "skills": normalize_skills,
}


def normalize(field: str, raw: Any) -> Result:
    fn = NORMALIZERS.get(field)
    if fn is None:
        return hard(f"'{field}' isn't a field we collect.")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return hard("Empty value.")
    return fn(raw)


def is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, (str, list)) and len(value) == 0)
