from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


SUPPORTED_LANGUAGE_CODES = ("en", "si", "ta")


@dataclass(frozen=True)
class LanguageProfile:
    code: str
    name: str
    locale: str
    greeting: str
    response_policy: str
    fillers: dict[str, str]


@dataclass(frozen=True)
class LanguageDecision:
    language: str | None
    confidence: float
    source: str


TAMIL_FORBIDDEN_SINHALA_MARKERS = frozenset({"hari", "ow", "puluwan", "stuthi"})
TAMIL_FORMAL_PHRASES = (
    "\u0bb5\u0bbf\u0bb0\u0bc1\u0bae\u0bcd\u0baa\u0bc1\u0b95\u0bbf\u0bb1\u0bc0\u0bb0\u0bcd\u0b95\u0bb3\u0bbe",
    "\u0bb5\u0bbf\u0bb0\u0bc1\u0bae\u0bcd\u0baa\u0bc1\u0b95\u0bbf\u0bb1\u0bc0\u0bb0\u0bcd",
    "\u0b89\u0b99\u0bcd\u0b95\u0bb3\u0bc1\u0b95\u0bcd\u0b95\u0bc1 \u0b8e\u0baa\u0bcd\u0baa\u0b9f\u0bbf \u0b89\u0ba4\u0bb5\u0bb2\u0bbe\u0bae\u0bcd",
    "\u0b86\u0b95\u0bbf\u0baf\u0bb5\u0bc8",
    "\u0baa\u0bc6\u0bb1\u0baa\u0bcd\u0baa\u0b9f\u0bcd\u0b9f\u0ba4\u0bc1",
    "\u0b89\u0bb1\u0bc1\u0ba4\u0bbf\u0baa\u0bcd\u0baa\u0b9f\u0bc1\u0ba4\u0bcd\u0ba4\u0bb2\u0bbe\u0bae\u0bbe",
    "\u0ba8\u0bc0\u0b99\u0bcd\u0b95\u0bb3\u0bc7 \u0bb5\u0ba8\u0bcd\u0ba4\u0bc1 \u0baa\u0bc6\u0bb1\u0bcd\u0bb1\u0bc1\u0b95\u0bcd\u0b95\u0bca\u0bb3\u0bcd\u0bb3",
)


LANGUAGE_PROFILES = {
    "en": LanguageProfile(
        code="en",
        name="English",
        locale="en-LK",
        greeting="Hello, this is Zeptaz Voice. How can I help you today?",
        response_policy=(
            "Use concise, friendly spoken English. Keep one consistent conversational register. "
            "Do not introduce Sinhala or Tamil discourse words unless the caller explicitly asks to switch languages."
        ),
        fillers={
            "information": "Sure, I am checking those details.",
            "appointment": "Sure, I am checking availability.",
            "order": "Sure, I am checking the order details.",
            "default": "Sure, one moment while I check.",
        },
    ),
    "si": LanguageProfile(
        code="si",
        name="Sinhala",
        locale="si-LK",
        greeting="හලෝ, මේ Zeptaz Voice. අද මම ඔබට උදව් කරන්නෙ කොහොමද?",
        response_policy=(
            "Use the same natural, conversational Sinhala-English style as the caller. "
            "Keep responses short and suitable for a Sri Lankan phone conversation. "
            "Do not introduce Tamil discourse words unless the caller explicitly asks to switch languages."
        ),
        fillers={
            "information": "Mama details check karanawa.",
            "appointment": "Mama available welawa balannam.",
            "order": "Mama order details balannam.",
            "default": "Poddak balannam.",
        },
    ),
    "ta": LanguageProfile(
        code="ta",
        name="Tamil",
        locale="ta-LK",
        greeting="வணக்கம், இது Zeptaz Voice. என்ன உதவி வேணும்?",
        response_policy=(
            "Use friendly, everyday spoken Sri Lankan Tamil for the entire response. "
            "Mirror the caller's casual or Tamil-English mixed register and keep that register consistent. "
            "For a casual caller, use natural spoken forms rather than formal plural verb endings, literary connectors, passive announcements, or official-sounding confirmation language. "
            "Keep wording short and telephone-friendly; do not become more formal during clarification, summary, or confirmation. "
            "Avoid formal wording such as 'விரும்புகிறீர்களா' and 'உங்களுக்கு எப்படி உதவலாம்'. "
            "Do not use Sinhala words or discourse markers such as 'hari', 'ow', 'puluwan', or 'stuthi'. "
            "English business names, menu item names, prices, addresses, and common ordering terms are allowed. "
            "Language style must not introduce business-flow questions; follow the backend conversation directive."
        ),
        fillers={
            "information": "சரி, details பார்த்துட்டு சொல்றேன்.",
            "appointment": "சரி, நேரம் இருக்கானு பார்க்கிறேன்.",
            "order": "சரி, order details பார்க்கிறேன்.",
            "default": "சரி, ஒரு நிமிஷம்.",
        },
    ),
}


_ALIASES = {
    "en": "en",
    "en-lk": "en",
    "en-us": "en",
    "english": "en",
    "si": "si",
    "si-lk": "si",
    "sin": "si",
    "sinhala": "si",
    "ta": "ta",
    "ta-lk": "ta",
    "ta-in": "ta",
    "tam": "ta",
    "tamil": "ta",
}

_ROMANIZED_MARKERS = {
    "en": {
        "anything",
        "available",
        "can",
        "else",
        "hello",
        "help",
        "how",
        "please",
        "sure",
        "thanks",
        "today",
        "what",
        "which",
        "would",
        "you",
    },
    "si": {
        "ane",
        "ekak",
        "eka",
        "karanna",
        "mata",
        "meka",
        "mokakda",
        "monawada",
        "nathnam",
        "oneda",
        "oneh",
        "oya",
        "oyage",
        "puluwan",
        "thiyenawa",
    },
    "ta": {
        "aama",
        "edhu",
        "enaku",
        "enakku",
        "enga",
        "irukku",
        "kandippa",
        "mudiyuma",
        "nandri",
        "neenga",
        "onnu",
        "panna",
        "pannanum",
        "pannunga",
        "seri",
        "sollunga",
        "unga",
        "vanakkam",
        "venum",
        "vera",
    },
}

_ROMANIZED_PREFIXES = {
    "si": ("karann", "thiyen"),
    "ta": ("enak", "mudiy", "panni", "pannu"),
}

_EXPLICIT_REQUESTS = {
    "en": ("speak english", "english please", "in english"),
    "si": ("speak sinhala", "sinhala please", "sinhala walin"),
    "ta": ("speak tamil", "tamil please", "tamil-la", "tamil la", "தமிழில்", "தமிழ்ல"),
}


def normalize_language(value: str | None, *, default: str | None = None) -> str | None:
    normalized = (value or "").strip().casefold().replace("_", "-")
    if not normalized:
        return default
    language = _ALIASES.get(normalized)
    if language is None:
        raise ValueError(f"unsupported language: {value}; expected one of en, si, ta")
    return language


def get_language_profile(value: str) -> LanguageProfile:
    return LANGUAGE_PROFILES[normalize_language(value) or "en"]


def detect_language(text: str, allowed: Iterable[str] = SUPPORTED_LANGUAGE_CODES) -> LanguageDecision:
    allowed_codes = {normalize_language(code) for code in allowed}
    value = (text or "").strip()
    if not value:
        return LanguageDecision(None, 0.0, "empty")

    lowered = value.casefold()
    for language, phrases in _EXPLICIT_REQUESTS.items():
        if language in allowed_codes and any(phrase in lowered for phrase in phrases):
            return LanguageDecision(language, 1.0, "explicit_request")

    tamil_chars = sum("\u0b80" <= char <= "\u0bff" for char in value)
    sinhala_chars = sum("\u0d80" <= char <= "\u0dff" for char in value)
    if tamil_chars and "ta" in allowed_codes and tamil_chars >= sinhala_chars:
        return LanguageDecision("ta", min(1.0, 0.9 + tamil_chars / 100), "unicode_script")
    if sinhala_chars and "si" in allowed_codes:
        return LanguageDecision("si", min(1.0, 0.9 + sinhala_chars / 100), "unicode_script")

    tokens = set(re.findall(r"[a-z]+", lowered))
    scores = {}
    for language, markers in _ROMANIZED_MARKERS.items():
        if language not in allowed_codes:
            continue
        prefixes = _ROMANIZED_PREFIXES.get(language, ())
        matched_tokens = {
            token
            for token in tokens
            if token in markers or any(token.startswith(prefix) for prefix in prefixes)
        }
        scores[language] = len(matched_tokens)
    best_language = max(scores, key=scores.get, default=None)
    best_score = scores.get(best_language, 0)
    other_score = max((score for language, score in scores.items() if language != best_language), default=0)
    if best_score >= 2 and best_score > other_score:
        confidence = min(0.95, 0.62 + best_score * 0.08)
        return LanguageDecision(best_language, confidence, "romanized_markers")

    ascii_letters = sum(char.isascii() and char.isalpha() for char in value)
    letters = sum(char.isalpha() for char in value)
    if "en" in allowed_codes and letters and ascii_letters / letters > 0.9:
        return LanguageDecision("en", 0.55, "latin_fallback")
    return LanguageDecision(None, 0.0, "undetermined")


def output_policy_violations(text: str, language: str | None) -> list[str]:
    """Return deterministic spoken-language policy violations worth reviewing."""
    if normalize_language(language) != "ta":
        return []

    value = (text or "").casefold()
    tokens = set(re.findall(r"[a-z]+", value))
    violations = [
        f"sinhala_marker:{marker}"
        for marker in sorted(tokens.intersection(TAMIL_FORBIDDEN_SINHALA_MARKERS))
    ]
    violations.extend(
        f"formal_tamil:{phrase}"
        for phrase in TAMIL_FORMAL_PHRASES
        if phrase in value
    )
    return violations


def filler_category(tool_name: str | None) -> str:
    name = (tool_name or "").casefold()
    if "information" in name:
        return "information"
    if "appointment" in name:
        return "appointment"
    if any(token in name for token in ("menu", "cart", "order")):
        return "order"
    return "default"
