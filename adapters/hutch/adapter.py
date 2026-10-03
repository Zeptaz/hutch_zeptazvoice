from __future__ import annotations

from uuid import uuid4

from .client import HutchResolveClient, ResolveClientError
from .contracts import VoiceTurnRequest, VoiceTurnResponse

VOICE_TOOLS = [{
    "name": "forward_final_turn_to_hutch_resolve",
    "description": "Forward the caller's complete finalized turn to Hutch Resolve. Never perform account changes in Voice.",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}]

SYSTEM_INSTRUCTION = """You are the voice channel for Hutch Resolve. Speak English, Sinhala, or Tamil as the caller does. Ask one clear question at a time. Never invent account details, diagnoses, charges, or action outcomes. For every caller turn, call forward_final_turn_to_hutch_resolve exactly once and wait for its returned reply. Never call Hutch systems directly. Resolve is the authority for account identity, evidence, action eligibility, confirmation, execution, and human escalation. Read proposals and consequences exactly enough for the caller to understand them. For ACTIVATE_PACKAGE, convey every supplied package_terms value and the consequences without rounding, omitting, or changing a term. A fresh clear affirmative response to the latest proposal is required; ambiguity must be sent to Resolve as ordinary speech and cannot be treated as confirmation. Never claim a change completed unless Resolve returned the actual completed operation status."""


class HutchAdapter:
    def __init__(self, client: HutchResolveClient):
        self.client = client
        self.pending_proposal: dict | None = None
        self.presented_proposal: dict | None = None
        self.latest_case_id: str | None = None
        self.pending_turn: VoiceTurnRequest | None = None

    def mark_proposal_presented(self, proposal_id: str, proposal_hash: str) -> bool:
        if not self.pending_proposal:
            return False
        if (proposal_id, proposal_hash) != (self.pending_proposal.get("id"), self.pending_proposal.get("proposal_hash")):
            return False
        self.presented_proposal = self.pending_proposal.copy()
        return True

    async def forward_final_turn(self, *, binding_id: str, voice_session_id: str,
                                 transcript: str, language: str) -> VoiceTurnResponse:
        if not transcript.strip() or len(transcript) > 4000:
            raise ValueError("final transcript is empty or exceeds 4000 characters")
        normalized_language = language if language in {"en", "si", "ta"} else "en"
        if self.pending_turn is not None:
            request = self.pending_turn
            if (request.transcript != transcript.strip() or request.language != normalized_language
                    or request.binding_id != binding_id or request.voice_session_id != voice_session_id):
                raise ResolveClientError("resolve_turn_pending", retryable=True)
        else:
            proposal = self.presented_proposal
            self.presented_proposal = None
            request = VoiceTurnRequest(
                binding_id=binding_id, voice_session_id=voice_session_id,
                event_id=str(uuid4()), turn_id=str(uuid4()), transcript=transcript.strip(),
                language=normalized_language, is_final=True,
                presented_proposal_id=proposal.get("id") if proposal else None,
                presented_proposal_hash=proposal.get("proposal_hash") if proposal else None,
            )
            self.pending_turn = request
        try:
            response = await self.client.send_turn(request)
        except ResolveClientError as exc:
            if not exc.retryable:
                self.pending_turn = None
            raise
        self.pending_turn = None
        self.pending_proposal = response.proposal.model_dump() if response.proposal else None
        self.latest_case_id = response.case_id or self.latest_case_id
        return response


class HutchVoiceTools:
    """Adapter-owned tool routing for finalized caller turns."""

    def __init__(self, adapter: HutchAdapter, binding_id: str, voice_session_id: str):
        self.adapter, self.binding_id, self.voice_session_id = adapter, binding_id, voice_session_id

    async def execute(self, name: str, *, transcript: str, language: str) -> dict:
        if name != "forward_final_turn_to_hutch_resolve":
            return {"error": "unsupported_tool"}
        try:
            result = await self.adapter.forward_final_turn(
                binding_id=self.binding_id, voice_session_id=self.voice_session_id,
                transcript=transcript, language=language,
            )
        except ResolveClientError as exc:
            return {"error": exc.code, "retryable": exc.retryable,
                    "message": "I cannot reach the support service right now. Please continue by text or try again shortly."}
        except ValueError:
            return {"error": "invalid_final_turn", "retryable": False}
        return result.model_dump()
