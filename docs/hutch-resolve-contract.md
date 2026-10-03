# Zeptaz Voice ↔ Hutch Resolve contract

This repository implements the external Voice side. Resolve signed bridge, mock providers and operation APIs are implemented on `ResolveDev`; Tevin's live conversation controller and Jayith's v2 browser remain integration dependencies.

## Current qualification and shared plan

The [canonical team plan](https://github.com/Zeptaz/hutch_resolve/blob/ResolveDev/context.md) and [Resolve API specification](https://github.com/Zeptaz/hutch_resolve/blob/ResolveDev/docs/contracts.md) define upcoming integration work. Existing Voice models remain the compatibility boundary; new Resolve routes are proposed, not deployed.

On 2026-10-02 the fake runtime suite passed (34 tests). It covers split transcription/tool events, repeated turns, interruption, malformed acknowledgements, sensitive speech grounding and retry identity. A real microphone/model and browser integration remain unverified. See [Voice context](../context.md).

The browser protocol is `zeptaz-hutch-v2`. Voice emits `{"type":"interrupted","response_id":null}` (affected ID when known) before later output and clears proposal eligibility. The browser must discard queued audio and pending acknowledgements. After speech followed by roughly 900 ms of quiet, the browser sends `{"type":"input_audio_end"}`; Voice forwards it to Gemini as `audio_stream_end`, once per audio segment. `end_session=true` delivers final grounded output, then `ended` after playback/turn completion or bounded timeout.

## Session setup

Resolve calls Voice `POST /api/hutch/sessions` over HTTPS with the standard HMAC headers. Body:

```json
{"binding_id":"...","conversation_id":"...","voice_session_id":"...","account_id":"...","origin":"https://resolve.example","expires_at":1790938800}
```

Resolve generates the scoped binding and unique IDs. Voice checks the exact configured browser origin and binding expiry, stores the binding, and returns a browser grant good for at most 60 seconds plus an absolute WebSocket URL from `ZEPTAZ_PUBLIC_BASE_URL` and `/ws/hutch/{voice_session_id}`. The browser presents protocols `zeptaz-hutch-v2` and `hutch-grant.{token}`; Voice selects only `zeptaz-hutch-v2`. The signed grant is origin/session bound and consumed atomically once. It is never put in a URL. Audio frames are binary mono PCM16, 16 kHz in and 24 kHz out. Frame limit is 16 KiB and the default call audio budget is 3.84 MB. Audio recording is disabled.

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

Partial transcripts are displayed only. The Gemini function declaration exposes one tool, `forward_final_turn_to_hutch_resolve`; its input is ignored and the server forwards the finalized transcript received from the audio transcription channel. Current Gemini Live also emits authoritative `input_transcription` without a `finished` member and may not request the declared tool. In that case Voice forwards the final transcript directly through the same Hutch adapter exactly once, discards the original ungrounded model turn, then asks the existing Live session to speak Resolve's canonical `speech_text`. That second turn's audio is buffered until its output transcript matches the canonical text exactly; otherwise Voice sends `audio_fallback` with verified text. The browser may try local speech synthesis for the fallback, but it never reports a proposal as voice-presented on that path; acceptance uses explicit text controls. When the model does call the tool, tool output is the Resolve response. The WebSocket sends `resolve_result` with `response_id`, `speech_text`, `sensitive_audio`, proposal ID/hash and status. Audio is framed by `audio_start` and `audio_end` with the same response ID. Sensitive proposal/operation audio is buffered and its output transcript checked against Resolve's `speech_text`; on mismatch Voice sends `audio_fallback` with verified text and no proposal eligibility. After fully draining model audio, the browser sends `{"type":"playback_complete","response_id":"..."}` and requires an accepted `playback_ack`. It then sends `{"type":"proposal_presented","response_id":"...","proposal_id":"...","proposal_hash":"..."}` and requires an accepted `proposal_ack`. Only that acknowledged ID/hash is attached once to the next final turn. An interruption clears eligibility. Resolve also checks the original transcript for fresh affirmative consent and validates the persisted proposal response, binding and hash.

## Failure / privacy

Resolve unavailable or invalid response: say support is temporarily unavailable and direct the user to the independent Resolve text channel. Disconnect sends a best-effort lifecycle event; case and operation state remain in Resolve. Voice logs IDs, error class and model/profile only; it does not log audio, transcripts, secrets or raw provider payloads. Gemini Live keys and HMAC secrets are environment-only. No credentials from the inspected source repo are copied.

## Proposed vs existing

The inspected Zeptaz Voice repository already has a Gemini Live PCM audio runtime, multilingual policy, audio codec, origin-bound single-use demo grants, and reconnect/buffering/tool handling. Its existing session scenarios and business tool executor do not implement Hutch Resolve sessions or callbacks. This Hutch-specific session API, scoped grant store, adapter, HMAC client and Resolve contracts are new Voice-side capabilities. They are implemented here without creating Resolve endpoints.
