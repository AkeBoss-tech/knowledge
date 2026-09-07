"""Trusted local company incident-response domain extension.

The extension is a read-only adapter over procedural memory. It neither
reviews nor activates procedures and receives its live authority from the
embedding caller, just like the robotics world-memory extension.
"""
from __future__ import annotations

from krail.provider.v1 import ResourceRef
from rail.authorized_context import HostedAccessContextAuthorizer
from rail.core_provenance import (
    ProcedureActionableGuidanceResult,
    ProcedureExplanation,
    ProcedureExplanationRequest,
    ProcedureExplanationService,
    procedure_record_ref,
)
from rail.extension_registry import (
    DomainExtensionRegistry,
    ExtensionDescriptor,
    HANDLER_LINEAGE_REFS,
    describe_extension,
    describe_operator,
)


COMPANY_INCIDENT_OPERATOR_ID = "company.incident-response.guidance"
COMPANY_INCIDENT_OPERATOR_VERSION = "1.0.0"
PROCEDURE_REQUEST_SCHEMA = "krail.procedure-explanation-request.v1"
PROCEDURE_GUIDANCE_RESULT_SCHEMA = (
    "krail.procedure-actionable-guidance-result.v1"
)


def company_incident_response_extension() -> ExtensionDescriptor:
    operator = describe_operator(
        operator_id=COMPANY_INCIDENT_OPERATOR_ID,
        version=COMPANY_INCIDENT_OPERATOR_VERSION,
        input_schema=PROCEDURE_REQUEST_SCHEMA,
        output_schema=PROCEDURE_GUIDANCE_RESULT_SCHEMA,
        deterministic=False,
    )
    return describe_extension(
        extension_id="company.incident-response",
        version="1.0.0",
        payload_schemas=(
            PROCEDURE_REQUEST_SCHEMA,
            PROCEDURE_GUIDANCE_RESULT_SCHEMA,
        ),
        operators=(operator,),
    )


def _explanation_refs(explanation: ProcedureExplanation) -> tuple[ResourceRef, ...]:
    records = (explanation.candidate,) + (
        (explanation.reviewed,) if explanation.reviewed is not None else ()
    )
    return tuple(
        dict.fromkeys(
            (
                *(procedure_record_ref(record) for record in records),
                *explanation.package_refs,
                *explanation.command_refs,
                *explanation.environment_refs,
                *explanation.evidence_refs,
                *explanation.dependency_refs,
                *(decision.reviewer_ref for decision in explanation.decisions),
                *explanation.decision_refs,
                *explanation.invalidation_refs,
            )
        )
    )


def register_company_incident_response_extension(
    registry: DomainExtensionRegistry,
    service: ProcedureExplanationService,
    reader: HostedAccessContextAuthorizer,
) -> ExtensionDescriptor:
    """Register the discoverable read operator with caller-owned authority."""

    if not isinstance(reader, HostedAccessContextAuthorizer):
        raise TypeError("company incident response requires a signed live reader")
    descriptor = company_incident_response_extension()
    operator = descriptor.operators[0]

    def verify_operator_authority() -> None:
        try:
            claims = reader.authority.verify(
                reader.context, as_of=reader.clock()
            )
        except PermissionError as exc:
            raise PermissionError("company incident response access denied") from exc
        if (
            (claims.tenant_id, claims.project_id)
            != (service.tenant_id, service.project_id)
            or claims.capability_id != operator.operator_id
            or claims.capability_version != operator.version
            or claims.capability_digest != operator.descriptor_digest
            or "context.read" not in claims.actions
        ):
            raise PermissionError("company incident response access denied")

    def guidance(inputs, _config):
        verify_operator_authority()
        if len(inputs) != 1:
            raise ValueError("incident guidance requires one procedure request")
        request = ProcedureExplanationRequest.model_validate(inputs[0])
        actionable = service.actionable_guidance(request, authorizer=reader)
        # Re-read after the actionable decision. This is the provenance basis
        # exported for both guidance and abstention, so an invalidation that
        # lands between the initial internal explanation and the decision is
        # represented rather than silently inheriting an older snapshot.
        explanation = service.explain(request, authorizer=reader)
        if actionable is not None and (
            explanation.review_status != "accepted"
            or explanation.support_status != "current"
            or explanation.reviewed is None
            or explanation.reviewed.record_digest != actionable.reviewed_digest
        ):
            actionable = None
        if actionable is None:
            result = ProcedureActionableGuidanceResult(
                status="abstained", abstention_reason="not-current"
            )
        else:
            result = ProcedureActionableGuidanceResult(
                status="guidance", guidance=actionable
            )
        projection_ref = (
            service.projection_state_ref(actionable.projection_state_digest)
            if actionable is not None
            and actionable.projection_state_digest is not None
            else None
        )
        if (
            actionable is not None
            and actionable.projection_state_digest is not None
            and projection_ref is None
        ):
            raise ValueError("actionable projection lineage is unavailable")
        lineage_refs = (
            *_explanation_refs(explanation),
            *((projection_ref,) if projection_ref is not None else ()),
        )
        try:
            for ref in lineage_refs:
                reader.authorize(ref)
        except PermissionError as exc:
            raise PermissionError("company incident response access denied") from exc
        verify_operator_authority()
        return {
            **result.model_dump(mode="json"),
            HANDLER_LINEAGE_REFS: lineage_refs,
        }

    registry.register(descriptor, {COMPANY_INCIDENT_OPERATOR_ID: guidance})
    return descriptor


__all__ = [
    "COMPANY_INCIDENT_OPERATOR_ID",
    "COMPANY_INCIDENT_OPERATOR_VERSION",
    "company_incident_response_extension",
    "register_company_incident_response_extension",
]
