from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class PackageTerms(StrictModel):
    name: str = Field(strict=True, min_length=1, max_length=120)
    price_minor: int = Field(strict=True, ge=0, le=9_007_199_254_740_991)
    currency: Literal["LKR"]
    data_bytes: int = Field(strict=True, gt=0, le=9_007_199_254_740_991)
    validity_seconds: int = Field(strict=True, gt=0, le=9_007_199_254_740_991)
    recurring: Literal[False]


class Proposal(StrictModel):
    id: str
    proposal_hash: str
    action_type: Literal["DEACTIVATE_VAS", "SEND_SETTINGS_INSTRUCTIONS", "CREATE_REVIEW_TICKET", "ACTIVATE_PACKAGE"]
    target_label: str
    consequences: str
    expires_at: str
    package_terms: PackageTerms | None = None

    @model_validator(mode="after")
    def validate_package_terms(self) -> "Proposal":
        if self.action_type == "ACTIVATE_PACKAGE" and self.package_terms is None:
            raise ValueError("ACTIVATE_PACKAGE proposals require package_terms")
        if self.action_type != "ACTIVATE_PACKAGE" and self.package_terms is not None:
            raise ValueError("package_terms are only valid for ACTIVATE_PACKAGE proposals")
        return self


class VoiceTurnResponse(StrictModel):
    response_id: str
    case_id: str | None = None
    reply_text: str
    speech_text: str
    pending_question: str | None = None
    proposal: Proposal | None = None
    operation_status: Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "UNKNOWN", "REVIEW_REQUIRED"] | None = None
    end_session: bool = False


class VoiceDecisionReplyRequest(StrictModel):
    """Ask Resolve for its reply to a decision the caller tapped on screen (read-only)."""

    binding_id: str
    voice_session_id: str
    event_id: str
    proposal_id: str


class VoiceEventRequest(StrictModel):
    binding_id: str
    voice_session_id: str
    event_id: str
    event_type: Literal["connected", "disconnected", "error", "usage"]
    details: dict = Field(default_factory=dict)
