from datetime import UTC, datetime

import pytest

from krail.provider.v1 import ResourceRef
from rail.belief_inference import (
    BeliefInferenceEngine,
    BeliefProjectService,
    FactorRecord,
    create_belief_state,
    create_inference_edge,
    posterior_probability,
    register_belief_extension,
)
from rail.extension_registry import DomainExtensionRegistry
from rail.procedure_projection import TemporalProjectionService
from rail.temporal_records import create_temporal_record


NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)
DIGEST = "sha256:" + "d" * 64


def ref(resource_id: str, resource_type: str = "evidence") -> ResourceRef:
    return ResourceRef(
        authority="krail://fixture",
        resource_type=resource_type,
        resource_id=resource_id,
        version="v1",
        digest="sha256:" + (resource_id.encode().hex() * 64)[:64],
    )


def factor(*, factor_id: str, method: str, input_ref: ResourceRef, output_ref: ResourceRef, ratio: float | None = None) -> FactorRecord:
    return FactorRecord(
        factor_id=factor_id,
        factor_version="1.0.0",
        factor_ref=ref(factor_id, "factor"),
        operator_id=f"belief.{method}",
        operator_version="1.0.0",
        method=method,
        input_refs=(input_ref,),
        output_ref=output_ref,
        likelihood_ratio=ratio,
        conditional_independence=method == "likelihood_ratio",
        config_digest=DIGEST,
    )


def state(*, belief_id: str, state_ref: ResourceRef, value: float | bool | dict[str, float], semantics: str = "support_contradiction"):
    return create_belief_state(
        belief_id=belief_id,
        semantics=semantics,
        value=value,
        state_ref=state_ref,
        source_refs=(state_ref,),
    )


def test_hand_verifiable_log_odds_update_is_stable_and_replayable():
    assert posterior_probability(0.2, (3.0,)) == pytest.approx(3 / 7)
    assert posterior_probability(0.2, (1e300, 1e300)) > 0.999999
    assert posterior_probability(0.2, (1e-300, 1e-300)) < 1e-12
    assert posterior_probability(0.0, (3.0,)) == 0.0
    assert posterior_probability(1.0, (1 / 3,)) == 1.0
    with pytest.raises(ValueError, match="finite and positive"):
        posterior_probability(0.2, (float("nan"),))


def test_public_engine_removes_stale_factor_keeps_prior_and_records_exact_derivation():
    evidence_a, evidence_b = ref("evidence-a"), ref("evidence-b")
    output_ref = ref("belief-target", "belief")
    prior = state(belief_id="target", state_ref=output_ref, value=0.2, semantics="calibrated_probability")
    input_a = state(belief_id="a", state_ref=evidence_a, value={"support": 1.0, "contradiction": 0.0})
    input_b = state(belief_id="b", state_ref=evidence_b, value={"support": 1.0, "contradiction": 0.0})
    engine = BeliefInferenceEngine()
    for item, ratio in (("factor-a", 3.0), ("factor-b", 5.0)):
        source = evidence_a if item.endswith("a") else evidence_b
        engine.register_factor(factor(factor_id=item, method="likelihood_ratio", input_ref=source, output_ref=output_ref, ratio=ratio))
        engine.register_edge(create_inference_edge(input_ref=source, output_ref=output_ref))

    run, outputs, derivations = engine.recompute(
        changed_refs=(evidence_a,), prior_states={output_ref.exact_key: prior},
        input_states={evidence_a.exact_key: input_a, evidence_b.exact_key: input_b},
        stale_refs=(evidence_a,), run_id="run-1", at=NOW,
    )
    assert run.affected_outputs == (output_ref,)
    assert outputs[0].value == pytest.approx(5 / 9)
    assert outputs[0].value != 0
    assert derivations[0].omitted_stale_refs == (evidence_a,)
    assert {item.factor_ref.resource_id for item in derivations} == {"factor-a", "factor-b"}
    replay, replay_outputs, replay_derivations = engine.recompute(
        changed_refs=(evidence_a,), prior_states={output_ref.exact_key: prior},
        input_states={evidence_a.exact_key: input_a, evidence_b.exact_key: input_b},
        stale_refs=(evidence_a,), run_id="run-1", at=NOW,
    )
    assert replay.run_digest == run.run_digest
    assert replay_outputs == outputs
    assert replay_derivations == derivations


def test_all_stale_factors_preserve_original_prior_and_lineage():
    evidence = ref("all-stale-evidence")
    output = ref("all-stale-belief", "belief")
    prior = state(belief_id="all-stale", state_ref=output, value=0.2, semantics="calibrated_probability")
    evidence_state = state(belief_id="evidence", state_ref=evidence, value={"support": 0.5, "contradiction": 0.0})
    engine = BeliefInferenceEngine()
    engine.register_factor(factor(factor_id="stale-factor", method="likelihood_ratio", input_ref=evidence, output_ref=output, ratio=3.0))
    engine.register_edge(create_inference_edge(input_ref=evidence, output_ref=output))
    run, outputs, derivations = engine.recompute(
        changed_refs=(evidence,), prior_states={output.exact_key: prior},
        input_states={evidence.exact_key: evidence_state}, stale_refs=(evidence,),
        run_id="all-stale-run", at=NOW,
    )
    assert outputs == ()
    assert run.unchanged_outputs == (output,)
    assert derivations[0].output_state == prior
    assert derivations[0].omitted_stale_refs == (evidence,)


def test_stale_recompute_reverts_previous_posterior_to_original_prior():
    evidence = ref("revert-evidence")
    output = ref("revert-belief", "belief")
    prior = state(belief_id="revert", state_ref=output, value=0.2, semantics="calibrated_probability")
    evidence_state = state(belief_id="evidence", state_ref=evidence, value={"support": 0.5, "contradiction": 0.0})
    engine = BeliefInferenceEngine()
    engine.register_factor(factor(factor_id="revert-factor", method="likelihood_ratio", input_ref=evidence, output_ref=output, ratio=3.0))
    engine.register_edge(create_inference_edge(input_ref=evidence, output_ref=output))
    first, posterior, _ = engine.recompute(
        changed_refs=(evidence,), prior_states={output.exact_key: prior},
        input_states={evidence.exact_key: evidence_state}, stale_refs=(), run_id="revert-run", at=NOW,
    )
    assert posterior[0].value == pytest.approx(3 / 7)
    second, reverted, _ = engine.recompute(
        changed_refs=(evidence,), prior_states={output.exact_key: prior},
        input_states={evidence.exact_key: evidence_state}, stale_refs=(evidence,),
        previous_states={output.exact_key: posterior[0]}, run_id="revert-run-2", at=NOW,
    )
    assert first.recomputed_outputs == (output,)
    assert second.recomputed_outputs == (output,)
    assert reverted[0] == prior


def test_threshold_suppresses_downstream_and_topology_uses_new_upstream_state():
    source = ref("chain-source")
    middle = ref("chain-middle", "belief")
    final = ref("chain-final", "belief")
    prior_middle = state(belief_id="middle", state_ref=middle, value=0.2, semantics="calibrated_probability")
    prior_final = state(belief_id="final", state_ref=final, value=0.4, semantics="calibrated_probability")
    source_state = state(belief_id="source", state_ref=source, value={"support": 0.5, "contradiction": 0.0})
    middle_input = state(belief_id="middle-input", state_ref=middle, value=0.2, semantics="calibrated_probability")
    engine = BeliefInferenceEngine()
    engine.register_factor(factor(factor_id="to-middle", method="likelihood_ratio", input_ref=source, output_ref=middle, ratio=3.0))
    engine.register_factor(factor(factor_id="to-final", method="likelihood_ratio", input_ref=middle, output_ref=final, ratio=2.0))
    engine.register_edge(create_inference_edge(input_ref=source, output_ref=middle))
    engine.register_edge(create_inference_edge(input_ref=middle, output_ref=final, downstream_threshold=0.5))
    run, outputs, _ = engine.recompute(
        changed_refs=(source,), prior_states={middle.exact_key: prior_middle, final.exact_key: prior_final},
        input_states={source.exact_key: source_state, middle.exact_key: middle_input}, stale_refs=(),
        run_id="chain-run", at=NOW,
    )
    assert middle in run.recomputed_outputs
    assert final not in run.affected_outputs
    assert [item.state_ref for item in outputs] == [middle]


def test_diamond_propagation_is_insertion_order_invariant():
    source = ref("diamond-source")
    left, right, final = (ref(name, "belief") for name in ("diamond-left", "diamond-right", "diamond-final"))
    prior = {
        left.exact_key: state(belief_id="left", state_ref=left, value=0.2, semantics="calibrated_probability"),
        right.exact_key: state(belief_id="right", state_ref=right, value=0.2, semantics="calibrated_probability"),
        final.exact_key: state(belief_id="final", state_ref=final, value=0.2, semantics="calibrated_probability"),
    }
    inputs = {
        source.exact_key: state(belief_id="source", state_ref=source, value={"support": 0.5, "contradiction": 0.0}),
        left.exact_key: prior[left.exact_key], right.exact_key: prior[right.exact_key],
    }

    def build(order):
        engine = BeliefInferenceEngine()
        for item in order:
            engine.register_factor(factor(factor_id=f"f-{item[0]}", method="likelihood_ratio", input_ref=item[1], output_ref=item[2], ratio=2.0))
            engine.register_edge(create_inference_edge(input_ref=item[1], output_ref=item[2]))
        return engine

    branches = (("left", source, left), ("right", source, right), ("final-left", left, final), ("final-right", right, final))
    reverse = build(tuple(reversed(branches)))
    forward = build(branches)
    first, first_outputs, _ = forward.recompute(changed_refs=(source,), prior_states=prior, input_states=inputs, stale_refs=(), run_id="diamond", at=NOW)
    second, second_outputs, _ = reverse.recompute(changed_refs=(source,), prior_states=prior, input_states=inputs, stale_refs=(), run_id="diamond", at=NOW)
    assert first.run_digest == second.run_digest
    assert first_outputs == second_outputs
    assert {item.state_ref for item in first_outputs} == {left, right, final}


def test_inference_recomputes_only_affected_region_and_support_is_not_probability():
    source_a, source_b = ref("source-a"), ref("source-b")
    out_a, out_b = ref("belief-a", "belief"), ref("belief-b", "belief")
    prior_a = state(belief_id="a", state_ref=out_a, value={"support": 0.0, "contradiction": 0.0})
    prior_b = state(belief_id="b", state_ref=out_b, value={"support": 0.0, "contradiction": 0.0})
    input_a = state(belief_id="source-a", state_ref=source_a, value={"support": 0.8, "contradiction": 0.2})
    input_b = state(belief_id="source-b", state_ref=source_b, value={"support": 0.9, "contradiction": 0.0})
    engine = BeliefInferenceEngine()
    for fid, source, output in (("support-a", source_a, out_a), ("support-b", source_b, out_b)):
        engine.register_factor(factor(factor_id=fid, method="support_contradiction_aggregation", input_ref=source, output_ref=output))
        engine.register_edge(create_inference_edge(input_ref=source, output_ref=output))
    run, outputs, _ = engine.recompute(
        changed_refs=(source_a,), prior_states={out_a.exact_key: prior_a, out_b.exact_key: prior_b},
        input_states={source_a.exact_key: input_a, source_b.exact_key: input_b}, stale_refs=(), run_id="support-run", at=NOW,
    )
    assert run.affected_outputs == (out_a,)
    assert outputs[0].semantics == "support_contradiction"
    assert outputs[0].value == {"support": 0.8, "contradiction": 0.2}
    assert out_b not in run.recomputed_outputs


def test_projection_dependency_region_can_drive_recompute_without_a_second_store():
    source, output = ref("projection-source"), ref("projection-belief", "belief")
    prior = state(belief_id="projection", state_ref=output, value={"support": 0.0, "contradiction": 0.0})
    evidence = state(belief_id="projection-source", state_ref=source, value={"support": 0.4, "contradiction": 0.0})
    engine = BeliefInferenceEngine()
    engine.register_factor(factor(factor_id="projection-factor", method="support_contradiction_aggregation", input_ref=source, output_ref=output))
    run, outputs, _ = engine.recompute(
        changed_refs=(source,), prior_states={output.exact_key: prior},
        input_states={source.exact_key: evidence}, stale_refs=(), run_id="projection-run", at=NOW,
        dependency_region=lambda changed: (output,) if changed.exact_key == source.exact_key else (),
    )
    assert run.affected_outputs == (output,)
    assert outputs[0].value == {"support": 0.4, "contradiction": 0.0}


def test_incompatible_semantics_and_factor_replay_are_rejected():
    source = ref("source")
    output = ref("output", "belief")
    engine = BeliefInferenceEngine()
    engine.register_factor(factor(factor_id="lr", method="likelihood_ratio", input_ref=source, output_ref=output, ratio=2.0))
    engine.register_edge(create_inference_edge(input_ref=source, output_ref=output))
    engine.register_factor(factor(factor_id="support", method="support_contradiction_aggregation", input_ref=source, output_ref=output))
    prior = state(belief_id="target", state_ref=output, value=0.5, semantics="calibrated_probability")
    source_state = state(belief_id="source", state_ref=source, value={"support": 1.0, "contradiction": 0.0})
    with pytest.raises(ValueError, match="incompatible factor methods"):
        engine.recompute(
            changed_refs=(source,), prior_states={output.exact_key: prior},
            input_states={source.exact_key: source_state}, stale_refs=(), run_id="bad", at=NOW,
        )
    with pytest.raises(ValueError, match="another version"):
        engine.register_factor(factor(factor_id="lr", method="likelihood_ratio", input_ref=source, output_ref=output, ratio=4.0))

    numeric_state = state(belief_id="numeric", state_ref=source, value=0.5, semantics="calibrated_probability")
    support_engine = BeliefInferenceEngine()
    support_engine.register_factor(factor(factor_id="support-only", method="support_contradiction_aggregation", input_ref=source, output_ref=output))
    support_engine.register_edge(create_inference_edge(input_ref=source, output_ref=output))
    with pytest.raises(ValueError, match="support/contradiction inputs"):
        support_engine.recompute(
            changed_refs=(source,), prior_states={output.exact_key: state(belief_id="support-target", state_ref=output, value={"support": 0.0, "contradiction": 0.0})},
            input_states={source.exact_key: numeric_state}, stale_refs=(), run_id="semantic-bad", at=NOW,
        )

    duplicate_engine = BeliefInferenceEngine()
    duplicate_engine.register_factor(factor(factor_id="duplicate-a", method="likelihood_ratio", input_ref=source, output_ref=output, ratio=2.0))
    duplicate_engine.register_factor(factor(factor_id="duplicate-b", method="likelihood_ratio", input_ref=source, output_ref=output, ratio=3.0))
    duplicate_engine.register_edge(create_inference_edge(input_ref=source, output_ref=output))
    with pytest.raises(ValueError, match="unique"):
        duplicate_engine.recompute(
            changed_refs=(source,), prior_states={output.exact_key: prior},
            input_states={source.exact_key: numeric_state}, stale_refs=(), run_id="duplicate", at=NOW,
        )


def test_belief_value_ranges_and_cycles_are_rejected():
    source, output = ref("range-source"), ref("range-output", "belief")
    with pytest.raises(ValueError, match="between zero and one"):
        state(belief_id="bad-probability", state_ref=output, value=1.5, semantics="calibrated_probability")
    with pytest.raises(ValueError, match="boolean"):
        state(belief_id="bad-rule", state_ref=output, value=0.5, semantics="deterministic_rule")
    engine = BeliefInferenceEngine()
    engine.register_edge(create_inference_edge(input_ref=source, output_ref=output))
    with pytest.raises(ValueError, match="cycles"):
        engine.register_edge(create_inference_edge(input_ref=output, output_ref=source))


def test_public_output_identity_binds_full_factor_definition():
    source, output = ref("identity-source"), ref("identity-output", "belief")
    base = factor(factor_id="same-id", method="likelihood_ratio", input_ref=source, output_ref=output, ratio=2.0)
    changed = base.model_copy(update={"factor_version": "1.0.1", "config_digest": "sha256:" + "e" * 64})
    assert BeliefProjectService._output_ref("same-candidate", (base,)).exact_key != BeliefProjectService._output_ref("same-candidate", (changed,)).exact_key


def test_public_trusted_domain_operator_dispatches_exact_inputs():
    registry = DomainExtensionRegistry()
    register_belief_extension(registry)
    source = ref("operator-input")

    class Allow:
        def authorize(self, candidate):
            assert candidate.resource_id in {"operator-input", "operator-evidence"}

    result = registry.dispatch(
        "belief.posterior-likelihood-ratio",
        "1.0.0",
        (
            (source, {"role": "prior", "semantics": "calibrated_probability", "value": 0.2}),
            (ref("operator-evidence"), {"role": "evidence", "likelihood_ratio": 3.0}),
        ),
        config={"factor_ids": ["factor-operator"], "conditional_independence": True},
        authorizer=Allow(),
    )
    assert result.output["posterior_probability"] == pytest.approx(3 / 7)
    assert tuple(item.resource_id for item in result.input_refs) == ("operator-input", "operator-evidence")


def test_public_temporal_belief_journey_reviews_restarts_and_recomputes_stale_inputs(tmp_path):
    projection = TemporalProjectionService(str(tmp_path / "belief.json"), tenant_id="tenant", project_id="project", clock=lambda: NOW)

    class Writer:
        def authorize(self, record, *, at):
            return None

    class Invalidator:
        def authorize_invalidation(self, event_id, changed_ref, event_digest, *, at):
            return None

    def evidence(record_id: str, support: float, *, valid_from=NOW, valid_to=None):
        return create_temporal_record(
            record_id=record_id, entity_id=record_id, entity_authority="krail://fixture",
            payload_schema="fixture.evidence", payload_schema_version="v1", kind="observation",
            authority="krail://fixture", writer_family="fixture", valid_from=valid_from, valid_to=valid_to, recorded_at=NOW,
            source_refs=(ref(record_id, "raw"),), revision="1", visibility="internal",
            payload={"semantics": "support_contradiction", "value": {"support": support, "contradiction": 0.0}},
        )

    first_evidence, second_evidence = evidence("journey-a", 0.8, valid_to=NOW.replace(day=8)), evidence("journey-b", 0.7)
    writer = Writer()
    projection.ingest(first_evidence, at=NOW, writer=writer)
    projection.ingest(second_evidence, at=NOW, writer=writer)
    first_ref, second_ref = projection.record_ref(first_evidence), projection.record_ref(second_evidence)
    candidate_id = "journey-belief"
    factor_a = factor(factor_id="journey-a-factor", method="likelihood_ratio", input_ref=first_ref, output_ref=first_ref, ratio=3.0)
    factor_b = factor(factor_id="journey-b-factor", method="likelihood_ratio", input_ref=second_ref, output_ref=second_ref, ratio=5.0)
    output_ref = BeliefProjectService._output_ref(candidate_id, (factor_a, factor_b))
    factor_a = factor_a.model_copy(update={"output_ref": output_ref})
    factor_b = factor_b.model_copy(update={"output_ref": output_ref})
    prior = state(belief_id=candidate_id, state_ref=output_ref, value=0.2, semantics="calibrated_probability")
    service = BeliefProjectService(projection)
    candidate = service.propose(candidate_id=candidate_id, prior=prior, factors=(factor_a, factor_b), at=NOW, writer=writer)
    service.review_promote(candidate, reviewer_id="reviewer-1", at=NOW, writer=writer)
    denied_run, denied_outputs, _ = service.recompute(
        valid_at=NOW, known_at=NOW, at=NOW, writer=writer,
        authorize=lambda _ref: (_ for _ in ()).throw(PermissionError("source denied")),
    )
    assert denied_outputs == ()
    assert denied_run.affected_outputs == ()
    run, outputs, _ = service.recompute(valid_at=NOW, known_at=NOW, at=NOW, writer=writer, authorize=lambda _ref: None)
    assert outputs[0].value == pytest.approx(15 / 19)
    assert run.recomputed_outputs == (output_ref,)
    assert any(record.payload_schema == service.DERIVATION_SCHEMA for record in projection.temporal_records())

    restarted = BeliefProjectService(TemporalProjectionService(str(tmp_path / "belief.json"), tenant_id="tenant", project_id="project", clock=lambda: NOW))
    stable_run, stable_outputs, _ = restarted.recompute(valid_at=NOW, known_at=NOW, at=NOW, writer=writer, authorize=lambda _ref: None)
    assert stable_outputs == ()
    assert stable_run.affected_outputs == ()
    assert restarted._records(restarted.DERIVATION_SCHEMA)
    assert restarted.authorized_explanations(valid_at=NOW, known_at=NOW, authorize=lambda _ref: None)
    assert restarted.authorized_explanations(
        valid_at=NOW, known_at=NOW,
        authorize=lambda _ref: (_ for _ in ()).throw(PermissionError("explanation denied")),
    ) == ()
    before_write = len(projection.temporal_records())
    calls = 0
    def revoked_during_write(_ref):
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise PermissionError("review revoked before write")
    race_run, race_outputs, _ = service.recompute(
        valid_at=NOW, known_at=NOW, at=NOW, writer=writer, authorize=revoked_during_write,
    )
    assert race_outputs == ()
    assert race_run.recomputed_outputs == ()
    assert len(projection.temporal_records()) == before_write

    expired_run, expired_outputs, _ = BeliefProjectService(projection).recompute(
        valid_at=NOW.replace(day=9), known_at=NOW, at=NOW, writer=writer, authorize=lambda _ref: None,
    )
    assert expired_outputs[0].value == pytest.approx(5 / 9)
    assert expired_run.recomputed_outputs == (output_ref,)
    explanations_after_expiry = BeliefProjectService(projection).authorized_explanations(
        valid_at=NOW.replace(day=9), known_at=NOW, authorize=lambda _ref: None,
    )
    assert explanations_after_expiry
    assert all(item.input_refs == (second_ref,) for item in explanations_after_expiry)

    projection.tombstone(first_ref, event_id="journey-revoke-a", reason="evidence withdrawn", effective_at=NOW, recorded_at=NOW, authorizer=Invalidator())
    revoked_run, revoked_outputs, _ = BeliefProjectService(projection).recompute(valid_at=NOW, known_at=NOW, at=NOW, writer=writer, authorize=lambda _ref: None)
    assert revoked_outputs == ()
    assert revoked_run.unchanged_outputs == (output_ref,)


def test_public_temporal_belief_ignores_future_expired_and_revoked_reviews(tmp_path):
    projection = TemporalProjectionService(str(tmp_path / "time-aware.json"), tenant_id="tenant", project_id="project", clock=lambda: NOW)

    class Writer:
        def authorize(self, record, *, at):
            return None

    class Invalidator:
        def authorize_invalidation(self, event_id, changed_ref, event_digest, *, at):
            return None

    future = create_temporal_record(
        record_id="future-evidence", entity_id="future-evidence", entity_authority="krail://fixture",
        payload_schema="fixture.evidence", payload_schema_version="v1", kind="observation",
        authority="krail://fixture", writer_family="fixture", valid_from=NOW.replace(day=8), recorded_at=NOW,
        source_refs=(ref("future-evidence", "raw"),), revision="1", visibility="internal",
        payload={"semantics": "deterministic_rule", "value": True},
    )
    projection.ingest(future, at=NOW, writer=Writer())
    future_ref = projection.record_ref(future)
    candidate_id = "time-aware-belief"
    pending_factor = factor(factor_id="time-factor", method="likelihood_ratio", input_ref=future_ref, output_ref=future_ref, ratio=3.0)
    output_ref = BeliefProjectService._output_ref(candidate_id, (pending_factor,))
    pending_factor = pending_factor.model_copy(update={"output_ref": output_ref})
    service = BeliefProjectService(projection)
    candidate = service.propose(candidate_id=candidate_id, prior=state(belief_id=candidate_id, state_ref=output_ref, value=0.2, semantics="calibrated_probability"), factors=(pending_factor,), at=NOW, writer=Writer())
    service.review_promote(candidate, reviewer_id="reviewer", at=NOW, writer=Writer())
    run, outputs, _ = service.recompute(valid_at=NOW, known_at=NOW, at=NOW, writer=Writer(), authorize=lambda _ref: None)
    assert outputs == ()
    assert run.recomputed_outputs == ()
    future_run, future_outputs, _ = service.recompute(valid_at=NOW.replace(day=8), known_at=NOW, at=NOW, writer=Writer(), authorize=lambda _ref: None)
    assert future_outputs[0].value == pytest.approx(3 / 7)

    approved = next(record for record in projection.temporal_records() if record.payload_schema == service.APPROVED_SCHEMA)
    projection.tombstone(projection.record_ref(approved), event_id="review-revoked", reason="review withdrawn", effective_at=NOW, recorded_at=NOW, authorizer=Invalidator())
    revoked_run, revoked_outputs, _ = BeliefProjectService(projection).recompute(valid_at=NOW.replace(day=8), known_at=NOW, at=NOW, writer=Writer(), authorize=lambda _ref: None)
    assert revoked_outputs == ()
    assert revoked_run.affected_outputs == ()
    assert BeliefProjectService(projection).authorized_explanations(
        valid_at=NOW.replace(day=8), known_at=NOW, authorize=lambda _ref: None,
    ) == ()


def test_public_temporal_two_candidate_graph_recomputes_upstream_and_downstream(tmp_path):
    projection = TemporalProjectionService(str(tmp_path / "graph.json"), tenant_id="tenant", project_id="project", clock=lambda: NOW)

    class Writer:
        def authorize(self, record, *, at):
            return None

    class Invalidator:
        def authorize_invalidation(self, event_id, changed_ref, event_digest, *, at):
            return None

    source = create_temporal_record(
        record_id="graph-source", entity_id="graph-source", entity_authority="krail://fixture",
        payload_schema="fixture.evidence", payload_schema_version="v1", kind="observation",
        authority="krail://fixture", writer_family="fixture", valid_from=NOW, recorded_at=NOW,
        source_refs=(ref("graph-source", "raw"),), revision="1", visibility="internal",
        payload={"semantics": "deterministic_rule", "value": True},
    )
    unrelated = create_temporal_record(
        record_id="graph-unrelated", entity_id="graph-unrelated", entity_authority="krail://fixture",
        payload_schema="fixture.evidence", payload_schema_version="v1", kind="observation",
        authority="krail://fixture", writer_family="fixture", valid_from=NOW, recorded_at=NOW,
        source_refs=(ref("graph-unrelated", "raw"),), revision="1", visibility="internal",
        payload={"semantics": "support_contradiction", "value": {"support": 1.0, "contradiction": 0.0}},
    )
    projection.ingest(source, at=NOW, writer=Writer())
    projection.ingest(unrelated, at=NOW, writer=Writer())
    source_ref, unrelated_ref = projection.record_ref(source), projection.record_ref(unrelated)
    upstream_factor = factor(factor_id="graph-upstream-factor", method="deterministic_implication", input_ref=source_ref, output_ref=source_ref)
    upstream_ref = BeliefProjectService._output_ref("graph-upstream", (upstream_factor,))
    upstream_factor = upstream_factor.model_copy(update={"output_ref": upstream_ref})
    downstream_factor = factor(factor_id="graph-downstream-factor", method="deterministic_implication", input_ref=upstream_ref, output_ref=upstream_ref)
    downstream_ref = BeliefProjectService._output_ref("graph-downstream", (downstream_factor,))
    downstream_factor = downstream_factor.model_copy(update={"output_ref": downstream_ref})
    unrelated_factor = factor(factor_id="graph-unrelated-factor", method="likelihood_ratio", input_ref=unrelated_ref, output_ref=unrelated_ref, ratio=2.0)
    unrelated_output = BeliefProjectService._output_ref("graph-unrelated-output", (unrelated_factor,))
    unrelated_factor = unrelated_factor.model_copy(update={"output_ref": unrelated_output})
    service = BeliefProjectService(projection)
    for candidate_id, prior_value, factors in (
        ("graph-upstream", False, (upstream_factor,)),
        ("graph-downstream", True, (downstream_factor,)),
        ("graph-unrelated-output", 0.2, (unrelated_factor,)),
    ):
        semantics = "deterministic_rule" if candidate_id in {"graph-upstream", "graph-downstream"} else "calibrated_probability"
        candidate = service.propose(candidate_id=candidate_id, prior=state(belief_id=candidate_id, state_ref=BeliefProjectService._output_ref(candidate_id, factors), value=prior_value, semantics=semantics), factors=factors, at=NOW, writer=Writer())
        service.review_promote(candidate, reviewer_id="graph-reviewer", at=NOW, writer=Writer())
    first_run, first_outputs, _ = service.recompute(valid_at=NOW, known_at=NOW, at=NOW, writer=Writer(), authorize=lambda _ref: None)
    assert {item.state_ref for item in first_outputs} == {upstream_ref, downstream_ref, unrelated_output}
    explanations = service.authorized_explanations(valid_at=NOW, known_at=NOW, authorize=lambda _ref: None)
    assert {item.factor_ref.resource_id for item in explanations} == {"graph-upstream-factor", "graph-downstream-factor", "graph-unrelated-factor"}
    denied_explanations = service.authorized_explanations(
        valid_at=NOW, known_at=NOW,
        authorize=lambda item: (_ for _ in ()).throw(PermissionError("upstream source denied")) if item.resource_id == "graph-source" else None,
    )
    assert {item.factor_ref.resource_id for item in denied_explanations} == {"graph-unrelated-factor"}
    derived_refs = {projection.record_ref(record).exact_key for record in projection.temporal_records() if record.payload_schema == service.DERIVED_SCHEMA}
    denied_derived = service.authorized_explanations(
        valid_at=NOW, known_at=NOW,
        authorize=lambda item: (_ for _ in ()).throw(PermissionError("derived row denied")) if item.exact_key in derived_refs else None,
    )
    assert denied_derived == ()
    projection.tombstone(source_ref, event_id="graph-source-revoke", reason="source withdrawn", effective_at=NOW, recorded_at=NOW, authorizer=Invalidator())
    second_run, second_outputs, _ = BeliefProjectService(projection).recompute(valid_at=NOW, known_at=NOW, at=NOW, writer=Writer(), authorize=lambda _ref: None)
    assert {item.state_ref for item in second_outputs} == {upstream_ref, downstream_ref}
    assert unrelated_output not in second_run.affected_outputs
    restarted = BeliefProjectService(TemporalProjectionService(str(tmp_path / "graph.json"), tenant_id="tenant", project_id="project", clock=lambda: NOW))
    stable_run, stable_outputs, _ = restarted.recompute(valid_at=NOW, known_at=NOW, at=NOW, writer=Writer(), authorize=lambda _ref: None)
    assert stable_outputs == ()
    assert set(stable_run.affected_outputs) == {upstream_ref, downstream_ref}
