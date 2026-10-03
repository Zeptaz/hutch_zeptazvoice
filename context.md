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

Implementation commits remotely confirmed: this Voice `voice_test2` branch **47e8c92** and paired Resolve `voice_test` **8911392**. Both feature trees were clean after the implementation pushes.

## Live speech latency change — 2026-10-04

The previous exact `speech_text`/output-transcript gate withheld all Gemini PCM until the whole reply completed and matched. This Voice branch now streams valid PCM immediately from the first frame, without an approved-text buffer, output-transcript comparison or local fallback TTS. Each Resolve result supplies a per-turn Gemini Live session memory snapshot containing standing rules, the latest canonical reply, case ID, operation status and proposal. The existing Live connection remains per call. The browser receives only `reply_text` for canonical display. Model speech is a natural summary, so it may differ from the displayed text; that risk requires live semantic and native-language review. Proposal consent is tap-only because free-form streamed speech cannot prove that every term was heard; `proposal_presented` is rejected. No Resolve backend or source Zeptaz Voice repository was changed.

Verification: focused runtime tests **22 passed**; full Voice suite **54/54 passed** with a workspace-local pytest temp directory. The paired Resolve frontend typecheck/build and mock browser suite **24/24 passed**. A new live Gemini/physical microphone latency measurement remains open. The HTTP `speech_text` response remains for backwards compatibility but is ignored by this browser flow.

### Late Gemini tool-call repair — 2026-10-04

A real call after the streaming change reached Resolve successfully but then showed `speech_unavailable`. The Voice log recorded a final input transcript, one successful Resolve callback, a subsequent Gemini tool call, and a 45-second speech timeout with zero PCM. The unflagged-transcript fallback had already consumed the caller turn; the later tool call remained unanswered while Voice waited for an original model turn completion. Voice now keeps that one Resolve result until the original turn completes or the tool call arrives. A late tool call receives the saved result and session snapshot, and the pending reply becomes the streamed reply; Resolve is not invoked twice. Coalesced tool-call/turn-completion events also take the tool path without leaking original ungrounded audio. Interruption and timeout clear the saved result. No Resolve backend or frontend code changed. Focused runtime suite **25/25 passed** and full Voice suite **57/57 passed**. A fresh live Gemini call is still required to confirm audio after this exact repair.
