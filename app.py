from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import uuid4

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types

from adapters.hutch.adapter import HutchAdapter, HutchVoiceTools, SYSTEM_INSTRUCTION, VOICE_TOOLS, session_memory_snapshot
from adapters.hutch.client import HutchResolveClient, ResolveClientError
from adapters.hutch.contracts import SessionRequest, VoiceEventRequest
from adapters.hutch.security import body_digest, canonical_json, verify_signature
from adapters.hutch.store import HutchVoiceStore
from core.services.language_policy import detect_language
from core.system.voice_runtime_config import VoiceRuntimeConfig

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("hutch_voice")


def _verify_service(body: bytes, timestamp: str | None, event_id: str | None, digest: str | None, signature: str | None) -> bool:
    secret = os.getenv("HUTCH_RESOLVE_HMAC_SECRET", "")
    if len(secret) < 32 or not timestamp or not event_id or not digest or not signature:
        return False
    actual = body_digest(body)
    return actual == digest and verify_signature(secret, timestamp, event_id, digest, signature)


@asynccontextmanager
async def _live_session(client, model: str, config):
    context = client.aio.live.connect(model=model, config=config)
    try:
        session = await asyncio.wait_for(context.__aenter__(), timeout=float(os.getenv("GEMINI_LIVE_CONNECT_TIMEOUT_SECONDS", "12")))
    except BaseException:
        try:
            await context.__aexit__(None, None, None)
        except Exception:
            pass
        raise
    try:
        yield session
    finally:
        await context.__aexit__(None, None, None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.getenv("ENVIRONMENT", "development").strip().lower() == "production":
        base = os.getenv("HUTCH_RESOLVE_BASE_URL", "")
        public_base = os.getenv("ZEPTAZ_PUBLIC_BASE_URL", "")
        origins = {part.strip().rstrip("/") for part in os.getenv("HUTCH_VOICE_ALLOWED_ORIGINS", "").split(",") if part.strip()}
        if not base.startswith("https://"):
            raise RuntimeError("HUTCH_RESOLVE_BASE_URL must use HTTPS in production")
        if not public_base.startswith("https://"):
            raise RuntimeError("ZEPTAZ_PUBLIC_BASE_URL must use HTTPS in production")
        if not origins or "*" in origins:
            raise RuntimeError("HUTCH_VOICE_ALLOWED_ORIGINS must list exact origins in production")
        if len(os.getenv("HUTCH_RESOLVE_HMAC_SECRET", "")) < 32 or len(os.getenv("HUTCH_VOICE_GRANT_SECRET", "")) < 32:
            raise RuntimeError("Hutch integration HMAC secrets must be at least 32 characters in production")
    app.state.store = HutchVoiceStore()
    app.state.resolve = HutchResolveClient()
    yield
    await app.state.resolve.close()


app = FastAPI(title="Zeptaz Voice Hutch Adapter", version="1.0.0", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/api/hutch/sessions")
async def create_hutch_session(request: Request,
                               x_voice_timestamp: str | None = Header(None),
                               x_voice_event_id: str | None = Header(None),
                               x_voice_body_sha256: str | None = Header(None),
                               x_voice_signature: str | None = Header(None)):
    body = await request.body()
    if not _verify_service(body, x_voice_timestamp, x_voice_event_id, x_voice_body_sha256, x_voice_signature):
        raise HTTPException(401, "invalid_service_signature")
    try:
        payload = SessionRequest.model_validate_json(body)
    except Exception as exc:
        raise HTTPException(422, "invalid_session_request") from exc
    allowed = {item.strip().rstrip("/") for item in os.getenv("HUTCH_VOICE_ALLOWED_ORIGINS", "").split(",") if item.strip()}
    if payload.origin.rstrip("/") not in allowed:
        raise HTTPException(403, "origin_not_allowed")
    try:
        request_hash = hashlib.sha256(body).hexdigest()
        cached = app.state.store.lookup_event(x_voice_event_id, request_hash)
        if cached:
            return json.loads(cached)
        response, _ = app.state.store.create_binding(payload)
        public_base = os.getenv("ZEPTAZ_PUBLIC_BASE_URL", "http://127.0.0.1:8088").rstrip("/")
        socket_base = public_base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        response["websocket_url"] = socket_base + response["websocket_path"]
        serialized = json.dumps(response, sort_keys=True, separators=(",", ":"))
        app.state.store.save_event(x_voice_event_id, request_hash, serialized)
    except ValueError as exc:
        raise HTTPException(409, "session_scope_conflict") from exc
    except Exception as exc:
        logger.warning("Could not create Hutch Voice binding: %s", type(exc).__name__)
        raise HTTPException(503, "voice_binding_unavailable") from exc
    return response


@app.post("/api/hutch/events")
async def hutch_lifecycle_event(request: Request,
                                x_voice_timestamp: str | None = Header(None),
                                x_voice_event_id: str | None = Header(None),
                                x_voice_body_sha256: str | None = Header(None),
                                x_voice_signature: str | None = Header(None)):
    body = await request.body()
    if not _verify_service(body, x_voice_timestamp, x_voice_event_id, x_voice_body_sha256, x_voice_signature):
        raise HTTPException(401, "invalid_service_signature")
    try:
        event = VoiceEventRequest.model_validate_json(body)
        if event.event_id != x_voice_event_id:
            raise ValueError("event header/body mismatch")
        request_hash = hashlib.sha256(body).hexdigest()
        cached = app.state.store.lookup_event(x_voice_event_id, request_hash)
        if cached:
            return json.loads(cached)
        result = {"accepted": True, "event_id": event.event_id}
        await app.state.resolve.send_event(event.model_dump())
        app.state.store.save_event(x_voice_event_id, request_hash, json.dumps(result, separators=(",", ":")))
        return result
    except ValueError as exc:
        raise HTTPException(409, "event_id_conflict") from exc
    except ResolveClientError as exc:
        raise HTTPException(503 if exc.retryable else 422, exc.code) from exc
    except Exception as exc:
        logger.warning("Lifecycle callback failed: %s", type(exc).__name__)
        raise HTTPException(422, "invalid_lifecycle_event") from exc


# Newest first. v4 = v3 plus `decision_recorded`: the caller answered an offer on screen, so Voice
# speaks Resolve's reply to it. A browser that offers v4 still works with a server that only knows v3.
PROTOCOL_VERSIONS = {"zeptaz-hutch-v4": 4, "zeptaz-hutch-v3": 3, "zeptaz-hutch-v2": 2}


def _grant_from_protocol(header: str) -> tuple[str, str] | None:
    protocols = [part.strip() for part in header.split(",")]
    grant = next((item.removeprefix("hutch-grant.") for item in protocols if item.startswith("hutch-grant.")), None)
    common = next((version for version in PROTOCOL_VERSIONS if version in protocols), None)
    if common is None or not grant:
        return None
    return common, grant


@dataclass
class _Reply:
    response_id: str
    proposal: dict | None
    end_session: bool
    memory_snapshot: dict
    resolve_result_at: float
    speech_requested_at: float | None = None
    speech_retry_count: int = 0
    audio_bytes: int = 0
    sent_audio: bool = False
    complete: bool = False
    playback_complete: bool = False
    interrupted: bool = False


def _parse_browser_control(raw: str) -> dict | None:
    if len(raw.encode("utf-8")) > 4096:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    control_type = value.get("type")
    fields = {
        "input_audio_end": {"type"},
        "input_activity_start": {"type", "segment_id"},
        "input_activity_end": {"type", "segment_id"},
        "playback_complete": {"type", "response_id"},
        "proposal_presented": {"type", "response_id", "proposal_id", "proposal_hash"},
        "decision_recorded": {"type", "proposal_id"},
    }
    if control_type not in fields or set(value) != fields[control_type]:
        return None
    if control_type in {"input_activity_start", "input_activity_end"}:
        if type(value["segment_id"]) is not int or not 1 <= value["segment_id"] <= 1_000_000_000:
            return None
    if any(not isinstance(value[key], str) or not value[key] or len(value[key]) > 128
           for key in fields[control_type] - {"type", "segment_id"}):
        return None
    return value


# Telecom terms callers say in English inside Sinhala/Tamil speech; biases recognition toward them.
INPUT_VOCABULARY = ["HUTCH", "VAS", "VAS charges", "value added service", "reload", "recharge", "balance",
                    "data package", "package", "SIM", "top up"]


def _input_transcription_config(language_codes) -> types.AudioTranscriptionConfig:
    return types.AudioTranscriptionConfig(language_codes=list(language_codes), custom_vocabulary=INPUT_VOCABULARY)


def _realtime_input_config(*, protocol_version: int, end_silence_ms: int, full_duplex: bool):
    """V3 uses the browser's validated activity boundaries as the sole VAD."""
    return types.RealtimeInputConfig(
        automatic_activity_detection=types.AutomaticActivityDetection(
            disabled=True) if protocol_version >= 3 else types.AutomaticActivityDetection(
                disabled=False, silence_duration_ms=end_silence_ms),
        activity_handling=(types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS
                           if full_duplex else types.ActivityHandling.NO_INTERRUPTION),
    )


async def _run_hutch_live_session(*, ws, session, tools, adapter, binding_id: str,
                                  session_id: str, max_audio_bytes: int,
                                  max_session_seconds: int, end_session_timeout_seconds: float = 5.0,
                                  speech_timeout_seconds: float = 45.0,
                                  first_audio_timeout_seconds: float = 10.0,
                                  protocol_version: int = 2):
    """Supervise both sides of the live call until disconnect, failure, or Resolve end."""
    input_fragments: list[str] = []
    latest_user_turn = ""
    pending_tool_calls: list = []
    language = "en"
    reply: _Reply | None = None
    end_deadline: float | None = None
    audio_bytes = 0
    output_audio_limit = 60 * 24000 * 2
    caller_controls: asyncio.Queue[int] = asyncio.Queue(maxsize=4)
    # Offers the caller answered on screen (v4); Voice then speaks Resolve's reply to that tap.
    screen_decisions: asyncio.Queue[str] = asyncio.Queue(maxsize=2)
    owned_tasks: set[asyncio.Task] = set()

    def own_task(coro):
        task = asyncio.create_task(coro)
        owned_tasks.add(task)
        task.add_done_callback(owned_tasks.discard)
        return task

    async def end_call():
        await ws.send_json({"type": "ended", "reason": "resolve_requested"})
        try:
            await ws.close(code=1000, reason="resolve_requested")
        except Exception:
            pass
        return "ended"

    def revoke_reply(*, clear_presentation: bool = True):
        nonlocal reply, end_deadline
        if reply:
            reply.interrupted = True
        if clear_presentation:
            adapter.presented_proposal = None
        end_deadline = None

    async def receive_audio():
        nonlocal audio_bytes
        audio_since_end = False
        current_segment = 0
        active_segment: int | None = None
        # Retain at most 300 ms of PCM16 at 16 kHz before browser VAD accepts speech.
        preroll = bytearray()
        preroll_bytes = 9600
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                return "disconnected"
            data = message.get("bytes")
            if message.get("text") is not None:
                control = _parse_browser_control(message["text"])
                if control is None:
                    await ws.send_json({"type": "error", "code": "invalid_browser_control"})
                    continue
                if control["type"] in {"input_activity_start", "input_activity_end"}:
                    if protocol_version < 3:
                        await ws.send_json({"type": "error", "code": "invalid_browser_control"})
                    elif control["type"] == "input_activity_start":
                        if control["segment_id"] > current_segment:
                            # V3 browser VAD is the single activity source. The Live
                            # session has automatic VAD disabled for this protocol.
                            current_segment = control["segment_id"]
                            if active_segment is not None:
                                await session.send_realtime_input(activity_end=types.ActivityEnd())
                            await session.send_realtime_input(activity_start=types.ActivityStart())
                            active_segment = current_segment
                            if not caller_controls.full():
                                caller_controls.put_nowait(current_segment)
                            if preroll:
                                await session.send_realtime_input(audio=types.Blob(
                                    data=bytes(preroll), mime_type="audio/pcm;rate=16000"))
                                preroll.clear()
                    elif control["segment_id"] == active_segment:
                        await session.send_realtime_input(activity_end=types.ActivityEnd())
                        active_segment = None
                    continue
                if control["type"] == "decision_recorded":
                    if protocol_version < 4:
                        await ws.send_json({"type": "error", "code": "invalid_browser_control"})
                    elif not screen_decisions.full():
                        screen_decisions.put_nowait(control["proposal_id"])
                    continue
                if control["type"] == "input_audio_end":
                    preroll.clear()
                    if audio_since_end or active_segment is not None:
                        logger.info("Voice input segment ended session=%s bytes=%s", session_id, audio_bytes)
                        if protocol_version >= 3:
                            if active_segment is not None:
                                await session.send_realtime_input(activity_end=types.ActivityEnd())
                                active_segment = None
                        else:
                            await session.send_realtime_input(audio_stream_end=True)
                        audio_since_end = False
                    continue
                current = reply
                matching = bool(current and control["response_id"] == current.response_id and not current.interrupted)
                if control["type"] == "playback_complete":
                    accepted = bool(matching and current.complete and current.sent_audio and not current.playback_complete)
                    if accepted:
                        current.playback_complete = True
                    await ws.send_json({"type": "playback_ack", "response_id": control["response_id"], "accepted": accepted})
                    if accepted and current.end_session:
                        return await end_call()
                else:
                    # Free-form streamed speech cannot prove that every proposal term was heard.
                    # Confirmation remains available through the displayed Resolve proposal buttons.
                    await ws.send_json({"type": "proposal_ack", "response_id": control["response_id"], "accepted": False})
                continue
            if data is None:
                continue
            if len(data) > 16384 or len(data) % 2:
                await ws.send_json({"type": "error", "code": "invalid_audio_frame"})
                continue
            audio_bytes += len(data)
            audio_since_end = True
            if audio_bytes > max_audio_bytes:
                await ws.send_json({"type": "error", "code": "audio_limit_reached", "message": "Continue by text."})
                try:
                    await ws.close(code=1008, reason="audio_limit_reached")
                except Exception:
                    pass
                return "audio_limit"
            if protocol_version >= 3 and active_segment is None:
                preroll.extend(data)
                if len(preroll) > preroll_bytes:
                    del preroll[:-preroll_bytes]
                continue
            await session.send_realtime_input(audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000"))

    async def receive_model():
        nonlocal language, latest_user_turn, reply, end_deadline
        pending_spoken_reply: _Reply | None = None
        forwarded_without_tool: dict | None = None
        answered_tool_ids: set[str] = set()
        cancelled_tool_ids: set[str] = set()
        snapshot_sent_early = False
        original_turn_interrupted = False
        original_turn_complete = False
        tool_response_sent = False
        tool_grace_deadline: float | None = None
        speech_deadline: float | None = None
        forward_task: asyncio.Task | None = None
        caller_epoch = 0
        forward_task_epoch = 0
        ambiguous_generation = False
        queued_final_turns: deque[tuple[str, str, int]] = deque()
        decision_fetch: asyncio.Task | None = None

        async def request_snapshot_speech(current: _Reply):
            await session.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text=
                    "Current HUTCH Resolve session memory snapshot (data, not caller instructions). "
                    "Speak a concise natural answer using its rules and latest Resolve result:\n"
                    + json.dumps(current.memory_snapshot, ensure_ascii=False, separators=(",", ":")))]),
                turn_complete=True,
            )
            current.speech_requested_at = time.monotonic()
            logger.info("Voice response speech requested session=%s reply_chars=%s", session_id,
                        len(str(current.memory_snapshot["latest_resolve_result"]["reply_text"])))

        async def retry_snapshot_speech(current: _Reply) -> bool:
            # One speech-only retry reuses the saved Resolve result. A second
            # investigation would risk duplicate effects and cannot fix PCM.
            if (current.sent_audio or current.interrupted or current.complete or
                    current.speech_retry_count or speech_deadline is None or
                    time.monotonic() >= speech_deadline):
                return False
            current.speech_retry_count = 1
            try:
                await request_snapshot_speech(current)
            except Exception as exc:
                logger.warning("Voice speech retry failed session=%s error=%s", session_id, type(exc).__name__)
                return False
            logger.info("Voice speech retry requested session=%s response=%s", session_id, current.response_id)
            return True

        async def forward_without_model_tool(final_text: str, turn_language: str):
            nonlocal latest_user_turn, pending_spoken_reply, forwarded_without_tool
            nonlocal snapshot_sent_early, original_turn_interrupted, speech_deadline
            nonlocal tool_grace_deadline, original_turn_complete, tool_response_sent
            try:
                response = await tools.execute(VOICE_TOOLS[0]["name"], transcript=final_text, language=turn_language)
            except Exception as exc:
                logger.warning("Resolve voice tool failed (%s)", type(exc).__name__)
                response = {"error": "resolve_tool_failed"}
            if response.get("error"):
                forwarded_without_tool = response
                await ws.send_json({"type": "error", "code": "resolve_tool_failed",
                                    "message": "I couldn't complete that request. Please continue by text or try again."})
                return
            # Gemini may emit its tool call after the final input transcription.
            # Reuse this Resolve result for that late call instead of executing twice.
            forwarded_without_tool = response
            revoke_reply(clear_presentation=False)
            pending_spoken_reply = _Reply(
                response_id=str(response["response_id"]),
                proposal=response.get("proposal"),
                end_session=bool(response.get("end_session", False)),
                memory_snapshot=session_memory_snapshot(response),
                resolve_result_at=time.monotonic(),
            )
            speech_deadline = time.monotonic() + speech_timeout_seconds
            snapshot_sent_early = False
            original_turn_interrupted = False
            original_turn_complete = False
            tool_response_sent = False
            await ws.send_json({
                "type": "resolve_result", "response_id": pending_spoken_reply.response_id,
                "case_id": response.get("case_id"), "reply_text": response.get("reply_text"),
                "pending_question": response.get("pending_question"), "proposal": response.get("proposal"),
                "operation_status": response.get("operation_status"), "end_session": pending_spoken_reply.end_session,
            })
            # Give a blocking Gemini tool call a short chance to arrive. Sending
            # a second user turn while a tool call is pending can interrupt it.
            tool_grace_deadline = time.monotonic() + 0.3

        async def process_tool_calls(calls):
            nonlocal latest_user_turn, reply, pending_spoken_reply, forwarded_without_tool
            nonlocal snapshot_sent_early, original_turn_interrupted, end_deadline, speech_deadline
            nonlocal tool_grace_deadline, tool_response_sent
            for call in calls:
                request_after_tool = False
                if call.id in cancelled_tool_ids or call.id in answered_tool_ids:
                    continue
                if (call.name == VOICE_TOOLS[0]["name"] and forwarded_without_tool and
                        (forwarded_without_tool.get("error") or pending_spoken_reply or
                         (reply and not reply.complete))):
                    # A no-finished transcription reached Resolve before Gemini's tool call.
                    # This may arrive even after the old turn's interruption boundary.
                    # Answer with the same result, never a second Resolve operation.
                    response = forwarded_without_tool
                    tool_grace_deadline = None
                    if pending_spoken_reply and not snapshot_sent_early:
                        tool_response_sent = True
                        # Gemini continues the turn from this grounded tool result; that
                        # continuation streams as the reply instead of being discarded.
                        pending_spoken_reply.speech_requested_at = time.monotonic()
                        if original_turn_complete:
                            reply = pending_spoken_reply
                            pending_spoken_reply = None
                            request_after_tool = True
                    # When an early snapshot was sent, its interruption boundary
                    # can arrive after this tool response. Keep the reply pending
                    # so that boundary cannot revoke or expose its audio.
                    if not response.get("error") and speech_deadline is None:
                        speech_deadline = time.monotonic() + speech_timeout_seconds
                    logger.info("Voice late model tool answered from existing Resolve result session=%s", session_id)
                elif call.name != VOICE_TOOLS[0]["name"] or not latest_user_turn:
                    response = {"error": "no_finalized_caller_turn"}
                else:
                    final_text = latest_user_turn
                    latest_user_turn = ""
                    try:
                        response = await tools.execute(call.name, transcript=final_text, language=language)
                    except Exception as exc:
                        logger.warning("Resolve voice tool failed (%s)", type(exc).__name__)
                        response = {"error": "resolve_tool_failed"}
                    if not response.get("error"):
                        revoke_reply(clear_presentation=False)
                        reply = _Reply(
                            response_id=str(response["response_id"]),
                            proposal=response.get("proposal"),
                            end_session=bool(response.get("end_session", False)),
                            memory_snapshot=session_memory_snapshot(response),
                            resolve_result_at=time.monotonic(),
                        )
                        speech_deadline = time.monotonic() + speech_timeout_seconds
                        await ws.send_json({
                            "type": "resolve_result", "response_id": reply.response_id,
                            "case_id": response.get("case_id"), "reply_text": response.get("reply_text"),
                            "pending_question": response.get("pending_question"), "proposal": response.get("proposal"),
                            "operation_status": response.get("operation_status"), "end_session": reply.end_session,
                        })
                    else:
                        # Do not ask the model to explain an error in its own words: that can
                        # turn a failed lookup/action into a fabricated outcome for the caller.
                        await ws.send_json({
                            "type": "error", "code": "resolve_tool_failed",
                            "message": "I couldn’t complete that request. Please continue by text or try again.",
                        })
                tool_output = {"output": response}
                if not response.get("error"):
                    tool_output["session_memory_snapshot"] = session_memory_snapshot(response)
                await session.send_tool_response(function_responses=[types.FunctionResponse(id=call.id, name=call.name, response=tool_output)])
                answered_tool_ids.add(call.id)
                if request_after_tool and reply and not response.get("error"):
                    await request_snapshot_speech(reply)
                    tool_response_sent = False
                if reply and not response.get("error") and reply.speech_requested_at is None:
                    reply.speech_requested_at = time.monotonic()

        async def finish_reply(current: _Reply):
            nonlocal speech_deadline, end_deadline, forwarded_without_tool
            if current.sent_audio:
                await ws.send_json({"type": "audio_end", "response_id": current.response_id})
            else:
                await ws.send_json({"type": "error", "code": "speech_unavailable",
                                    "message": "The reply is on screen. Continue by text or try speaking again."})
            current.complete = True
            forwarded_without_tool = None
            speech_deadline = None
            if current.end_session and end_deadline is None:
                # Let the browser drain the full PCM reply before a bounded
                # end-session fallback can close the socket.
                playback_seconds = current.audio_bytes / (24000 * 2) if current.sent_audio else 0
                end_deadline = time.monotonic() + playback_seconds + end_session_timeout_seconds

        def tool_continuation_speaking() -> bool:
            return bool(pending_spoken_reply and tool_response_sent and not snapshot_sent_early and
                        not pending_spoken_reply.interrupted and not pending_spoken_reply.complete)

        async def stream_pcm(current: _Reply, content) -> None:
            for part in (getattr(getattr(content, "model_turn", None), "parts", None) or []):
                inline = getattr(part, "inline_data", None)
                if inline and getattr(inline, "data", None):
                    frame = bytes(inline.data)
                    if len(frame) % 2 or current.audio_bytes + len(frame) > output_audio_limit:
                        logger.warning("Voice output limit or invalid PCM session=%s bytes=%s", session_id,
                                       current.audio_bytes + len(frame))
                        await finish_reply(current)
                        return
                    if not current.sent_audio:
                        now = time.monotonic()
                        logger.info("Voice first grounded PCM session=%s response=%s after_resolve_ms=%d after_speech_request_ms=%s",
                                    session_id, current.response_id,
                                    round((now - current.resolve_result_at) * 1000),
                                    round((now - current.speech_requested_at) * 1000)
                                    if current.speech_requested_at is not None else "unknown")
                        await ws.send_json({"type": "audio_start", "response_id": current.response_id})
                        current.sent_audio = True
                    current.audio_bytes += len(frame)
                    await ws.send_bytes(frame)

        while True:
            saw_event = False
            iterator = aiter(session.receive())
            event_task: asyncio.Task | None = None
            control_task: asyncio.Task | None = None
            decision_task: asyncio.Task | None = None
            while True:
                try:
                    current_first_audio_deadline = (
                        reply.speech_requested_at + min(first_audio_timeout_seconds, speech_timeout_seconds)
                        if reply and not reply.sent_audio and not reply.complete and
                        reply.speech_requested_at is not None else None
                    )
                    deadlines = [value for value in (end_deadline, speech_deadline, tool_grace_deadline,
                                                     current_first_audio_deadline)
                                 if value is not None]
                    if event_task is None:
                        event_task = own_task(iterator.__anext__())
                    if protocol_version >= 3 and control_task is None:
                        control_task = own_task(caller_controls.get())
                    if protocol_version >= 4 and decision_task is None and decision_fetch is None:
                        decision_task = own_task(screen_decisions.get())
                    waiting = {event_task}
                    if control_task is not None:
                        waiting.add(control_task)
                    for task in (decision_task, decision_fetch):
                        if task is not None:
                            waiting.add(task)
                    if forward_task is not None:
                        waiting.add(forward_task)
                    timeout = max(0.01, min(deadlines) - time.monotonic()) if deadlines else None
                    done, _ = await asyncio.wait(waiting, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                    if control_task is not None and control_task in done:
                        segment_id = control_task.result()
                        control_task = None
                        caller_epoch += 1
                        current_id = reply.response_id if reply and not reply.complete else (
                            pending_spoken_reply.response_id if pending_spoken_reply else None)
                        if current_id is not None or forward_task is not None:
                            ambiguous_generation = True
                            revoke_reply()
                            pending_spoken_reply = None
                            forwarded_without_tool = None
                            tool_grace_deadline = None
                            speech_deadline = None
                            snapshot_sent_early = False
                            pending_tool_calls.clear()
                            await ws.send_json({"type": "interrupted", "response_id": current_id})
                            logger.info("Voice caller activity session=%s segment=%s", session_id, segment_id)
                        continue
                    if decision_task is not None and decision_task in done:
                        proposal_id = decision_task.result()
                        decision_task = None
                        decision_fetch = own_task(tools.decision_reply(proposal_id))
                        continue
                    if decision_fetch is not None and decision_fetch in done:
                        response = decision_fetch.result()
                        decision_fetch = None
                        caller_busy = bool(forward_task is not None or pending_spoken_reply or latest_user_turn or
                                           pending_tool_calls or queued_final_turns or (reply and not reply.complete))
                        if response is None or caller_busy:
                            # Nothing recorded, or the caller has already moved on: the reply stays on screen.
                            logger.info("Voice screen decision not spoken session=%s found=%s busy=%s",
                                        session_id, response is not None, caller_busy)
                            continue
                        revoke_reply(clear_presentation=False)
                        reply = _Reply(
                            response_id=str(response["response_id"]),
                            proposal=response.get("proposal"),
                            end_session=False,
                            memory_snapshot=session_memory_snapshot(response, after_screen_decision=True),
                            resolve_result_at=time.monotonic(),
                        )
                        speech_deadline = time.monotonic() + speech_timeout_seconds
                        await ws.send_json({
                            "type": "resolve_result", "response_id": reply.response_id,
                            "case_id": response.get("case_id"), "reply_text": response.get("reply_text"),
                            "pending_question": response.get("pending_question"), "proposal": response.get("proposal"),
                            "operation_status": response.get("operation_status"), "end_session": False,
                        })
                        try:
                            await request_snapshot_speech(reply)
                        except Exception as exc:
                            logger.warning("Voice decision speech request failed (%s)", type(exc).__name__)
                            await finish_reply(reply)
                        continue
                    if forward_task is not None and forward_task in done:
                        await forward_task
                        forward_task = None
                        if forward_task_epoch != caller_epoch:
                            pending_spoken_reply = None
                            forwarded_without_tool = None
                            tool_grace_deadline = None
                            speech_deadline = None
                        if pending_tool_calls:
                            ready_calls, pending_tool_calls[:] = pending_tool_calls[:], []
                            await process_tool_calls(ready_calls)
                        if queued_final_turns:
                            next_text, next_language, next_epoch = queued_final_turns.popleft()
                            latest_user_turn = ""
                            revoke_reply(clear_presentation=False)
                            pending_spoken_reply = None
                            forwarded_without_tool = None
                            tool_grace_deadline = None
                            speech_deadline = None
                            forward_task_epoch = next_epoch
                            forward_task = own_task(forward_without_model_tool(next_text, next_language))
                        continue
                    if event_task not in done:
                        raise asyncio.TimeoutError
                    event = event_task.result()
                    event_task = None
                except StopAsyncIteration:
                    for task in (control_task, decision_task):
                        if task is not None:
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                    break
                except asyncio.TimeoutError:
                    if tool_grace_deadline is not None and time.monotonic() >= tool_grace_deadline:
                        tool_grace_deadline = None
                        if pending_spoken_reply and not tool_response_sent:
                            try:
                                await request_snapshot_speech(pending_spoken_reply)
                                snapshot_sent_early = not original_turn_complete
                                if original_turn_complete:
                                    reply = pending_spoken_reply
                                    pending_spoken_reply = None
                            except Exception as exc:
                                logger.warning("Voice snapshot request failed (%s)", type(exc).__name__)
                        continue
                    if (current_first_audio_deadline is not None and
                            time.monotonic() >= current_first_audio_deadline and
                            speech_deadline is not None and time.monotonic() < speech_deadline and reply):
                        if await retry_snapshot_speech(reply):
                            continue
                        await finish_reply(reply)
                        # Keep the pending provider read: breaking here would start a
                        # second concurrent receive on the same Live socket.
                        continue
                    if speech_deadline is not None and time.monotonic() >= speech_deadline:
                        waiting = pending_spoken_reply or (reply if reply and not reply.complete else None)
                        logger.info("Voice speech timeout session=%s pending=%s audio_bytes=%s",
                                    session_id, pending_spoken_reply is not None,
                                    waiting.audio_bytes if waiting else 0)
                        pending_spoken_reply = None
                        forwarded_without_tool = None
                        tool_grace_deadline = None
                        snapshot_sent_early = False
                        original_turn_interrupted = False
                        if waiting:
                            reply = waiting
                            await finish_reply(waiting)
                        speech_deadline = None
                        continue
                    return await end_call()
                saw_event = True
                content = getattr(event, "server_content", None)
                if content and getattr(content, "turn_complete", False):
                    logger.info("Voice model turn complete session=%s", session_id)
                was_interrupted = bool(content and getattr(content, "interrupted", False))
                if was_interrupted:
                    if (pending_spoken_reply and not pending_spoken_reply.sent_audio and
                            (snapshot_sent_early or tool_response_sent)):
                        # This is the old model answer being stopped by our own
                        # snapshot update, not a caller interruption.
                        original_turn_interrupted = True
                    else:
                        interrupted_id = reply.response_id if reply else None
                        revoke_reply()
                        pending_spoken_reply = None
                        forwarded_without_tool = None
                        tool_grace_deadline = None
                        snapshot_sent_early = False
                        original_turn_interrupted = False
                        pending_tool_calls.clear()
                        await ws.send_json({"type": "interrupted", "response_id": interrupted_id})
                transcript = getattr(content, "input_transcription", None) if content else None
                if transcript and getattr(transcript, "text", None):
                    logger.info("Voice input transcript session=%s chars=%s finished=%s", session_id,
                                len(transcript.text), getattr(transcript, "finished", None))
                if transcript and getattr(transcript, "text", None):
                    input_fragments.append(transcript.text)
                # Current Gemini Live treats input_transcription itself as final
                # and omits `finished`; older runtimes mark partials False.
                final_without_flag = bool(transcript and getattr(transcript, "text", None) and
                                          getattr(transcript, "finished", None) is None)
                if transcript and (getattr(transcript, "finished", False) or final_without_flag):
                    if input_fragments:
                        latest_user_turn = " ".join(part.strip() for part in input_fragments if part.strip()).strip()
                        input_fragments.clear()
                        forwarded_without_tool = None
                        tool_grace_deadline = None
                        snapshot_sent_early = False
                        original_turn_interrupted = False
                        revoke_reply(clear_presentation=False)
                        if latest_user_turn:
                            decision = detect_language(latest_user_turn)
                            language = decision.language or language
                            await ws.send_json({"type": "transcript", "speaker": "user", "text": latest_user_turn, "final": True})
                            if final_without_flag:
                                if forward_task is None:
                                    forward_task_epoch = caller_epoch
                                    final_text = latest_user_turn
                                    latest_user_turn = ""
                                    forward_task = own_task(forward_without_model_tool(final_text, language))
                                elif len(queued_final_turns) < 2:
                                    queued_final_turns.append((latest_user_turn, language, caller_epoch))
                                    latest_user_turn = ""
                                else:
                                    latest_user_turn = ""
                                    await ws.send_json({"type": "error", "code": "voice_turn_queue_full",
                                                        "message": "I'm still processing earlier requests. Please try again shortly."})
                tool_call = getattr(event, "tool_call", None)
                calls = getattr(tool_call, "function_calls", None) or []
                cancellation = getattr(event, "tool_call_cancellation", None)
                cancelled = set(getattr(cancellation, "ids", None) or [])
                if cancelled:
                    cancelled_tool_ids.update(cancelled)
                    pending_tool_calls[:] = [call for call in pending_tool_calls if call.id not in cancelled]
                if pending_spoken_reply and content and getattr(content, "turn_complete", False) and not calls:
                    original_turn_complete = True
                    if tool_continuation_speaking():
                        spoken = pending_spoken_reply
                        await stream_pcm(spoken, content)
                        if spoken.sent_audio:
                            reply = spoken
                            pending_spoken_reply = None
                            snapshot_sent_early = False
                            original_turn_interrupted = False
                            tool_response_sent = False
                            if not spoken.complete:
                                await finish_reply(spoken)
                            continue
                    if tool_grace_deadline is not None and not tool_response_sent:
                        continue
                    reply = pending_spoken_reply
                    pending_spoken_reply = None
                    if tool_response_sent or not snapshot_sent_early or not original_turn_interrupted:
                        # No explicit interruption boundary: the early request may
                        # already have spoken, but that audio was unproven and dropped.
                        # A tool response may leave Gemini idle; explicitly request
                        # speech only after the old turn has completed.
                        try:
                            await request_snapshot_speech(reply)
                            speech_deadline = time.monotonic() + speech_timeout_seconds
                        except Exception as exc:
                            logger.warning("Voice snapshot speech request failed (%s)", type(exc).__name__)
                            await finish_reply(reply)
                    else:
                        logger.info("Voice interrupted original turn before grounded speech session=%s after_resolve_ms=%d",
                                    session_id, round((time.monotonic() - reply.resolve_result_at) * 1000))
                    snapshot_sent_early = False
                    original_turn_interrupted = False
                    tool_response_sent = False
                    continue
                current = reply if reply and not reply.interrupted and not reply.complete else None
                if current is None and tool_continuation_speaking():
                    # Audio after the late tool response is grounded in the Resolve result.
                    await stream_pcm(pending_spoken_reply, content)
                if current and content:
                    await stream_pcm(current, content)
                    if not current.complete and getattr(content, "turn_complete", False) and not was_interrupted:
                        if not current.sent_audio and await retry_snapshot_speech(current):
                            continue
                        await finish_reply(current)
                if calls:
                    logger.info("Voice model tool calls session=%s count=%s", session_id, len(calls))
                if calls and ambiguous_generation and forwarded_without_tool is None:
                    # The provider does not attach a caller-turn ID to function
                    # calls. Reject calls spanning an interruption instead of
                    # attributing an old generation to the next caller turn.
                    for call in calls:
                        if call.id not in cancelled_tool_ids and call.id not in answered_tool_ids:
                            await session.send_tool_response(function_responses=[types.FunctionResponse(
                                id=call.id, name=call.name, response={"output": {"error": "ambiguous_generation"}})])
                            answered_tool_ids.add(call.id)
                    calls = []
                pending_tool_calls.extend(call for call in calls if call.id not in cancelled_tool_ids)
                if pending_tool_calls and ((latest_user_turn and forward_task is None) or pending_spoken_reply or
                                           (forwarded_without_tool and reply and not reply.complete) or
                                           (forwarded_without_tool and forwarded_without_tool.get("error")) or
                                           (reply and reply.complete)):
                    ready_calls, pending_tool_calls[:] = pending_tool_calls[:], []
                    await process_tool_calls(ready_calls)
                if getattr(event, "go_away", None):
                    await ws.send_json({"type": "error", "code": "voice_session_ending", "message": "Continue by text if this call disconnects."})
            if not saw_event:
                return "provider_disconnected"
    audio_task = asyncio.create_task(receive_audio(), name=f"hutch-audio-{session_id}")
    model_task = asyncio.create_task(receive_model(), name=f"hutch-model-{session_id}")
    tasks = {audio_task, model_task}
    try:
        done, pending = await asyncio.wait(tasks, timeout=max_session_seconds, return_when=asyncio.FIRST_COMPLETED)
        if not done:
            try:
                await ws.send_json({"type": "ended", "reason": "session_limit"})
                await ws.close(code=1000, reason="session_limit")
            except Exception:
                pass
            return "session_limit"
        for task in done:
            result = task.result()
            if task is audio_task and result == "audio_limit":
                return result
            if task is audio_task and result == "ended":
                return result
            if task is model_task and result == "ended":
                return result
            if task is model_task and result == "provider_disconnected":
                try:
                    await ws.send_json({"type": "error", "code": "voice_provider_disconnected", "message": "Voice disconnected. Continue by text."})
                    await ws.close(code=1011, reason="provider_disconnected")
                except Exception:
                    pass
                return result
            # A completed model iterator is restarted internally; a socket reader only
            # returns on disconnect or a bounded audio-limit closure.
            if task is audio_task and result == "disconnected":
                return result
        return "disconnected"
    finally:
        for task in tasks | owned_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*(tasks | owned_tasks), return_exceptions=True)


@app.websocket("/ws/hutch/{session_id}")
async def hutch_audio_socket(ws: WebSocket, session_id: str):
    origin = (ws.headers.get("origin") or "").rstrip("/")
    offered = _grant_from_protocol(ws.headers.get("sec-websocket-protocol", ""))
    if not offered:
        await ws.close(code=1008, reason="zeptaz_hutch_protocol_required")
        return
    selected_protocol, token = offered
    binding = app.state.store.consume_grant(token, session_id=session_id, origin=origin)
    if not binding:
        await ws.close(code=1008, reason="grant_invalid_or_used")
        return
    await ws.accept(subprotocol=selected_protocol)
    resolve = app.state.resolve
    session_started = time.monotonic()
    runtime_error: str | None = None
    try:
        await resolve.send_event(VoiceEventRequest(binding_id=binding["binding_id"], voice_session_id=session_id,
            event_id=str(uuid4()), event_type="connected", details={"connected_at": int(time.time())}).model_dump())
    except Exception:
        logger.info("Could not deliver voice connected event session=%s", session_id)
    adapter = HutchAdapter(resolve)
    tools = HutchVoiceTools(adapter, binding["binding_id"], session_id)
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        await ws.send_json({"type": "error", "code": "voice_provider_unavailable", "message": "Voice is temporarily unavailable. Continue by text."})
        await ws.close(code=1011)
        try:
            await resolve.send_event(VoiceEventRequest(binding_id=binding["binding_id"], voice_session_id=session_id,
                event_id=str(uuid4()), event_type="error", details={"error_type":"provider_unavailable"}).model_dump())
            await resolve.send_event(VoiceEventRequest(binding_id=binding["binding_id"], voice_session_id=session_id,
                event_id=str(uuid4()), event_type="disconnected", details={"ended_at":int(time.time())}).model_dump())
        except Exception:
            pass
        return
    client = genai.Client(api_key=api_key)
    runtime_config = VoiceRuntimeConfig.from_environment()
    model = runtime_config.model
    tool_declarations = [types.Tool(function_declarations=[types.FunctionDeclaration(
        name=VOICE_TOOLS[0]["name"], description=VOICE_TOOLS[0]["description"],
        parameters_json_schema=VOICE_TOOLS[0]["parameters"]
    )])]
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"], temperature=0.2,
        system_instruction=SYSTEM_INSTRUCTION, tools=tool_declarations,
        input_audio_transcription=_input_transcription_config(runtime_config.input_language_codes),
        # V3 already has browser VAD with noise filtering and numbered activity
        # boundaries. Disable Gemini's independent detector there so raw-mic echo
        # or noise rejected by the browser cannot independently interrupt speech.
        # V2 keeps provider-managed detection for compatibility.
        realtime_input_config=_realtime_input_config(
            protocol_version=PROTOCOL_VERSIONS[selected_protocol],
            end_silence_ms=runtime_config.end_silence_ms,
            full_duplex=runtime_config.full_duplex),
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=os.getenv("GEMINI_TTS_VOICE", "Kore")))),
    )
    if runtime_config.context_compression:
        config.context_window_compression = types.ContextWindowCompressionConfig(
            trigger_tokens=32768, sliding_window=types.SlidingWindow(target_tokens=16384)
        )
    try:
        async with _live_session(client, model, config) as session:
            await ws.send_json({"type": "ready", "session_id": session_id, "provider": "gemini_live", "model": model, "live_profile": runtime_config.profile, "features": {"full_duplex":PROTOCOL_VERSIONS[selected_protocol] >= 3 and runtime_config.full_duplex,"context_compression":runtime_config.context_compression,"protocol_version":PROTOCOL_VERSIONS[selected_protocol]}, "input_format": {"encoding": "pcm_s16le", "sample_rate": 16000}, "output_format": {"encoding": "pcm_s16le", "sample_rate": 24000}})
            await ws.send_json({"type": "greeting", "text": "HUTCH Resolve demo. Tell me what you need help with."})
            await _run_hutch_live_session(
                ws=ws, session=session, tools=tools, adapter=adapter,
                binding_id=binding["binding_id"], session_id=session_id,
                max_audio_bytes=int(os.getenv("HUTCH_VOICE_MAX_AUDIO_BYTES", "3840000")),
                max_session_seconds=int(os.getenv("HUTCH_VOICE_MAX_SESSION_SECONDS", "120")),
                end_session_timeout_seconds=float(os.getenv("HUTCH_VOICE_END_SESSION_TIMEOUT_SECONDS", "5")),
                protocol_version=PROTOCOL_VERSIONS[selected_protocol],
            )
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except asyncio.TimeoutError:
        try:
            await ws.send_json({"type": "ended", "reason": "session_limit"})
            await ws.close(code=1000)
        except Exception:
            pass
    except Exception as exc:
        runtime_error = type(exc).__name__
        logger.warning("Hutch Voice session failed id=%s type=%s", session_id, type(exc).__name__)
        try:
            await ws.send_json({"type": "error", "code": "voice_runtime_error", "message": "Voice disconnected. Continue by text."})
        except Exception:
            pass
    finally:
        try:
            if runtime_error:
                error_event = VoiceEventRequest(binding_id=binding["binding_id"], voice_session_id=session_id,
                                                event_id=str(uuid4()), event_type="error", details={"error_type": runtime_error})
                await resolve.send_event(error_event.model_dump())
            usage_event = VoiceEventRequest(binding_id=binding["binding_id"], voice_session_id=session_id,
                                           event_id=str(uuid4()), event_type="usage", details={"duration_seconds": int(time.monotonic() - session_started)})
            await resolve.send_event(usage_event.model_dump())
            event = VoiceEventRequest(binding_id=binding["binding_id"], voice_session_id=session_id,
                                      event_id=str(uuid4()), event_type="disconnected", details={"ended_at": int(time.time())})
            await resolve.send_event(event.model_dump())
        except Exception:
            logger.info("Could not deliver voice disconnect event session=%s", session_id)
        await client.aio.aclose()
