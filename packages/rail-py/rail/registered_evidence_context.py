"""Bounded context reads over explicitly reviewed Git and caller-owned resources.

This adapter creates no records or grants. Its caller owns project membership,
source selection, operational bytes and live exact-resource authorization.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256

from krail.provider.v1 import (
    GetResourceRequest, GetResourceResult, MAX_RESOURCE_BYTES, ResourcePayload,
    ResourceRef, SearchHit, SearchRequest, SearchResult,
)
from rail.context_brief import MAX_CONTEXT_ITEMS
from rail.registered_git_evidence import RegisteredGitEvidenceBridge


@dataclass(frozen=True)
class RegisteredEvidenceSelection:
    capture_id: str
    exact_ref: ResourceRef


class RegisteredEvidenceContextReader:
    """Request-scoped composite reader; retained plaintext is never cached.

    ``read_operational`` must return complete exact content, under current Core
    authorization. ``authorize`` applies to all exact refs before and after
    reads. Search ranks only explicitly selected documents; a zero lexical
    score does not undo the caller's explicit selection.
    """

    def __init__(
        self, *, bridge: RegisteredGitEvidenceBridge, subject: str,
        selections: tuple[RegisteredEvidenceSelection, ...],
        operational_refs: tuple[ResourceRef, ...],
        read_operational: Callable[[GetResourceRequest], GetResourceResult],
        authorize: Callable[[ResourceRef], None],
        max_source_bytes: int = MAX_RESOURCE_BYTES,
        max_total_source_bytes: int = 4 * MAX_RESOURCE_BYTES,
    ) -> None:
        refs = (*operational_refs, *(item.exact_ref for item in selections))
        if not subject.strip() or not 2 <= len(refs) <= MAX_CONTEXT_ITEMS:
            raise ValueError('context requires 2..32 explicitly selected resources')
        if len({ref.resource_id for ref in refs}) != len(refs):
            raise ValueError('resource IDs must be unique across selected authorities and revisions')
        if len({item.capture_id for item in selections}) != len(selections):
            raise ValueError('capture IDs must be unique')
        if any(not item.capture_id or item.exact_ref.resource_type != 'git.file' for item in selections):
            raise ValueError('reviewed selections require exact Git file refs')
        if not 1 <= max_source_bytes <= MAX_RESOURCE_BYTES or not 1 <= max_total_source_bytes <= 32 * MAX_RESOURCE_BYTES:
            raise ValueError('source byte bounds exceed reader limits')
        self._bridge, self._subject = bridge, subject
        self._selections = tuple(selections)
        self._documents = {item.exact_ref.exact_key: item for item in selections}
        self._operational = {ref.exact_key for ref in operational_refs}
        self._read_operational, self._authorize = read_operational, authorize
        self._max_source_bytes, self._max_total_source_bytes = max_source_bytes, max_total_source_bytes

    def _read(self, ref: ResourceRef) -> tuple[bytes, str]:
        selection = self._documents.get(ref.exact_key)
        if selection is None and ref.exact_key not in self._operational:
            raise PermissionError('selected evidence unavailable')
        self._authorize(ref)
        if selection is not None:
            retained = self._bridge.retrieve(user_id=self._subject, capture_id=selection.capture_id)
            if retained.source_ref.exact_key != ref.exact_key or not retained.review_id:
                raise PermissionError('selected evidence unavailable')
            raw, media_type = retained.content, 'text/plain'
        else:
            result = self._read_operational(GetResourceRequest(ref=ref, max_bytes=self._max_source_bytes))
            payload = result.resource
            if payload.ref.exact_key != ref.exact_key or payload.truncated:
                raise PermissionError('complete exact operational evidence required')
            raw, media_type = payload.content.encode('utf-8'), payload.media_type
        if len(raw) > self._max_source_bytes:
            raise ValueError('selected source exceeds byte bound')
        if 'sha256:' + sha256(raw).hexdigest() != ref.digest:
            raise PermissionError('selected evidence unavailable')
        # Do not silently repair invalid encoding and change the source bytes.
        raw.decode('utf-8')
        self._authorize(ref)
        return raw, media_type

    def get_resource(self, request: GetResourceRequest) -> GetResourceResult:
        raw, media_type = self._read(request.ref)
        content = raw[:request.max_bytes].decode('utf-8', errors='ignore')
        result = GetResourceResult(resource=ResourcePayload(
            ref=request.ref, media_type=media_type, content=content,
            truncated=len(raw) > request.max_bytes,
        ))
        self._authorize(request.ref)
        return result

    def search(self, request: SearchRequest) -> SearchResult:
        if request.cursor is not None:
            raise ValueError('selected evidence search does not support cursors')
        hits: list[SearchHit] = []
        total = 0
        words = set(request.query.casefold().split())
        for selection in self._selections:
            ref = selection.exact_ref
            if request.resource_types and ref.resource_type not in request.resource_types:
                continue
            raw, _ = self._read(ref)
            total += len(raw)
            if total > self._max_total_source_bytes:
                raise ValueError('selected evidence search exceeds total byte bound')
            content = raw.decode('utf-8')
            # EvidenceItem relevance is a fraction in [0, 1]. Dividing by
            # the same query-term count preserves ordering and deterministic ties.
            score = sum(word in content.casefold() for word in words) / len(words)
            hits.append(SearchHit(ref=ref, title=ref.resource_id[:1024], score=score))
        hits.sort(key=lambda hit: (-hit.score, hit.ref.exact_key))
        selected = hits[:request.limit]
        result = SearchResult(hits=selected, truncated=len(hits) > request.limit)
        # Even omitted hits contribute to truncation and ordering metadata.
        for hit in hits:
            self._authorize(hit.ref)
        return result
