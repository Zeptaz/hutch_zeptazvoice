# HUTCH Voice integration context

The canonical three-person plan for this voice repair is [hutch_resolve/context.md](https://github.com/Zeptaz/hutch_resolve/blob/voice_test/context.md). Harry owns this repository's integration/testing and the Resolve backend. Jayith owns browser audio/customer/dashboard UI; Tevin owns the conversation module inside Resolve.

Read the [Harry plan](https://github.com/Zeptaz/hutch_resolve/blob/voice_test/docs/plans/harry.md), [shared contracts](https://github.com/Zeptaz/hutch_resolve/blob/voice_test/docs/contracts.md), and [existing Voice wire contract](docs/hutch-resolve-contract.md) before work. After meaningful progress, update this file plus canonical context and Harry's plan with verification and remaining work. Mark complete only after implementation and verification; do not duplicate the combined plan here.

Architecture remains external Zeptaz Voice Core -> Hutch Adapter -> Resolve. Resolve owns identity, evidence, investigation, proposals, confirmation policy, actions, cases, receipts and dashboard data. Voice forwards final caller input and renders Resolve results; it cannot mutate telecom providers directly.

## Current verification: 2026-10-02

- [x] External Voice core and Hutch adapter remain on `adapter_buildation`; the complete local suite passed: 34 tests.
- [x] Fake-runtime regressions cover fragmented finalized input, multiple model turns, interruption, sensitive audio grounding, response-scoped playback/proposal acknowledgements, malformed controls, bounded end-session and provider/task cleanup.
- [x] Adapter retains event/turn IDs for retryable pending callbacks and bounds retries to an 18-second total budget.
- [x] Commit `3f33b03` was pushed and remotely confirmed on `adapter_buildation`; Resolve counterpart `a92bea5` was pushed on `ResolveDev` (with follow-up `02ce2f6`).
- [ ] Jayith must implement browser protocol `zeptaz-hutch-v2`, drain audio before `playback_complete`, then send `proposal_presented` only after accepted playback acknowledgement; discard queued audio on interruption.
- [ ] Qualify a real browser/microphone/Gemini session through Tevin's mounted Resolve controller, including confirmation, decline and text continuation. No live qualification claim is made from fake tests.

The prior 15-test baseline and ephemeral streaming failure are superseded by the committed regression suite. Keep credentials server-side and audio recording disabled. The HTTP Voice turn/event shapes remain strict and unchanged; the browser WebSocket protocol changed to v2 and requires a coordinated frontend update.

## Audit repair: spoken reply grounding and adapter failures — 2026-10-03

- [x] The Hutch runtime now buffers model-generated assistant audio and transcript until it exactly matches Resolve's canonical `speech_text`; mismatch falls back to the canonical text and never plays the unverified model audio.
- [x] Resolve tool exceptions and error responses now produce a sanitized typed `error` event and no caller-facing model reply. No transcript, customer input, or exception text is logged or returned.
- [x] Verification on `adapter_buildation`: focused runtime tests **20 passed**, full Voice suite **52 passed**, `git diff --check` passed.
- [ ] Real microphone/Resolve/model qualification remains outstanding. Synchronize this audit repair with `hutch_resolve/context.md` and `docs/plans/harry.md` after the Resolve phase is committed.

## Package confirmation integration — 2026-10-03

- [x] The Hutch adapter accepts `ACTIVATE_PACKAGE` proposals only with typed `package_terms` (`name`, `price_minor`, `currency`, `data_bytes`, `validity_seconds`, `recurring=false`) and rejects mismatched action/terms combinations.
- [x] Voice system instructions require reciting those exact terms. Confirmation still requires the response-scoped acknowledged proposal and a fresh affirmative final transcript; tool/model output cannot authorize an action.
- [x] Voice suite: 49 passed; `git diff --check` passed.
- [x] Resolve v1.1 package counterpart is committed and pushed as `hutch_resolve/ResolveDev` commit `d5c881a`.
- [x] Voice adapter package confirmation commit `93a0a1b` is pushed to `adapter_buildation` and remotely confirmed.
- [ ] Disposable PostgreSQL now verifies migration/reset, concurrent same-offer confirmation, one debit/subscription/provider operation and Trust Receipt. Package-specific crash/lost-response and injected-provider-failure recovery still need qualification; the Resolve package feature flag remains false by default.

## Main branch merge verification — 2026-10-03

`adapter_buildation` was fast-forwarded to `main` at `3bc6a27`; no history was rewritten. `python -m pytest -q` on the resulting Voice tree passed **52 tests**. Live Gemini/model/microphone qualification remains open. Resolve was merged separately in `hutch_resolve` and its independent `main` branch.

## Voice repair on `voice_test2` — 2026-10-03

Live synthetic audio showed Gemini 3.1 emits `input_transcription` without a `finished` member and sometimes no declared tool call. The old runtime waited for both, leaving the caller with no transcript or Resolve response. Voice now forwards that authoritative final transcript through the existing Hutch adapter once, drops the original ungrounded model turn, and asks the existing Live session to speak Resolve's canonical text. It releases audio only after the model's output transcript exactly matches that text; mismatch/timeout sends `audio_fallback`. An `input_audio_end` browser control flushes the Gemini audio segment after speech and quiet. Safe metadata logs record segment/transcript/verification lengths without raw speech or audio. End-session timeout now allows the generated audio to drain. No Resolve shared backend engine code changed.

Voice unit suite **54 passed**, including no-finished/no-tool and one-shot audio-end regressions. Against live Resolve, isolated PostgreSQL and Gemini, a synthetic WAV browser call reached `transcript`, `resolve_result`, `audio_start`, 38 binary audio frames, `audio_end`, `playback_complete` and `playback_ack`. One logged output transcript matched Resolve's 134-character speech text exactly; playback drained in the browser. The earlier 15-second speech timeout yielded text fallback on a slower model reply; the bounded Live speech window was extended to 45 seconds. A separate Gemini TTS route was rejected by automatic approval review because it would add a new egress of Resolve reply text; it was not implemented. Physical microphone/speaker and native-language voice quality still require human qualification. Fallback proposal acceptance remains text-only by design. The paired frontend changes and canonical contract are on Resolve `voice_test`.
