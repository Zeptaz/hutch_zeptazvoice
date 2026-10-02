# Hutch Zeptaz Voice adapter

Voice-side implementation for the HUTCH Resolve hackathon. Runtime path:

```text
Zeptaz Voice Core → Hutch Adapter → expected Hutch Resolve API
```

Resolve is not implemented in this repository. See [`docs/hutch-resolve-contract.md`](docs/hutch-resolve-contract.md) for server contracts, authentication, and failure behavior.

## Team plan and qualification

Read [context.md](context.md) before implementation. The existing 15 tests pass, but additional inspection reproduced transcript-finalization and single-turn session failures. Voice remains partially implemented until the runtime and live integration gates in Harry's plan pass.

## Local start

Python 3.12+ is recommended. Copy `.env.example` to `.env`, supply `GEMINI_API_KEY`, and configure HMAC secrets. Start:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8088
```

Only `GET /healthz`, authenticated `POST /api/hutch/sessions`, authenticated lifecycle callbacks, and protected `/ws/hutch/{session_id}` are exposed. A Resolve implementation or stub endpoint must be running at `HUTCH_RESOLVE_BASE_URL` for caller turns. Without Gemini credentials, voice starts with a safe unavailable response; Resolve text remains independent.

## Security and data handling

Server-to-server calls use timestamped HMAC-SHA256 with 60-second clock skew and SHA-256 of exact request bytes. Browser grants are short lived, bound to the exact configured Origin and Voice session, persisted locally and atomically single use. Secrets stay in environment configuration. `.env`, SQLite grants, logs, Python caches and build output are ignored. Audio is streamed without recording; logs exclude transcripts and provider payloads.

Run tests with `python -m pytest -q`. Gemini Live and Resolve production systems are not contacted by unit tests.
