"""Rule-based extraction of complaint fields from free text / a speech transcript (pure Python, no Django).

The original text is NEVER rewritten: `description` is the citizen's own words (whitespace tidied only). Everything
else (title, location phrase, ward, urgency) is a *suggestion* that the user reviews and can edit before submitting.
"""
import re

from .kannada import KN_LOCATION_MARKERS, detect_language, has_kannada

SENTENCE_SPLIT = re.compile(r"(?<=[.!?।])\s+|\n+")
EN_STRONG_MARKERS = r"(?:near|opposite|opp\.?|beside|behind|next to|in front of|close to|adjacent to)"
EN_WEAK_MARKERS = r"(?:at|in|on)"
EN_PLACE_WORDS = ("road", "street", "cross", "main", "layout", "nagar", "colony", "circle", "junction", "park", "lake",
                  "bus stand", "bus stop", "market", "temple", "school", "hospital", "stage", "extension", "block",
                  "sector", "phase", "area", "ward", "signal", "metro", "station", "apartment", "gate", "bridge",
                  "flyover", "underpass")
EN_STOP = {"and", "since", "for", "but", "from", "which", "where", "it", "is", "are", "was", "has", "have", "there",
           "because", "so", "please", "that", "when", "while", "again", "also", "now", "today", "yesterday"}
URGENT_EN = ("urgent", "emergency", "dangerous", "danger", "accident", "hazard", "fire", "electrocut", "live wire",
             "collapsed", "flooding", "flooded", "injury", "injured")
URGENT_KN = ("ತುರ್ತ", "ಅಪಾಯ", "ಅಪಘಾತ", "ಬೆಂಕಿ", "ಪ್ರವಾಹ")   # stems: ತುರ್ತು / ತುರ್ತಾಗಿ, ಅಪಾಯಕಾರಿ ...

MIN_TITLE, MIN_DESCRIPTION, MAX_TITLE = 5, 15, 80


def tidy(text: str) -> str:
    """Collapse runs of spaces / tabs, keep paragraph breaks, strip. Does not change any word."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def make_title(text: str) -> str:
    text = tidy(text)
    if not text:
        return ""
    first = next((s.strip() for s in SENTENCE_SPLIT.split(text) if s.strip()), text)
    if len(first) < MIN_TITLE:
        first = text
    if len(first) <= MAX_TITLE:
        return first.rstrip(".!?। ")
    cut = first[:MAX_TITLE]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(",;:- ") + "…"


def _en_location(text: str) -> str:
    low = text.lower()
    best = ""
    strong = re.search(rf"\b{EN_STRONG_MARKERS}\s+(?:the\s+)?", low)
    candidates = []
    if strong:
        candidates.append((strong.start(), strong.end(), True))
    for m in re.finditer(rf"\b{EN_WEAK_MARKERS}\s+(?:the\s+)?", low):
        candidates.append((m.start(), m.end(), False))
    for start, end, is_strong in sorted(candidates, key=lambda c: (not c[2], c[0])):
        words = []
        for m in re.finditer(r"([\w'/-]+)([,.;!?]?)", text[end:]):
            word, punct = m.group(1), m.group(2)
            if word.lower() in EN_STOP or len(words) >= 6:
                break
            words.append(word)
            if punct:                      # a comma / full stop ends the place phrase
                break
        phrase = " ".join(words).strip()
        if not phrase:
            continue
        if is_strong or any(p in phrase.lower() for p in EN_PLACE_WORDS):
            best = text[start:end].strip() + " " + phrase if is_strong else phrase
            break
    return best.strip()


def _kn_location(text: str) -> str:
    tokens = text.replace(",", " ").replace(".", " ").split()
    for i, tok in enumerate(tokens):
        if any(tok.startswith(m) for m in KN_LOCATION_MARKERS):
            return " ".join(tokens[max(0, i - 3): i + 1]).strip()
    return ""


def find_location_phrase(text: str) -> str:
    text = tidy(text)
    if has_kannada(text):
        loc = _kn_location(text)
        if loc:
            return loc
    return _en_location(text)


def match_ward(text: str, wards) -> dict | None:
    """`wards` = iterable of (id, name). Returns the ward whose name is written in the text (longest name wins)."""
    low = (text or "").lower()
    hits = [(len(n), i, n) for i, n in wards if n and len(n) >= 4 and n.lower() in low]
    if not hits:
        return None
    _, wid, name = max(hits)
    return {"id": wid, "name": name, "how": "ward name mentioned in your text"}


def urgency_words(text: str) -> list[str]:
    low = (text or "").lower()
    return [w for w in URGENT_EN if w in low] + [w for w in URGENT_KN if w in (text or "")]


def extract_fields(text: str, wards=()) -> dict:
    """Return suggested complaint fields for the review screen. Nothing here is authoritative."""
    desc = tidy(text)
    lang = detect_language(desc)
    urgent = urgency_words(desc)
    problems = []
    if len(desc) < MIN_DESCRIPTION:
        problems.append(f"Description is shorter than {MIN_DESCRIPTION} characters - please add more detail.")
    title = make_title(desc)
    if title and len(title) < MIN_TITLE:
        problems.append(f"Title is shorter than {MIN_TITLE} characters.")
    return {
        "language": lang,
        "title": title,
        "description": desc,               # the citizen's original words, unchanged
        "location_text": find_location_phrase(desc),
        "ward": match_ward(desc, wards),
        "urgency_words": urgent,
        "suggested_severity": 4 if urgent else None,
        "problems": problems,
    }
