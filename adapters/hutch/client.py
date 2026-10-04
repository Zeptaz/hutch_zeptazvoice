from __future__ import annotations

import asyncio
import json
import os
import time
from uuid import uuid4

import httpx

from .contracts import VoiceDecisionReplyRequest, VoiceTurnRequest, VoiceTurnResponse
from .security import canonical_json, signed_headers


class ResolveClientError(RuntimeError):
    def __init__(self, code: str, retryable: bool = False):
        super().__init__(code)
        self.code, self.retryable = code, retryable


class HutchResolveClient:
    """Voice-side client for the future Resolve turn and lifecycle contracts."""

    def __init__(self, *, base_url: str | None = None, secret: str | None = None, client: httpx.AsyncClient | None = None):
        self.base_url = (base_url or os.getenv("HUTCH_RESOLVE_BASE_URL", "")).rstrip("/")
        self.secret = secret or os.getenv("HUTCH_RESOLVE_HMAC_SECRET", "")
        if not self.base_url or not self.secret:
            raise ValueError("HUTCH_RESOLVE_BASE_URL and HUTCH_RESOLVE_HMAC_SECRET are required")
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(float(os.getenv("HUTCH_RESOLVE_READ_TIMEOUT_SECONDS", "8")),
                                  connect=float(os.getenv("HUTCH_RESOLVE_CONNECT_TIMEOUT_SECONDS", "2"))),
            follow_redirects=False,
        )
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def send_turn(self, turn: VoiceTurnRequest) -> VoiceTurnResponse:
        body = canonical_json(turn.model_dump())
        event_id = turn.event_id
        url = f"{self.base_url}/integrations/voice/turns"
        deadline = time.monotonic() + 18.0
        for attempt in range(2):
            headers = signed_headers(self.secret, event_id, body)
            try:
                response = await asyncio.wait_for(
                    self._client.post(url, content=body, headers=headers),
                    timeout=max(0.01, deadline - time.monotonic()),
                )
            except (asyncio.TimeoutError, httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt == 0:
                    await asyncio.sleep(0.15)
                    continue
                raise ResolveClientError("resolve_unavailable", retryable=True) from exc
            if response.status_code >= 500:
                if attempt == 0:
                    await asyncio.sleep(0.15)
                    continue
                raise ResolveClientError("resolve_unavailable", retryable=True)
            if response.status_code in (401, 403):
                raise ResolveClientError("resolve_auth_failed")
            if response.status_code == 409:
                try:
                    error = response.json().get("error", {})
                except (ValueError, TypeError, AttributeError):
                    error = {}
                code = error.get("code") if isinstance(error, dict) else None
                if code in {"TURN_IN_PROGRESS", "CONVERSATION_BUSY"} and error.get("retryable") is True:
                    if attempt == 0:
                        await asyncio.sleep(0.15)
                        continue
                    raise ResolveClientError("resolve_turn_pending", retryable=True)
                raise ResolveClientError("resolve_event_conflict")
            if response.status_code >= 400:
                raise ResolveClientError("resolve_rejected")
            try:
                return VoiceTurnResponse.model_validate(response.json())
            except (ValueError, TypeError) as exc:
                raise ResolveClientError("resolve_invalid_response") from exc
        raise ResolveClientError("resolve_unavailable", retryable=True)

    async def decision_reply(self, request: VoiceDecisionReplyRequest) -> VoiceTurnResponse | None:
        """Resolve's reply to a decision tapped on screen; None when there is none (404). Read-only, one try."""
        body = canonical_json(request.model_dump())
        headers = signed_headers(self.secret, request.event_id, body)
        try:
            response = await self._client.post(f"{self.base_url}/integrations/voice/decision-replies",
                                               content=body, headers=headers)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ResolveClientError("resolve_unavailable", retryable=True) from exc
        if response.status_code == 404:
            return None
        if response.status_code in (401, 403):
            raise ResolveClientError("resolve_auth_failed")
        if response.status_code >= 400:
            raise ResolveClientError("resolve_rejected", retryable=response.status_code >= 500)
        try:
            return VoiceTurnResponse.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise ResolveClientError("resolve_invalid_response") from exc

    async def send_event(self, payload: dict) -> None:
        body = canonical_json(payload)
        event_id = payload.get("event_id") or str(uuid4())
        headers = signed_headers(self.secret, event_id, body)
        try:
            response = await self._client.post(f"{self.base_url}/integrations/voice/events", content=body, headers=headers)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ResolveClientError("resolve_unavailable", retryable=True) from exc
        if response.status_code >= 400:
            raise ResolveClientError("resolve_event_rejected", retryable=response.status_code >= 500)
