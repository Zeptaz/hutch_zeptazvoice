# Zeptaz Voice ↔ Hutch Resolve contract

This repository implements the external Voice side. Resolve's signed bridge and Jayith's current v3 browser live in the separate `hutch_resolve` repository.

## Current qualification and shared plan

The [canonical team plan](https://github.com/Zeptaz/hutch_resolve/blob/ResolveDev/context.md) and [Resolve API specification](https://github.com/Zeptaz/hutch_resolve/blob/ResolveDev/docs/contracts.md) define upcoming integration work. Existing Voice models remain the compatibility boundary; new Resolve routes are proposed, not deployed.

On 2026-10-02 the fake runtime suite passed (34 tests). It covers split transcription/tool events, repeated turns, interruption, malformed acknowledgements, sensitive speech grounding and retry identity. A real microphone/model and browser integration remain unverified. See [Voice context](../context.md).

The current browser protocol is `zeptaz-hutch-v3`; Voice accepts v2 for older clients. The v3 browser sends numbered `{"type":"input_activity_start","segment_id":N}` and `{"type":"input_activity_end","segment_id":N}` only after local speech detection; IDs increase within the call. For v3, Voice disables Gemini's independent automatic VAD and maps these validated controls to Gemini `ActivityStart`/`ActivityEnd`. Automatic silence settings are omitted when detection is disabled, as required by Live setup validation. Voice buffers at most 300 ms of inactive PCM and sends it after `ActivityStart`, preserving the start of speech; mute discards this buffer. The browser requires three consecutive 100 ms speech frames and seven quiet frames (700 ms), with stronger echo rejection until local playback drains. A second raw-microphone detector cannot interrupt a reply independently. Microphone PCM continues through playback so intentional caller activity can interrupt; Voice emits `{"type":"interrupted","response_id":null}` (affected ID when known), discards old PCM, and clears proposal eligibility. The browser discards queued audio and pending acknowledgements. `input_audio_end` indicates a microphone pause or mute. V2 retains provider-managed automatic VAD with its configured silence duration (700 ms by default). `end_session=true` delivers final grounded output, then `ended` after playback/turn completion or bounded timeout.

## Session setup

Resolve calls Voice `POST /api/hutch/sessions` over HTTPS with the standard HMAC headers. Body:

```json
{"binding_id":"...","conversation_id":"...","voice_session_id":"...","account_id":"...","origin":"https://resolve.example","expires_at":1790938800}
```

Resolve generates the scoped binding and unique IDs. Voice checks the exact configured browser origin and binding expiry, stores the binding, and returns a browser grant good for at most 60 seconds plus an absolute WebSocket URL from `ZEPTAZ_PUBLIC_BASE_URL` and `/ws/hutch/{voice_session_id}`. The current browser presents protocols `zeptaz-hutch-v3` and `hutch-grant.{token}`; Voice selects v3. Older v2 clients remain accepted. The signed grant is origin/session bound and consumed atomically once. It is never put in a URL. Audio frames are binary mono PCM16, 16 kHz in and 24 kHz out. Frame limit is 16 KiB and the default call audio budget is 3.84 MB. Audio recording is disabled.

## Voice requests Resolve

Base URL is fixed at `HUTCH_RESOLVE_BASE_URL` (must include `/api/v1`). The adapter sends finalized turns to `POST /integrations/voice/turns` and lifecycle events to `POST /integrations/voice/events`.

Turn request:

```json
{"binding_id":"...","voice_session_id":"...","event_id":"...","turn_id":"...","transcript":"The data stopped working","language":"en","is_final":true,"presented_proposal_id":null,"presented_proposal_hash":null}
```

Resolve response (contract v1.1.0; `package_terms` is nullable and required only when `proposal.action_type` is `ACTIVATE_PACKAGE`):

```json
{"response_id":"...","case_id":"...","reply_text":"...","speech_text":"...","pending_question":null,"proposal":{"id":"...","proposal_hash":"...","action_type":"ACTIVATE_PACKAGE","target_label":"Synthetic 1 GB one-day add-on","consequences":"One MAIN debit of LKR 49.00; existing packages are retained; auto-renewal is off.","package_terms":{"name":"Synthetic 1 GB one-day add-on","price_minor":4900,"currency":"LKR","data_bytes":1000000000,"validity_seconds":86400,"recurring":false},"expires_at":"..."},"operation_status":null,"end_session":false}
```

`proposal` and `case_id` may be null. `operation_status` reports an actual saved operation state, never intent. Resolve owns identity and account authorization, evidence, diagnosis, proposal eligibility, fresh confirmation, idempotent execution/readback, human review and Trust Receipt. The next final caller turn includes the most recently presented proposal ID and hash. Voice cannot call simulator/provider methods directly and does not expose arbitrary billing or account mutation tools.

Lifecycle event:

```json
{"binding_id":"...","voice_session_id":"...","event_id":"...","event_type":"disconnected","details":{"ended_at":1790938800}}
```

## Authentication and retry behavior

Headers: `X-Voice-Timestamp`, `X-Voice-Event-Id`, `X-Voice-Body-Sha256`, `X-Voice-Signature`. Signature is HMAC-SHA256 over UTF-8 `timestamp.event_id.body_sha256`, with a maximum 60-second clock skew. The body digest is lowercase SHA-256 of the exact transmitted bytes. Configure the shared Resolve integration secret server side only.

Voice uses a fixed base URL, TLS in production, 2-second connect and 8-second response timeout, and an 18-second absolute retry budget. It retries transient transport/5xx and retryable `TURN_IN_PROGRESS`/`CONVERSATION_BUSY` with the exact event ID and body. If still pending, the adapter retains the turn identity for an identical caller retry. Resolve replays matching events and rejects changed content with 409. Voice never describes an unknown operation as successful. Gemini connection setup has a 12-second timeout; a call is capped at 120 seconds and 3.84 MB of audio by default.

Partial transcripts are displayed only. The Gemini function declaration exposes one tool, `forward_final_turn_to_hutch_resolve`; its input is ignored and the server forwards the finalized transcript received from the audio transcription channel. Current Gemini Live also emits authoritative `input_transcription` without a `finished` member and may not request the declared tool. Voice forwards that final transcript through the same Hutch adapter exactly once and discards the original ungrounded model output. If Gemini's tool call arrives after this direct forward, Voice answers it with the same saved Resolve result instead of invoking Resolve again. Resolve's reply is shown in the UI. A per-turn session memory snapshot gives the existing Gemini Live connection the latest Resolve reply, case ID, operation status, proposal and standing rules. For the no-tool path, Voice requests a Live turn from that snapshot after a 300 ms tool grace period. For a tool call, the snapshot accompanies the function response and a speech request follows the old model turn's completion. Model PCM streams to the browser from the first valid frame, bracketed by `audio_start` and `audio_end` with one response ID. There is no exact output-transcript comparison, approved-text audio buffer, `audio_fallback`, or browser speech-synthesis fallback. If no PCM arrives within the bounded speech window, Voice sends `speech_unavailable`; the verified Resolve reply remains visible. The browser sends `{"type":"playback_complete","response_id":"..."}` after draining model audio and receives `playback_ack`. Streamed speech cannot prove every proposal term was heard, so Voice never accepts `proposal_presented`; proposals are accepted or declined only with explicit displayed buttons through Resolve's normal text decision contract. Spoken yes/no alone carries no proposal presentation evidence. An interruption discards queued audio. Resolve remains the authority for consent and action execution. The Resolve HTTP response still includes `speech_text` for backwards compatibility; this Voice browser flow ignores it.

## Failure / privacy

After a Resolve result, Voice waits 300 ms for a Gemini tool call. If one arrives, Voice answers it with the saved result and waits for the old model turn to complete before requesting speech from the same snapshot. If no call arrives, Voice sends the snapshot after the grace period, discards the old ungrounded output through its completion boundary, then streams grounded PCM. Multiple calls for the same turn reuse the result; cancelled calls receive no response, and unresolved ambiguous calls after caller interruption receive a typed error. Resolve HTTP work runs independently from the Gemini event reader so interruptions remain responsive during slow investigations. Per-response logs record elapsed milliseconds from Resolve result and snapshot request to first PCM without transcript or audio contents. Proposal consent remains buttons only.

Once a grounded speech request is active, Voice allows 10 seconds for first PCM within the existing 45-second total speech window. A zero-PCM model completion or first-audio watchdog requests speech from the same saved snapshot once. That retry never resends the customer turn to Resolve or repeats an action. A second zero-PCM completion/watchdog sends `speech_unavailable` while keeping the canonical Resolve text on screen.

Resolve unavailable or invalid response: say support is temporarily unavailable and direct the user to the independent Resolve text channel. Disconnect sends a best-effort lifecycle event; case and operation state remain in Resolve. Voice logs IDs, error class and model/profile only; it does not log audio, transcripts, secrets or raw provider payloads. Gemini Live keys and HMAC secrets are environment-only. No credentials from the inspected source repo are copied.

## Proposed vs existing

The inspected Zeptaz Voice repository already has a Gemini Live PCM audio runtime, multilingual policy, audio codec, origin-bound single-use demo grants, and reconnect/buffering/tool handling. Its existing session scenarios and business tool executor do not implement Hutch Resolve sessions or callbacks. This Hutch-specific session API, scoped grant store, adapter, HMAC client and Resolve contracts are new Voice-side capabilities. They are implemented here without creating Resolve endpoints.
