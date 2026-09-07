from hashlib import sha256

import pytest

from krail.provider.v1 import ResourceRef
from rail.extension_registry import (
    DomainExtensionRegistry,
    HANDLER_LINEAGE_REFS,
    describe_extension,
    describe_operator,
    verify_invocation_integrity,
)


def _ref(resource_id: str) -> ResourceRef:
    return ResourceRef(
        authority="https://knowledge.example.test",
        resource_type="evidence",
        resource_id=resource_id,
        version="v1",
        digest="sha256:" + sha256(resource_id.encode()).hexdigest(),
    )


class Allow:
    def authorize(self, ref: ResourceRef) -> None:
        return None


class Deny:
    def authorize(self, ref: ResourceRef) -> None:
        raise PermissionError("hidden source")


class RevokeAfterFirstCheck:
    def __init__(self):
        self.calls = 0

    def authorize(self, ref: ResourceRef) -> None:
        self.calls += 1
        if self.calls > 1:
            raise PermissionError("revoked")


class DenyResource:
    def __init__(self, resource_id: str) -> None:
        self.resource_id = resource_id

    def authorize(self, ref: ResourceRef) -> None:
        if ref.resource_id == self.resource_id:
            raise PermissionError("hidden source")


def _extension(extension_id: str, schema: str, operator_id: str):
    operator = describe_operator(
        operator_id=operator_id,
        version="1.0.0",
        input_schema=schema,
        output_schema=schema,
        deterministic=True,
    )
    return describe_extension(
        extension_id=extension_id,
        version="1.0.0",
        payload_schemas=(schema,),
        operators=(operator,),
    ), operator


def test_robotics_and_company_extensions_share_discovery_and_dispatch() -> None:
    registry = DomainExtensionRegistry()
    robotics, robot_operator = _extension(
        "robotics", "robotics.object_state_estimate.v1", "robotics.validate-pose"
    )
    company, company_operator = _extension(
        "company", "company.service_ownership.v1", "company.validate-owner"
    )
    registry.register(robotics, {robot_operator.operator_id: lambda inputs, config: {"valid": "pose" in inputs[0]}})
    registry.register(company, {company_operator.operator_id: lambda inputs, config: {"valid": "owner" in inputs[0]}})

    robot = registry.dispatch(robot_operator.operator_id, robot_operator.version, ((_ref("robot"), {"pose": [1, 2]}),), config={"frame": "map"}, authorizer=Allow())
    owner = registry.dispatch(company_operator.operator_id, company_operator.version, ((_ref("service"), {"owner": "team-a"}),), config={}, authorizer=Allow())
    assert robot.output == {"valid": True}
    assert owner.output == {"valid": True}
    assert robot.lineage_digest != owner.lineage_digest


def test_registry_rejects_duplicate_versions_and_schema_collisions() -> None:
    registry = DomainExtensionRegistry()
    first, operator = _extension("robotics", "robotics.pose.v1", "robotics.validate")
    registry.register(first, {operator.operator_id: lambda inputs, config: {}})
    with pytest.raises(ValueError, match="already registered"):
        registry.register(first, {operator.operator_id: lambda inputs, config: {}})
    collision, collision_operator = _extension("company", "robotics.pose.v1", "company.validate")
    with pytest.raises(ValueError, match="owned by another"):
        registry.register(collision, {collision_operator.operator_id: lambda inputs, config: {}})


def test_dispatch_requires_exact_operator_and_authorization_before_handler() -> None:
    registry = DomainExtensionRegistry()
    descriptor, operator = _extension("robotics", "robotics.pose.v1", "robotics.validate")
    calls = []
    registry.register(descriptor, {operator.operator_id: lambda inputs, config: calls.append(1) or {}})
    with pytest.raises(PermissionError, match="extension access denied"):
        registry.dispatch(operator.operator_id, operator.version, ((_ref("secret"), {}),), config={}, authorizer=Deny())
    assert calls == []
    with pytest.raises(LookupError, match="unknown operator"):
        registry.dispatch(operator.operator_id, "2.0.0", ((_ref("source"), {}),), config={}, authorizer=Allow())


def test_registry_does_not_accept_untrusted_install_or_handler_shape() -> None:
    registry = DomainExtensionRegistry()
    descriptor, operator = _extension("robotics", "robotics.pose.v1", "robotics.validate")
    untrusted = descriptor.model_copy(update={"trust": "third_party"})
    with pytest.raises(ValueError, match="trusted local"):
        registry.register(untrusted, {operator.operator_id: lambda inputs, config: {}})
    with pytest.raises(TypeError, match="callables"):
        registry.register(descriptor, {operator.operator_id: "eval('bad')"})


def test_dispatch_rechecks_authorization_after_handler_before_exposure() -> None:
    registry = DomainExtensionRegistry()
    descriptor, operator = _extension("robotics", "robotics.pose.v1", "robotics.validate")
    registry.register(descriptor, {operator.operator_id: lambda inputs, config: {"valid": True}})

    with pytest.raises(PermissionError, match="extension access denied"):
        registry.dispatch(
            operator.operator_id,
            operator.version,
            ((_ref("revoked-during-call"), {}),),
            config={},
            authorizer=RevokeAfterFirstCheck(),
        )


def test_invocation_lineage_exposes_output_digest_for_integrity_checks() -> None:
    registry = DomainExtensionRegistry()
    descriptor, operator = _extension("robotics", "robotics.pose.v1", "robotics.validate")
    registry.register(descriptor, {operator.operator_id: lambda inputs, config: {"valid": True}})
    result = registry.dispatch(
        operator.operator_id,
        operator.version,
        ((_ref("lineage"), {}),),
        config={},
        authorizer=Allow(),
    )
    assert result.output_digest.startswith("sha256:")
    verify_invocation_integrity(result, operator)
    result.output["valid"] = False
    with pytest.raises(ValueError, match="output was mutated"):
        verify_invocation_integrity(result, operator)


def test_dispatch_binds_handler_consumed_exact_refs_into_lineage() -> None:
    registry = DomainExtensionRegistry()
    descriptor, operator = _extension("robotics", "robotics.pose.v1", "robotics.validate")
    canonical_ref = _ref("canonical-snapshot")
    registry.register(
        descriptor,
        {operator.operator_id: lambda inputs, config: {"valid": True, HANDLER_LINEAGE_REFS: (canonical_ref,)}},
    )
    caller_ref = _ref("caller")
    result = registry.dispatch(
        operator.operator_id,
        operator.version,
        ((caller_ref, {}),),
        config={},
        authorizer=Allow(),
    )
    assert result.output == {"valid": True}
    assert {ref.exact_key for ref in result.input_refs} == {caller_ref.exact_key, canonical_ref.exact_key}
    verify_invocation_integrity(result, operator)


def test_dispatch_rejects_denied_or_malformed_handler_lineage_refs() -> None:
    registry = DomainExtensionRegistry()
    descriptor, operator = _extension("robotics", "robotics.pose.v1", "robotics.validate")
    canonical_ref = _ref("canonical-secret")
    calls = []
    registry.register(
        descriptor,
        {operator.operator_id: lambda inputs, config: calls.append(True) or {HANDLER_LINEAGE_REFS: (canonical_ref,)}},
    )
    with pytest.raises(PermissionError, match="extension access denied"):
        registry.dispatch(
            operator.operator_id,
            operator.version,
            ((_ref("caller"), {}),),
            config={},
            authorizer=DenyResource("canonical-secret"),
        )
    assert calls == [True]

    malformed = DomainExtensionRegistry()
    malformed.register(
        descriptor,
        {operator.operator_id: lambda inputs, config: {HANDLER_LINEAGE_REFS: ("not-a-ref",)}},
    )
    with pytest.raises(TypeError, match="lineage refs"):
        malformed.dispatch(operator.operator_id, operator.version, ((_ref("caller"), {}),), config={}, authorizer=Allow())


def test_invocation_integrity_rejects_output_schema_tampering() -> None:
    registry = DomainExtensionRegistry()
    descriptor, operator = _extension("robotics", "robotics.pose.v1", "robotics.validate")
    registry.register(descriptor, {operator.operator_id: lambda inputs, config: {"valid": True}})
    result = registry.dispatch(
        operator.operator_id,
        operator.version,
        ((_ref("schema"), {}),),
        config={},
        authorizer=Allow(),
    )
    tampered = result.model_copy(update={"output_schema": "robotics.unauthorized.v1"})
    with pytest.raises(ValueError, match="output schema"):
        verify_invocation_integrity(tampered, operator)
