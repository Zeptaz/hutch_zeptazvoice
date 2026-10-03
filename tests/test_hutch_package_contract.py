from __future__ import annotations

import pytest
from pydantic import ValidationError

from adapters.hutch.adapter import HutchAdapter
from adapters.hutch.client import ResolveClientError
from adapters.hutch.contracts import Proposal, VoiceTurnResponse


TERMS = {
    "name": "Data Max 30",
    "price_minor": 12500,
    "currency": "LKR",
    "data_bytes": 10_000_000_000,
    "validity_seconds": 2_592_000,
    "recurring": False,
}


def package_proposal(**overrides) -> dict:
    return {
        "id": "package-proposal-1",
        "proposal_hash": "a" * 64,
        "action_type": "ACTIVATE_PACKAGE",
        "target_label": "Synthetic SIM-01",
        "consequences": "Data Max 30 costs LKR 125.00, includes 10 GB for 30 days, and does not renew.",
        "expires_at": "2030-01-01T00:00:00Z",
        "package_terms": TERMS,
        **overrides,
    }


def test_package_proposal_requires_complete_typed_terms():
    parsed = Proposal.model_validate(package_proposal())
    assert parsed.package_terms is not None
    assert parsed.package_terms.model_dump() == TERMS
    assert Proposal.model_validate({**package_proposal(), "action_type": "DEACTIVATE_VAS",
                                    "package_terms": None}).package_terms is None

    missing = package_proposal()
    del missing["package_terms"]
    with pytest.raises(ValidationError):
        Proposal.model_validate(missing)

    with pytest.raises(ValidationError):
        Proposal.model_validate(package_proposal(action_type="DEACTIVATE_VAS"))

    incomplete_terms = TERMS.copy()
    del incomplete_terms["validity_seconds"]
    with pytest.raises(ValidationError):
        Proposal.model_validate(package_proposal(package_terms=incomplete_terms))

    extended_terms = {**TERMS, "duration_days": 30}
    with pytest.raises(ValidationError):
        Proposal.model_validate(package_proposal(package_terms=extended_terms))


@pytest.mark.parametrize("field,value", [
    ("name", ""),
    ("name", 22),
    ("price_minor", -1),
    ("price_minor", True),
    ("price_minor", 1.5),
    ("price_minor", 9_007_199_254_740_992),
    ("currency", "USD"),
    ("data_bytes", 0),
    ("data_bytes", 1.0),
    ("validity_seconds", -1),
    ("validity_seconds", False),
    ("recurring", True),
])
def test_package_proposal_rejects_invalid_or_unsafe_terms(field, value):
    invalid_terms = {**TERMS, field: value}
    with pytest.raises(ValidationError):
        Proposal.model_validate(package_proposal(package_terms=invalid_terms))


def test_voice_forwards_exact_affirmative_transcript_only_with_ack_and_keeps_retry_identity():
    class StubResolve:
        def __init__(self):
            self.requests = []
            self.responses = [
                VoiceTurnResponse(
                    response_id="response-package",
                    reply_text="Review the package terms.",
                    speech_text="Review the package terms.",
                    proposal=package_proposal(),
                ),
                ResolveClientError("resolve_unavailable", retryable=True),
                VoiceTurnResponse(
                    response_id="response-accepted",
                    reply_text="The request is pending.",
                    speech_text="The request is pending.",
                    operation_status="PENDING",
                ),
            ]

        async def send_turn(self, request):
            self.requests.append(request.model_dump(mode="json"))
            result = self.responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

    async def run():
        client = StubResolve()
        adapter = HutchAdapter(client)
        await adapter.forward_final_turn(binding_id="binding", voice_session_id="voice",
                                         transcript="Please show the package", language="en")
        assert adapter.mark_proposal_presented("package-proposal-1", "a" * 64)

        with pytest.raises(ResolveClientError):
            await adapter.forward_final_turn(binding_id="binding", voice_session_id="voice",
                                             transcript="yes", language="en")
        with pytest.raises(ResolveClientError) as changed:
            await adapter.forward_final_turn(binding_id="binding", voice_session_id="voice",
                                             transcript="yes, activate it", language="en")
        assert changed.value.code == "resolve_turn_pending"

        completed = await adapter.forward_final_turn(binding_id="binding", voice_session_id="voice",
                                                     transcript="yes", language="en")
        assert completed.operation_status == "PENDING"
        first_retry, exact_retry = client.requests[1:]
        assert first_retry == exact_retry
        assert first_retry["transcript"] == "yes"
        assert first_retry["presented_proposal_id"] == "package-proposal-1"
        assert first_retry["presented_proposal_hash"] == "a" * 64

    import asyncio
    asyncio.run(run())


def test_negative_ambiguous_and_stale_proposal_ack_are_forwarded_without_confirmation_provenance():
    class StubResolve:
        def __init__(self):
            self.requests = []
            self.responses = [
                VoiceTurnResponse(response_id="r1", reply_text="Review terms.", speech_text="Review terms.",
                                  proposal=package_proposal()),
                VoiceTurnResponse(response_id="r2", reply_text="Do you want details?", speech_text="Do you want details?",
                                  proposal=package_proposal(id="package-proposal-2", proposal_hash="b" * 64)),
                VoiceTurnResponse(response_id="r3", reply_text="No action was taken.", speech_text="No action was taken."),
                VoiceTurnResponse(response_id="r4", reply_text="No action was taken.", speech_text="No action was taken."),
            ]

        async def send_turn(self, request):
            self.requests.append(request)
            return self.responses.pop(0)

    async def run():
        client = StubResolve()
        adapter = HutchAdapter(client)
        await adapter.forward_final_turn(binding_id="b", voice_session_id="s", transcript="show a package", language="en")
        assert adapter.mark_proposal_presented("package-proposal-1", "a" * 64)
        await adapter.forward_final_turn(binding_id="b", voice_session_id="s", transcript="maybe later", language="en")
        assert adapter.mark_proposal_presented("package-proposal-1", "a" * 64) is False
        assert adapter.mark_proposal_presented("package-proposal-2", "b" * 64)

        # Simulate runtime revocation on interruption; the next transcript is ordinary speech.
        adapter.presented_proposal = None
        await adapter.forward_final_turn(binding_id="b", voice_session_id="s", transcript="No, thanks", language="en")
        assert client.requests[2].transcript == "No, thanks"
        assert client.requests[2].presented_proposal_id is None
        assert client.requests[2].presented_proposal_hash is None

        # A stale acknowledgement for the prior proposal cannot restore eligibility.
        assert adapter.mark_proposal_presented("package-proposal-2", "b" * 64) is False
        await adapter.forward_final_turn(binding_id="b", voice_session_id="s", transcript="yes", language="en")
        assert client.requests[3].transcript == "yes"
        assert client.requests[3].presented_proposal_id is None
        assert client.requests[3].presented_proposal_hash is None

    import asyncio
    asyncio.run(run())
