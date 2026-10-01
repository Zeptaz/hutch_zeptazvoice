from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from contextlib import contextmanager

from .contracts import SessionRequest


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class HutchVoiceStore:
    """Small durable store for short lived browser grants and Resolve bindings."""

    def __init__(self, path: str | None = None, secret: str | None = None):
        self.path = path or os.getenv("HUTCH_VOICE_STORE_PATH", ".runtime/hutch_voice.sqlite3")
        self.secret = secret or os.getenv("HUTCH_VOICE_GRANT_SECRET", "")
        if len(self.secret) < 32:
            raise ValueError("HUTCH_VOICE_GRANT_SECRET must be at least 32 characters")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS bindings (binding_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, session_id TEXT UNIQUE NOT NULL, account_id TEXT NOT NULL, origin TEXT NOT NULL, expires_at INTEGER NOT NULL, grant_id TEXT UNIQUE NOT NULL, grant_used INTEGER NOT NULL DEFAULT 0)")
            db.execute("CREATE TABLE IF NOT EXISTS service_requests (event_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, response_json TEXT NOT NULL, created_at INTEGER NOT NULL DEFAULT (unixepoch()))")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def create_binding(self, request: SessionRequest, *, now: int | None = None) -> tuple[dict, bool]:
        current = int(time.time()) if now is None else now
        if request.expires_at <= current or request.expires_at > current + 1800:
            raise ValueError("Resolve binding expiry must be within the next 30 minutes")
        grant_id = secrets.token_urlsafe(24)
        with self._db() as db:
            db.execute("DELETE FROM bindings WHERE expires_at < ?", (current,))
            db.execute("DELETE FROM service_requests WHERE created_at < ?", (current - 604800,))
            existing = db.execute("SELECT * FROM bindings WHERE binding_id=?", (request.binding_id,)).fetchone()
            if existing:
                if existing["grant_used"]:
                    raise ValueError("used browser grant requires a new Resolve binding")
                if (existing["session_id"], existing["conversation_id"], existing["account_id"], existing["origin"]) != (request.voice_session_id, request.conversation_id, request.account_id, request.origin):
                    raise ValueError("binding_id already belongs to a different scope")
                row = existing
                reused = True
            else:
                db.execute("INSERT INTO bindings(binding_id,conversation_id,session_id,account_id,origin,expires_at,grant_id) VALUES(?,?,?,?,?,?,?)",
                           (request.binding_id, request.conversation_id, request.voice_session_id, request.account_id, request.origin, request.expires_at, grant_id))
                row = db.execute("SELECT * FROM bindings WHERE binding_id=?", (request.binding_id,)).fetchone()
                reused = False
        configured_ttl = max(1, min(60, int(os.getenv("HUTCH_VOICE_GRANT_TTL_SECONDS", "60"))))
        grant = {"binding_id": row["binding_id"], "session_id": row["session_id"], "origin": row["origin"], "exp": min(row["expires_at"], current + configured_ttl), "jti": row["grant_id"]}
        payload = _b64(json.dumps(grant, sort_keys=True, separators=(",", ":")).encode())
        token = payload + "." + _b64(hmac.new(self.secret.encode(), payload.encode(), hashlib.sha256).digest())
        return ({"binding_id": row["binding_id"], "voice_session_id": row["session_id"], "websocket_path": f"/ws/hutch/{row['session_id']}", "browser_grant": token, "expires_at": grant["exp"], "input_format": {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1}, "output_format": {"encoding": "pcm_s16le", "sample_rate": 24000, "channels": 1}}, reused)

    def consume_grant(self, token: str, *, session_id: str, origin: str, now: int | None = None) -> dict | None:
        current = int(time.time()) if now is None else now
        try:
            payload, sig = token.split(".", 1)
            expected = _b64(hmac.new(self.secret.encode(), payload.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(expected, sig):
                return None
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        except Exception:
            return None
        if claims.get("exp", 0) < current or claims.get("session_id") != session_id or claims.get("origin") != origin:
            return None
        with self._db() as db:
            changed = db.execute("UPDATE bindings SET grant_used=1 WHERE grant_id=? AND session_id=? AND origin=? AND expires_at>=? AND grant_used=0", (claims.get("jti"), session_id, origin, current)).rowcount
            row = db.execute("SELECT * FROM bindings WHERE session_id=? AND grant_id=?", (session_id, claims.get("jti"))).fetchone()
        if changed != 1 or not row:
            return None
        return dict(row)

    def lookup_event(self, event_id: str, request_hash: str) -> str | None:
        with self._db() as db:
            row = db.execute("SELECT request_hash,response_json FROM service_requests WHERE event_id=?", (event_id,)).fetchone()
        if row and not hmac.compare_digest(row["request_hash"], request_hash):
            raise ValueError("event ID was reused with a different request")
        return row["response_json"] if row else None

    def save_event(self, event_id: str, request_hash: str, response_json: str) -> None:
        with self._db() as db:
            db.execute("INSERT INTO service_requests(event_id,request_hash,response_json) VALUES(?,?,?)", (event_id, request_hash, response_json))
