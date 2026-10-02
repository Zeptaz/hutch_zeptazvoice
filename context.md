# HUTCH Voice integration context

The canonical three-person plan is [hutch_resolve/context.md](https://github.com/Zeptaz/hutch_resolve/blob/main/context.md). Harry owns this repository's integration/testing and the Resolve backend. Jayith owns browser audio/customer/dashboard UI; Tevin owns the conversation module inside Resolve.

Read the [Harry plan](https://github.com/Zeptaz/hutch_resolve/blob/main/docs/plans/harry.md), [shared contracts](https://github.com/Zeptaz/hutch_resolve/blob/main/docs/contracts.md), and [existing Voice wire contract](docs/hutch-resolve-contract.md) before work. After meaningful progress, update this file plus canonical context and Harry's plan with verification and remaining work. Mark complete only after implementation and verification; do not duplicate the combined plan here.

Architecture remains external Zeptaz Voice Core -> Hutch Adapter -> Resolve. Resolve owns identity, evidence, investigation, proposals, confirmation policy, actions, cases, receipts and dashboard data. Voice forwards final caller input and renders Resolve results; it cannot mutate telecom providers directly.

## Verified baseline: 2026-10-02

- [x] Correct Git remote and clean main baseline verified at `a7f723c663fe05090851eaaa8e97ff80c770d52e`.
- [x] Existing `python -m pytest -q`: 15 passed in 3.53 seconds.
- [x] Additional ephemeral fake SDK/WebSocket reproduction: separate transcription/tool/turn-complete events produced `no_finalized_caller_turn`, zero Resolve calls, then disconnect. No source edits or network were used.
- [ ] H-08 Add regression tests and fix finalization and multi-turn receive lifecycle.
- [ ] H-08 Verify interruption, grounded output, presentation acknowledgement, timeout/task cleanup, end-session and retry identity.
- [ ] H-07/H-08 Integrate actual Resolve handlers and prove call-to-text continuity.
- [ ] H-08 Run a live microphone journey and record actual model/profile, commit and outcomes.

The installed SDK's `AsyncSession.receive()` ends at an assistant turn boundary; app.py currently calls it once and closes on FIRST_COMPLETED. Input transcript finalization also incorrectly depends on transcription text and turn_complete appearing in the same event. The 15-test suite does not cover these runtime paths. Voice is **partially implemented, not release-qualified**.

No credentials, live provider quota or microphone journey were verified in this documentation task. Keep audio recording disabled and credentials server-side. Contract compatibility must be reviewed in both repositories before changes. Runtime improvements are upcoming implementation work; this planning commit changes no application features.

Documentation verification: Resolve's published specification matches all four existing Voice request/response/session schemas and the nested proposal schema; three concrete turn/response/event examples parse through the actual Voice Pydantic models. The proposed `interrupted` browser event is additive and remains unimplemented. Repository AGENTS.md now requires context/owner-plan updates after meaningful progress.
