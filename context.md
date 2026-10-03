# HUTCH Voice integration context

The canonical three-person plan is [hutch_resolve/context.md](https://github.com/Zeptaz/hutch_resolve/blob/ResolveDev/context.md). Harry owns this repository's integration/testing and the Resolve backend. Jayith owns browser audio/customer/dashboard UI; Tevin owns the conversation module inside Resolve.

Read the [Harry plan](https://github.com/Zeptaz/hutch_resolve/blob/ResolveDev/docs/plans/harry.md), [shared contracts](https://github.com/Zeptaz/hutch_resolve/blob/ResolveDev/docs/contracts.md), and [existing Voice wire contract](docs/hutch-resolve-contract.md) before work. After meaningful progress, update this file plus canonical context and Harry's plan with verification and remaining work. Mark complete only after implementation and verification; do not duplicate the combined plan here.

Architecture remains external Zeptaz Voice Core -> Hutch Adapter -> Resolve. Resolve owns identity, evidence, investigation, proposals, confirmation policy, actions, cases, receipts and dashboard data. Voice forwards final caller input and renders Resolve results; it cannot mutate telecom providers directly.

## Current verification: 2026-10-02

- [x] External Voice core and Hutch adapter remain on `adapter_buildation`; the complete local suite passed: 34 tests.
- [x] Fake-runtime regressions cover fragmented finalized input, multiple model turns, interruption, sensitive audio grounding, response-scoped playback/proposal acknowledgements, malformed controls, bounded end-session and provider/task cleanup.
- [x] Adapter retains event/turn IDs for retryable pending callbacks and bounds retries to an 18-second total budget.
- [x] Commit `3f33b03` was pushed and remotely confirmed on `adapter_buildation`; Resolve counterpart `a92bea5` was pushed on `ResolveDev` (with follow-up `02ce2f6`).
- [ ] Jayith must implement browser protocol `zeptaz-hutch-v2`, drain audio before `playback_complete`, then send `proposal_presented` only after accepted playback acknowledgement; discard queued audio on interruption.
- [ ] Qualify a real browser/microphone/Gemini session through Tevin's mounted Resolve controller, including confirmation, decline and text continuation. No live qualification claim is made from fake tests.

The prior 15-test baseline and ephemeral streaming failure are superseded by the committed regression suite. Keep credentials server-side and audio recording disabled. The HTTP Voice turn/event shapes remain strict and unchanged; the browser WebSocket protocol changed to v2 and requires a coordinated frontend update.

## Package confirmation integration — 2026-10-03

- [x] The Hutch adapter accepts `ACTIVATE_PACKAGE` proposals only with typed `package_terms` (`name`, `price_minor`, `currency`, `data_bytes`, `validity_seconds`, `recurring=false`) and rejects mismatched action/terms combinations.
- [x] Voice system instructions require reciting those exact terms. Confirmation still requires the response-scoped acknowledged proposal and a fresh affirmative final transcript; tool/model output cannot authorize an action.
- [x] Voice suite: 49 passed; `git diff --check` passed.
- [x] Resolve v1.1 package counterpart is committed and pushed as `hutch_resolve/ResolveDev` commit `d5c881a`.
- [ ] Disposable PostgreSQL now verifies migration/reset, concurrent same-offer confirmation, one debit/subscription/provider operation and Trust Receipt. Package-specific crash/lost-response and injected-provider-failure recovery still need qualification; the Resolve package feature flag remains false by default.
