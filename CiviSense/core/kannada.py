"""Small Kannada / English helpers (pure Python, no Django).

CiviSense's classifier and duplicate detector are TF-IDF models trained on *English* complaint text, and
`ml.wrangling.clean_text` removes every non-Latin character. A Kannada complaint therefore produces an EMPTY
feature vector. To keep the original Kannada text untouched while still giving the existing models something to work
with, `english_hints()` maps a small, hand-written lexicon of common civic words to English keywords. The hints are
used ONLY as extra input for classification / duplicate matching and are never stored in place of the citizen's text.

Limits (be honest in the UI and README): the lexicon is small and has not been reviewed by a native-speaker linguist;
it recognises words by substring, so inflected forms work but unusual vocabulary does not. For richer understanding
the AI assistant (Claude) reads the Kannada text directly.
"""
import re

KANNADA_RE = re.compile(r"[ಀ-೿]")

# (Kannada word stem, English keywords). Order does not matter. Stems are matched as substrings so that case endings
# (e.g. ರಸ್ತೆಯಲ್ಲಿ "on the road") still match ರಸ್ತೆ.
LEXICON = [
    ("ಕಸ", "garbage waste dustbin"), ("ತ್ಯಾಜ್ಯ", "garbage waste"), ("ಕಸದ ರಾಶಿ", "garbage overflow"),
    ("ನೀರು", "water supply"), ("ನೀರಿನ", "water supply"), ("ಕುಡಿಯುವ ನೀರು", "drinking water supply"),
    ("ಪೈಪ್", "water pipe leak"), ("ಸೋರಿಕೆ", "leak leakage"),
    ("ರಸ್ತೆ", "road"), ("ಗುಂಡಿ", "pothole road damage"), ("ಪಾದಚಾರಿ", "footpath"), ("ಫುಟ್‌ಪಾತ್", "footpath"),
    ("ಡಾಂಬರು", "road asphalt"),
    ("ವಿದ್ಯುತ್", "electricity power supply"), ("ಕರೆಂಟ್", "electricity power supply"), ("ಟ್ರಾನ್ಸ್‌ಫಾರ್ಮರ್", "electricity transformer"),
    ("ಬೀದಿ ದೀಪ", "street light"), ("ಬೀದಿದೀಪ", "street light"), ("ದೀಪ", "light lamp"), ("ಲೈಟ್", "light"),
    ("ಚರಂಡಿ", "drain drainage"), ("ಒಳಚರಂಡಿ", "sewage sewerage drain"), ("ಮ್ಯಾನ್‌ಹೋಲ್", "manhole sewage"),
    ("ಮರ", "tree"), ("ಕೊಂಬೆ", "tree branch"),
    ("ಶೌಚಾಲಯ", "public toilet"), ("ಕೆರೆ", "lake"), ("ಉದ್ಯಾನ", "park garden"), ("ಪಾರ್ಕ್", "park garden"),
    ("ಬಸ್", "bus public transport"), ("ಸಂಚಾರ", "traffic"), ("ಟ್ರಾಫಿಕ್", "traffic"), ("ಸಿಗ್ನಲ್", "traffic signal"),
    ("ನಾಯಿ", "stray dog animal"), ("ದನ", "cattle animal"),
    ("ಮಾಲಿನ್ಯ", "pollution"), ("ಹೊಗೆ", "smoke pollution"), ("ದುರ್ವಾಸನೆ", "bad smell"),
    ("ಬೆಂಕಿ", "fire"), ("ಅಗ್ನಿ", "fire"), ("ಕಳ್ಳತನ", "theft crime police"), ("ಪೊಲೀಸ್", "police crime"),
    ("ಅತಿಕ್ರಮಣ", "encroachment"), ("ಸೊಳ್ಳೆ", "mosquito"),
]

# Words that introduce a place in Kannada ("near X", "opposite X", "beside X", "in X layout", ...)
KN_LOCATION_MARKERS = ["ಹತ್ತಿರ", "ಬಳಿ", "ಪಕ್ಕದಲ್ಲಿ", "ಪಕ್ಕ", "ಎದುರು", "ಎದುರಿಗೆ", "ಹಿಂಭಾಗ", "ಸಮೀಪ"]
KN_PLACE_WORDS = ["ಬಡಾವಣೆ", "ಲೇಔಟ್", "ನಗರ", "ಮುಖ್ಯ ರಸ್ತೆ", "ಕ್ರಾಸ್", "ಸರ್ಕಲ್", "ವೃತ್ತ", "ಬಸ್ ನಿಲ್ದಾಣ", "ಬಡಾವಣೆಯ"]


def has_kannada(text: str) -> bool:
    return bool(KANNADA_RE.search(text or ""))


def kannada_ratio(text: str) -> float:
    letters = [ch for ch in (text or "") if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for ch in letters if "ಀ" <= ch <= "೿") / len(letters)


def detect_language(text: str) -> str:
    """'kn' when at least a fifth of the letters are Kannada, otherwise 'en'."""
    return "kn" if kannada_ratio(text) >= 0.2 else "en"


def english_hints(text: str) -> str:
    """English keywords for Kannada civic words found in `text` ('' if none / no Kannada)."""
    if not has_kannada(text):
        return ""
    seen, out = set(), []
    for stem, eng in LEXICON:
        # Kannada inflects with SUFFIXES, so a stem must start a word (not sit in the middle of another word).
        # ... and must not be followed by a dependent vowel sign / virama (ಮರ + ೆ = ಮರೆ is a different syllable).
        if re.search(r"(?<![ಀ-೿])" + re.escape(stem) + r"(?![ಾ-್ೕೖ])", text):
            for w in eng.split():
                if w not in seen:
                    seen.add(w)
                    out.append(w)
    return " ".join(out)


def nlp_text(*parts: str) -> str:
    """Text handed to the English TF-IDF models: the original text plus English hints for Kannada words.

    For purely English input this is identical to the plain joined text, so existing behaviour is unchanged.
    """
    joined = " ".join(p.strip() for p in parts if p and p.strip())
    hints = english_hints(joined)
    return f"{joined} {hints}".strip() if hints else joined
