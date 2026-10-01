from __future__ import annotations

import hashlib
import hmac
import json
import os
import time


def body_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def signature(secret: str, timestamp: str, event_id: str, digest: str) -> str:
    message = f"{timestamp}.{event_id}.{digest}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_signature(secret: str, timestamp: str, event_id: str, digest: str, supplied: str, *, now: int | None = None) -> bool:
    try:
        if abs((now if now is not None else int(time.time())) - int(timestamp)) > 60:
            return False
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(signature(secret, timestamp, event_id, digest), supplied)


def signed_headers(secret: str, event_id: str, body: bytes, *, now: int | None = None) -> dict[str, str]:
    timestamp = str(now if now is not None else int(time.time()))
    digest = body_digest(body)
    return {
        "X-Voice-Timestamp": timestamp,
        "X-Voice-Event-Id": event_id,
        "X-Voice-Body-Sha256": digest,
        "X-Voice-Signature": signature(secret, timestamp, event_id, digest),
        "Content-Type": "application/json",
    }


def canonical_json(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
