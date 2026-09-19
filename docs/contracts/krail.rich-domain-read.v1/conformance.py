"""Pure design-time negotiation evaluator for rich-domain conformance fixtures.

This is not a provider runtime or registration mechanism.  It freezes the
deterministic decision rule proposed by the contract using the existing
descriptor convention: supported major range plus optional immutable digest pin.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_V1 = re.compile(r"^1\.\d+\.\d+$")


@dataclass(frozen=True)
class NegotiationResult:
    compatible: bool
    operations: tuple[str, ...]
    diagnostic: str


def evaluate_negotiation(
    *,
    published_descriptor_digest: str,
    published_operations: tuple[str, ...],
    consumer_version: str,
    expected_descriptor_digest: str | None,
    requested_operations: tuple[str, ...],
) -> NegotiationResult:
    if not _V1.fullmatch(consumer_version):
        return NegotiationResult(False, (), "consumer version is outside the advertised compatibility range")
    if expected_descriptor_digest is not None and expected_descriptor_digest != published_descriptor_digest:
        return NegotiationResult(False, (), "expected descriptor digest does not match published descriptor")
    if not set(requested_operations).issubset(published_operations):
        return NegotiationResult(False, (), "requested operation is not advertised")
    return NegotiationResult(True, tuple(item for item in published_operations if item in requested_operations), "compatible")
