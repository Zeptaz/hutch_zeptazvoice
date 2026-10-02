# Zeptaz Voice ↔ Hutch Resolve contract

This repository implements only the Voice side. Resolve API handlers, browser UI, operations and mock providers are future work in `hutch_resolve`; the client below targets that contract and uses stubs in tests.

## Current qualification and shared plan

The [canonical team plan](https://github.com/Zeptaz/hutch_resolve/blob/main/context.md) and [Resolve API specification](https://github.com/Zeptaz/hutch_resolve/blob/main/docs/contracts.md) define upcoming integration work. Existing Voice models remain the compatibility boundary; new Resolve routes are proposed, not deployed.

On 2026-10-02 all 15 existing Voice tests passed. A separate fake SDK/WebSocket reproduction found that split transcription/tool/turn-complete events lose the finalized caller turn and the session terminates after one model turn. The audio behavior described below is the intended contract and is **not fully qualified**. Harry's H-08 tasks include committed runtime regressions, fixes, interruption/cleanup checks and a live integrated call. See [Voice context](../context.md).

Upcoming additive browser event (not implemented yet): `{"type":"interrupted","response_id":null}`; supply the affected Resolve response ID when known. Voice emits it on provider interruption before further output and clears proposal-presentation eligibility; Jayith's browser stops/discards queued audio and pending presentation acknowledgement. The current strict HTTP request/response models remain unchanged. For `end_session=true`, the planned runtime delivers final grounded output, emits `ended` with reason `resolve_requested`, and closes after turn completion or bounded timeout. Shared OpenAPI documents the planned browser control schemas separately from HTTP endpoints.

## Session setup

Resolve calls Voice `POST /api/hutch/sessions` over HTTPS with the standard HMAC headers. Body:

```json
{"binding_id":"...","conversation_id":"...","voice_session_id":"...","account_id":"...","origin":"https://resolve.example","expires_at":1790938800}
```

Resolve generates the scoped binding and unique IDs. Voice checks the exact configured browser origin and binding expiry, stores the binding, and returns a browser grant good for at most 60 seconds plus an absolute WebSocket URL from `ZEPTAZ_PUBLIC_BASE_URL` and `/ws/hutch/{voice_session_id}`. The browser presents protocols `zeptaz-hutch-v1` and `hutch-grant.{token}`; Voice selects only `zeptaz-hutch-v1`. The signed grant is origin/session bound and consumed atomically once. It is never put in a URL. Audio frames are binary mono PCM16, 16 kHz in and 24 kHz out. Frame limit is 16 KiB and the default call audio budget is 3.84 MB. Audio recording is disabled.

## Voice requests Resolve

Base URL is fixed at `HUTCH_RESOLVE_BASE_URL` (must include `/api/v1`). The adapter sends finalized turns to `POST /integrations/voice/turns` and lifecycle events to `POST /integrations/voice/events`.

Turn request:

```json
{"binding_id":"...","voice_session_id":"...","event_id":"...","turn_id":"...","transcript":"The data stopped working","language":"en","is_final":true,"presented_proposal_id":null,"presented_proposal_hash":null}
```

Resolve response:

```json
{"response_id":"...","case_id":"...","reply_text":"...","speech_text":"...","pending_question":null,"proposal":{"id":"...","proposal_hash":"...","action_type":"DEACTIVATE_VAS","target_label":"Synthetic video alerts","consequences":"Stops future renewal only","expires_at":"..."},"operation_status":null,"end_session":false}
```

`proposal` and `case_id` may be null. `operation_status` reports an actual saved operation state, never intent. Resolve owns identity and account authorization, evidence, diagnosis, proposal eligibility, fresh confirmation, idempotent execution/readback, human review and Trust Receipt. The next final caller turn includes the most recently presented proposal ID and hash. Voice cannot call simulator/provider methods directly and does not expose arbitrary billing or account mutation tools.

Lifecycle event:

```json
{"binding_id":"...","voice_session_id":"...","event_id":"...","event_type":"disconnected","details":{"ended_at":1790938800}}
```

## Authentication and retry behavior

Headers: `X-Voice-Timestamp`, `X-Voice-Event-Id`, `X-Voice-Body-Sha256`, `X-Voice-Signature`. Signature is HMAC-SHA256 over UTF-8 `timestamp.event_id.body_sha256`, with a maximum 60-second clock skew. The body digest is lowercase SHA-256 of the exact transmitted bytes. Configure the shared Resolve integration secret server side only.

Voice uses a fixed configured base URL, TLS in production, 2-second connect and 8-second response timeout. It retries a finalized turn at most once on network timeout or 5xx, reusing the exact event ID and body. Resolve must return its cached response for duplicate matching requests and reject the same ID with a changed hash (409). Voice never retries with a fresh ID and never describes an unknown operation as successful. 401/403, 409, 422 and 5xx produce safe caller messages; uncertain results continue to Resolve recovery or text follow-up. Gemini connection setup has a 12-second timeout; a call is capped at 120 seconds and 3.84 MB of audio by default.

Partial transcripts are displayed only. The Gemini function declaration exposes one tool, `forward_final_turn_to_hutch_resolve`; its input is ignored and the server forwards the finalized transcript received from the audio transcription channel. Tool output is the Resolve response. The websocket sends the structured `resolve_result` (including the exact proposal and hash) to the client. Voice accepts a presentation acknowledgement only after Gemini reports the proposal turn complete; after playback finishes, the client sends `{"type":"proposal_presented","proposal_id":"...","proposal_hash":"..."}`. Only that one acknowledged ID/hash is attached to the next final turn; it is consumed once. If the caller interrupts before proposal playback completes, no confirmation context is sent. Resolve still checks the original transcript for fresh affirmative consent.

## Failure / privacy

Resolve unavailable or invalid response: say support is temporarily unavailable and direct the user to the independent Resolve text channel. Disconnect sends a best-effort lifecycle event; case and operation state remain in Resolve. Voice logs IDs, error class and model/profile only; it does not log audio, transcripts, secrets or raw provider payloads. Gemini Live keys and HMAC secrets are environment-only. No credentials from the inspected source repo are copied.

## Proposed vs existing

The inspected Zeptaz Voice repository already has a Gemini Live PCM audio runtime, multilingual policy, audio codec, origin-bound single-use demo grants, and reconnect/buffering/tool handling. Its existing session scenarios and business tool executor do not implement Hutch Resolve sessions or callbacks. This Hutch-specific session API, scoped grant store, adapter, HMAC client and Resolve contracts are new Voice-side capabilities. They are implemented here without creating Resolve endpoints.
