"""Validated, call-scoped settings for the realtime voice path."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass

logger = logging.getLogger(__name__)


def _bounded_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid %s; using %s", name, default)
        return default
    if not low <= value <= high:
        logger.warning("Ignoring out-of-range %s; using %s", name, default)
        return default
    return value


def _choice(name: str, default: str, choices: set[str]) -> str:
    value = os.getenv(name, default).strip().lower()
    if value not in choices:
        logger.warning("Ignoring invalid %s; using %s", name, default)
        return default
    return value


def _enabled(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    logger.warning("Ignoring invalid %s; using %s", name, default)
    return default


_LANGUAGE_CODE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
# Sri Lankan callers: without hints, short Sinhala turns were transcribed as Japanese or Spanish.
DEFAULT_INPUT_LANGUAGES = ("si-LK", "en-US", "ta-IN")


def _language_codes(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None:
        return default
    codes = tuple(code.strip() for code in raw.split(",") if code.strip())
    if not codes or not all(_LANGUAGE_CODE.fullmatch(code) for code in codes):
        logger.warning("Ignoring invalid %s; using %s", name, ",".join(default))
        return default
    return codes


@dataclass(frozen=True)
class VoiceRuntimeConfig:
    model: str
    profile: str
    packet_duration_ms: int
    end_silence_ms: int
    full_duplex: bool
    context_compression: bool
    protocol_version: int
    knowledge_mode: str
    tool_execution_mode: str
    tool_result_mode: str
    retrieval_mode: str
    input_language_codes: tuple[str, ...] = DEFAULT_INPUT_LANGUAGES

    @classmethod
    def from_environment(cls) -> "VoiceRuntimeConfig":
        model = os.getenv("GEMINI_LIVE_MODEL", "models/gemini-3.1-flash-live-preview").strip()
        profile = _choice(
            "GEMINI_LIVE_COMPATIBILITY_PROFILE", "legacy", {"legacy", "gemini_3_8_blocking"}
        )
        return cls(
            model=model or "models/gemini-3.1-flash-live-preview",
            profile=profile,
            packet_duration_ms=_bounded_int("ZEPTAZ_VOICE_INPUT_PACKET_MS", 128, 20, 250),
            end_silence_ms=_bounded_int("GEMINI_LIVE_END_SILENCE_MS", 700, 300, 1500),
            full_duplex=_enabled("ZEPTAZ_FULL_DUPLEX_ENABLED", True),
            context_compression=_enabled("GEMINI_LIVE_CONTEXT_COMPRESSION_ENABLED", True),
            protocol_version=_bounded_int("ZEPTAZ_VOICE_PROTOCOL_VERSION", 2, 2, 3),
            knowledge_mode=_choice("ZEPTAZ_KNOWLEDGE_MODE", "lookup_only", {"lookup_only", "snapshot"}),
            tool_execution_mode=_choice("ZEPTAZ_TOOL_EXECUTION_MODE", "sequential", {"sequential", "selective_async"}),
            tool_result_mode=_choice("ZEPTAZ_TOOL_RESULT_MODE", "legacy", {"legacy", "compact"}),
            retrieval_mode=_choice("ZEPTAZ_RETRIEVAL_MODE", "lexical", {"lexical", "shadow_hybrid", "hybrid"}),
            input_language_codes=_language_codes("GEMINI_LIVE_INPUT_LANGUAGES", DEFAULT_INPUT_LANGUAGES),
        )

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()[:16]

    def public_dict(self) -> dict[str, object]:
        return {**asdict(self), "fingerprint": self.fingerprint}
