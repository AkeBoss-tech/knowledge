"""Request-scoped local selected-file reader using the KRAIL v1 read shapes.

This is a bounded live scan, not a persistent index, identity provider, model,
or a grant to read a filesystem. The caller owns selection and authorization.
"""
from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from urllib.parse import quote

from krail.provider.v1 import (
    EvidenceItem, GetResourceRequest, GetResourceResult, MAX_EVIDENCE_ITEM_BYTES,
    MAX_RESOURCE_BYTES, ResourcePayload, ResourceRef, SearchHit, SearchRequest, SearchResult,
)


def _open_canonical_root(path: Path) -> int:
    """Open each absolute path component without following symbolic links."""
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


_DEFAULT_TEXT_EXTENSIONS = frozenset({'.md', '.txt', '.rst', '.py', '.js', '.jsx', '.ts', '.tsx',
                                      '.json', '.csv', '.yaml', '.yml', '.toml'})
_EXCLUDED_DIRS = frozenset({'node_modules', 'vendor', 'build', 'dist', '__pycache__'})
_SENSITIVE_NAME = re.compile(r'(?:^|[._-])(?:secret|secrets|credential|credentials|token|tokens|private|id_rsa|id_ed25519|service[._-]account)(?:[._-]|$)', re.I)


def discover_text_files(root: str | Path, *, max_files: int = 256, max_entries: int = 4096,
                        max_depth: int = 12) -> tuple[str, ...]:
    """Propose text paths below a selected folder; discovery grants no read access.

    Hidden names, common generated directories, secret-like names, symlinks,
    hard links and special files are omitted. Limits fail closed rather than silently
    presenting a partial inventory. Callers must still explicitly select and
    authorize every file they pass to LocalProjectSources.
    """
    if not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY'):
        raise OSError('safe folder discovery is unavailable on this platform')
    requested = Path(root)
    if not requested.is_absolute() or requested.is_symlink():
        raise ValueError('root must be an absolute, non-symlink directory')
    if not 1 <= max_files <= 256 or not 1 <= max_entries <= 16384 or not 0 <= max_depth <= 32:
        raise ValueError('folder discovery bounds are invalid')
    root_path = requested.resolve(strict=True)
    if Path(os.path.normpath(requested)) != root_path:
        raise ValueError('root must be canonical and contain no symlink ancestors')
    root_fd = _open_canonical_root(root_path)
    root_device = os.fstat(root_fd).st_dev
    paths: list[str] = []
    visited = 0
    def visit(fd: int, prefix: str, depth: int) -> None:
        nonlocal visited
        children = []
        with os.scandir(fd) as entries:
            for entry in entries:
                visited += 1
                if visited > max_entries:
                    raise ValueError('folder discovery exceeds entry bound')
                children.append(entry)
        for entry in sorted(children, key=lambda entry: entry.name):
            name = entry.name
            if name.startswith('.') or name in _EXCLUDED_DIRS or _SENSITIVE_NAME.search(name):
                continue
            rel = f'{prefix}/{name}' if prefix else name
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                if depth >= max_depth:
                    raise ValueError('folder discovery exceeds depth bound')
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    if os.fstat(child).st_dev == root_device:
                        visit(child, rel, depth + 1)
                finally:
                    os.close(child)
            elif (entry.is_file(follow_symlinks=False) and entry.stat(follow_symlinks=False).st_dev == root_device
                  and entry.stat(follow_symlinks=False).st_nlink == 1
                  and Path(name).suffix.lower() in _DEFAULT_TEXT_EXTENSIONS):
                LocalProjectSources._validate_path(rel)
                paths.append(rel)
                if len(paths) > max_files:
                    raise ValueError('folder discovery exceeds file bound')
    try:
        visit(root_fd, '', 0)
    finally:
        os.close(root_fd)
    return tuple(sorted(paths))


class LocalProjectSources:
    """Read only explicitly selected text files below one selected directory.

    Every search reads the selected bytes afresh. Exact refs cannot be used
    after a file changes or disappears. The caller's authorization callback is
    called before each read and before return; it must raise on denial.
    POSIX descriptor traversal rejects symlinks and special files. No retained
    plaintext or historical version is stored by this adapter.
    """
    def __init__(self, *, root: str | Path, authority: str, selected_paths: tuple[str, ...],
                 authorize: Callable[[str], None], max_file_bytes: int = MAX_RESOURCE_BYTES,
                 max_total_bytes: int = 8 * MAX_RESOURCE_BYTES) -> None:
        if not callable(authorize):
            raise ValueError('a live authorization callback is required')
        if not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY'):
            raise OSError('safe selected-file traversal is unavailable on this platform')
        requested = Path(root)
        if not requested.is_absolute() or requested.is_symlink():
            raise ValueError('root must be an absolute, non-symlink directory')
        self._root = requested.resolve(strict=True)
        if Path(os.path.normpath(requested)) != self._root:
            raise ValueError('root must be canonical and contain no symlink ancestors')
        if not self._root.is_dir():
            raise ValueError('root must be a directory')
        if not 1 <= len(selected_paths) <= 256 or len(set(selected_paths)) != len(selected_paths):
            raise ValueError('select 1..256 distinct files')
        self._paths = tuple(self._validate_path(p) for p in selected_paths)
        if not 1 <= max_file_bytes <= MAX_RESOURCE_BYTES or not 1 <= max_total_bytes <= 32 * MAX_RESOURCE_BYTES:
            raise ValueError('source byte limits exceed provider bounds')
        if authority != authority.strip():
            raise ValueError('authority must not have surrounding whitespace')
        # Validate authority with the provider's canonical validation rules.
        ResourceRef(authority=authority, resource_type='local.file', resource_id='validation',
                    version='sha256:' + '0' * 64, digest='sha256:' + '0' * 64)
        self._authority, self._authorize = authority, authorize
        self._max_file_bytes, self._max_total_bytes = max_file_bytes, max_total_bytes
        fd = self._open_root()
        try:
            root_stat = os.fstat(fd)
            self._root_identity = (root_stat.st_dev, root_stat.st_ino)
        finally:
            os.close(fd)

    @staticmethod
    def _validate_path(value: str) -> str:
        if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
            raise ValueError('selected path must be a nonempty relative POSIX path')
        path = PurePosixPath(value)
        if path.is_absolute() or any(p in ('', '.', '..') for p in value.split('/')):
            raise ValueError('selected path must not escape the root')
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('selected path contains a control character')
        return value

    def _open_root(self) -> int:
        return _open_canonical_root(self._root)

    def _read(self, path: str) -> bytes:
        self._authorize(path)
        fd = self._open_root()
        try:
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) != self._root_identity:
                raise PermissionError('selected root changed')
            parts = path.split('/')
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
                if os.fstat(fd).st_dev != self._root_identity[0]:
                    raise PermissionError('selected source crosses a filesystem boundary')
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                before = os.fstat(file_fd)
                if before.st_dev != self._root_identity[0]:
                    raise PermissionError('selected source crosses a filesystem boundary')
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError('selected source is not a regular file')
                if before.st_nlink != 1:
                    raise ValueError('hard-linked selected sources are not supported')
                if before.st_size > self._max_file_bytes:
                    raise ValueError('selected source exceeds byte bound')
                chunks, count = [], 0
                while True:
                    chunk = os.read(file_fd, min(65536, self._max_file_bytes + 1 - count))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    count += len(chunk)
                    if count > self._max_file_bytes:
                        raise ValueError('selected source exceeds byte bound')
                after = os.fstat(file_fd)
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise ValueError('selected source changed during read')
                data = b''.join(chunks)
                data.decode('utf-8')
            finally:
                os.close(file_fd)
        finally:
            os.close(fd)
        self._authorize(path)
        return data

    def _ref(self, path: str, data: bytes) -> ResourceRef:
        digest = 'sha256:' + sha256(data).hexdigest()
        return ResourceRef(authority=self._authority, resource_type='local.file', resource_id=path,
                           version=digest, digest=digest)

    def get_resource(self, request: GetResourceRequest) -> GetResourceResult:
        ref = request.ref
        if ref.authority != self._authority or ref.resource_type != 'local.file' or ref.resource_id not in self._paths:
            raise PermissionError('selected source unavailable')
        try:
            data = self._read(ref.resource_id)
        except FileNotFoundError as exc:
            raise PermissionError('selected source unavailable') from exc
        if self._ref(ref.resource_id, data).exact_key != ref.exact_key:
            raise PermissionError('selected source changed')
        content = data[:request.max_bytes].decode('utf-8', errors='ignore')
        try:
            current = self._read(ref.resource_id)
        except FileNotFoundError as exc:
            raise PermissionError('selected source unavailable') from exc
        if current != data:
            raise PermissionError('selected source changed')
        self._authorize(ref.resource_id)
        return GetResourceResult(resource=ResourcePayload(ref=ref, media_type='text/plain', content=content,
                                                          truncated=len(data) > request.max_bytes))

    def snapshot_token(self) -> str:
        """Fingerprint all selected current bytes and missing markers without returning them.

        This token is a change detector, not a grant or an atomic snapshot.
        Callers must acquire it before work and revalidate it after work, in
        addition to re-reading exact citations. Even after successful checking,
        later source changes and authorization revocation remain possible.
        """
        def collect() -> list[tuple[str, str | None]]:
            total = 0
            items: list[tuple[str, str | None]] = []
            for path in self._paths:
                try:
                    data = self._read(path)
                except FileNotFoundError:
                    self._authorize(path)
                    items.append((path, None))
                    continue
                total += len(data)
                if total > self._max_total_bytes:
                    raise ValueError('selected sources exceed total byte bound')
                items.append((path, sha256(data).hexdigest()))
            return items
        first = collect()
        if first != collect():
            raise ValueError('selected sources changed during snapshot')
        raw = json.dumps({'version': 1, 'authority': self._authority, 'root': self._root_identity,
                          'selection': sorted(first)}, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        return 'sha256:' + sha256(raw).hexdigest()

    def revalidate_snapshot(self, token: str) -> None:
        """Fail closed when selected sources or current read authority changed."""
        if not isinstance(token, str) or re.fullmatch(r'sha256:[0-9a-f]{64}', token) is None:
            raise ValueError('invalid selected source snapshot token')
        if not hmac.compare_digest(token, self.snapshot_token()):
            raise PermissionError('selected sources changed')

    def _find(self, query: str) -> list[tuple[ResourceRef, str, int, float]]:
        terms = set(re.findall(r'\w+', query.casefold()))
        if not terms:
            return []
        matches, seen, total = [], {}, 0
        for path in self._paths:
            try:
                data = self._read(path)
            except FileNotFoundError:
                seen[path] = None
                continue
            total += len(data)
            if total > self._max_total_bytes:
                raise ValueError('selected sources exceed total byte bound')
            seen[path] = sha256(data).digest()
            text = data.decode('utf-8')
            score = sum(term in text.casefold() for term in terms) / len(terms)
            if not score:
                continue
            lines = text.splitlines()
            best = max(range(len(lines)), key=lambda n: sum(term in lines[n].casefold() for term in terms)) if lines else 0
            excerpt = lines[best] if lines else ''
            excerpt = excerpt.encode('utf-8')[:MAX_EVIDENCE_ITEM_BYTES].decode('utf-8', errors='ignore')
            matches.append((self._ref(path, data), excerpt, best + 1, score))
        # Detect changes/removals/appearances during the scan; avoid releasing a
        # mixed result set. Changes after this final check remain possible.
        for path, previous in seen.items():
            try:
                current = sha256(self._read(path)).digest()
            except FileNotFoundError:
                current = None
            if previous != current:
                raise ValueError('selected sources changed during search')
        matches.sort(key=lambda item: (-item[3], item[0].resource_id))
        return matches

    def search(self, request: SearchRequest) -> SearchResult:
        if request.cursor is not None:
            raise ValueError('local selected source search does not support cursors')
        if request.resource_types and 'local.file' not in request.resource_types:
            return SearchResult(hits=[])
        found = self._find(request.query)
        hits = [SearchHit(ref=ref, title=ref.resource_id[:1024], preview=line.encode('utf-8')[:4096].decode('utf-8', errors='ignore'), score=score)
                for ref, line, _, score in found[:request.limit]]
        return SearchResult(hits=hits, truncated=len(found) > request.limit)

    def evidence(self, query: str, *, limit: int = 20) -> tuple[EvidenceItem, ...]:
        """Return exact source refs with one-based line locators and excerpts."""
        if not isinstance(limit, int) or not 1 <= limit <= 32:
            raise ValueError('evidence limit must be 1..32')
        if not query.strip() or len(query) > 8192:
            raise ValueError('query must contain 1..8192 characters')
        return tuple(EvidenceItem(source=ref, locator=f'{quote(ref.resource_id, safe="/")}#L{line}', excerpt=excerpt,
                                  relevance=score) for ref, excerpt, line, score in self._find(query)[:limit] if excerpt)
