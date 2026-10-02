# HUTCH Resolve Voice frontend

Standalone customer voice call page for HUTCH Resolve: a dark "Talk to Resolve" page with one call panel
(status line, microphone orb, live captions, the offer to confirm and the action's progress). It is a
separate UI from the Resolve text chat but keeps its brand colour, fonts, languages and confirmation rules,
and it implements the browser side of Voice protocol `zeptaz-hutch-v2`.

```text
browser ──/api (proxy)──▶ Resolve :8080   sign-in, conversation, voice grant, cards, operations, receipts
browser ──WebSocket────▶ Voice   :8088   PCM16 16 kHz up, 24 kHz down, control messages
```

## Run

```sh
npm ci
npm run dev        # http://localhost:5174
```

`VITE_API_MODE=mock` (default) runs everything in the browser: a scripted Resolve and Voice, no microphone.
During a mock call, tap a "Try saying" line to speak it; replies play a tone. To use real services, create `.env.local` with
`VITE_API_MODE=live`, start Resolve on 8080 and Voice on 8088, and add `http://localhost:5174` to Voice's
`HUTCH_VOICE_ALLOWED_ORIGINS` and to Resolve's allowed customer origins.

## Call rules (docs/hutch-resolve-contract.md)

- Sign in with a demo line (CUSTOMER session); a guest has no line to investigate.
- Microphone first, then `POST /conversations/{id}/voice-sessions` for a single-use grant (about 60 s).
  The grant goes in the `hutch-grant.{token}` subprotocol, never in the URL.
- `playback_complete` only after a reply's audio has fully played; `proposal_presented` only after an
  accepted `playback_ack`. Nothing is acknowledged for an `audio_fallback` or interrupted reply.
- `interrupted` discards queued audio and pending acknowledgements; the offer must be read again.
- The browser never accepts an offer on its own. A spoken yes is judged by Resolve. When an offer can't be
  confirmed by voice (failed speech check, interruption, call ended) the panel shows explicit Yes/No
  buttons, sent to Resolve as an `action_decision` turn. Nothing is preselected.
- Calls stop at 2 minutes. Each session keeps one Resolve conversation, so calling again continues the same case.

## Contract

`contracts/` holds a copy of Resolve's OpenAPI and examples (see `contracts/README.md`); `npm run gen:api`
regenerates `src/api/schema.d.ts`. Sinhala and Tamil UI text in `src/i18n/messages.ts` are unreviewed drafts.
