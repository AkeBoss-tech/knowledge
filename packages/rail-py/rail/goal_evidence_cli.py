"""Owner-operated exact accepted Goal evidence capture and recall (loopback only)."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import sys
from urllib.parse import urlsplit

from krail.provider.v1 import ResourceRef
from rail.observed_run_artifact import ObservedRunArtifactRepository
from rail.opensaddle_goal_run_artifact import OpenSaddleGoalReviewedRunArtifactSource


def _token(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
            raise PermissionError('credential file must be owner-only and not hard-linked')
        data = os.read(fd, 8194)
        if len(data) > 8192:
            raise ValueError('credential is too large')
        token = data.decode('ascii').strip()
        if not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ValueError('credential is invalid')
        return token
    finally:
        os.close(fd)


def _exists_at(directory, name):
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def _private_store(path: str) -> str:
    # Walk without following links. A private owner-controlled directory fences
    # other local users between validation and the existing store's path opens.
    target = Path(os.path.abspath(path))
    if target.name in {'', '.', '..'}:
        raise ValueError('store path is invalid')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open('/', flags)
    try:
        for component in target.parent.parts[1:]:
            next_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd); fd = next_fd
            info = os.fstat(fd)
            if info.st_uid not in {0, os.getuid()} or (info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)):
                raise PermissionError('store ancestor is not controlled')
        directory = os.fstat(fd)
        if directory.st_uid != os.getuid() or directory.st_mode & 0o077:
            raise PermissionError('store directory must be owner-only')
        lock_fd = os.open(target.name + '.lock', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd) if not _exists_at(fd, target.name + '.lock') else None
        if lock_fd is not None: os.close(lock_fd)
        for name in (target.name, target.name + '.lock'):
            try:
                file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                continue
            try:
                info = os.fstat(file_fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
                    raise PermissionError('store file must be private and unlinked elsewhere')
            finally:
                os.close(file_fd)
    finally:
        os.close(fd)
    return str(target)


class _ExactSelection:
    def __init__(self, refs):
        self.refs = frozenset(ref.exact_key for ref in refs)
    def authorize(self, ref, *, at=None):
        if ref.exact_key not in self.refs:
            raise PermissionError('reference is outside the explicit selection')


def execute(args):
    url = urlsplit(args.core_origin)
    if (url.scheme != 'http' or url.hostname not in {'127.0.0.1', '::1'} or not url.port
            or url.username or url.password or url.path not in {'', '/'} or url.query or url.fragment):
        raise ValueError('Core origin must be an explicit loopback HTTP origin')
    if not re.fullmatch(r'[0-9a-f]{64}', args.artifact_digest):
        raise ValueError('exact artifact SHA-256 is required')
    if not args.trust_local_core or not args.dedicated_short_lived_credential:
        raise PermissionError('explicit local endpoint trust and dedicated short-lived credential required')
    store = _private_store(args.store)
    token = _token(Path(args.token_file))
    source = OpenSaddleGoalReviewedRunArtifactSource.pin(
        goal_id=args.goal_id, run_id=args.run_id, observer_subject=args.observer,
        expected_review_revision=args.review_revision, expected_artifact_id=args.artifact_id,
        expected_artifact_digest=args.artifact_digest, base_url=args.core_origin,
        bearer_token=token, authority=args.authority, project_id=args.project_id)
    observation = source.identify(args.run_id)
    repo = ObservedRunArtifactRepository(store, tenant_id=args.tenant, project_id=args.project_id)
    refs = [observation.run_ref, observation.artifact_ref]
    if args.operation == 'capture':
        result = source.observe(repo, authorizer=_ExactSelection(refs), at=datetime.now(timezone.utc))
        return {'evidence_ref':result.exact_ref().model_dump(mode='json'), 'evidence_state':result.evidence_state,
                'human_review':'exact_accepted_goal_result', 'content_included':False}
    digest = args.evidence_digest
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError('exact evidence digest is required')
    ref = ResourceRef(authority='krail://observed-run-artifact',resource_type='observed-run-artifact',
        resource_id=digest,version='krail.observed-run-artifact.v1',digest=digest)
    authorizer = source.authorizer(_ExactSelection([*refs, ref]))
    result, = repo.recall((ref,), source=source, authorizer=authorizer)
    raw = source.read_artifact(result.observation.artifact_ref, max_bytes=512000)
    if source.identify(args.run_id) != result.observation:
        raise PermissionError('Goal evidence changed')
    import base64
    return {'evidence_ref':ref.model_dump(mode='json'), 'evidence_state':result.evidence_state,
            'human_review':'exact_accepted_goal_result', 'content_base64':base64.b64encode(raw).decode('ascii')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['capture','recall'])
    for name in ('core-origin','token-file','authority','project-id','goal-id','run-id','observer','artifact-id','artifact-digest','tenant','store'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--review-revision', required=True, type=int)
    parser.add_argument('--evidence-digest')
    parser.add_argument('--trust-local-core', action='store_true')
    parser.add_argument('--dedicated-short-lived-credential', action='store_true')
    args = parser.parse_args()
    try:
        if args.operation == 'recall' and not args.evidence_digest:
            raise ValueError('exact evidence digest is required')
        print(json.dumps(execute(args), sort_keys=True))
    except (ValueError, PermissionError, OSError, LookupError, TimeoutError):
        print('Goal evidence is unavailable or the selection is invalid.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
