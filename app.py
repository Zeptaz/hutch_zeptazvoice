from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
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


def _grant_from_protocol(header: str) -> tuple[str, str] | None:
    common = "zeptaz-hutch-v2"
    protocols = [part.strip() for part in header.split(",")]
    grant = next((item.removeprefix("hutch-grant.") for item in protocols if item.startswith("hutch-grant.")), None)
    if common not in protocols or not grant:
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
        "playback_complete": {"type", "response_id"},
        "proposal_presented": {"type", "response_id", "proposal_id", "proposal_hash"},
    }
    if control_type not in fields or set(value) != fields[control_type]:
        return None
    if any(not isinstance(value[key], str) or not value[key] or len(value[key]) > 128
           for key in fields[control_type] - {"type"}):
        return None
    return value


async def _run_hutch_live_session(*, ws, session, tools, adapter, binding_id: str,
                                  session_id: str, max_audio_bytes: int,
                                  max_session_seconds: int, end_session_timeout_seconds: float = 5.0,
                                  speech_timeout_seconds: float = 45.0):
    """Supervise both sides of the live call until disconnect, failure, or Resolve end."""
    input_fragments: list[str] = []
    latest_user_turn = ""
    pending_tool_calls: list = []
    language = "en"
    reply: _Reply | None = None
    end_deadline: float | None = None
    audio_bytes = 0
    output_audio_limit = 60 * 24000 * 2

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
                if control["type"] == "input_audio_end":
                    if audio_since_end:
                        logger.info("Voice input segment ended session=%s bytes=%s", session_id, audio_bytes)
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
            await session.send_realtime_input(audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000"))

    async def receive_model():
        nonlocal language, latest_user_turn, reply, end_deadline
        pending_spoken_reply: _Reply | None = None
        forwarded_without_tool: dict | None = None
        snapshot_sent_early = False
        original_turn_interrupted = False
        speech_deadline: float | None = None

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

        async def forward_without_model_tool():
            nonlocal latest_user_turn, pending_spoken_reply, forwarded_without_tool
            nonlocal snapshot_sent_early, original_turn_interrupted, speech_deadline
            final_text = latest_user_turn
            latest_user_turn = ""
            try:
                response = await tools.execute(VOICE_TOOLS[0]["name"], transcript=final_text, language=language)
            except Exception as exc:
                logger.warning("Resolve voice tool failed (%s)", type(exc).__name__)
                response = {"error": "resolve_tool_failed"}
            if response.get("error"):
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
            await ws.send_json({
                "type": "resolve_result", "response_id": pending_spoken_reply.response_id,
                "case_id": response.get("case_id"), "reply_text": response.get("reply_text"),
                "pending_question": response.get("pending_question"), "proposal": response.get("proposal"),
                "operation_status": response.get("operation_status"), "end_session": pending_spoken_reply.end_session,
            })
            # Interrupt the original ungrounded answer now. Any already queued
            # PCM is discarded until Gemini marks that old turn complete.
            try:
                await request_snapshot_speech(pending_spoken_reply)
                snapshot_sent_early = True
                speech_deadline = time.monotonic() + speech_timeout_seconds
            except Exception as exc:
                logger.warning("Voice early snapshot request failed (%s)", type(exc).__name__)
                # The old turn can still complete normally; retry the snapshot then.

        async def process_tool_calls(calls):
            nonlocal latest_user_turn, reply, pending_spoken_reply, forwarded_without_tool
            nonlocal snapshot_sent_early, original_turn_interrupted, end_deadline, speech_deadline
            for call in calls:
                if call.name == VOICE_TOOLS[0]["name"] and pending_spoken_reply and forwarded_without_tool:
                    # A no-finished transcription reached Resolve before Gemini's tool call.
                    # Answer that call with the same result, allowing the Live turn to resume.
                    response = forwarded_without_tool
                    forwarded_without_tool = None
                    if not snapshot_sent_early:
                        reply = pending_spoken_reply
                        pending_spoken_reply = None
                        original_turn_interrupted = False
                    # When an early snapshot was sent, its interruption boundary
                    # can arrive after this tool response. Keep the reply pending
                    # so that boundary cannot revoke or expose its audio.
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
                if reply and not response.get("error") and reply.speech_requested_at is None:
                    reply.speech_requested_at = time.monotonic()

        async def finish_reply(current: _Reply):
            nonlocal speech_deadline, end_deadline
            if current.sent_audio:
                await ws.send_json({"type": "audio_end", "response_id": current.response_id})
            else:
                await ws.send_json({"type": "error", "code": "speech_unavailable",
                                    "message": "The reply is on screen. Continue by text or try speaking again."})
            current.complete = True
            speech_deadline = None
            if current.end_session and end_deadline is None:
                # Let the browser drain the full PCM reply before a bounded
                # end-session fallback can close the socket.
                playback_seconds = current.audio_bytes / (24000 * 2) if current.sent_audio else 0
                end_deadline = time.monotonic() + playback_seconds + end_session_timeout_seconds

        while True:
            saw_event = False
            iterator = aiter(session.receive())
            while True:
                try:
                    deadlines = [value for value in (end_deadline, speech_deadline) if value is not None]
                    if not deadlines:
                        event = await iterator.__anext__()
                    else:
                        event = await asyncio.wait_for(iterator.__anext__(), timeout=max(0.01, min(deadlines) - time.monotonic()))
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    if speech_deadline is not None and time.monotonic() >= speech_deadline:
                        waiting = pending_spoken_reply or (reply if reply and not reply.complete else None)
                        logger.info("Voice speech timeout session=%s pending=%s audio_bytes=%s",
                                    session_id, pending_spoken_reply is not None,
                                    waiting.audio_bytes if waiting else 0)
                        pending_spoken_reply = None
                        forwarded_without_tool = None
                        snapshot_sent_early = False
                        original_turn_interrupted = False
                        if waiting:
                            reply = waiting
                            await finish_reply(waiting)
                        speech_deadline = None
                        saw_event = True
                        break
                    return await end_call()
                saw_event = True
                content = getattr(event, "server_content", None)
                if content and getattr(content, "turn_complete", False):
                    logger.info("Voice model turn complete session=%s", session_id)
                was_interrupted = bool(content and getattr(content, "interrupted", False))
                if was_interrupted:
                    if pending_spoken_reply and snapshot_sent_early:
                        # This is the old model answer being stopped by our own
                        # snapshot update, not a caller interruption.
                        original_turn_interrupted = True
                    else:
                        interrupted_id = reply.response_id if reply else None
                        revoke_reply()
                        pending_spoken_reply = None
                        forwarded_without_tool = None
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
                        snapshot_sent_early = False
                        original_turn_interrupted = False
                        revoke_reply(clear_presentation=False)
                        if latest_user_turn:
                            decision = detect_language(latest_user_turn)
                            language = decision.language or language
                            await ws.send_json({"type": "transcript", "speaker": "user", "text": latest_user_turn, "final": True})
                            if final_without_flag:
                                await forward_without_model_tool()
                tool_call = getattr(event, "tool_call", None)
                calls = getattr(tool_call, "function_calls", None) or []
                if pending_spoken_reply and content and getattr(content, "turn_complete", False) and not calls:
                    reply = pending_spoken_reply
                    pending_spoken_reply = None
                    forwarded_without_tool = None
                    if not snapshot_sent_early or not original_turn_interrupted:
                        # No explicit interruption boundary: the early request may
                        # already have spoken, but that audio was unproven and dropped.
                        # Retry only after the old turn ends, preserving grounding.
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
                    continue
                current = reply if reply and not reply.interrupted and not reply.complete else None
                if current and content:
                    for part in (getattr(getattr(content, "model_turn", None), "parts", None) or []):
                        inline = getattr(part, "inline_data", None)
                        if inline and getattr(inline, "data", None):
                            frame = bytes(inline.data)
                            if len(frame) % 2 or current.audio_bytes + len(frame) > output_audio_limit:
                                logger.warning("Voice output limit or invalid PCM session=%s bytes=%s", session_id,
                                               current.audio_bytes + len(frame))
                                await finish_reply(current)
                                break
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
                    if not current.complete and getattr(content, "turn_complete", False) and not was_interrupted:
                        await finish_reply(current)
                if calls:
                    logger.info("Voice model tool calls session=%s count=%s", session_id, len(calls))
                pending_tool_calls.extend(calls)
                if pending_tool_calls and (latest_user_turn or pending_spoken_reply or (reply and reply.complete)):
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
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@app.websocket("/ws/hutch/{session_id}")
async def hutch_audio_socket(ws: WebSocket, session_id: str):
    origin = (ws.headers.get("origin") or "").rstrip("/")
    offered = _grant_from_protocol(ws.headers.get("sec-websocket-protocol", ""))
    if not offered:
        await ws.close(code=1008, reason="zeptaz_hutch_v2_required")
        return
    _, token = offered
    binding = app.state.store.consume_grant(token, session_id=session_id, origin=origin)
    if not binding:
        await ws.close(code=1008, reason="grant_invalid_or_used")
        return
    await ws.accept(subprotocol="zeptaz-hutch-v2")
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
        input_audio_transcription=types.AudioTranscriptionConfig(),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=False, silence_duration_ms=runtime_config.end_silence_ms),
            activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS if runtime_config.full_duplex else None,
        ),
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=os.getenv("GEMINI_TTS_VOICE", "Kore")))),
    )
    if runtime_config.context_compression:
        config.context_window_compression = types.ContextWindowCompressionConfig(
            trigger_tokens=32768, sliding_window=types.SlidingWindow(target_tokens=16384)
        )
    try:
        async with _live_session(client, model, config) as session:
            await ws.send_json({"type": "ready", "session_id": session_id, "provider": "gemini_live", "model": model, "live_profile": runtime_config.profile, "features": {"full_duplex":runtime_config.full_duplex,"context_compression":runtime_config.context_compression,"protocol_version":runtime_config.protocol_version}, "input_format": {"encoding": "pcm_s16le", "sample_rate": 16000}, "output_format": {"encoding": "pcm_s16le", "sample_rate": 24000}})
            await ws.send_json({"type": "greeting", "text": "HUTCH Resolve demo. Tell me what you need help with."})
            await _run_hutch_live_session(
                ws=ws, session=session, tools=tools, adapter=adapter,
                binding_id=binding["binding_id"], session_id=session_id,
                max_audio_bytes=int(os.getenv("HUTCH_VOICE_MAX_AUDIO_BYTES", "3840000")),
                max_session_seconds=int(os.getenv("HUTCH_VOICE_MAX_SESSION_SECONDS", "120")),
                end_session_timeout_seconds=float(os.getenv("HUTCH_VOICE_END_SESSION_TIMEOUT_SECONDS", "5")),
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
