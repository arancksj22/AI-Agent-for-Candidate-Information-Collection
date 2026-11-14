"""Cheap conversational signals on each candidate message (regex + word lists, no model).

They do two jobs:
- go into the PRE-CHECKS so the LLM knows what code noticed (sarcasm, a self-correction, abuse...);
- feed hard rules in code that don't depend on the LLM noticing (a joking value needs confirmation,
  repeated abuse ends the chat with a human hand-off).
"""
from __future__ import annotations

import re

_W = lambda words: re.compile(r"(?<![a-z])(" + "|".join(words) + r")(?![a-z])", re.I)  # noqa: E731

JOKE = re.compile(
    r"(?<![a-z])(lol+|lmao+|lmfao|rofl|haha+|hehe+|hihi|jk|j/k|just kidding|kidding|joking|"
    r"yeah right|as if|obviously not|sure buddy|in your dreams|mazak|mazaak|majak)(?![a-z])"
    r"|/s\b|😂|🤣|😜|😝|🙄|😏|🤡", re.I)

CORRECTION = _W([r"no wait", r"wait no", r"sorry i mean", r"sorry,? i meant", r"i meant", r"i mean",
                 r"actually", r"correction", r"scratch that", r"not \S+,? (?:it'?s|its)", r"my bad",
                 r"typo", r"oops", r"nahi nahi", r"matlab", r"galti se", r"sorry galat"])

OPT_OUT = _W([r"stop messaging", r"stop texting", r"stop contacting", r"don'?t (?:message|text|contact|call) me",
              r"do not (?:message|text|contact|call) me", r"unsubscribe", r"leave me alone", r"remove me",
              r"not interested", r"no longer interested", r"withdraw", r"cancel my application",
              r"delete my (?:data|application|details)", r"mat karo message", r"message mat karo",
              r"pareshan mat karo", r"interest nahi"])

ABUSE = _W([r"f+u+c+k\w*", r"wtf", r"stfu", r"shit\w*", r"bullshit", r"bitch\w*", r"bastard", r"asshole",
            r"idiot\w*", r"stupid", r"dumb\w*", r"moron", r"useless", r"pathetic", r"scam\w*", r"fraud",
            r"chutiy\w*", r"bc", r"mc", r"bhenchod", r"madarchod", r"bsdk", r"gandu", r"harami", r"kamina",
            r"saala", r"bakwas", r"pagal", r"bewakoof", r"ullu"])

CONFUSED = re.compile(
    r"^\s*(what|wut|huh|hmm+|sorry\??|pardon|come again|matlab|kya|kya matlab|samjha nahi|samajh nahi aaya|"
    r"i don'?t understand|didn'?t get (?:it|that)|what do you mean|meaning)\s*\??\s*$|"
    r"\b(what do you mean|don'?t understand|samjha nahi|samajh nahi)\b", re.I)

DONT_KNOW = _W([r"idk", r"i don'?t know", r"dunno", r"no idea", r"not sure", r"don'?t remember",
                r"can'?t remember", r"pata nahi", r"pta nahi", r"maloom nahi", r"yaad nahi", r"will check",
                r"need to check", r"let me check", r"check karke"])

SKIP = re.compile(r"^\s*(skip|next|pass|chhodo|chodo|leave it|move on|skip it|skip this)\s*[.!]*\s*$",
                  re.I)

HUMAN = _W([r"real person", r"human", r"talk to (?:someone|somebody|a person|hr)", r"speak to (?:someone|hr)",
            r"are you (?:a )?(?:bot|robot|ai|human|real)", r"is this (?:a )?(?:bot|ai|automated)", r"insaan",
            r"manager", r"call me instead"])

TEST_INPUT = re.compile(r"^\s*(test|testing|asdf\w*|qwer\w*|hello\?*|hi+|hey+|\.+|\?+)\s*$", re.I)

MAX_MESSAGE_CHARS = 1500


def _gibberish(text: str) -> bool:
    letters = re.sub(r"[^a-z]", "", text.lower())
    if len(letters) < 6 or " " in text.strip():
        return False
    vowels = sum(c in "aeiou" for c in letters)
    return vowels / len(letters) < 0.15 or bool(re.search(r"(.)\1{4,}", letters))


def detect(message: str, previous: list[str]) -> dict[str, bool]:
    text = message or ""
    letters = re.sub(r"[^A-Za-z]", "", text)
    return {
        "joking": bool(JOKE.search(text)),
        "self_correction": bool(CORRECTION.search(text)),
        "opt_out": bool(OPT_OUT.search(text)),
        "abuse": bool(ABUSE.search(text)),
        "shouting": len(letters) >= 12 and letters.isupper(),
        "confused": bool(CONFUSED.search(text)),
        "dont_know": bool(DONT_KNOW.search(text)),
        "skip": bool(SKIP.match(text)),
        "wants_human": bool(HUMAN.search(text)),
        "test_or_greeting": bool(TEST_INPUT.match(text)),
        "gibberish": _gibberish(text),
        "repeated": bool(previous) and text.strip().lower() == previous[-1].strip().lower(),
        "very_long": len(text) > MAX_MESSAGE_CHARS,
        "emoji_only": bool(text.strip()) and not re.search(r"[\w]", text),
    }


_HINTS = {
    "joking": "message looks like a joke/sarcasm (lol, jk, 🙄...): don't save values from it unless they "
              "confirm the real value; code will require candidate_confirmed=true",
    "self_correction": "candidate corrected themselves in this message: use the LAST value they gave",
    "opt_out": "candidate may be opting out / withdrawing: confirm once, don't push; if confirmed, note it, "
               "escalate and end",
    "abuse": "abusive language: apologise once, escalate and end (code ends the chat if it happens again)",
    "shouting": "ALL CAPS: candidate may be frustrated; acknowledge briefly and keep it short",
    "confused": "candidate didn't understand: rephrase your last question more simply, don't repeat it word "
                "for word",
    "dont_know": "candidate doesn't know / needs to check: don't press; offer to skip it for now or to "
                 "continue later",
    "skip": "candidate wants to skip this: move to the next field; come back once at the end",
    "wants_human": "candidate is asking about bots/humans: be honest (automated assistant), offer a person; "
                   "escalate only if they want one",
    "test_or_greeting": "no content (greeting/test): reply briefly and ask your question again",
    "gibberish": "looks like gibberish/keyboard mash: ask them to say that again",
    "repeated": "same message as last time: they may think you didn't get it; acknowledge and move on",
    "very_long": f"very long message (cut to {MAX_MESSAGE_CHARS} chars): pick out the relevant details only",
    "emoji_only": "emoji-only reply: a 👍/👌 to a yes/no question is yes; otherwise ask in words",
}


def hints(flags: dict[str, bool]) -> list[str]:
    return [f"signal: {_HINTS[k]}" for k, on in flags.items() if on]
