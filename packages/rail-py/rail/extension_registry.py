"""Trusted in-process domain-extension registry.

Registration accepts already-imported local callables only. This module never
imports, evaluates, installs, or activates third-party code; OpenSaddle remains
the authority for capabilities and runtime execution.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from krail.provider.v1 import ResourceRef

if TYPE_CHECKING:
    from rail.temporal_records import TemporalRecord


Digest = Annotated[str, StringConstraints(to_lower=True, pattern=r"^sha256:[0-9a-f]{64}$")]
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.:/-]{0,199}$")]
Version = Annotated[str, StringConstraints(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
Payload = dict[str, Any]
# Reserved trusted-handler output field. Dispatch removes it before response
# serialization and binds the exact refs into InvocationResult lineage.
HANDLER_LINEAGE_REFS = "__krail_lineage_refs__"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


class OperatorDescriptor(StrictModel):
    operator_id: Identifier
    version: Version
    input_schema: Identifier
    output_schema: Identifier
    deterministic: bool = True
    descriptor_digest: Digest

    @model_validator(mode="after")
    def _digest_matches(self) -> "OperatorDescriptor":
        body = self.model_dump(mode="json", exclude={"descriptor_digest"})
        if self.descriptor_digest != _digest(body):
            raise ValueError("operator descriptor digest does not match")
        return self


def describe_operator(**values: object) -> OperatorDescriptor:
    values = dict(values)
    provisional = OperatorDescriptor.model_construct(**values, descriptor_digest="sha256:" + "0" * 64)
    values["descriptor_digest"] = _digest(provisional.model_dump(mode="json", exclude={"descriptor_digest"}))
    return OperatorDescriptor.model_validate(values)


class ExtensionDescriptor(StrictModel):
    extension_id: Identifier
    version: Version
    payload_schemas: tuple[Identifier, ...] = Field(min_length=1, max_length=64)
    operators: tuple[OperatorDescriptor, ...] = Field(max_length=64)
    trust: Literal["trusted_local"] = "trusted_local"
    descriptor_digest: Digest

    @model_validator(mode="after")
    def _validate_descriptor(self) -> "ExtensionDescriptor":
        if len(set(self.payload_schemas)) != len(self.payload_schemas):
            raise ValueError("extension payload schemas must be unique")
        operator_ids = [item.operator_id for item in self.operators]
        if len(set(operator_ids)) != len(operator_ids):
            raise ValueError("extension operators must be unique")
        supported = set(self.payload_schemas)
        if any(item.input_schema not in supported or item.output_schema not in supported for item in self.operators):
            raise ValueError("operator schemas must be declared by the extension")
        body = self.model_dump(mode="json", exclude={"descriptor_digest"})
        if self.descriptor_digest != _digest(body):
            raise ValueError("extension descriptor digest does not match")
        return self


def describe_extension(**values: object) -> ExtensionDescriptor:
    values = dict(values)
    provisional = ExtensionDescriptor.model_construct(**values, descriptor_digest="sha256:" + "0" * 64)
    values["descriptor_digest"] = _digest(provisional.model_dump(mode="json", exclude={"descriptor_digest"}))
    return ExtensionDescriptor.model_validate(values)


class ExtensionAuthorizer(Protocol):
    def authorize(self, ref: ResourceRef) -> None: ...


OperatorHandler = Callable[[tuple[Mapping[str, Any], ...], Mapping[str, Any]], Mapping[str, Any]]


class InvocationResult(StrictModel):
    operator_id: Identifier
    operator_version: Version
    output_schema: Identifier
    output: Payload
    input_refs: tuple[ResourceRef, ...]
    config_digest: Digest
    output_digest: Digest
    lineage_digest: Digest


class ProjectionStorageBinding(StrictModel):
    """A rebuildable adapter's declared input authority, never a new grant."""

    extension_id: Identifier
    extension_version: Version
    projection_id: Identifier
    version: Version
    canonical_authority: str = Field(min_length=1, max_length=300)
    canonical_resource_type: Identifier
    declared_schema: Identifier
    canonical_payload_schema: Identifier
    canonical_schema_version: Version
    canonical_writer_family: Identifier
    projection_writer_family: Identifier
    rebuildable: Literal[True] = True
    binding_digest: Digest

    @model_validator(mode="after")
    def _digest_matches(self) -> "ProjectionStorageBinding":
        ResourceRef(
            authority=self.canonical_authority,
            resource_type=self.canonical_resource_type,
            resource_id="binding-validation", version=self.version,
            digest="sha256:" + "0" * 64,
        )
        if self.binding_digest != _digest(self.model_dump(mode="json", exclude={"binding_digest"})):
            raise ValueError("projection storage binding digest does not match")
        return self


def describe_projection_storage(**values: object) -> ProjectionStorageBinding:
    values = dict(values)
    provisional = ProjectionStorageBinding.model_construct(**values, binding_digest="sha256:" + "0" * 64)
    values["binding_digest"] = _digest(provisional.model_dump(mode="json", exclude={"binding_digest"}))
    return ProjectionStorageBinding.model_validate(values)


class ProjectionReadUnavailable(ValueError):
    """The disposable projection cannot be used against current exact inputs."""


ProjectionBuilder = Callable[[tuple["TemporalRecord", ...], Mapping[str, Any]], object]


def verify_invocation_integrity(result: InvocationResult, descriptor: OperatorDescriptor) -> InvocationResult:
    """Revalidate mutable output and lineage before a consumer exposes it."""

    if result.operator_id != descriptor.operator_id or result.operator_version != descriptor.version:
        raise ValueError("invocation operator does not match descriptor")
    if result.output_schema != descriptor.output_schema:
        raise ValueError("invocation output schema does not match descriptor")
    output_digest = _digest(result.output)
    if output_digest != result.output_digest:
        raise ValueError("invocation output was mutated after dispatch")
    lineage = {
        "operator_id": descriptor.operator_id,
        "operator_version": descriptor.version,
        "operator_descriptor_digest": descriptor.descriptor_digest,
        "output_schema": descriptor.output_schema,
        "config_digest": result.config_digest,
        "input_refs": [ref.model_dump(mode="json") for ref in result.input_refs],
        "output_digest": output_digest,
    }
    if result.lineage_digest != _digest(lineage):
        raise ValueError("invocation lineage digest is invalid")
    return result


class DomainExtensionRegistry:
    """Deterministic descriptor discovery and trusted local dispatch."""

    def __init__(self) -> None:
        self._extensions: dict[tuple[str, str], ExtensionDescriptor] = {}
        self._schema_owners: dict[str, str] = {}
        self._operators: dict[tuple[str, str], tuple[OperatorDescriptor, OperatorHandler]] = {}
        self._projections: dict[str, tuple[ProjectionStorageBinding, ProjectionBuilder]] = {}
        self._projection_inputs: dict[str, tuple[tuple[ResourceRef, ...], str, object]] = {}
        self._canonical_writers: dict[tuple[str, str], str] = {}

    @staticmethod
    def _projection_refs(binding: ProjectionStorageBinding, records: tuple["TemporalRecord", ...]) -> tuple[ResourceRef, ...]:
        from rail.temporal_records import TemporalRecord, verify_temporal_record_integrity

        if len(records) > 10_000:
            raise ProjectionReadUnavailable("projection input bound exceeded")
        refs: list[ResourceRef] = []
        for record in records:
            if not isinstance(record, TemporalRecord):
                raise TypeError("projection inputs must be canonical temporal records")
            try:
                verify_temporal_record_integrity(record)
            except ValueError as exc:
                raise ProjectionReadUnavailable("projection canonical input integrity changed") from exc
            if (record.authority != binding.canonical_authority
                    or record.payload_schema != binding.canonical_payload_schema
                    or record.payload_schema_version != binding.canonical_schema_version
                    or record.writer_family != binding.canonical_writer_family):
                raise ProjectionReadUnavailable("projection canonical source declaration differs")
            refs.append(ResourceRef(
                authority=binding.canonical_authority,
                resource_type=binding.canonical_resource_type,
                resource_id=record.record_id,
                version=record.revision,
                digest=record.record_digest,
            ))
        if len({ref.exact_key for ref in refs}) != len(refs):
            raise ProjectionReadUnavailable("projection input references are duplicated")
        return tuple(sorted(refs, key=lambda ref: ref.exact_key))

    def register_projection(self, binding: ProjectionStorageBinding, builder: ProjectionBuilder) -> None:
        if (binding.extension_id, binding.extension_version) not in self._extensions:
            raise ValueError("projection owner extension is not registered")
        if self._schema_owners.get(binding.declared_schema) != binding.extension_id:
            raise ValueError("projection canonical schema belongs to another extension")
        if binding.projection_id in self._projections:
            raise ValueError("projection storage is already registered")
        if not callable(builder):
            raise TypeError("projection builder must be an already-imported callable")
        source = (binding.canonical_authority, binding.canonical_resource_type)
        writer = self._canonical_writers.get(source)
        if writer is not None and writer != binding.canonical_writer_family:
            raise ValueError("canonical source has a conflicting writer family")
        self._canonical_writers[source] = binding.canonical_writer_family
        self._projections[binding.projection_id] = binding, builder

    def register_with_projection(self, descriptor: ExtensionDescriptor,
                                 handlers: Mapping[str, OperatorHandler],
                                 binding: ProjectionStorageBinding,
                                 builder: ProjectionBuilder) -> None:
        """Validate both registrations before publishing either capability."""
        if (binding.extension_id, binding.extension_version) != (descriptor.extension_id, descriptor.version):
            raise ValueError("projection owner extension identity differs")
        if binding.declared_schema not in descriptor.payload_schemas:
            raise ValueError("projection canonical schema is not declared")
        if binding.projection_id in self._projections:
            raise ValueError("projection storage is already registered")
        if not callable(builder):
            raise TypeError("projection builder must be an already-imported callable")
        writer = self._canonical_writers.get((binding.canonical_authority, binding.canonical_resource_type))
        if writer is not None and writer != binding.canonical_writer_family:
            raise ValueError("canonical source has a conflicting writer family")
        self.register(descriptor, handlers)
        self.register_projection(binding, builder)

    def prepare_projection(self, projection_id: str, records: tuple["TemporalRecord", ...], *,
                           config: Mapping[str, Any], authorizer: ExtensionAuthorizer) -> object:
        entry = self._projections.get(projection_id)
        if entry is None:
            raise LookupError("unknown projection storage binding")
        binding, builder = entry
        refs = self._projection_refs(binding, records)
        config_digest = _digest(config)
        for ref in refs:
            authorizer.authorize(ref)
        projection = builder(records, config)
        for ref in refs:
            authorizer.authorize(ref)
        if self._projection_refs(binding, records) != refs or _digest(config) != config_digest:
            raise ProjectionReadUnavailable("projection inputs or configuration changed during build")
        self._projection_inputs[projection_id] = (refs, config_digest, projection)
        return projection

    def read_projection(self, projection_id: str, records: tuple["TemporalRecord", ...], *,
                        config: Mapping[str, Any], authorizer: ExtensionAuthorizer,
                        read: Callable[[object], Any]) -> Any:
        entry = self._projections.get(projection_id)
        snapshot = self._projection_inputs.get(projection_id)
        if entry is None or snapshot is None:
            raise ProjectionReadUnavailable("projection has not been prepared")
        binding, _builder = entry
        refs = self._projection_refs(binding, records)
        if refs != snapshot[0] or _digest(config) != snapshot[1]:
            raise ProjectionReadUnavailable("projection exact inputs or configuration changed")
        for ref in refs:
            authorizer.authorize(ref)
        result = read(snapshot[2])
        for ref in refs:
            authorizer.authorize(ref)
        if self._projection_refs(binding, records) != refs or _digest(config) != snapshot[1]:
            raise ProjectionReadUnavailable("projection inputs or configuration changed during read")
        return result

    def projection_bindings(self) -> tuple[ProjectionStorageBinding, ...]:
        return tuple(self._projections[key][0] for key in sorted(self._projections))

    def register(self, descriptor: ExtensionDescriptor, handlers: Mapping[str, OperatorHandler]) -> None:
        if descriptor.trust != "trusted_local":
            raise ValueError("only trusted local extensions may be registered")
        key = (descriptor.extension_id, descriptor.version)
        if key in self._extensions:
            raise ValueError("extension version is already registered")
        if set(handlers) != {item.operator_id for item in descriptor.operators}:
            raise ValueError("handlers must exactly match declared operators")
        for schema in descriptor.payload_schemas:
            owner = self._schema_owners.get(schema)
            if owner is not None and owner != descriptor.extension_id:
                raise ValueError("payload schema is owned by another extension")
        for operator in descriptor.operators:
            operator_key = (operator.operator_id, operator.version)
            if operator_key in self._operators:
                raise ValueError("operator version is already registered")
            if not callable(handlers[operator.operator_id]):
                raise TypeError("operator handlers must be already-imported callables")
        self._extensions[key] = descriptor
        for schema in descriptor.payload_schemas:
            self._schema_owners[schema] = descriptor.extension_id
        for operator in descriptor.operators:
            self._operators[(operator.operator_id, operator.version)] = (operator, handlers[operator.operator_id])

    def discover(self) -> tuple[ExtensionDescriptor, ...]:
        return tuple(self._extensions[key] for key in sorted(self._extensions))

    def dispatch(
        self,
        operator_id: str,
        version: str,
        inputs: tuple[tuple[ResourceRef, Mapping[str, Any]], ...],
        *,
        config: Mapping[str, Any],
        authorizer: ExtensionAuthorizer,
    ) -> InvocationResult:
        entry = self._operators.get((operator_id, version))
        if entry is None:
            raise LookupError("unknown operator version")
        descriptor, handler = entry
        if not inputs:
            raise ValueError("operator dispatch requires at least one exact input ref")
        refs = tuple(ref for ref, _payload in inputs)
        try:
            for ref in refs:
                authorizer.authorize(ref)
        except PermissionError as exc:
            raise PermissionError("extension access denied") from exc
        config_digest = _digest(config)
        raw_output = handler(tuple(payload for _ref, payload in inputs), config)
        if not isinstance(raw_output, Mapping):
            raise TypeError("operator handler must return a mapping")
        output = dict(raw_output)
        raw_lineage_refs = output.pop(HANDLER_LINEAGE_REFS, ())
        if not isinstance(raw_lineage_refs, (tuple, list)) or not all(isinstance(ref, ResourceRef) for ref in raw_lineage_refs):
            raise TypeError("operator handler lineage refs must be exact ResourceRefs")
        # A trusted local handler may read immutable canonical evidence in
        # addition to caller inputs. Bind every exact record it consumed into
        # the invocation before exposing its output.
        unique_refs: dict[tuple[str, str, str, str, str], ResourceRef] = {}
        for ref in (*refs, *raw_lineage_refs):
            unique_refs.setdefault(ref.exact_key, ref)
        refs = tuple(unique_refs.values())
        try:
            for ref in refs:
                authorizer.authorize(ref)
        except PermissionError as exc:
            raise PermissionError("extension access denied") from exc
        output_digest = _digest(output)
        lineage = {
            "operator_id": descriptor.operator_id,
            "operator_version": descriptor.version,
            "operator_descriptor_digest": descriptor.descriptor_digest,
            "output_schema": descriptor.output_schema,
            "config_digest": config_digest,
            "input_refs": [ref.model_dump(mode="json") for ref in refs],
            "output_digest": output_digest,
        }
        return InvocationResult(
            operator_id=descriptor.operator_id,
            operator_version=descriptor.version,
            output_schema=descriptor.output_schema,
            output=output,
            input_refs=refs,
            config_digest=config_digest,
            output_digest=output_digest,
            lineage_digest=_digest(lineage),
        )


__all__ = [
    "DomainExtensionRegistry",
    "ExtensionDescriptor",
    "HANDLER_LINEAGE_REFS",
    "InvocationResult",
    "OperatorDescriptor",
    "ProjectionReadUnavailable",
    "ProjectionStorageBinding",
    "describe_extension",
    "describe_operator",
    "describe_projection_storage",
    "verify_invocation_integrity",
]
