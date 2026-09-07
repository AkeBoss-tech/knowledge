"""Executable fixtures for the proposed opt-in rich-domain read contract."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


CONTRACT = (
    Path(__file__).parents[3]
    / "docs/contracts/krail.rich-domain-read.v1"
)


def _cases(name: str) -> list[dict[str, object]]:
    return json.loads((CONTRACT / "fixtures" / name).read_text(encoding="utf-8"))


def test_rich_domain_read_contract_validates_robotics_and_company_examples() -> None:
    schema = json.loads((CONTRACT / "schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())

    cases = _cases("valid.json")

    assert {case["name"] for case in cases} >= {
        "robotics-immutable-asset-metadata",
        "robotics-spatial-query-with-frame-and-revision",
        "company-effective-tuesday-learned-thursday",
        "robotics-asset-response-has-auth-snapshot-and-lineage",
        "company-temporal-response-keeps-effective-and-recorded-times",
    }
    for case in cases:
        assert list(validator.iter_errors(case["value"])) == [], case["name"]


def test_rich_domain_read_contract_rejects_unbounded_or_wrong_contract_requests() -> None:
    schema = json.loads((CONTRACT / "schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())

    for case in _cases("invalid.json"):
        assert list(validator.iter_errors(case["value"])), case["name"]
