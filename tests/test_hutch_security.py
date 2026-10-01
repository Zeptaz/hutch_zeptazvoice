import json

import httpx
import pytest

from adapters.hutch.client import HutchResolveClient
from adapters.hutch.adapter import HutchAdapter
from adapters.hutch.contracts import VoiceTurnRequest, VoiceTurnResponse
from adapters.hutch.security import body_digest, canonical_json, signed_headers, verify_signature
from adapters.hutch.store import HutchVoiceStore


def test_hmac_binds_timestamp_event_and_body_and_expires():
    body = b'{"safe":true}'
    headers = signed_headers("s" * 32, "event-1", body, now=1000)
    assert verify_signature("s" * 32, headers["X-Voice-Timestamp"], "event-1", body_digest(body), headers["X-Voice-Signature"], now=1000)
    assert not verify_signature("s" * 32, "1000", "event-2", body_digest(body), headers["X-Voice-Signature"], now=1000)
    assert not verify_signature("s" * 32, "1000", "event-1", body_digest(body), headers["X-Voice-Signature"], now=1061)


def test_browser_grant_is_origin_bound_expiring_and_single_use(tmp_path):
    store = HutchVoiceStore(str(tmp_path / "voice.sqlite"), "g" * 32)
    request = {"binding_id": "bind-1", "conversation_id": "conv-1", "voice_session_id": "voice-1", "account_id": "acct-1", "origin": "https://demo.example", "expires_at": 2000}
    from adapters.hutch.contracts import SessionRequest
    result, _ = store.create_binding(SessionRequest(**request), now=1000)
    assert store.consume_grant(result["browser_grant"], session_id="voice-1", origin="https://attacker.example", now=1001) is None
    assert store.consume_grant(result["browser_grant"], session_id="voice-2", origin="https://demo.example", now=1001) is None
    assert store.consume_grant(result["browser_grant"], session_id="voice-1", origin="https://demo.example", now=1001)
    assert store.consume_grant(result["browser_grant"], session_id="voice-1", origin="https://demo.example", now=1001) is None


def test_event_ids_cannot_be_reused_with_changed_payload(tmp_path):
    store = HutchVoiceStore(str(tmp_path / "voice.sqlite"), "g" * 32)
    store.save_event("evt-1", "hash-a", '{"accepted":true}')
    assert store.lookup_event("evt-1", "hash-a") == '{"accepted":true}'
    with pytest.raises(ValueError):
        store.lookup_event("evt-1", "hash-b")


def test_confirmation_proposal_is_forwarded_only_after_exact_presentation_ack():
    class StubClient:
        async def send_turn(self, turn):
            self.turn = turn
            return VoiceTurnResponse(response_id="r1", reply_text="Please confirm", speech_text="Please confirm",
                                     proposal={"id":"p1","proposal_hash":"h1","action_type":"DEACTIVATE_VAS","target_label":"VAS","consequences":"Stops future renewal","expires_at":"2030-01-01T00:00:00Z"})
    async def run():
        client = StubClient()
        adapter = HutchAdapter(client)
        await adapter.forward_final_turn(binding_id="b", voice_session_id="s", transcript="Turn off the VAS", language="en")
        assert not adapter.mark_proposal_presented("p1", "wrong-hash")
        assert adapter.mark_proposal_presented("p1", "h1")
        await adapter.forward_final_turn(binding_id="b", voice_session_id="s", transcript="yes", language="en")
        assert client.turn.presented_proposal_id == "p1"
        assert client.turn.presented_proposal_hash == "h1"
        await adapter.forward_final_turn(binding_id="b", voice_session_id="s", transcript="yes again", language="en")
        assert client.turn.presented_proposal_id is None
    import asyncio
    asyncio.run(run())


@pytest.mark.asyncio
async def test_turn_retry_reuses_same_id_and_body_after_timeout():
    calls = []
    async def handler(request):
        calls.append((request.headers["X-Voice-Event-Id"], request.content))
        if len(calls) == 1:
            raise httpx.ReadTimeout("lost reply")
        return httpx.Response(200, json={"response_id":"r1","reply_text":"We found the case.","speech_text":"We found the case."})
    client = HutchResolveClient(base_url="http://resolve.test/api/v1", secret="r" * 32,
                                client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    turn = VoiceTurnRequest(binding_id="b",voice_session_id="s",event_id="evt-1",turn_id="turn-1",transcript="My balance is wrong",language="en",is_final=True)
    response = await client.send_turn(turn)
    assert response.reply_text == "We found the case."
    assert calls[0] == calls[1]
    await client._client.aclose()
