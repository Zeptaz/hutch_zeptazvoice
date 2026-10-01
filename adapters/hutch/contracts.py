from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SessionRequest(StrictModel):
    binding_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=128)
    voice_session_id: str = Field(min_length=1, max_length=128)
    account_id: str = Field(min_length=1, max_length=128)
    origin: str = Field(min_length=8, max_length=255)
    expires_at: int


class VoiceTurnRequest(StrictModel):
    binding_id: str
    voice_session_id: str
    event_id: str
    turn_id: str
    transcript: str = Field(min_length=1, max_length=4000)
    language: Literal["en", "si", "ta"]
    is_final: Literal[True]
    presented_proposal_id: str | None = None
    presented_proposal_hash: str | None = None


class Proposal(StrictModel):
    id: str
    proposal_hash: str
    action_type: Literal["DEACTIVATE_VAS", "SEND_SETTINGS_INSTRUCTIONS", "CREATE_REVIEW_TICKET"]
    target_label: str
    consequences: str
    expires_at: str


class VoiceTurnResponse(StrictModel):
    response_id: str
    case_id: str | None = None
    reply_text: str
    speech_text: str
    pending_question: str | None = None
    proposal: Proposal | None = None
    operation_status: str | None = None
    end_session: bool = False


class VoiceEventRequest(StrictModel):
    binding_id: str
    voice_session_id: str
    event_id: str
    event_type: Literal["connected", "disconnected", "error", "usage"]
    details: dict = Field(default_factory=dict)
