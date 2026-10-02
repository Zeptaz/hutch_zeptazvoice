import asyncio
from types import SimpleNamespace as NS

import pytest

import app


def event(*, transcript=None, transcript_finished=None, complete=False, tool_call=None, interrupted=False, audio=None):
    content = NS(
        input_transcription=NS(text=transcript, finished=bool(complete) if transcript_finished is None else transcript_finished) if transcript is not None else None,
        output_transcription=None,
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

    async def send_realtime_input(self, *, audio):
        self.audio_inputs.append(audio)


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
    def __init__(self, *, end_session=False):
        self.transcripts = []
        self.end_session = end_session

    async def execute(self, name, *, transcript, language):
        self.transcripts.append((transcript, language))
        return {
            "response_id": f"response-{len(self.transcripts)}", "case_id": "case-1",
            "reply_text": "Grounded support response", "proposal": {"id": "proposal-1", "proposal_hash": "hash-1"},
            "end_session": self.end_session,
        }


@pytest.fixture(autouse=True)
def fake_genai_types(monkeypatch):
    monkeypatch.setattr(app, "types", NS(
        Blob=lambda **kwargs: NS(**kwargs),
        FunctionResponse=lambda **kwargs: NS(**kwargs),
    ))


async def run(ws, session, tools, adapter, *, max_session_seconds=2, end_session_timeout_seconds=5):
    return await app._run_hutch_live_session(
        ws=ws, session=session, tools=tools, adapter=adapter,
        binding_id="binding-1", session_id="voice-1", max_audio_bytes=100,
        max_session_seconds=max_session_seconds, end_session_timeout_seconds=end_session_timeout_seconds,
    )


@pytest.mark.asyncio
async def test_split_final_transcription_is_forwarded_and_receive_cycles_handle_two_turns():
    ws, session, adapter, tools = FakeWebSocket(), FakeSession(), FakeAdapter(), FakeTools()
    task = asyncio.create_task(run(ws, session, tools, adapter))
    await session.cycles.put([event(transcript="My service"), event(transcript="stopped", complete=True), event(tool_call=[call("c1")])])
    await ws.wait_for(lambda: len(tools.transcripts) == 1)
    await session.cycles.put([event(complete=True, audio=b"grounded audio")])
    await session.cycles.put([event(transcript="I need"), event(transcript="help again", complete=True), event(tool_call=[call("c2")])])
    await ws.wait_for(lambda: len(tools.transcripts) == 2)
    assert [turn[0] for turn in tools.transcripts] == ["My service stopped", "I need help again"]
    await ws.incoming.put({"type": "websocket.disconnect"})
    assert await task == "disconnected"
    assert len(session.tool_responses) == 2
    assert b"grounded audio" in ws.outgoing
    assert session.cancelled


@pytest.mark.asyncio
async def test_proposal_ack_lifecycle_and_interruption_revoke_presentation_eligibility():
    ws, session, adapter, tools = FakeWebSocket(), FakeSession(), FakeAdapter(), FakeTools()
    task = asyncio.create_task(run(ws, session, tools, adapter))
    await session.cycles.put([event(transcript="Stop renewal", complete=True), event(tool_call=[call()])])
    await ws.wait_for(lambda: any(item.get("type") == "resolve_result" for item in ws.outgoing if isinstance(item, dict)))
    await session.cycles.put([event(complete=True, audio=b"proposal")])
    await ws.wait_for(lambda: b"proposal" in ws.outgoing)
    await ws.incoming.put({"text": '{"type":"proposal_presented","proposal_id":"proposal-1","proposal_hash":"hash-1"}'})
    await ws.wait_for(lambda: any(item.get("type") == "proposal_ack" for item in ws.outgoing if isinstance(item, dict)))
    assert ws.outgoing[-1] == {"type": "proposal_ack", "accepted": True}
    assert adapter.presented_proposal is not None
    await session.cycles.put([event(interrupted=True)])
    await ws.wait_for(lambda: any(item.get("type") == "interrupted" for item in ws.outgoing if isinstance(item, dict)))
    assert ws.outgoing[-1] == {"type": "interrupted", "response_id": "response-1"}
    assert adapter.presented_proposal is None
    await session.cycles.put([event(complete=True, audio=b"interrupted tail")])
    await asyncio.sleep(0.01)
    await ws.incoming.put({"text": '{"type":"proposal_presented","proposal_id":"proposal-1","proposal_hash":"hash-1"}'})
    await asyncio.sleep(0)
    assert ws.outgoing[-1] == {"type": "proposal_ack", "accepted": False}
    await ws.incoming.put({"type": "websocket.disconnect"})
    assert await task == "disconnected"


@pytest.mark.asyncio
async def test_resolve_end_session_waits_for_final_grounded_output_then_closes():
    ws, session, adapter, tools = FakeWebSocket(), FakeSession(), FakeAdapter(), FakeTools(end_session=True)
    task = asyncio.create_task(run(ws, session, tools, adapter))
    await session.cycles.put([event(transcript="That is all", complete=True), event(tool_call=[call()])])
    await ws.wait_for(lambda: any(item.get("type") == "resolve_result" for item in ws.outgoing if isinstance(item, dict)))
    assert ws.closed is None
    await session.cycles.put([event(complete=True, audio=b"final grounded audio")])
    assert await task == "ended"
    assert ws.closed == (1000, "resolve_requested")
    ended_index = next(i for i, item in enumerate(ws.outgoing) if isinstance(item, dict) and item.get("type") == "ended")
    assert ws.outgoing.index(b"final grounded audio") < ended_index
    assert session.cancelled


@pytest.mark.asyncio
async def test_end_session_has_a_bound_when_provider_never_finishes_reply(monkeypatch):
    ws, session, adapter, tools = FakeWebSocket(), FakeSession(), FakeAdapter(), FakeTools(end_session=True)
    task = asyncio.create_task(run(ws, session, tools, adapter, end_session_timeout_seconds=0.03))
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
