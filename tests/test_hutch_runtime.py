import asyncio
from types import SimpleNamespace as NS

import pytest

import app


def event(*, transcript=None, transcript_finished=None, finished_missing=False, complete=False, tool_call=None, interrupted=False, audio=None, output=None):
    content = NS(
        input_transcription=(NS(text=transcript) if finished_missing else
            NS(text=transcript, finished=bool(complete) if transcript_finished is None else transcript_finished)) if transcript is not None else None,
        output_transcription=NS(text=output) if output is not None else None,
        turn_complete=complete,
        interrupted=interrupted,
        model_turn=NS(parts=[NS(inline_data=NS(data=audio))]) if audio else None,
    )
    return NS(server_content=content, tool_call=NS(function_calls=tool_call or []), go_away=None)


def call(call_id="call-1"):
    return NS(name=app.VOICE_TOOLS[0]["name"], id=call_id)


class FakeWebSocket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.outgoing = []
        self.closed = None
        self.changed = asyncio.Event()

    async def receive(self):
        return await self.incoming.get()

    async def send_json(self, value):
        self.outgoing.append(value)
        self.changed.set()

    async def send_bytes(self, value):
        self.outgoing.append(value)
        self.changed.set()

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)
        self.changed.set()

    async def wait_for(self, predicate, timeout=1):
        async def wait():
            while not predicate():
                self.changed.clear()
                await self.changed.wait()
        await asyncio.wait_for(wait(), timeout)


class FakeSession:
    def __init__(self):
        self.cycles = asyncio.Queue()
        self.tool_responses = []
        self.audio_inputs = []
        self.client_contents = []
        self.cancelled = False

    async def receive(self):
        try:
            batch = await self.cycles.get()
            if isinstance(batch, BaseException):
                raise batch
            for item in batch:
                yield item
        finally:
            self.cancelled = True

    async def send_tool_response(self, *, function_responses):
        self.tool_responses.extend(function_responses)

    async def send_realtime_input(self, *, audio=None, audio_stream_end=False):
        self.audio_inputs.append(audio if audio is not None else {"audio_stream_end": audio_stream_end})

    async def send_client_content(self, *, turns, turn_complete):
        self.client_contents.append((turns, turn_complete))


class FakeAdapter:
    def __init__(self):
        self.pending_proposal = {"id": "proposal-1", "proposal_hash": "hash-1"}
        self.presented_proposal = None

    def mark_proposal_presented(self, proposal_id, proposal_hash):
        if (proposal_id, proposal_hash) != ("proposal-1", "hash-1") or self.pending_proposal is None:
            return False
        self.presented_proposal = self.pending_proposal
        return True


class FakeTools:
    def __init__(self, *, end_session=False, failure=None):
        self.transcripts = []
        self.end_session = end_session
        self.failure = failure

    async def execute(self, name, *, transcript, language):
        self.transcripts.append((transcript, language))
        if isinstance(self.failure, BaseException):
            raise self.failure
        if self.failure:
            return {"error": self.failure}
        return {
            "response_id": f"response-{len(self.transcripts)}", "case_id": "case-1",
            "reply_text": "Grounded support response", "speech_text": "Grounded support response",
            "proposal": {"id": "proposal-1", "proposal_hash": "hash-1"},
            "end_session": self.end_session,
        }


@pytest.fixture(autouse=True)
def fake_genai_types(monkeypatch):
    monkeypatch.setattr(app, "types", NS(
        Blob=lambda **kwargs: NS(**kwargs),
        FunctionResponse=lambda **kwargs: NS(**kwargs),
        Content=lambda **kwargs: NS(**kwargs),
        Part=lambda **kwargs: NS(**kwargs),
    ))


async def run(ws, session, tools, adapter, *, max_session_seconds=2, end_session_timeout_seconds=5,
              speech_timeout_seconds=45):
    return await app._run_hutch_live_session(
        ws=ws, session=session, tools=tools, adapter=adapter,
        binding_id="binding-1", session_id="voice-1", max_audio_bytes=100,
        max_session_seconds=max_session_seconds, end_session_timeout_seconds=end_session_timeout_seconds,
        speech_timeout_seconds=speech_timeout_seconds,
    )


@pytest.mark.asyncio
async def test_split_final_transcription_is_forwarded_and_receive_cycles_handle_two_turns():
    ws, session, adapter, tools = FakeWebSocket(), FakeSession(), FakeAdapter(), FakeTools()
    task = asyncio.create_task(run(ws, session, tools, adapter))
    await session.cycles.put([event(transcript="My service"), event(transcript="stopped", complete=True), event(tool_call=[call("c1")])])
    await ws.wait_for(lambda: len(tools.transcripts) == 1)
    await session.cycles.put([event(complete=True, audio=b"grounded audio", output="Grounded support response")])
    await ws.wait_for(lambda: b"grounded audio" in ws.outgoing)
    await session.cycles.put([event(transcript="I need"), event(transcript="help again", complete=True), event(tool_call=[call("c2")])])
    await ws.wait_for(lambda: len(tools.transcripts) == 2)
    assert [turn[0] for turn in tools.transcripts] == ["My service stopped", "I need help again"]
    await ws.incoming.put({"type": "websocket.disconnect"})
    assert await task == "disconnected"
    assert len(session.tool_responses) == 2
    assert b"grounded audio" in ws.outgoing
    assert session.cancelled


@pytest.mark.asyncio
async def test_streamed_proposal_never_authorizes_spoken_presentation():
    ws, session, adapter, tools = FakeWebSocket(), FakeSession(), FakeAdapter(), FakeTools()
    task = asyncio.create_task(run(ws, session, tools, adapter))
    await session.cycles.put([event(transcript="Stop renewal", complete=True), event(tool_call=[call()])])
    await ws.wait_for(lambda: any(item.get("type") == "resolve_result" for item in ws.outgoing if isinstance(item, dict)))
    await session.cycles.put([event(complete=True, audio=b"proposal", output="Grounded support response")])
    await ws.wait_for(lambda: b"proposal" in ws.outgoing)
    await ws.incoming.put({"text": '{"type":"playback_complete","response_id":"response-1"}'})
    await ws.wait_for(lambda: any(item.get("type") == "playback_ack" for item in ws.outgoing if isinstance(item, dict)))
    await ws.incoming.put({"text": '{"type":"proposal_presented","response_id":"response-1","proposal_id":"proposal-1","proposal_hash":"hash-1"}'})
    await ws.wait_for(lambda: any(item.get("type") == "proposal_ack" for item in ws.outgoing if isinstance(item, dict)))
    assert ws.outgoing[-1] == {"type": "proposal_ack", "response_id": "response-1", "accepted": False}
    assert adapter.presented_proposal is None
    await session.cycles.put([event(interrupted=True)])
    await ws.wait_for(lambda: any(item.get("type") == "interrupted" for item in ws.outgoing if isinstance(item, dict)))
    assert ws.outgoing[-1] == {"type": "interrupted", "response_id": "response-1"}
    assert adapter.presented_proposal is None
    await session.cycles.put([event(complete=True, audio=b"interrupted tail")])
    await asyncio.sleep(0.01)
    await ws.incoming.put({"text": '{"type":"proposal_presented","response_id":"response-1","proposal_id":"proposal-1","proposal_hash":"hash-1"}'})
    await asyncio.sleep(0)
    assert ws.outgoing[-1] == {"type": "proposal_ack", "response_id": "response-1", "accepted": False}
    await ws.incoming.put({"type": "websocket.disconnect"})
    assert await task == "disconnected"


@pytest.mark.asyncio
async def test_resolve_end_session_waits_for_final_grounded_output_then_closes():
    ws, session, adapter, tools = FakeWebSocket(), FakeSession(), FakeAdapter(), FakeTools(end_session=True)
    task = asyncio.create_task(run(ws, session, tools, adapter))
    await session.cycles.put([event(transcript="That is all", complete=True), event(tool_call=[call()])])
    await ws.wait_for(lambda: any(item.get("type") == "resolve_result" for item in ws.outgoing if isinstance(item, dict)))
    assert ws.closed is None
    await session.cycles.put([event(complete=True, audio=b"final grounded audio", output="Grounded support response")])
    await ws.wait_for(lambda: b"final grounded audio" in ws.outgoing)
    await ws.incoming.put({"text": '{"type":"playback_complete","response_id":"response-1"}'})
    assert await task == "ended"
    assert ws.closed == (1000, "resolve_requested")
    ended_index = next(i for i, item in enumerate(ws.outgoing) if isinstance(item, dict) and item.get("type") == "ended")
    assert ws.outgoing.index(b"final grounded audio") < ended_index
    assert session.cancelled


@pytest.mark.asyncio
async def test_end_session_has_a_bound_when_provider_never_finishes_reply(monkeypatch):
    ws, session, adapter, tools = FakeWebSocket(), FakeSession(), FakeAdapter(), FakeTools(end_session=True)
    task = asyncio.create_task(run(ws, session, tools, adapter, end_session_timeout_seconds=0.03,
                                   speech_timeout_seconds=0.03))
    await session.cycles.put([event(transcript="Goodbye", complete=True), event(tool_call=[call()])])
    assert await task == "ended"
    assert ws.closed == (1000, "resolve_requested")
    assert any(item == {"type": "ended", "reason": "resolve_requested"} for item in ws.outgoing)
    assert session.cancelled


@pytest.mark.asyncio
async def test_socket_disconnect_cancels_provider_receive_and_audio_receive():
    ws, session, adapter, tools = FakeWebSocket(), FakeSession(), FakeAdapter(), FakeTools()
    task = asyncio.create_task(run(ws, session, tools, adapter))
    await asyncio.sleep(0)
    await ws.incoming.put({"type": "websocket.disconnect"})
    assert await task == "disconnected"
    assert session.cancelled


@pytest.mark.asyncio
async def test_session_timeout_closes_and_cleans_up_tasks():
    ws, session, adapter, tools = FakeWebSocket(), FakeSession(), FakeAdapter(), FakeTools()
    assert await run(ws, session, tools, adapter, max_session_seconds=0.02) == "session_limit"
    assert ws.closed == (1000, "session_limit")
    assert session.cancelled


@pytest.mark.asyncio
async def test_provider_receive_failure_cancels_sibling_tasks():
    ws, session, adapter, tools = FakeWebSocket(), FakeSession(), FakeAdapter(), FakeTools()
    task = asyncio.create_task(run(ws, session, tools, adapter))
    await session.cycles.put(RuntimeError("provider receive failed"))
    with pytest.raises(RuntimeError, match="provider receive failed"):
        await task
    assert session.cancelled


@pytest.mark.asyncio
async def test_live_provider_context_exits_when_call_task_is_cancelled():
    class Context:
        def __init__(self):
            self.exited = False

        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            self.exited = True

    context = Context()
    client = NS(aio=NS(live=NS(connect=lambda **kwargs: context)))

    async def hold_context():
        async with app._live_session(client, "model", object()):
            await asyncio.Event().wait()

    task = asyncio.create_task(hold_context())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert context.exited


@pytest.mark.asyncio
async def test_incomplete_transcription_is_never_sent_to_resolve():
    ws, session, adapter, tools = FakeWebSocket(), FakeSession(), FakeAdapter(), FakeTools()
    task = asyncio.create_task(run(ws, session, tools, adapter))
    await session.cycles.put([event(transcript="Unfinalized words", transcript_finished=False, complete=True), event(tool_call=[call()])])
    await asyncio.sleep(0.02)
    assert tools.transcripts == []
    await ws.incoming.put({"type": "websocket.disconnect"})
    assert await task == "disconnected"


@pytest.mark.asyncio
async def test_live_final_transcript_without_finished_uses_session_memory_and_streams_speech():
    ws, session, adapter, tools = FakeWebSocket(), FakeSession(), FakeAdapter(), FakeTools()
    task = asyncio.create_task(run(ws, session, tools, adapter))
    await session.cycles.put([event(transcript="Check my balance", finished_missing=True)])
    await ws.wait_for(lambda: any(isinstance(item, dict) and item.get("type") == "resolve_result" for item in ws.outgoing))
    assert tools.transcripts == [("Check my balance", "en")]
    assert [item["type"] for item in ws.outgoing if isinstance(item, dict)] == [
        "transcript", "resolve_result",
    ]
    assert not any(isinstance(item, bytes) for item in ws.outgoing)
    # Discard the original ungrounded model turn, then give Live the Resolve snapshot.
    await session.cycles.put([event(complete=True, audio=b"ungrounded", output="Unverified words")])
    for _ in range(50):
        if session.client_contents:
            break
        await asyncio.sleep(0.01)
    assert session.client_contents[0][1] is True
    assert 'hutch_resolve_session_memory_snapshot' in session.client_contents[0][0].parts[0].text
    assert "Grounded support response" in session.client_contents[0][0].parts[0].text
    assert b"ungrounded" not in ws.outgoing
    await session.cycles.put([event(audio=b"streamed audio", output="Different wording")])
    await ws.wait_for(lambda: b"streamed audio" in ws.outgoing)
    assert any(isinstance(item, dict) and item.get("type") == "audio_start" for item in ws.outgoing)
    assert not any(isinstance(item, dict) and item.get("type") == "audio_end" for item in ws.outgoing)
    await session.cycles.put([event(complete=True)])
    await ws.wait_for(lambda: any(isinstance(item, dict) and item.get("type") == "audio_end" for item in ws.outgoing))
    await session.cycles.put([event(tool_call=[call("late-model-tool")])])
    for _ in range(50):
        if session.tool_responses:
            break
        await asyncio.sleep(0.01)
    assert len(tools.transcripts) == 1
    assert session.tool_responses[0].response["output"] == {"error": "no_finalized_caller_turn"}
    await ws.incoming.put({"type": "websocket.disconnect"})
    assert await task == "disconnected"


@pytest.mark.asyncio
async def test_browser_end_of_speech_flushes_gemini_audio_once():
    ws, session, adapter, tools = FakeWebSocket(), FakeSession(), FakeAdapter(), FakeTools()
    task = asyncio.create_task(run(ws, session, tools, adapter))
    await ws.incoming.put({"bytes": b"\x01\x00" * 20})
    await ws.incoming.put({"text": '{"type":"input_audio_end"}'})
    await ws.incoming.put({"text": '{"type":"input_audio_end"}'})
    await asyncio.sleep(0.02)
    assert session.audio_inputs[1] == {"audio_stream_end": True}
    await ws.incoming.put({"type": "websocket.disconnect"})
    assert await task == "disconnected"


@pytest.mark.asyncio
async def test_input_transcription_finished_is_authoritative_independent_of_turn_complete():
    ws, session, adapter, tools = FakeWebSocket(), FakeSession(), FakeAdapter(), FakeTools()
    task = asyncio.create_task(run(ws, session, tools, adapter))
    # The model can close its turn before the independent input transcription is final.
    await session.cycles.put([
        event(transcript="My billing", transcript_finished=False, complete=True),
        event(tool_call=[call("deferred-call")]),
    ])
    await asyncio.sleep(0.02)
    assert tools.transcripts == []
    assert session.tool_responses == []

    # Final transcription may arrive later and without a model turn_complete event.
    await session.cycles.put([event(transcript=" looks wrong", transcript_finished=True, complete=False)])
    await ws.wait_for(lambda: len(tools.transcripts) == 1)
    assert tools.transcripts == [("My billing looks wrong", "en")]
    assert len(session.tool_responses) == 1
    await ws.incoming.put({"type": "websocket.disconnect"})
    assert await task == "disconnected"


@pytest.mark.asyncio
async def test_binary_audio_is_forwarded_and_audio_budget_closes_cleanly():
    ws, session, adapter, tools = FakeWebSocket(), FakeSession(), FakeAdapter(), FakeTools()
    task = asyncio.create_task(app._run_hutch_live_session(
        ws=ws, session=session, tools=tools, adapter=adapter,
        binding_id="binding-1", session_id="voice-1", max_audio_bytes=2,
        max_session_seconds=2,
    ))
    await ws.incoming.put({"bytes": b"\x00\x00"})
    await asyncio.sleep(0.01)
    assert len(session.audio_inputs) == 1
    await ws.incoming.put({"bytes": b"\x01\x00"})
    assert await task == "audio_limit"
    assert ws.closed == (1008, "audio_limit_reached")
    assert session.cancelled


@pytest.mark.asyncio
async def test_v2_rejects_malformed_controls_and_early_or_mismatched_proposal_ack():
    assert app._grant_from_protocol("zeptaz-hutch-v1,hutch-grant.token") is None
    assert app._grant_from_protocol("zeptaz-hutch-v2,hutch-grant.token") == ("zeptaz-hutch-v2", "token")
    ws, session, adapter, tools = FakeWebSocket(), FakeSession(), FakeAdapter(), FakeTools()
    task = asyncio.create_task(run(ws, session, tools, adapter))
    await session.cycles.put([event(transcript="Stop renewal", complete=True), event(tool_call=[call()])])
    await ws.wait_for(lambda: any(isinstance(item, dict) and item.get("type") == "resolve_result" for item in ws.outgoing))
    for invalid in ["[]", "null", "{", '{"type":"proposal_presented","proposal_id":"proposal-1","proposal_hash":"hash-1"}']:
        await ws.incoming.put({"text": invalid})
    await ws.wait_for(lambda: sum(isinstance(item, dict) and item.get("code") == "invalid_browser_control" for item in ws.outgoing) == 4)
    await ws.incoming.put({"text": '{"type":"proposal_presented","response_id":"response-1","proposal_id":"proposal-1","proposal_hash":"hash-1"}'})
    await ws.wait_for(lambda: any(isinstance(item, dict) and item.get("type") == "proposal_ack" for item in ws.outgoing))
    assert ws.outgoing[-1]["accepted"] is False
    await session.cycles.put([event(complete=True, audio=b"spoken", output="Grounded support response")])
    await ws.wait_for(lambda: b"spoken" in ws.outgoing)
    await ws.incoming.put({"text": '{"type":"playback_complete","response_id":"different"}'})
    await ws.wait_for(lambda: any(isinstance(item, dict) and item.get("type") == "playback_ack" for item in ws.outgoing))
    assert ws.outgoing[-1]["accepted"] is False
    await ws.incoming.put({"type": "websocket.disconnect"})
    assert await task == "disconnected"
    assert adapter.presented_proposal is None


@pytest.mark.asyncio
async def test_zero_audio_cannot_be_acknowledged_and_later_model_audio_is_dropped():
    ws, session, adapter, tools = FakeWebSocket(), FakeSession(), FakeAdapter(), FakeTools()
    task = asyncio.create_task(run(ws, session, tools, adapter))
    await session.cycles.put([event(transcript="Stop renewal", complete=True), event(tool_call=[call()])])
    await ws.wait_for(lambda: any(isinstance(item, dict) and item.get("type") == "resolve_result" for item in ws.outgoing))
    await session.cycles.put([event(complete=True, output="Grounded support response")])
    await ws.wait_for(lambda: any(isinstance(item, dict) and item.get("code") == "speech_unavailable" for item in ws.outgoing))
    await ws.incoming.put({"text": '{"type":"playback_complete","response_id":"response-1"}'})
    await ws.wait_for(lambda: any(isinstance(item, dict) and item.get("type") == "playback_ack" for item in ws.outgoing))
    assert ws.outgoing[-1]["accepted"] is False
    await session.cycles.put([event(complete=True, audio=b"unsolicited")])
    await asyncio.sleep(0.02)
    assert b"unsolicited" not in ws.outgoing
    await ws.incoming.put({"type": "websocket.disconnect"})
    assert await task == "disconnected"


@pytest.mark.asyncio
async def test_model_audio_streams_without_transcript_comparison_but_proposal_stays_tap_only():
    ws, session, adapter, tools = FakeWebSocket(), FakeSession(), FakeAdapter(), FakeTools()
    task = asyncio.create_task(run(ws, session, tools, adapter))
    await session.cycles.put([event(transcript="Stop renewal", complete=True), event(tool_call=[call()])])
    await ws.wait_for(lambda: any(isinstance(item, dict) and item.get("type") == "resolve_result" for item in ws.outgoing))
    await session.cycles.put([event(audio=b"live pcm", output="Different wording")])
    await ws.wait_for(lambda: b"live pcm" in ws.outgoing)
    assert not any(isinstance(item, dict) and item.get("type") == "audio_end" for item in ws.outgoing)
    await session.cycles.put([event(complete=True)])
    await ws.wait_for(lambda: any(isinstance(item, dict) and item.get("type") == "audio_end" for item in ws.outgoing))
    await ws.incoming.put({"text": '{"type":"playback_complete","response_id":"response-1"}'})
    await ws.incoming.put({"text": '{"type":"proposal_presented","response_id":"response-1","proposal_id":"proposal-1","proposal_hash":"hash-1"}'})
    await ws.wait_for(lambda: any(isinstance(item, dict) and item.get("type") == "proposal_ack" for item in ws.outgoing))
    assert ws.outgoing[-1]["accepted"] is False
    await ws.incoming.put({"type": "websocket.disconnect"})
    assert await task == "disconnected"


@pytest.mark.asyncio
async def test_tool_reply_carries_resolve_session_memory_without_audio_buffering():
    ws, session, adapter, tools = FakeWebSocket(), FakeSession(), FakeAdapter(), FakeTools()
    task = asyncio.create_task(run(ws, session, tools, adapter))
    await session.cycles.put([event(transcript="What happened to my bill?", complete=True), event(tool_call=[call()])])
    await ws.wait_for(lambda: any(item.get("type") == "resolve_result" for item in ws.outgoing if isinstance(item, dict)))
    for _ in range(50):
        if session.tool_responses:
            break
        await asyncio.sleep(0.01)
    assert session.tool_responses[0].response["session_memory_snapshot"]["latest_resolve_result"]["reply_text"] == "Grounded support response"
    await session.cycles.put([event(audio=b"first pcm0")])
    await ws.wait_for(lambda: b"first pcm0" in ws.outgoing)
    assert not any(isinstance(item, dict) and item.get("type") == "audio_end" for item in ws.outgoing)
    await session.cycles.put([event(complete=True)])
    await ws.wait_for(lambda: any(isinstance(item, dict) and item.get("type") == "audio_end" for item in ws.outgoing))
    await ws.incoming.put({"type": "websocket.disconnect"})
    assert await task == "disconnected"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["resolve_unavailable", RuntimeError("private detail")])
async def test_resolve_tool_failure_emits_typed_error_and_never_forwards_model_claim(failure):
    ws, session, adapter, tools = FakeWebSocket(), FakeSession(), FakeAdapter(), FakeTools(failure=failure)
    task = asyncio.create_task(run(ws, session, tools, adapter))
    await session.cycles.put([event(transcript="Check my refund", complete=True), event(tool_call=[call()])])
    await ws.wait_for(lambda: any(item.get("type") == "error" and item.get("code") == "resolve_tool_failed"
                                  for item in ws.outgoing if isinstance(item, dict)))
    # A model response following the tool error has no active Resolve reply and is discarded.
    await session.cycles.put([event(complete=True, audio=b"fabricated claim", output="Your refund was approved.")])
    await asyncio.sleep(0.02)
    errors = [item for item in ws.outgoing if isinstance(item, dict) and item.get("type") == "error"]
    assert errors == [{
        "type": "error", "code": "resolve_tool_failed",
        "message": "I couldn’t complete that request. Please continue by text or try again.",
    }]
    assert b"fabricated claim" not in ws.outgoing
    assert not any(isinstance(item, dict) and item.get("type") == "resolve_result" for item in ws.outgoing)
    await ws.incoming.put({"type": "websocket.disconnect"})
    assert await task == "disconnected"


@pytest.mark.asyncio
async def test_coalesced_completion_and_tool_call_cannot_end_before_new_reply():
    ws, session, adapter, tools = FakeWebSocket(), FakeSession(), FakeAdapter(), FakeTools(end_session=True)
    task = asyncio.create_task(run(ws, session, tools, adapter))
    await session.cycles.put([event(transcript="Goodbye", complete=True, tool_call=[call()])])
    await ws.wait_for(lambda: any(isinstance(item, dict) and item.get("type") == "resolve_result" for item in ws.outgoing))
    assert ws.closed is None
    await session.cycles.put([event(complete=True, audio=b"final pcm0", output="Grounded support response")])
    await ws.wait_for(lambda: b"final pcm0" in ws.outgoing)
    assert ws.closed is None
    await ws.incoming.put({"text": '{"type":"playback_complete","response_id":"response-1"}'})
    assert await task == "ended"
    assert ws.outgoing.index(b"final pcm0") < next(i for i, item in enumerate(ws.outgoing) if isinstance(item, dict) and item.get("type") == "ended")


@pytest.mark.asyncio
async def test_interruption_stops_later_model_audio():
    ws, session, adapter, tools = FakeWebSocket(), FakeSession(), FakeAdapter(), FakeTools()
    task = asyncio.create_task(run(ws, session, tools, adapter))
    await session.cycles.put([event(transcript="Stop renewal", complete=True), event(tool_call=[call()])])
    await ws.wait_for(lambda: any(isinstance(item, dict) and item.get("type") == "resolve_result" for item in ws.outgoing))
    await session.cycles.put([event(audio=b"unverified")])
    await session.cycles.put([event(interrupted=True), event(complete=True, output="Grounded support response")])
    await ws.wait_for(lambda: any(isinstance(item, dict) and item.get("type") == "interrupted" for item in ws.outgoing))
    assert b"unverified" in ws.outgoing
    await ws.incoming.put({"type": "websocket.disconnect"})
    assert await task == "disconnected"


@pytest.mark.asyncio
async def test_oversized_single_output_frame_is_rejected_without_playback():
    ws, session, adapter, tools = FakeWebSocket(), FakeSession(), FakeAdapter(), FakeTools()
    task = asyncio.create_task(run(ws, session, tools, adapter))
    await session.cycles.put([event(transcript="Stop renewal", complete=True), event(tool_call=[call()])])
    await ws.wait_for(lambda: any(isinstance(item, dict) and item.get("type") == "resolve_result" for item in ws.outgoing))
    oversized = b"\x00\x00" * (60 * 24000 + 1)
    await session.cycles.put([event(complete=True, audio=oversized, output="Grounded support response")])
    await ws.wait_for(lambda: any(isinstance(item, dict) and item.get("code") == "speech_unavailable" for item in ws.outgoing))
    assert oversized not in ws.outgoing
    assert not any(isinstance(item, dict) and item.get("type") == "audio_start" for item in ws.outgoing)
    await ws.incoming.put({"type": "websocket.disconnect"})
    assert await task == "disconnected"
