import httpx
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
async def test_decision_reply_is_signed_and_a_missing_reply_is_none():
    from adapters.hutch.contracts import VoiceDecisionReplyRequest

    seen = []

    def handler(request):
        seen.append((request.url.path, request.headers.get("x-voice-event-id")))
        if b"proposal-known" in request.content:
            return httpx.Response(200, json={"response_id": "r1", "reply_text": "Queued for review.",
                                             "speech_text": "Queued for review."})
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client = HutchResolveClient(base_url="http://resolve.test/api/v1", secret="s" * 32, client=transport)
        found = await client.decision_reply(VoiceDecisionReplyRequest(
            binding_id="b", voice_session_id="v", event_id="e1", proposal_id="proposal-known"))
        missing = await client.decision_reply(VoiceDecisionReplyRequest(
            binding_id="b", voice_session_id="v", event_id="e2", proposal_id="proposal-other"))
    assert found.reply_text == "Queued for review." and missing is None
    assert seen == [("/api/v1/integrations/voice/decision-replies", "e1"),
                    ("/api/v1/integrations/voice/decision-replies", "e2")]
