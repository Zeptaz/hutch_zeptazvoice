import time

from fastapi.testclient import TestClient

from adapters.hutch.security import canonical_json, signed_headers


class FakeResolve:
    def __init__(self):
        self.events = []

    async def close(self):
        pass

    async def send_event(self, event):
        self.events.append(event)


def test_resolve_session_endpoint_auth_origin_and_idempotency(tmp_path, monkeypatch):
    secret = "r" * 32
    monkeypatch.setenv("HUTCH_RESOLVE_HMAC_SECRET", secret)
    monkeypatch.setenv("HUTCH_RESOLVE_BASE_URL", "http://resolve.test/api/v1")
    monkeypatch.setenv("HUTCH_VOICE_GRANT_SECRET", "g" * 32)
    monkeypatch.setenv("HUTCH_VOICE_STORE_PATH", str(tmp_path / "api.sqlite"))
    monkeypatch.setenv("HUTCH_VOICE_ALLOWED_ORIGINS", "https://demo.example")
    from app import app
    with TestClient(app) as test:
        app.state.resolve = FakeResolve()
        body = canonical_json({"binding_id":"b1","conversation_id":"c1","voice_session_id":"v1","account_id":"a1","origin":"https://demo.example","expires_at":int(time.time())+600})
        headers = signed_headers(secret, "session-event-1", body)
        response = test.post("/api/hutch/sessions",content=body,headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["expires_at"] <= int(time.time())+60
        assert response.json()["websocket_url"].startswith("ws://127.0.0.1:8088/ws/hutch/")
        assert test.post("/api/hutch/sessions",content=body,headers=headers).json() == response.json()
        assert test.get("/healthz").json() == {"status":"ok"}


def test_unsigned_service_request_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("HUTCH_RESOLVE_HMAC_SECRET", "r"*32)
    monkeypatch.setenv("HUTCH_RESOLVE_BASE_URL", "http://resolve.test/api/v1")
    monkeypatch.setenv("HUTCH_VOICE_GRANT_SECRET", "g"*32)
    monkeypatch.setenv("HUTCH_VOICE_STORE_PATH", str(tmp_path / "api.sqlite"))
    from app import app
    with TestClient(app) as test:
        response = test.post("/api/hutch/sessions",json={})
        assert response.status_code == 401


def test_signed_lifecycle_event_is_deduplicated_and_scope_checked(tmp_path, monkeypatch):
    secret = "r" * 32
    monkeypatch.setenv("HUTCH_RESOLVE_HMAC_SECRET", secret)
    monkeypatch.setenv("HUTCH_RESOLVE_BASE_URL", "http://resolve.test/api/v1")
    monkeypatch.setenv("HUTCH_VOICE_GRANT_SECRET", "g" * 32)
    monkeypatch.setenv("HUTCH_VOICE_STORE_PATH", str(tmp_path / "events.sqlite"))
    from app import app
    with TestClient(app) as test:
        fake = FakeResolve()
        app.state.resolve = fake
        payload = {"binding_id":"b1","voice_session_id":"v1","event_id":"evt-1","event_type":"disconnected","details":{}}
        body = canonical_json(payload)
        headers = signed_headers(secret,"evt-1",body)
        assert test.post("/api/hutch/events",content=body,headers=headers).status_code == 200
        assert test.post("/api/hutch/events",content=body,headers=headers).status_code == 200
        assert len(fake.events) == 1
        changed = canonical_json({**payload,"details":{"ended_at":1}})
        changed_headers = signed_headers(secret,"evt-1",changed)
        assert test.post("/api/hutch/events",content=changed,headers=changed_headers).status_code == 409
        assert len(fake.events) == 1
