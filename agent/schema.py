"""Field rules, enums and alias tables, taken from GET /v1/schema."""
from __future__ import annotations

MANDATORY_FIELDS = [
    "full_name", "phone", "email", "college", "degree", "branch", "graduation_year",
    "cgpa", "preferred_location", "available_from", "internship_duration_months",
]
OPTIONAL_FIELDS = ["linkedin_url", "github_url", "skills"]

# Fields the agent may write with candidate data. full_name is always present and phone is read-only.
COLLECTABLE_FIELDS = [f for f in MANDATORY_FIELDS if f not in ("full_name", "phone")] + OPTIONAL_FIELDS

# Suggested asking order: easy and impersonal first, logistics next, sensitive last.
ASK_PRIORITY = [
    "email", "college", "degree", "branch", "graduation_year",
    "preferred_location", "available_from", "internship_duration_months", "cgpa",
]

DEGREES = ["B.Tech", "B.E.", "B.Sc", "BCA", "MBA Tech", "MCA", "M.Tech", "Other"]
LOCATIONS = ["Mumbai", "Bengaluru", "Remote", "No preference"]
DURATIONS = [2, 3, 6]
STATUSES = ["pending", "in_progress", "completed", "no_answer", "refused", "needs_human"]

GRAD_YEAR_MIN, GRAD_YEAR_MAX = 2026, 2028
AVAILABLE_MIN, AVAILABLE_MAX = "2026-01-01", "2027-12-31"
NOTES_MAX = 4000

# Keys are compared after lowercasing and stripping everything except letters/digits.
DEGREE_ALIASES = {
    "btech": "B.Tech", "bacheloroftechnology": "B.Tech", "btec": "B.Tech",
    "be": "B.E.", "bachelorofengineering": "B.E.", "beng": "B.E.",
    "bsc": "B.Sc", "bachelorofscience": "B.Sc",
    "bca": "BCA", "bachelorofcomputerapplications": "BCA",
    "mbatech": "MBA Tech", "mbatechintegrated": "MBA Tech",
    "mca": "MCA", "masterofcomputerapplications": "MCA",
    "mtech": "M.Tech", "masteroftechnology": "M.Tech",
    "other": "Other",
}

LOCATION_ALIASES = {
    "mumbai": "Mumbai", "bombay": "Mumbai", "navimumbai": "Mumbai", "thane": "Mumbai",
    "bengaluru": "Bengaluru", "bangalore": "Bengaluru", "blr": "Bengaluru", "bengalore": "Bengaluru",
    "banglore": "Bengaluru",
    "remote": "Remote", "wfh": "Remote", "workfromhome": "Remote", "online": "Remote", "fromhome": "Remote",
    "nopreference": "No preference", "anywhere": "No preference", "any": "No preference",
    "dontmind": "No preference", "doesntmatter": "No preference", "either": "No preference",
    "anyofthem": "No preference", "flexible": "No preference", "noprefernce": "No preference",
}

DURATION_WORDS = {
    "two": 2, "three": 3, "six": 6, "four": 4, "five": 5, "one": 1, "twelve": 12,
    "a quarter": 3, "half a year": 6, "half year": 6,
}

COMMON_EMAIL_DOMAINS = [
    "gmail.com", "yahoo.com", "yahoo.co.in", "outlook.com", "hotmail.com", "icloud.com",
    "rediffmail.com", "live.com", "protonmail.com",
]

FIELD_LABELS = {
    "email": "email address",
    "college": "college name",
    "degree": "degree",
    "branch": "branch / specialisation",
    "graduation_year": "graduation year",
    "cgpa": "CGPA (10-point scale)",
    "preferred_location": "preferred location",
    "available_from": "date they can start",
    "internship_duration_months": "internship duration",
    "linkedin_url": "LinkedIn URL",
    "github_url": "GitHub URL",
    "skills": "skills",
}
