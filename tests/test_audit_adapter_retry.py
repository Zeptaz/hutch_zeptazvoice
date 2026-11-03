import httpx
import json
import pytest

from adapters.hutch.adapter import HutchAdapter
from adapters.hutch.client import HutchResolveClient, ResolveClientError
from adapters.hutch.contracts import VoiceTurnRequest, VoiceTurnResponse


@pytest.mark.asyncio
async def test_pending_turn_uses_same_identity_after_transport_timeout():
    class Client:
        def __init__(self):
            self.requests = []

        async def send_turn(self, turn):
            self.requests.append(turn)
            if len(self.requests) == 1:
                raise ResolveClientError("resolve_unavailable", retryable=True)
            return VoiceTurnResponse(response_id="response-1", reply_text="Pending resolved", speech_text="Pending resolved")

    client = Client()
    adapter = HutchAdapter(client)
    with pytest.raises(ResolveClientError):
        await adapter.forward_final_turn(binding_id="b", voice_session_id="s", transcript="yes", language="en")
    with pytest.raises(ResolveClientError) as changed:
        await adapter.forward_final_turn(binding_id="b", voice_session_id="s", transcript="different", language="en")
    assert changed.value.code == "resolve_turn_pending"
    result = await adapter.forward_final_turn(binding_id="b", voice_session_id="s", transcript="yes", language="en")
    assert result.reply_text == "Pending resolved"
    assert len(client.requests) == 2
    assert client.requests[0].model_dump() == client.requests[1].model_dump()
    assert adapter.pending_turn is None


@pytest.mark.asyncio
async def test_retryable_processing_conflict_does_not_become_permanent_event_conflict():
    bodies = []

    async def handler(request):
        bodies.append(request.content)
        if len(bodies) == 1:
            raise httpx.ReadTimeout("simulated accepted turn", request=request)
        return httpx.Response(409, json={"error": {"code": "TURN_IN_PROGRESS", "retryable": True}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client = HutchResolveClient(base_url="http://resolve.test/api/v1", secret="s" * 32, client=transport)
        request = VoiceTurnRequest(binding_id="b", voice_session_id="s", event_id="e", turn_id="t",
                                   transcript="yes", language="en", is_final=True)
        with pytest.raises(ResolveClientError) as pending:
            await client.send_turn(request)
    assert pending.value.code == "resolve_turn_pending"
    assert pending.value.retryable is True
    assert bodies[0] == bodies[1]


@pytest.mark.asyncio
async def test_decision_readback_is_signed_and_validated():
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(200, json={
            "response_id": "saved-1", "reply_text": "Pending review",
            "speech_text": "Pending review", "operation_status": "REVIEW_REQUIRED",
            "end_session": False,
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client = HutchResolveClient(base_url="http://resolve.test/api/v1", secret="s" * 32, client=transport)
        result = await client.read_decision_result(
            binding_id="binding-1", voice_session_id="voice-1", client_turn_id="turn-1")
    assert result.response_id == "saved-1"
    assert len(seen) == 1
    assert seen[0].url.path.endswith("/integrations/voice/decision-results")
    body = json.loads(seen[0].content)
    assert body["binding_id"] == "binding-1"
    assert body["voice_session_id"] == "voice-1"
    assert body["client_turn_id"] == "turn-1"
    assert seen[0].headers["x-voice-event-id"] == body["event_id"]
    assert seen[0].headers["x-voice-signature"]
