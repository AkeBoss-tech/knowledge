"""Small deterministic belief and factor layer over exact KRAIL references.

This module deliberately does not reinterpret ``TemporalRecord`` payloads or
``ClaimRecord.confidence``.  It models only explicitly declared uncertainty
semantics and derives every result from an original prior plus the currently
admissible, exact-version factor inputs.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from krail.provider.v1 import ResourceRef
from rail.extension_registry import (
    HANDLER_LINEAGE_REFS,
    DomainExtensionRegistry,
    ExtensionDescriptor,
    describe_extension,
    describe_operator,
)
from rail.procedure_projection import ProjectionWriter, TemporalProjectionService
from rail.temporal_records import TemporalRecord, create_temporal_record

Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Version = Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
BeliefSemantics = Literal[
    "manual_confidence",
    "calibrated_probability",
    "log_odds",
    "likelihood_ratio",
    "learned_model_score",
    "deterministic_rule",
    "support_contradiction",
    "factor_model_reference",
]
FactorMethod = Literal[
    "deterministic_implication",
    "likelihood_ratio",
    "support_contradiction_aggregation",
]
BELIEF_EXTENSION_ID = "krail.belief-inference"
BELIEF_INPUT_SCHEMA = "krail.belief-input.v1"
BELIEF_OUTPUT_SCHEMA = "krail.belief-output.v1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _finite(value: float, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return value


class BeliefState(StrictModel):
    """A state with explicit semantics; numeric equality never implies semantic equality."""

    schema_version: Literal["krail.belief-state.v1"] = "krail.belief-state.v1"
    belief_id: str = Field(min_length=1, max_length=512)
    semantics: BeliefSemantics
    value: float | bool | dict[str, float]
    state_ref: ResourceRef
    source_refs: tuple[ResourceRef, ...] = Field(min_length=1, max_length=128)
    state_digest: Digest

    @model_validator(mode="after")
    def validate_value(self) -> "BeliefState":
        if isinstance(self.value, float):
            _finite(self.value, "belief value")
        elif isinstance(self.value, dict):
            if not self.value or set(self.value) - {"support", "contradiction"}:
                raise ValueError("support/contradiction values must be bounded named scores")
            for key, value in self.value.items():
                _finite(value, f"belief {key}")
                if value < 0 or value > 1:
                    raise ValueError("support/contradiction values must be between zero and one")
        numeric = {"manual_confidence", "calibrated_probability", "log_odds", "likelihood_ratio", "learned_model_score", "factor_model_reference"}
        if self.semantics in numeric and (not isinstance(self.value, float) or isinstance(self.value, bool)):
            raise ValueError(f"{self.semantics} requires a numeric value")
        if self.semantics == "calibrated_probability" and not 0 <= self.value <= 1:
            raise ValueError("calibrated probability must be between zero and one")
        if self.semantics == "deterministic_rule" and not isinstance(self.value, bool):
            raise ValueError("deterministic rule requires a boolean value")
        if self.semantics == "support_contradiction" and (
            not isinstance(self.value, dict) or set(self.value) != {"support", "contradiction"}
        ):
            raise ValueError("support/contradiction semantics require both bounded scores")
        expected = _digest(self.model_dump(mode="json", exclude={"state_digest"}))
        if self.state_digest != expected:
            raise ValueError("belief state digest does not match")
        return self


def create_belief_state(**values: object) -> BeliefState:
    provisional = BeliefState.model_construct(**values, state_digest="sha256:" + "0" * 64)
    values = provisional.model_dump(mode="python")
    values["state_digest"] = _digest(provisional.model_dump(mode="json", exclude={"state_digest"}))
    return BeliefState.model_validate(values)


class FactorRecord(StrictModel):
    """One declared, versioned update factor for one output belief."""

    schema_version: Literal["krail.factor-record.v1"] = "krail.factor-record.v1"
    factor_id: str = Field(min_length=1, max_length=512)
    factor_version: Version
    factor_ref: ResourceRef
    operator_id: str = Field(min_length=1, max_length=512)
    operator_version: Version
    method: FactorMethod
    input_refs: tuple[ResourceRef, ...] = Field(min_length=1, max_length=128)
    output_ref: ResourceRef
    likelihood_ratio: float | None = None
    conditional_independence: bool = False
    config_digest: Digest

    @model_validator(mode="after")
    def validate_factor(self) -> "FactorRecord":
        if len({ref.exact_key for ref in self.input_refs}) != len(self.input_refs):
            raise ValueError("factor input refs must be unique exact versions")
        if self.method == "likelihood_ratio":
            if self.likelihood_ratio is None or not math.isfinite(self.likelihood_ratio) or self.likelihood_ratio <= 0:
                raise ValueError("likelihood-ratio factors require a finite positive ratio")
            if not self.conditional_independence:
                raise ValueError("likelihood-ratio factors must declare conditional independence")
        elif self.likelihood_ratio is not None:
            raise ValueError("only likelihood-ratio factors may declare a ratio")
        return self


class InferenceEdge(StrictModel):
    schema_version: Literal["krail.inference-edge.v1"] = "krail.inference-edge.v1"
    input_ref: ResourceRef
    output_ref: ResourceRef
    downstream_threshold: float = 0.0
    edge_digest: Digest

    @model_validator(mode="after")
    def validate_edge(self) -> "InferenceEdge":
        if not math.isfinite(self.downstream_threshold) or self.downstream_threshold < 0:
            raise ValueError("edge threshold must be finite and non-negative")
        expected = _digest(self.model_dump(mode="json", exclude={"edge_digest"}))
        if self.edge_digest != expected:
            raise ValueError("inference edge digest does not match")
        return self

def create_inference_edge(**values: object) -> InferenceEdge:
    provisional = InferenceEdge.model_construct(**values, edge_digest="sha256:" + "0" * 64)
    values = provisional.model_dump(mode="python")
    values["edge_digest"] = _digest(provisional.model_dump(mode="json", exclude={"edge_digest"}))
    return InferenceEdge.model_validate(values)


class DerivationRecord(StrictModel):
    schema_version: Literal["krail.derivation-record.v1"] = "krail.derivation-record.v1"
    derivation_id: str = Field(min_length=1, max_length=512)
    output_ref: ResourceRef
    input_refs: tuple[ResourceRef, ...] = Field(max_length=256)
    factor_ref: ResourceRef
    factor_version: Version
    factor_config_digest: Digest
    likelihood_ratio: float | None = None
    conditional_independence: bool = False
    operator_id: str = Field(min_length=1, max_length=512)
    operator_version: Version
    context_keys: tuple[str, ...] = Field(max_length=64)
    prior_state: BeliefState | None
    input_states: tuple[BeliefState, ...] = Field(max_length=128)
    output_state: BeliefState | None
    omitted_stale_refs: tuple[ResourceRef, ...] = Field(max_length=128)
    inference_run_id: str = Field(min_length=1, max_length=512)
    derived_at: datetime
    method: FactorMethod
    derivation_digest: Digest

    @field_validator("derived_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("derivation time must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_digest(self) -> "DerivationRecord":
        expected = _digest(self.model_dump(mode="json", exclude={"derivation_digest"}))
        if self.derivation_digest != expected:
            raise ValueError("derivation digest does not match")
        return self


class InferenceRun(StrictModel):
    schema_version: Literal["krail.inference-run.v1"] = "krail.inference-run.v1"
    run_id: str = Field(min_length=1, max_length=512)
    changed_refs: tuple[ResourceRef, ...] = Field(max_length=128)
    affected_outputs: tuple[ResourceRef, ...] = Field(max_length=1024)
    recomputed_outputs: tuple[ResourceRef, ...] = Field(max_length=1024)
    unchanged_outputs: tuple[ResourceRef, ...] = Field(max_length=1024)
    derivation_ids: tuple[str, ...] = Field(max_length=1024)
    computed_at: datetime
    run_digest: Digest

    @model_validator(mode="after")
    def validate_digest(self) -> "InferenceRun":
        expected = _digest(self.model_dump(mode="json", exclude={"run_digest"}))
        if self.run_digest != expected:
            raise ValueError("inference run digest does not match")
        return self


def _stable_sigmoid(log_odds: float) -> float:
    if log_odds >= 0:
        z = math.exp(-log_odds) if log_odds < 745 else 0.0
        return 1.0 / (1.0 + z)
    z = math.exp(log_odds) if log_odds > -745 else 0.0
    return z / (1.0 + z)


def posterior_probability(prior: float, likelihood_ratios: tuple[float, ...]) -> float:
    """Apply declared conditionally independent likelihood ratios in log space."""

    _finite(prior, "prior probability")
    if prior < 0 or prior > 1:
        raise ValueError("prior probability must be between zero and one")
    if any(not math.isfinite(ratio) or ratio <= 0 for ratio in likelihood_ratios):
        raise ValueError("likelihood ratios must be finite and positive")
    if prior in (0.0, 1.0):
        return prior
    log_odds = math.log(prior) - math.log1p(-prior)
    log_odds += sum(math.log(ratio) for ratio in likelihood_ratios)
    return _stable_sigmoid(log_odds)


class BeliefInferenceEngine:
    """Bounded deterministic recomputation over explicitly registered edges."""

    def __init__(self) -> None:
        self._edges: dict[tuple[str, str, str, str, str], InferenceEdge] = {}
        self._factors: dict[str, FactorRecord] = {}

    def register_factor(self, factor: FactorRecord) -> None:
        existing = self._factors.get(factor.factor_id)
        if existing is not None and existing != factor:
            raise ValueError("factor identifier already names another version")
        self._factors[factor.factor_id] = factor

    def register_edge(self, edge: InferenceEdge) -> None:
        key = edge.input_ref.exact_key + edge.output_ref.exact_key
        existing = self._edges.get(key)
        if existing is not None and existing != edge:
            raise ValueError("inference edge replay conflicts")
        adjacency: dict[tuple[str, str, str, str, str], set[tuple[str, str, str, str, str]]] = defaultdict(set)
        for current in self._edges.values():
            adjacency[current.input_ref.exact_key].add(current.output_ref.exact_key)
        adjacency[edge.input_ref.exact_key].add(edge.output_ref.exact_key)
        frontier = [edge.output_ref.exact_key]
        seen: set[tuple[str, str, str, str, str]] = set()
        while frontier:
            node = frontier.pop()
            if node == edge.input_ref.exact_key:
                raise ValueError("inference edges may not contain cycles")
            if node in seen:
                continue
            seen.add(node)
            frontier.extend(adjacency.get(node, ()))
        self._edges[key] = edge

    def affected_outputs(self, changed_refs: tuple[ResourceRef, ...]) -> tuple[ResourceRef, ...]:
        reverse: dict[tuple[str, str, str, str, str], list[InferenceEdge]] = defaultdict(list)
        for edge in self._edges.values():
            reverse[edge.input_ref.exact_key].append(edge)
        frontier = deque(ref.exact_key for ref in changed_refs)
        seen: dict[tuple[str, str, str, str, str], ResourceRef] = {}
        while frontier:
            key = frontier.popleft()
            for edge in reverse.get(key, ()):
                if edge.output_ref.exact_key not in seen:
                    seen[edge.output_ref.exact_key] = edge.output_ref
                    frontier.append(edge.output_ref.exact_key)
        return tuple(seen[key] for key in sorted(seen))

    @staticmethod
    def _derivation(
        *, factor: FactorRecord, prior: BeliefState | None, inputs: tuple[BeliefState, ...],
        omitted: tuple[ResourceRef, ...], output: BeliefState | None, run_id: str, at: datetime,
    ) -> DerivationRecord:
        derivation_id = _digest({"run_id": run_id, "factor_id": factor.factor_id, "output": factor.output_ref.model_dump(mode="json")})
        values = {
            "derivation_id": derivation_id, "output_ref": factor.output_ref,
            "input_refs": factor.input_refs, "factor_ref": factor.factor_ref,
            "factor_version": factor.factor_version, "operator_id": factor.operator_id,
            "operator_version": factor.operator_version, "context_keys": ("conditional_independence",) if factor.conditional_independence else (),
            "factor_config_digest": factor.config_digest,
            "likelihood_ratio": factor.likelihood_ratio,
            "conditional_independence": factor.conditional_independence,
            "prior_state": prior, "input_states": inputs, "output_state": output,
            "omitted_stale_refs": omitted, "inference_run_id": run_id, "derived_at": at,
            "method": factor.method,
        }
        provisional = DerivationRecord.model_construct(
            **values, derivation_digest="sha256:" + "0" * 64
        )
        digest = _digest(provisional.model_dump(mode="json", exclude={"derivation_digest"}))
        return DerivationRecord(**values, derivation_digest=digest)

    def recompute(
        self, *, changed_refs: tuple[ResourceRef, ...], prior_states: Mapping[tuple[str, str, str, str, str], BeliefState],
        input_states: Mapping[tuple[str, str, str, str, str], BeliefState], stale_refs: tuple[ResourceRef, ...],
        run_id: str, at: datetime,
        previous_states: Mapping[tuple[str, str, str, str, str], BeliefState] | None = None,
        dependency_region: Callable[[ResourceRef], tuple[ResourceRef, ...]] | None = None,
    ) -> tuple[InferenceRun, tuple[BeliefState, ...], tuple[DerivationRecord, ...]]:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("inference time must include a timezone")
        stale = {ref.exact_key for ref in stale_refs}
        if len(stale) != len(stale_refs):
            raise ValueError("stale refs must be unique exact versions")
        for key, state in (*prior_states.items(), *input_states.items(), *(previous_states or {}).items()):
            if key != state.state_ref.exact_key:
                raise ValueError("belief state map key must match state_ref exact identity")
        by_output: dict[tuple[str, str, str, str, str], list[FactorRecord]] = defaultdict(list)
        outgoing: dict[tuple[str, str, str, str, str], list[InferenceEdge]] = defaultdict(list)
        for factor in self._factors.values():
            by_output[factor.output_ref.exact_key].append(factor)
        for edge in self._edges.values():
            outgoing[edge.input_ref.exact_key].append(edge)
        frontier: deque[ResourceRef] = deque()
        for changed in changed_refs:
            frontier.extend(edge.output_ref for edge in outgoing.get(changed.exact_key, ()))
            if dependency_region is not None:
                frontier.extend(dependency_region(changed))
        affected_map: dict[tuple[str, str, str, str, str], ResourceRef] = {}
        current_states = dict(input_states)
        current_states.update(prior_states)
        if previous_states:
            current_states.update(previous_states)
        outputs: list[BeliefState] = []
        derivations: list[DerivationRecord] = []
        unchanged: list[ResourceRef] = []
        recomputed: list[ResourceRef] = []
        processed: set[tuple[str, str, str, str, str]] = set()
        deferred_rounds = 0
        while frontier:
            output_ref = frontier.popleft()
            predecessor_keys = {
                edge.input_ref.exact_key
                for edge in self._edges.values()
                if edge.output_ref.exact_key == output_ref.exact_key
                and edge.input_ref.exact_key in by_output
            }
            if predecessor_keys - processed:
                frontier.append(output_ref)
                deferred_rounds += 1
                if deferred_rounds < len(frontier):
                    continue
            deferred_rounds = 0
            if output_ref.exact_key in affected_map:
                continue
            affected_map[output_ref.exact_key] = output_ref
            factors = sorted(by_output.get(output_ref.exact_key, ()), key=lambda item: item.factor_id)
            prior = prior_states.get(output_ref.exact_key)
            if prior is None:
                raise ValueError("affected belief output requires an original prior state")
            admissible: list[FactorRecord] = []
            omitted: set[tuple[str, str, str, str, str]] = set()
            for factor in factors:
                for ref in factor.input_refs:
                    if ref.exact_key in stale or ref.exact_key not in current_states:
                        omitted.add(ref.exact_key)
                if not any(ref.exact_key in stale or ref.exact_key not in current_states for ref in factor.input_refs):
                    admissible.append(factor)
            if not factors:
                processed.add(output_ref.exact_key)
                continue
            method = factors[0].method
            if any(factor.method != method for factor in admissible):
                raise ValueError("incompatible factor methods cannot be combined")
            active_inputs = tuple(current_states[ref.exact_key] for factor in admissible for ref in factor.input_refs)
            if method == "likelihood_ratio":
                if prior.semantics != "calibrated_probability" or not isinstance(prior.value, float):
                    raise ValueError("likelihood ratios require a calibrated probability prior")
                active_refs = tuple(ref for factor in admissible for ref in factor.input_refs)
                if len({ref.exact_key for ref in active_refs}) != len(active_refs):
                    raise ValueError("likelihood-ratio evidence refs must be unique; correlated replay is not implicit")
                value: float | bool | dict[str, float] = posterior_probability(
                    prior.value, tuple(factor.likelihood_ratio for factor in admissible if factor.likelihood_ratio is not None)
                )
                output = create_belief_state(
                    belief_id=prior.belief_id, semantics="calibrated_probability", value=value,
                    state_ref=prior.state_ref, source_refs=active_refs or prior.source_refs,
                )
            elif method == "support_contradiction_aggregation":
                if any(state.semantics != "support_contradiction" or not isinstance(state.value, dict) for state in active_inputs):
                    raise ValueError("support aggregation requires support/contradiction inputs")
                support = min(1.0, sum(float(state.value["support"]) for state in active_inputs))
                contradiction = min(1.0, sum(float(state.value["contradiction"]) for state in active_inputs))
                output = create_belief_state(
                    belief_id=prior.belief_id, semantics="support_contradiction",
                    value={"support": support, "contradiction": contradiction}, state_ref=prior.state_ref,
                    source_refs=tuple(ref for factor in admissible for ref in factor.input_refs) or prior.source_refs,
                )
            else:
                values = [state.value for state in active_inputs]
                if not values or not all(isinstance(value, bool) for value in values):
                    output = None
                else:
                    output = create_belief_state(
                        belief_id=prior.belief_id, semantics="deterministic_rule", value=all(values),
                        state_ref=prior.state_ref, source_refs=tuple(ref for factor in admissible for ref in factor.input_refs) or prior.source_refs,
                    )
            if output is None:
                output = prior
            previous = (previous_states or {}).get(output_ref.exact_key, prior)
            changed = output.state_digest != previous.state_digest
            if changed:
                recomputed.append(output_ref)
                outputs.append(output)
                current_states[output_ref.exact_key] = output
                for edge in outgoing.get(output_ref.exact_key, ()):
                    if method == "likelihood_ratio" and isinstance(output.value, float) and isinstance(prior.value, float):
                        if not isinstance(previous.value, float):
                            raise ValueError("probability replay requires a numeric previous state")
                        magnitude = abs(output.value - previous.value)
                    else:
                        magnitude = 1.0 if output.state_digest != previous.state_digest else 0.0
                    if magnitude > edge.downstream_threshold:
                        frontier.append(edge.output_ref)
            else:
                unchanged.append(output_ref)
            for factor in factors:
                factor_inputs = tuple(
                    current_states[ref.exact_key]
                    for ref in factor.input_refs
                    if ref.exact_key not in stale and ref.exact_key in current_states
                )
                derivations.append(self._derivation(
                    factor=factor, prior=prior, inputs=factor_inputs,
                    omitted=tuple(ref for ref in factor.input_refs if ref.exact_key in omitted),
                    output=output, run_id=run_id, at=at,
                ))
            processed.add(output_ref.exact_key)
        affected = tuple(affected_map[key] for key in sorted(affected_map))
        outputs = sorted(outputs, key=lambda item: item.state_ref.exact_key)
        derivations = sorted(derivations, key=lambda item: item.derivation_id)
        recomputed = sorted(recomputed, key=lambda item: item.exact_key)
        unchanged = sorted(unchanged, key=lambda item: item.exact_key)
        run_values = {
            "run_id": run_id, "changed_refs": changed_refs, "affected_outputs": affected,
            "recomputed_outputs": tuple(recomputed), "unchanged_outputs": tuple(unchanged),
            "derivation_ids": tuple(item.derivation_id for item in derivations), "computed_at": at,
        }
        provisional = InferenceRun.model_construct(
            **run_values, run_digest="sha256:" + "0" * 64
        )
        run_digest = _digest(provisional.model_dump(mode="json", exclude={"run_digest"}))
        run = InferenceRun(**run_values, run_digest=run_digest)
        return run, tuple(outputs), tuple(derivations)


class BeliefProjectService:
    """Public belief journey backed by the canonical temporal projection.

    Candidates and promoted outputs are ordinary immutable ``TemporalRecord``
    rows. The service keeps no second evidence store: restart/rebuild discovers
    candidates, reviews, evidence, and prior derived rows from the projection.
    """

    CANDIDATE_SCHEMA = "krail.belief-candidate"
    APPROVED_SCHEMA = "krail.belief-approved"
    DERIVED_SCHEMA = "krail.belief-derived"
    DERIVATION_SCHEMA = "krail.belief-derivation"

    def __init__(self, projection: TemporalProjectionService, *, projection_id: str = "belief-inference") -> None:
        self.projection = projection
        self.projection_id = projection_id

    @staticmethod
    def _output_ref(candidate_id: str, factors: tuple[FactorRecord, ...]) -> ResourceRef:
        body = {
            "candidate_id": candidate_id,
            "identity_version": "belief-output.v2",
            "factors": [item.model_dump(mode="json", exclude={"output_ref"}) for item in factors],
        }
        return ResourceRef(
            authority="krail://belief-inference",
            resource_type="belief-state",
            resource_id=candidate_id,
            version="v1",
            digest=_digest(body),
        )

    @staticmethod
    def _record_payload(record: TemporalRecord) -> dict[str, object]:
        return dict(record.payload)

    def _records(self, schema: str) -> tuple[TemporalRecord, ...]:
        return tuple(record for record in self.projection.temporal_records() if record.payload_schema == schema)

    @staticmethod
    def _decode_state(payload: Mapping[str, object], *, default_ref: ResourceRef | None = None) -> BeliefState:
        state_payload = payload.get("prior_state") or payload.get("state")
        if not isinstance(state_payload, Mapping):
            raise ValueError("belief journey record is missing a state")
        values = dict(state_payload)
        if default_ref is not None:
            values.setdefault("state_ref", default_ref.model_dump(mode="python"))
        return BeliefState.model_validate(values)

    @staticmethod
    def _decode_factor(payload: Mapping[str, object]) -> FactorRecord:
        raw = payload.get("factor")
        if not isinstance(raw, Mapping):
            raise ValueError("belief candidate is missing a factor")
        return FactorRecord.model_validate(raw)

    def propose(
        self,
        *,
        candidate_id: str,
        prior: BeliefState,
        factors: tuple[FactorRecord, ...],
        at: datetime,
        writer: ProjectionWriter,
    ) -> TemporalRecord:
        if not factors:
            raise ValueError("belief candidate requires a factor")
        output_ref = self._output_ref(candidate_id, factors)
        if any(factor.output_ref.exact_key != output_ref.exact_key for factor in factors):
            raise ValueError("candidate factors must target the derived belief output")
        source_refs = tuple(dict.fromkeys((*prior.source_refs, *(ref for factor in factors for ref in (factor.input_refs + (factor.factor_ref,))))))
        payload = {
            "status": "candidate",
            "prior_state": prior.model_dump(mode="json"),
            "factors": [factor.model_dump(mode="json") for factor in factors],
            "output_ref": output_ref.model_dump(mode="json"),
        }
        record = create_temporal_record(
            record_id=candidate_id,
            entity_id=candidate_id,
            entity_authority="krail://belief-inference",
            payload_schema=self.CANDIDATE_SCHEMA,
            payload_schema_version="v1",
            kind="hypothesis",
            authority="krail://belief-inference",
            writer_family="belief-candidate",
            valid_from=at,
            recorded_at=at,
            source_refs=source_refs,
            revision="1",
            visibility="internal",
            payload=payload,
        )
        return self.projection.ingest(record, at=at, writer=writer)

    def review_promote(
        self, candidate: TemporalRecord, *, reviewer_id: str, at: datetime, writer: ProjectionWriter
    ) -> TemporalRecord:
        if candidate.payload_schema != self.CANDIDATE_SCHEMA or candidate.payload.get("status") != "candidate":
            raise ValueError("only an unreviewed belief candidate can be promoted")
        payload = dict(candidate.payload)
        payload.update({"status": "promoted", "reviewer_id": reviewer_id})
        source_refs = candidate.source_refs
        promoted = create_temporal_record(
            record_id=f"{candidate.record_id}:review",
            entity_id=candidate.entity_id,
            entity_authority=candidate.entity_authority,
            payload_schema=self.APPROVED_SCHEMA,
            payload_schema_version="v1",
            kind="approved_state",
            authority="krail://belief-inference",
            writer_family="belief-review",
            valid_from=at,
            recorded_at=at,
            source_refs=source_refs,
            provenance_refs=(self.projection.record_ref(candidate),),
            revision="1",
            visibility="internal",
            payload=payload,
        )
        return self.projection.ingest(promoted, at=at, writer=writer)

    def recompute(
        self,
        *,
        valid_at: datetime,
        known_at: datetime,
        at: datetime,
        writer: ProjectionWriter,
        authorize: Callable[[ResourceRef], None] | None = None,
    ) -> tuple[InferenceRun, tuple[BeliefState, ...], tuple[DerivationRecord, ...]]:
        if not callable(authorize):
            raise PermissionError("belief recomputation requires a current lineage authorizer")
        all_records = self.projection.temporal_records()
        records = self.projection._current_records(all_records, valid_at=valid_at, known_at=known_at)
        by_ref = {self.projection.record_ref(record).exact_key: record for record in records}
        source_records = {
            self.projection.record_ref(record).exact_key: record
            for record in all_records
            if (record.ingested_at or record.recorded_at) <= known_at
            if record.payload_schema not in {self.CANDIDATE_SCHEMA, self.APPROVED_SCHEMA, self.DERIVED_SCHEMA, self.DERIVATION_SCHEMA}
        }
        # Derived temporal rows expose their stable logical BeliefState ref as
        # an additional exact input identity, allowing downstream candidates to
        # consume the current upstream state across rebuilds.
        for record in records:
            if record.payload_schema != self.DERIVED_SCHEMA:
                continue
            state_payload = record.payload.get("state")
            if isinstance(state_payload, Mapping) and isinstance(state_payload.get("state_ref"), Mapping):
                logical_ref = ResourceRef.model_validate(state_payload["state_ref"])
                source_records.setdefault(logical_ref.exact_key, record)
        invalidated = tuple(self.projection.active_invalidation_refs(valid_at=valid_at, known_at=known_at))
        stale = {ref.exact_key for ref in invalidated}
        for ref in invalidated:
            stale.update(item.exact_key for item in self.projection.affected_region(ref))
        stale.update(key for key in source_records if key not in by_ref)
        engine = BeliefInferenceEngine()
        prior_states: dict[tuple[str, str, str, str, str], BeliefState] = {}
        input_states: dict[tuple[str, str, str, str, str], BeliefState] = {}
        previous_states: dict[tuple[str, str, str, str, str], BeliefState] = {}
        changed: list[ResourceRef] = []
        output_by_source: dict[tuple[str, str, str, str, str], ResourceRef] = {}
        current_keys = set(by_ref)
        promoted = [record for record in records if record.payload_schema == self.APPROVED_SCHEMA and record.payload.get("status") == "promoted"]
        definition_outputs: dict[tuple[str, str, str, str, str], BeliefState] = {}
        definition_factors: dict[tuple[str, str, str, str, str], tuple[FactorRecord, ...]] = {}
        for approved in promoted:
            factors = tuple(self._decode_factor({"factor": item}) for item in approved.payload.get("factors", ()))
            output_ref = self._output_ref(approved.entity_id, factors)
            prior = self._decode_state(approved.payload)
            definition_outputs[output_ref.exact_key] = create_belief_state(
                belief_id=prior.belief_id, semantics=prior.semantics, value=prior.value,
                state_ref=output_ref, source_refs=prior.source_refs,
            )
            definition_factors[output_ref.exact_key] = factors
        for approved in sorted(promoted, key=lambda item: item.record_id):
            payload = approved.payload
            prior = self._decode_state(payload)
            factors = tuple(self._decode_factor({"factor": item}) for item in payload.get("factors", ()))
            output_ref = self._output_ref(approved.entity_id, factors)
            candidate_ref = next((ref for ref in approved.provenance_refs if ref.exact_key in current_keys), None)
            if candidate_ref is None:
                continue
            lineage_parts: tuple[ResourceRef, ...] = (self.projection.record_ref(approved),)
            if candidate_ref is not None:
                lineage_parts += (candidate_ref,)
            lineage_parts += prior.source_refs
            lineage_parts += tuple(ref for factor in factors for ref in (factor.input_refs + (factor.factor_ref,)))
            lineage_refs = tuple(dict.fromkeys(lineage_parts))
            try:
                for lineage_ref in lineage_refs:
                    authorize(lineage_ref)
            except PermissionError:
                continue
            factor_inputs = tuple(ref for factor in factors for ref in factor.input_refs)
            if any(ref.exact_key not in source_records and ref.exact_key not in definition_outputs for ref in factor_inputs):
                continue
            prior_derived = [record for record in all_records if record.payload_schema == self.DERIVED_SCHEMA and record.entity_id == approved.entity_id]
            current_derived = [record for record in records if record.payload_schema == self.DERIVED_SCHEMA and record.entity_id == approved.entity_id]
            latest_derived = max(current_derived, key=lambda item: (int(item.revision), item.recorded_at, item.record_digest)) if current_derived else None
            previous_input_keys = set()
            if latest_derived is not None:
                previous_input_keys = {ref.exact_key for ref in self._decode_state(latest_derived.payload, default_ref=output_ref).source_refs}
            for factor_record in factors:
                engine.register_factor(factor_record)
                for input_ref in factor_record.input_refs:
                    engine.register_edge(create_inference_edge(input_ref=input_ref, output_ref=output_ref))
                    output_by_source[input_ref.exact_key] = output_ref
                    source = source_records.get(input_ref.exact_key)
                    if source is None:
                        upstream = definition_outputs.get(input_ref.exact_key)
                        if upstream is None:
                            continue
                        input_states[input_ref.exact_key] = upstream
                        changed.append(input_ref)
                        continue
                    state_payload = source.payload.get("state", source.payload)
                    if state_payload.get("semantics") not in BeliefSemantics.__args__ or "value" not in state_payload:
                        continue
                    input_states[input_ref.exact_key] = create_belief_state(
                        belief_id=source.entity_id, semantics=state_payload["semantics"], value=state_payload["value"],
                        state_ref=input_ref, source_refs=(input_ref,),
                    )
                    if latest_derived is None or input_ref.exact_key not in previous_input_keys or input_ref.exact_key in stale:
                        changed.append(input_ref)
            prior = create_belief_state(
                belief_id=prior.belief_id, semantics=prior.semantics, value=prior.value,
                state_ref=output_ref, source_refs=prior.source_refs,
            )
            prior_states[output_ref.exact_key] = prior
            if latest_derived is not None:
                previous_states[output_ref.exact_key] = self._decode_state(latest_derived.payload, default_ref=output_ref)
        if not prior_states:
            run_values = {
                "run_id": f"{self.projection_id}:{at.isoformat()}", "changed_refs": (),
                "affected_outputs": (), "recomputed_outputs": (), "unchanged_outputs": (),
                "derivation_ids": (), "computed_at": at,
            }
            provisional = InferenceRun.model_construct(**run_values, run_digest="sha256:" + "0" * 64)
            return InferenceRun(**run_values, run_digest=_digest(provisional.model_dump(mode="json", exclude={"run_digest"}))), (), ()
        changed_refs = tuple(dict.fromkeys(changed))
        run, outputs, derivations = engine.recompute(
            changed_refs=changed_refs, prior_states=prior_states, input_states=input_states,
            stale_refs=tuple(dict.fromkeys(
                source_ref for source_key, source_ref in ((key, self.projection.record_ref(record)) for key, record in source_records.items())
                if source_key in stale
            )),
            previous_states=previous_states, run_id=f"{self.projection_id}:{at.isoformat()}", at=at,
            dependency_region=lambda ref: (output_by_source[ref.exact_key],) if ref.exact_key in output_by_source else (),
        )
        accepted_outputs: list[BeliefState] = []
        accepted_derivations: list[DerivationRecord] = []
        pending_records: list[TemporalRecord] = []
        for output in outputs:
            approved = next(item for item in promoted if self._output_ref(item.entity_id, tuple(self._decode_factor({"factor": factor}) for factor in item.payload.get("factors", ()))) == output.state_ref)
            factors = tuple(self._decode_factor({"factor": factor}) for factor in approved.payload.get("factors", ()))
            candidate_ref = next((ref for ref in approved.provenance_refs), None)
            prior_for_lineage = self._decode_state(approved.payload)
            output_lineage_parts: tuple[ResourceRef, ...] = (self.projection.record_ref(approved),)
            if candidate_ref is not None:
                output_lineage_parts += (candidate_ref,)
            output_lineage_parts += prior_for_lineage.source_refs + output.source_refs
            output_lineage_parts += tuple(ref for factor in factors for ref in (factor.input_refs + (factor.factor_ref,)))
            output_lineage = tuple(dict.fromkeys(output_lineage_parts))
            try:
                for lineage_ref in output_lineage:
                    authorize(lineage_ref)
            except PermissionError:
                continue
            source_refs = output.source_refs
            prior_derived = [record for record in all_records if record.payload_schema == self.DERIVED_SCHEMA and record.entity_id == approved.entity_id]
            previous_digest = max(prior_derived, key=lambda item: (int(item.revision), item.recorded_at, item.record_digest)).record_digest if prior_derived else None
            next_revision = str(max((int(item.revision) for item in prior_derived), default=0) + 1)
            derived_record = create_temporal_record(
                record_id=f"{approved.entity_id}:derived", entity_id=approved.entity_id,
                entity_authority="krail://belief-inference", payload_schema=self.DERIVED_SCHEMA,
                payload_schema_version="v1", kind="approved_state", authority="krail://belief-inference",
                writer_family="belief-engine", valid_from=at, recorded_at=at, source_refs=source_refs,
                provenance_refs=(self.projection.record_ref(approved),), revision=next_revision,
                supersedes_digest=previous_digest, visibility="internal",
                payload={"status": "derived", "state": output.model_dump(mode="json"), "derivation_ids": [item.derivation_id for item in derivations if item.output_ref.exact_key == output.state_ref.exact_key]},
            )
            pending_records.append(derived_record)
            accepted_outputs.append(output)
            for derivation in derivations:
                if derivation.output_ref.exact_key != output.state_ref.exact_key:
                    continue
                derivation_refs = tuple(dict.fromkeys((
                    *derivation.input_refs,
                    derivation.factor_ref,
                    *(derivation.prior_state.source_refs if derivation.prior_state is not None else ()),
                )))
                derivation_record = create_temporal_record(
                    record_id=f"{derivation.derivation_id}:{derivation.output_state.state_digest if derivation.output_state else 'none'}",
                    entity_id=f"{derivation.derivation_id}:{derivation.output_state.state_digest if derivation.output_state else 'none'}",
                    entity_authority="krail://belief-inference",
                    payload_schema=self.DERIVATION_SCHEMA,
                    payload_schema_version="v1",
                    kind="approved_state",
                    authority="krail://belief-inference",
                    writer_family="belief-engine",
                    valid_from=at,
                    recorded_at=at,
                    source_refs=derivation_refs,
                    provenance_refs=(self.projection.record_ref(approved),),
                    revision="1",
                    visibility="internal",
                    payload={"status": "derivation", "derivation": derivation.model_dump(mode="json")},
                )
                pending_records.append(derivation_record)
                accepted_derivations.append(derivation)
        def final_authorization() -> None:
            for output in accepted_outputs:
                approved = next(item for item in promoted if self._output_ref(item.entity_id, tuple(self._decode_factor({"factor": factor}) for factor in item.payload.get("factors", ()))) == output.state_ref)
                factors = tuple(self._decode_factor({"factor": factor}) for factor in approved.payload.get("factors", ()))
                final_refs = tuple(dict.fromkeys((self.projection.record_ref(approved), *(next((ref for ref in approved.provenance_refs),),), *output.source_refs, *(ref for factor in factors for ref in (factor.input_refs + (factor.factor_ref,))))))
                for lineage_ref in final_refs:
                    authorize(lineage_ref)
        try:
            self.projection.ingest_many(tuple(pending_records), at=at, writer=writer, before_commit=final_authorization)
        except PermissionError:
            return run, (), ()
        return run, tuple(accepted_outputs), tuple(accepted_derivations)

    def authorized_explanations(
        self, *, valid_at: datetime, known_at: datetime, authorize: Callable[[ResourceRef], None]
    ) -> tuple[DerivationRecord, ...]:
        """Read only current derivations whose complete lineage is authorized."""
        if not callable(authorize):
            raise PermissionError("belief explanations require a current lineage authorizer")
        all_records = self.projection.temporal_records()
        current = self.projection._current_records(all_records, valid_at=valid_at, known_at=known_at)
        current_keys = {self.projection.record_ref(item).exact_key for item in current}
        current_by_ref = {self.projection.record_ref(item).exact_key: item for item in current}
        logical_rows: dict[tuple[str, str, str, str, str], TemporalRecord] = {}
        for item in current:
            if item.payload_schema != self.DERIVED_SCHEMA:
                continue
            state_payload = item.payload.get("state")
            if isinstance(state_payload, Mapping) and isinstance(state_payload.get("state_ref"), Mapping):
                logical_rows[ResourceRef.model_validate(state_payload["state_ref"]).exact_key] = item
        stale_keys = {ref.exact_key for ref in self.projection.active_invalidation_refs(valid_at=valid_at, known_at=known_at)}
        for ref in tuple(self.projection.active_invalidation_refs(valid_at=valid_at, known_at=known_at)):
            stale_keys.update(item.exact_key for item in self.projection.affected_region(ref))
        historical_source_keys = {
            self.projection.record_ref(item).exact_key
            for item in all_records
            if item.payload_schema not in {self.CANDIDATE_SCHEMA, self.APPROVED_SCHEMA, self.DERIVED_SCHEMA, self.DERIVATION_SCHEMA}
            and (item.ingested_at or item.recorded_at) <= known_at
        }
        stale_keys.update(historical_source_keys - current_keys)
        explanations: list[DerivationRecord] = []
        for record in current:
            if record.payload_schema != self.DERIVATION_SCHEMA:
                continue
            raw = record.payload.get("derivation")
            if not isinstance(raw, Mapping):
                continue
            derivation = DerivationRecord.model_validate(raw)
            transitive_refs: list[ResourceRef] = []
            invalid = False
            for input_ref in derivation.input_refs:
                if input_ref.exact_key in current_keys:
                    if input_ref.exact_key in stale_keys:
                        invalid = True
                    continue
                upstream_row = logical_rows.get(input_ref.exact_key)
                if upstream_row is None:
                    invalid = True
                    continue
                transitive_refs.append(self.projection.record_ref(upstream_row))
                if self.projection.record_ref(upstream_row).exact_key in stale_keys or not upstream_row.provenance_refs:
                    invalid = True
                    continue
                upstream_approved = current_by_ref.get(upstream_row.provenance_refs[0].exact_key)
                if upstream_approved is None or not upstream_approved.provenance_refs:
                    invalid = True
                    continue
                upstream_candidate = upstream_approved.provenance_refs[0]
                if upstream_candidate.exact_key not in current_keys or upstream_candidate.exact_key in stale_keys:
                    invalid = True
                upstream_state = upstream_row.payload.get("state")
                if isinstance(upstream_state, Mapping):
                    for source_ref in upstream_state.get("source_refs", ()):
                        source = ResourceRef.model_validate(source_ref)
                        transitive_refs.append(source)
                        if source.exact_key not in current_keys or source.exact_key in stale_keys:
                            invalid = True
                transitive_refs.extend((self.projection.record_ref(upstream_approved), upstream_candidate))
            if invalid:
                continue
            if any(ref.exact_key not in current_keys for ref in record.provenance_refs):
                continue
            approved = current_by_ref.get(record.provenance_refs[0].exact_key) if record.provenance_refs else None
            if approved is None or approved.payload_schema != self.APPROVED_SCHEMA or not approved.provenance_refs:
                continue
            candidate_ref = approved.provenance_refs[0]
            if candidate_ref.exact_key not in current_keys or candidate_ref.exact_key in stale_keys:
                continue
            approved_ref = self.projection.record_ref(approved)
            derived_rows = [
                item for item in current
                if item.payload_schema == self.DERIVED_SCHEMA
                and item.provenance_refs
                and item.provenance_refs[0].exact_key == approved_ref.exact_key
                and derivation.derivation_id in item.payload.get("derivation_ids", ())
            ]
            if not derived_rows:
                continue
            refs = tuple(dict.fromkeys((self.projection.record_ref(record), candidate_ref, *(self.projection.record_ref(item) for item in derived_rows), *transitive_refs, *derivation.input_refs, derivation.factor_ref, *(derivation.prior_state.source_refs if derivation.prior_state else ()), *record.provenance_refs)))
            try:
                for ref in refs:
                    authorize(ref)
            except PermissionError:
                continue
            explanations.append(derivation)
        return tuple(sorted(explanations, key=lambda item: item.derivation_id))


def belief_extension() -> ExtensionDescriptor:
    """Describe the small public operator exposed through the trusted registry."""

    operator = describe_operator(
        operator_id="belief.posterior-likelihood-ratio",
        version="1.0.0",
        input_schema=BELIEF_INPUT_SCHEMA,
        output_schema=BELIEF_OUTPUT_SCHEMA,
        deterministic=True,
    )
    return describe_extension(
        extension_id=BELIEF_EXTENSION_ID,
        version="1.0.0",
        payload_schemas=(BELIEF_INPUT_SCHEMA, BELIEF_OUTPUT_SCHEMA),
        operators=(operator,),
    )


def register_belief_extension(registry: DomainExtensionRegistry) -> None:
    """Register the bounded operator at the existing trusted domain boundary."""

    descriptor = belief_extension()

    def posterior_handler(inputs, config):
        if not inputs or inputs[0].get("role") != "prior":
            raise ValueError("belief posterior operator requires an actual prior input")
        prior_payload = inputs[0]
        prior = prior_payload.get("value")
        if prior_payload.get("semantics") != "calibrated_probability" or not isinstance(prior, (float, int)) or isinstance(prior, bool):
            raise ValueError("belief posterior operator requires a calibrated prior input")
        evidence = inputs[1:]
        if not evidence or any(payload.get("role") != "evidence" for payload in evidence):
            raise ValueError("belief posterior operator requires declared evidence inputs")
        ratios = tuple(payload.get("likelihood_ratio") for payload in evidence)
        if any(not isinstance(ratio, (float, int)) or isinstance(ratio, bool) for ratio in ratios):
            raise ValueError("each evidence input must declare its likelihood ratio")
        factor_ids = config.get("factor_ids")
        if not isinstance(factor_ids, (tuple, list)) or len(factor_ids) != len(evidence) or not all(isinstance(item, str) and item for item in factor_ids):
            raise ValueError("posterior operator factor configuration does not match evidence inputs")
        if config.get("conditional_independence") is not True:
            raise ValueError("posterior operator requires conditional independence")
        result = {
            "semantics": "calibrated_probability",
            "posterior_probability": posterior_probability(float(prior), tuple(float(value) for value in ratios)),
            "assumption": "conditional_independence",
            "factor_ids": tuple(factor_ids),
            "operator_id": "belief.posterior-likelihood-ratio",
            "operator_version": "1.0.0",
            HANDLER_LINEAGE_REFS: (),
        }
        return result

    registry.register(descriptor, {"belief.posterior-likelihood-ratio": posterior_handler})


__all__ = [
    "BeliefInferenceEngine", "BeliefProjectService", "BeliefState", "DerivationRecord", "FactorRecord",
    "InferenceEdge", "InferenceRun", "create_belief_state", "create_inference_edge",
    "posterior_probability",
    "BELIEF_EXTENSION_ID",
    "belief_extension",
    "register_belief_extension",
]
