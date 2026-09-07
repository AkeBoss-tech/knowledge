"""Executable fixtures for the proposed opt-in rich-domain read contract."""

from __future__ import annotations

import json
import importlib.util
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


CONTRACT = (
    Path(__file__).parents[3]
    / "docs/contracts/krail.rich-domain-read.v1"
)
ASSET_CONTRACT = Path(__file__).parents[3] / "docs/contracts/krail.asset-read.v1"


def _cases(name: str) -> list[dict[str, object]]:
    return json.loads((CONTRACT / "fixtures" / name).read_text(encoding="utf-8"))


def test_rich_domain_read_contract_validates_robotics_and_company_examples() -> None:
    schema = json.loads((CONTRACT / "schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())

    cases = _cases("valid.json")

    assert {case["name"] for case in cases} >= {
        "robotics-immutable-asset-metadata",
        "robotics-spatial-query-with-frame-and-revision",
        "company-effective-tuesday-learned-thursday",
        "robotics-asset-response-has-auth-snapshot-and-lineage",
        "company-temporal-response-keeps-effective-and-recorded-times",
        "spatial-abstention-is-explicit-and-page-bound",
        "incompatible-negotiation-has-deterministic-shape",
        "unavailable-error-is-bounded",
        "unauthorized-error-hides-auth-snapshot-and-lineage",
    }
    for case in cases:
        assert list(validator.iter_errors(case["value"])) == [], case["name"]


def test_rich_domain_read_contract_rejects_unbounded_or_wrong_contract_requests() -> None:
    schema = json.loads((CONTRACT / "schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())

    for case in _cases("invalid.json"):
        assert list(validator.iter_errors(case["value"])), case["name"]


def test_asset_read_contract_is_bounded_and_its_fixtures_are_executable() -> None:
    schema = json.loads((ASSET_CONTRACT / "schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for filename, expected_errors in (("valid.json", False), ("invalid.json", True)):
        cases = json.loads((ASSET_CONTRACT / "fixtures" / filename).read_text(encoding="utf-8"))
        for case in cases:
            assert bool(list(validator.iter_errors(case["value"]))) is expected_errors, case["name"]


def test_asset_read_semantic_fixtures_verify_bytes_and_immutable_digest_rules() -> None:
    spec = importlib.util.spec_from_file_location("asset_read_conformance", ASSET_CONTRACT / "conformance.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    valid = json.loads((ASSET_CONTRACT / "fixtures" / "valid.json").read_text(encoding="utf-8"))
    responses = [case for case in valid if "returned_bytes" in case["value"]]
    for case in responses:
        module.validate_response(case["value"])
    invalid = json.loads((ASSET_CONTRACT / "fixtures" / "semantic-invalid.json").read_text(encoding="utf-8"))
    for case in invalid:
        try:
            module.validate_response(case["value"])
        except ValueError:
            continue
        raise AssertionError(case["name"])


def test_rich_domain_negotiation_decision_table_is_deterministic() -> None:
    spec = importlib.util.spec_from_file_location("rich_domain_conformance", CONTRACT / "conformance.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    rows = json.loads((CONTRACT / "fixtures" / "negotiation-decision-table.json").read_text(encoding="utf-8"))
    for row in rows:
        result = module.evaluate_negotiation(
            published_descriptor_digest="sha256:" + "a" * 64,
            published_operations=("asset_metadata", "temporal_query", "spatial_query"),
            consumer_version=row["consumer_version"],
            expected_descriptor_digest=row["expected_descriptor_digest"],
            requested_operations=tuple(row["requested_operations"]),
        )
        assert (result.compatible, result.operations, result.diagnostic) == (
            row["compatible"], tuple(row["operations"]), row["diagnostic"]
        ), row["name"]
