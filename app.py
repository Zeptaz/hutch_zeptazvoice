from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types

from adapters.hutch.adapter import HutchAdapter, HutchVoiceTools, SYSTEM_INSTRUCTION, VOICE_TOOLS
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
    common = "zeptaz-hutch-v1"
    protocols = [part.strip() for part in header.split(",")]
    grant = next((item.removeprefix("hutch-grant.") for item in protocols if item.startswith("hutch-grant.")), None)
    if common not in protocols or not grant:
        return None
    return common, grant


@app.websocket("/ws/hutch/{session_id}")
async def hutch_audio_socket(ws: WebSocket, session_id: str):
    origin = (ws.headers.get("origin") or "").rstrip("/")
    offered = _grant_from_protocol(ws.headers.get("sec-websocket-protocol", ""))
    if not offered:
        await ws.close(code=1008, reason="grant_required")
        return
    _, token = offered
    binding = app.state.store.consume_grant(token, session_id=session_id, origin=origin)
    if not binding:
        await ws.close(code=1008, reason="grant_invalid_or_used")
        return
    await ws.accept(subprotocol="zeptaz-hutch-v1")
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
    language = "en"
    input_fragments: list[str] = []
    latest_user_turn = ""
    allow_model_audio = False
    proposal_audio_complete = False
    proposal_acknowledged = False
    tool_declarations = [types.Tool(function_declarations=[types.FunctionDeclaration(
        name=VOICE_TOOLS[0]["name"], description=VOICE_TOOLS[0]["description"],
        parameters_json_schema=VOICE_TOOLS[0]["parameters"]
    )])]
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"], temperature=0.2,
        system_instruction=SYSTEM_INSTRUCTION,
        tools=tool_declarations,
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
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

            async def receive_audio():
                nonlocal proposal_audio_complete, proposal_acknowledged
                total_bytes = 0
                while True:
                    message = await ws.receive()
                    if message.get("type") == "websocket.disconnect":
                        return
                    data = message.get("bytes")
                    if message.get("text"):
                        try:
                            control = json.loads(message["text"])
                        except (ValueError, TypeError):
                            control = {}
                        if control.get("type") == "proposal_presented":
                            accepted = (proposal_audio_complete and not proposal_acknowledged and
                                adapter.mark_proposal_presented(str(control.get("proposal_id", "")), str(control.get("proposal_hash", ""))))
                            proposal_acknowledged = accepted or proposal_acknowledged
                            await ws.send_json({"type": "proposal_ack", "accepted": accepted})
                        continue
                    if data is None:
                        continue
                    if len(data) > 16384 or len(data) % 2:
                        await ws.send_json({"type": "error", "code": "invalid_audio_frame"})
                        continue
                    total_bytes += len(data)
                    if total_bytes > int(os.getenv("HUTCH_VOICE_MAX_AUDIO_BYTES", "3840000")):
                        await ws.send_json({"type": "error", "code": "audio_limit_reached", "message": "Continue by text."})
                        await ws.close(code=1008, reason="audio_limit_reached")
                        return
                    await session.send_realtime_input(audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000"))

            async def receive_model():
                nonlocal language, latest_user_turn, allow_model_audio, proposal_audio_complete, proposal_acknowledged
                async for event in session.receive():
                    content = getattr(event, "server_content", None)
                    transcript = getattr(content, "input_transcription", None) if content else None
                    if transcript and getattr(transcript, "text", None):
                        input_fragments.append(transcript.text)
                        if getattr(content, "turn_complete", False):
                            latest_user_turn = " ".join(input_fragments).strip()
                            input_fragments.clear()
                            allow_model_audio = False
                            proposal_audio_complete = False
                            proposal_acknowledged = False
                            if latest_user_turn:
                                decision = detect_language(latest_user_turn)
                                language = decision.language or language
                                await ws.send_json({"type": "transcript", "speaker": "user", "text": latest_user_turn, "final": True})
                    output_transcript = getattr(content, "output_transcription", None) if content else None
                    if output_transcript and getattr(output_transcript, "text", None):
                        await ws.send_json({"type": "transcript", "speaker": "assistant", "text": output_transcript.text, "final": bool(getattr(content, "turn_complete", False))})
                    if content:
                        for part in (getattr(getattr(content, "model_turn", None), "parts", None) or []):
                            inline = getattr(part, "inline_data", None)
                            if allow_model_audio and inline and getattr(inline, "data", None):
                                await ws.send_bytes(inline.data)
                        if getattr(content, "turn_complete", False) and adapter.pending_proposal:
                            proposal_audio_complete = True
                    tool_call = getattr(event, "tool_call", None)
                    calls = getattr(tool_call, "function_calls", None) or []
                    if calls:
                        for call in calls:
                            response: dict
                            if call.name != VOICE_TOOLS[0]["name"] or not latest_user_turn:
                                response = {"error": "no_finalized_caller_turn"}
                            else:
                                final_text = latest_user_turn
                                latest_user_turn = ""
                                response = await tools.execute(call.name, transcript=final_text, language=language)
                                if not response.get("error"):
                                    await ws.send_json({
                                        "type": "resolve_result",
                                        "response_id": response.get("response_id"),
                                        "case_id": response.get("case_id"),
                                        "reply_text": response.get("reply_text"),
                                        "pending_question": response.get("pending_question"),
                                        "proposal": response.get("proposal"),
                                        "operation_status": response.get("operation_status"),
                                        "end_session": response.get("end_session", False),
                                    })
                            await session.send_tool_response(function_responses=[types.FunctionResponse(id=call.id, name=call.name, response={"output": response})])
                            allow_model_audio = True
                    if getattr(event, "go_away", None):
                        await ws.send_json({"type": "error", "code": "voice_session_ending", "message": "Continue by text if this call disconnects."})

            audio_task = asyncio.create_task(receive_audio())
            model_task = asyncio.create_task(receive_model())
            done, pending = await asyncio.wait({audio_task, model_task}, timeout=int(os.getenv("HUTCH_VOICE_MAX_SESSION_SECONDS", "120")), return_when=asyncio.FIRST_COMPLETED)
            if not done:
                raise asyncio.TimeoutError
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                if not task.cancelled() and task.exception():
                    raise task.exception()
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
