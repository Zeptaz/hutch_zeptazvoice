import base64
from typing import Optional

import numpy as np


MU_LAW_MU = 255.0


def _resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate or audio.size == 0:
        return audio.astype(np.int16, copy=False)

    duration = audio.size / float(source_rate)
    target_samples = max(1, int(round(duration * target_rate)))
    source_positions = np.linspace(0.0, audio.size - 1, num=audio.size)
    target_positions = np.linspace(0.0, audio.size - 1, num=target_samples)
    resampled = np.interp(target_positions, source_positions, audio.astype(np.float32))
    return np.clip(resampled, -32768, 32767).astype(np.int16)


def ulaw_bytes_to_pcm16(payload: bytes, sample_rate: int = 8000, target_rate: int = 16000) -> bytes:
    if not payload:
        return b""

    encoded = np.frombuffer(payload, dtype=np.uint8).astype(np.float32)
    normalized = (encoded / 255.0) * 2.0 - 1.0
    magnitude = (1.0 / MU_LAW_MU) * ((1.0 + MU_LAW_MU) ** np.abs(normalized) - 1.0)
    decoded = np.sign(normalized) * magnitude
    pcm = np.clip(decoded * 32767.0, -32768, 32767).astype(np.int16)
    return _resample(pcm, sample_rate, target_rate).tobytes()


def pcm16_to_ulaw_bytes(payload: bytes, sample_rate: int = 24000, target_rate: int = 8000) -> bytes:
    if not payload:
        return b""

    pcm = np.frombuffer(payload, dtype=np.int16)
    pcm = _resample(pcm, sample_rate, target_rate).astype(np.float32) / 32768.0
    magnitude = np.log1p(MU_LAW_MU * np.abs(pcm)) / np.log1p(MU_LAW_MU)
    encoded = np.sign(pcm) * magnitude
    ulaw = np.clip(((encoded + 1.0) * 127.5).round(), 0, 255).astype(np.uint8)
    return ulaw.tobytes()


def decode_twilio_payload(payload_b64: str) -> bytes:
    return ulaw_bytes_to_pcm16(base64.b64decode(payload_b64))


def encode_twilio_payload(pcm_payload: bytes) -> str:
    return base64.b64encode(pcm16_to_ulaw_bytes(pcm_payload)).decode("ascii")


def decode_pcm_payload(payload_b64: str) -> bytes:
    return base64.b64decode(payload_b64)


def encode_pcm_payload(pcm_payload: bytes) -> str:
    return base64.b64encode(pcm_payload).decode("ascii")


def safe_b64decode(payload: Optional[str]) -> bytes:
    if not payload:
        return b""
    return base64.b64decode(payload)
