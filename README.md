# Zeptaz Voice for HUTCH Resolve

**The voice channel for HUTCH Resolve: a caller speaks in English, Sinhala or Tamil, and Resolve answers through the same engine as the text chat.**

| | |
| --- | --- |
| Product | HUTCH Resolve (voice service) |
| Team | Zeptaz · IIT · 23SEP |
| Event / track | IgnitX 2026 · Track A: Resolve & Support |
| Release | `v1.0.1` in both repositories (tagged 4 October 2026) |
| Live demo | https://resolve.zeptaz.com (sign in on `/chat`, then start a call) |
| Demo video | [Google Drive](https://drive.google.com/drive/folders/1W-2ktmFYeZHLNYF7e67zjlPjy3Sb8_l3?usp=sharing) |
| Main repository | [`Zeptaz/hutch_resolve`](https://github.com/Zeptaz/hutch_resolve): Resolve backend, chat, agent dashboard and the full project README |

> All data is **synthetic**. No HUTCH system, real customer record or call recording is used. Audio is streamed and never stored.

## What this service does

The browser streams microphone audio to this service over a WebSocket. The service runs a Google Gemini Live session for speech in and speech out. When the caller finishes a turn, the service sends the final transcript to Resolve, signed with HMAC-SHA256. Resolve decides everything: account scope, evidence, calculations, proposals and confirmations. The service speaks Resolve's grounded reply back to the caller.

```text
Browser (mic + speaker) ──PCM16 WebSocket + single-use grant──▶ Zeptaz Voice ◀──▶ Gemini Live
                                                                    │
                                                    HMAC-signed final transcript
                                                                    ▼
                                                     HUTCH Resolve (/api/v1/integrations/voice/*)
```

- **Same engine as text:** a spoken complaint goes through the same Resolve conversation service as a typed one.
- **Consent stays in Resolve:** a spoken "yes" counts only with the final transcript, the latest presented proposal and a valid signed binding. Ambiguous answers cannot accept an action.
- **Trilingual recognition:** speech-recognition hints for Sinhala, English and Tamil (`GEMINI_LIVE_INPUT_LANGUAGES`).
- **Safe without AI:** if the Gemini key is missing or Gemini is unavailable, the service returns a safe "unavailable" response and the text chat keeps working.

## Technology

| Layer | Technology |
| --- | --- |
| Runtime | Python 3.12+, FastAPI, Uvicorn, Pydantic 2, httpx, NumPy |
| AI | Google Gemini Live via `google-genai` 2.19.0, model `models/gemini-3.1-flash-live-preview` |
| Audio | PCM16 mono, 16 kHz in and 24 kHz out, over WebSocket |
| Protocol | `zeptaz-hutch-v3` (v2 still accepted); HMAC-SHA256 to Resolve |
| Storage | SQLite for short-lived browser grants and bindings (one Voice worker) |
| Testing | pytest, pytest-asyncio |

Versions are pinned in `requirements.txt` and `requirements-dev.txt`.

## Repository map

| Path | Contents |
| --- | --- |
| `app.py` | FastAPI app: health check, session creation, WebSocket call loop, Gemini Live session handling |
| `adapters/hutch/` | Resolve contracts, signed client, HMAC security, grant store and turn forwarding |
| `core/audio/` | Audio codec helpers |
| `core/services/language_policy.py` | English, Sinhala and Tamil language policy |
| `core/system/voice_runtime_config.py` | Validated runtime configuration |
| `docs/architecture.md` | Architecture and design limits |
| `docs/hutch-resolve-contract.md` | Wire contract with Resolve: endpoints, signing and failure behaviour |
| `tests/` | Unit tests with a fake Gemini Live and fake Resolve |

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/healthz` | Liveness |
| POST | `/api/hutch/sessions` | Resolve creates a voice session (HMAC-signed); returns a single-use browser grant |
| WebSocket | `/ws/hutch/{session_id}` | Browser audio stream; needs the grant and an allowed Origin |

## Setup

**Requirements:** Python 3.12+, a Google Gemini API key with Gemini Live access, and a running Resolve backend (see the [main repository](https://github.com/Zeptaz/hutch_resolve)).

```sh
cp .env.example .env
python -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

Edit `.env`:

| Variable | Purpose |
| --- | --- |
| `GEMINI_API_KEY` | Gemini key (required for live speech) |
| `GEMINI_LIVE_MODEL` | Default `models/gemini-3.1-flash-live-preview` |
| `GEMINI_LIVE_INPUT_LANGUAGES` | Recognition hints, default `si-LK,en-US,ta-IN` |
| `HUTCH_RESOLVE_BASE_URL` | Resolve API, e.g. `http://localhost:8080/api/v1` |
| `HUTCH_RESOLVE_HMAC_SECRET` | Must equal Resolve's `VOICE_HMAC_SECRET` |
| `HUTCH_VOICE_ALLOWED_ORIGINS` | Browser origins allowed to open calls, e.g. `http://localhost:5173` |
| `HUTCH_VOICE_GRANT_SECRET` | Random secret for browser grants (different from the HMAC secret) |
| `HUTCH_VOICE_MAX_SESSION_SECONDS` | Call cap, default 120 |
| `HUTCH_VOICE_MAX_AUDIO_BYTES` | Input audio cap, default 3,840,000 |

## Run and test

```sh
python -m uvicorn app:app --host 127.0.0.1 --port 8088   # run the service
python -m pytest -q                                      # 75 tests at v1.0.1; no Gemini or Resolve calls
```

Then set Resolve's `VOICE_BASE_URL=http://localhost:8088` and `VOICE_HMAC_SECRET`, open the Resolve chat, sign in with a demo customer and press the call button.

## Security and privacy

- Server-to-server calls use timestamped HMAC-SHA256 over the exact request bytes, with a 60-second clock-skew window.
- Browser grants are short-lived, single-use and bound to the exact allowed Origin and voice session.
- Audio is streamed and never recorded; logs exclude transcripts and provider payloads.
- Secrets live only in `.env` (ignored by Git) or the hosting provider's environment variables.

## Known limitations

- Tested with a fake Gemini Live and with a live probe using real Gemini Live and synthesized speech (9/9 turns spoken); a physical microphone call has not been verified.
- Designed for one Voice worker: grants and live sessions are local. A multi-worker deployment needs a shared store.
- Calls are capped at 120 seconds; spoken numbers can be misheard, so Resolve confirms critical values before any action.
- Sinhala and Tamil recognition and replies have not been reviewed by a fluent speaker.

## Third-party components and AI disclosure

| Component | Use | Licence |
| --- | --- | --- |
| FastAPI, Uvicorn, Pydantic, httpx, NumPy, python-dotenv | Runtime | Open source (see each package) |
| `google-genai` | Gemini SDK | Apache 2.0 |
| Google Gemini Live API | Hosted speech model | Google terms of service |
| pytest, pytest-asyncio | Testing | Open source |

The Zeptaz Voice core (audio codec, live configuration and language policy) is **pre-existing team IP**, disclosed as such. The HUTCH session API, adapter, signed Resolve client and call flow were built for this hackathon.

AI coding assistants used by the team: Naveen Harry: OpenAI Codex; Tevin Silverster: Claude (Anthropic); Jayith Wijethunge: Claude Code (Anthropic); Ojith Adithya: Claude (Anthropic). All AI-generated code was reviewed and tested by the team.

## Team Zeptaz

| Member | Role |
| --- | --- |
| Naveen Harry | Backend and integration, including this Voice adapter |
| Tevin Silverster | Conversation and AI |
| Jayith Wijethunge | Frontend and hosting |
| Ojith Adithya | Documentation and presentation |
