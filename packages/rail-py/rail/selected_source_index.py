"""Rebuildable private lexical index for explicitly selected local sources.

The caller owns directory selection and current authorization. The index stores
paths and salted term hashes but no source text; this is not encryption, a grant,
or protection against an attacker with access to the same OS account. Queries
re-read the entire selected corpus before returning cited source lines.
"""
from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import sqlite3
import stat
from typing import Protocol
from urllib.parse import quote

from krail.provider.v1 import EvidenceItem, MAX_EVIDENCE_ITEM_BYTES, ResourceRef
from rail.vector_store import _TOKEN_RE, tokenize
from rail.local_project_sources import _excerpt_window

_APP_ID = 0x4B524149
_MARKER = 'krail.selected-source-index.v2'
_MAX_SOURCES = 256
_MAX_BYTES = 8 * 1_048_576
_MAX_POSTINGS = 200_000


class SelectedReader(Protocol):
    def snapshot_token(self) -> str: ...
    def revalidate_snapshot(self, token: str) -> None: ...
    def read_selected(self) -> tuple[tuple[str, bytes], ...]: ...
    def evidence(self, query: str, *, limit: int = 20) -> tuple[EvidenceItem, ...]: ...
    def evidence_offset(self, query: str, *, limit: int, excerpt_window_bytes: int) -> tuple[EvidenceItem, ...]: ...
    def get_resource(self, request): ...


def _canonical_directory(path: str | Path) -> Path:
    value = Path(path)
    if not value.is_absolute() or value.is_symlink() or value != value.resolve(strict=True):
        raise ValueError('directory must be an absolute canonical non-symlink path')
    if not value.is_dir():
        raise ValueError('directory is unavailable')
    return value


def _source_path(value: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > 2048 or '\\' in value
            or PurePosixPath(value).is_absolute() or any(part in ('', '.', '..') for part in value.split('/'))
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError('invalid source path')
    return value


def _term_hash(salt: bytes, term: str) -> str:
    # Salted hashes avoid retaining source words but are dictionary-guessable.
    return sha256(salt + b'\0' + term.encode('utf-8')).hexdigest()


def _indexed_match_span(line: str, terms: set[str]) -> tuple[int, int]:
    """Locate a term using the exact token and split rules used by postings."""
    candidates = []
    for match in _TOKEN_RE.finditer(line):
        raw = match.group()
        valid = set(tokenize(raw))
        if raw.lower() in terms and raw.lower() in valid:
            candidates.append((match.start(), match.end()))
        for part in re.finditer(r'[^_.-]+', raw):
            token = part.group().lower()
            if token in terms and token in valid:
                candidates.append((match.start()+part.start(), match.start()+part.end()))
    if not candidates:
        raise ValueError('indexed match token unavailable')
    return min(candidates, key=lambda span: (span[0], -(span[1]-span[0])))


class SelectedSourceIndex:
    """One private, rebuildable selection cache; reopening does not grant access.

    `reader` is a trusted selection-bound live reader. `discover` must return
    current selected paths for the same scope, and `authorize` must fail when
    the caller loses permission to use this selection. Callers must protect the
    state directory from modification by other processes under the same account.
    """
    def __init__(self, *, state_dir: str | Path, source_root: str | Path, reader: SelectedReader,
                 discover: Callable[[], tuple[str, ...]], authorize: Callable[[], None],
                 authority: str = 'local-project://operator') -> None:
        self.state_dir = _canonical_directory(state_dir)
        source = _canonical_directory(source_root)
        if self.state_dir == source or self.state_dir.is_relative_to(source):
            raise ValueError('index state must be outside the selected source tree')
        st = self.state_dir.stat()
        if st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise PermissionError('index state directory must be owner-only')
        if not callable(discover) or not callable(authorize):
            raise ValueError('live discovery and authorization callbacks are required')
        ResourceRef(authority=authority, resource_type='local.file', resource_id='validation',
                    version='sha256:' + '0'*64, digest='sha256:' + '0'*64)
        self.reader, self.discover, self.authorize, self.authority = reader, discover, authorize, authority
        self.path = self.state_dir/'selected-source-index.sqlite'

    def _db(self) -> sqlite3.Connection:
        self.authorize()
        # The caller supplies an owned, private state directory and its path is
        # trusted/stable across operations; SQLite cannot bind an existing file
        # descriptor portably. We never initialize an unrelated existing file.
        if not self.path.exists():
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            try:
                fd = os.open(self.path, flags, 0o600)
            except FileExistsError:
                pass  # competing creator: inspect it as existing below
            else:
                os.close(fd)
                try:
                    connection = sqlite3.connect(self.path, isolation_level=None)
                    connection.execute('PRAGMA journal_mode=DELETE')
                    connection.execute('BEGIN IMMEDIATE')
                    connection.execute(f'PRAGMA application_id={_APP_ID}')
                    connection.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                    connection.execute('CREATE TABLE files(path TEXT PRIMARY KEY,digest TEXT NOT NULL)')
                    connection.execute('CREATE TABLE postings(path TEXT NOT NULL,line INTEGER NOT NULL,term TEXT NOT NULL,PRIMARY KEY(path,line,term),FOREIGN KEY(path) REFERENCES files(path))')
                    connection.execute('CREATE INDEX postings_term ON postings(term,path,line)')
                    connection.executemany('INSERT INTO metadata VALUES (?,?)', [('marker',_MARKER),('salt',secrets.token_hex(32)),('snapshot',''),('generation','0'),('checksum','')])
                    connection.commit()
                    connection.close()
                except BaseException:
                    # Preserve the incomplete exclusively created file for diagnosis.
                    raise
        info = self.path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PermissionError('index file must be an owned private regular file')
        try:
            # Read-only preflight rejects other SQLite files before any write.
            check = sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True)
            try:
                if check.execute('PRAGMA application_id').fetchone()[0] != _APP_ID:
                    raise ValueError('index marker mismatch')
                objects = list(check.execute("SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
                if set(objects) != {('table','metadata'),('table','files'),('table','postings'),('index','postings_term')}:
                    raise ValueError('index schema mismatch')
                tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if tables != {'metadata','files','postings'}:
                    raise ValueError('index schema mismatch')
                expected = {'metadata':['key','value'], 'files':['path','digest'], 'postings':['path','line','term']}
                for table, cols in expected.items():
                    if [row[1] for row in check.execute(f'PRAGMA table_info({table})')] != cols:
                        raise ValueError('index schema mismatch')
                values = dict(check.execute('SELECT key,value FROM metadata'))
                if (set(values) != {'marker','salt','snapshot','generation','checksum'} or values['marker'] != _MARKER
                        or re.fullmatch(r'[a-f0-9]{64}', values['salt']) is None
                        or not values['generation'].isdigit()
                        or values['snapshot'] and re.fullmatch(r'sha256:[a-f0-9]{64}', values['snapshot']) is None
                        or values['checksum'] and re.fullmatch(r'[a-f0-9]{64}', values['checksum']) is None):
                    raise ValueError('index metadata mismatch')
            finally:
                check.close()
            connection = sqlite3.connect(self.path.as_uri()+'?mode=rw', uri=True, isolation_level=None, timeout=5)
            connection.execute('PRAGMA foreign_keys=ON')
            return connection
        except sqlite3.Error as exc:
            raise ValueError('index unavailable or corrupt') from exc

    def _capture(self) -> tuple[str, tuple[tuple[str, bytes], ...]]:
        self.authorize()
        selected = self.discover()
        if not isinstance(selected, tuple) or len(selected) > _MAX_SOURCES or len(set(selected)) != len(selected):
            raise ValueError('invalid selection')
        for path in selected: _source_path(path)
        token = self.reader.snapshot_token()
        if not isinstance(token, str) or re.fullmatch(r'sha256:[a-f0-9]{64}', token) is None:
            raise ValueError('invalid reader snapshot')
        rows = self.reader.read_selected()
        if not isinstance(rows, tuple) or len(rows) != len(selected):
            raise PermissionError('selected sources changed')
        validated = []
        total = 0
        for item in rows:
            if not isinstance(item, tuple) or len(item) != 2:
                raise ValueError('invalid source')
            name, content = item
            _source_path(name)
            if not isinstance(content, bytes) or len(content) > 1_048_576:
                raise ValueError('source exceeds file bound')
            content.decode('utf-8')
            total += len(content)
            if total > _MAX_BYTES: raise ValueError('source selection exceeds byte bound')
            validated.append((name, content))
        if tuple(sorted(name for name, _ in validated)) != tuple(sorted(selected)):
            raise PermissionError('selected sources changed')
        self.reader.revalidate_snapshot(token)
        if self.discover() != selected:
            raise PermissionError('selected folder changed')
        self.authorize()
        return token, tuple(validated)

    @staticmethod
    def _checksum(connection: sqlite3.Connection) -> str:
        digest = sha256()
        for row in connection.execute("SELECT key,value FROM metadata WHERE key IN ('salt','snapshot','generation') ORDER BY key"):
            digest.update(json.dumps(row,ensure_ascii=True,separators=(',',':')).encode('utf-8') + b'\n')
        for table, order in (('files','path'),('postings','path,line,term')):
            for row in connection.execute(f'SELECT * FROM {table} ORDER BY {order}'):
                digest.update(json.dumps(row,ensure_ascii=True,separators=(',',':')).encode('utf-8') + b'\n')
        return digest.hexdigest()

    @staticmethod
    def _write_generation(connection: sqlite3.Connection, rows: tuple[tuple[str, bytes], ...], salt: bytes, token: str) -> None:
        connection.execute('DELETE FROM postings')
        connection.execute('DELETE FROM files')
        count = 0
        for path, raw in rows:
            connection.execute('INSERT INTO files VALUES (?,?)',(path,'sha256:'+sha256(raw).hexdigest()))
            for line, text in enumerate(raw.decode('utf-8').splitlines(),1):
                hashes = {_term_hash(salt, term) for term in tokenize(text)}
                count += len(hashes)
                if count > _MAX_POSTINGS: raise ValueError('index exceeds posting bound')
                connection.executemany('INSERT INTO postings VALUES (?,?,?)', ((path,line,item) for item in sorted(hashes)))
        connection.execute("UPDATE metadata SET value=? WHERE key='snapshot'", (token,))
        connection.execute("UPDATE metadata SET value=CAST(value AS INTEGER)+1 WHERE key='generation'")
        checksum = SelectedSourceIndex._checksum(connection)
        connection.execute("UPDATE metadata SET value=? WHERE key='checksum'", (checksum,))

    def search(self, query: str, *, limit: int = 12, excerpt_window_bytes: int | None = None) -> tuple[EvidenceItem, ...]:
        if not isinstance(query,str) or not query.strip() or len(query)>2000 or type(limit) is not int or not 1<=limit<=32:
            raise ValueError('invalid query')
        if excerpt_window_bytes is not None and (type(excerpt_window_bytes) is not int or not 1 <= excerpt_window_bytes <= MAX_EVIDENCE_ITEM_BYTES):
            raise ValueError('invalid excerpt window size')
        token, rows = self._capture()
        connection = self._db()
        try:
            connection.execute('BEGIN IMMEDIATE')
            meta = dict(connection.execute('SELECT key,value FROM metadata'))
            if (meta['generation'] == '0') != (meta['snapshot'] == '' and meta['checksum'] == ''):
                raise ValueError('index generation mismatch')
            if meta['generation'] == '0' and (connection.execute('SELECT COUNT(*) FROM files').fetchone()[0] or connection.execute('SELECT COUNT(*) FROM postings').fetchone()[0]):
                raise ValueError('index generation mismatch')
            if meta['generation'] != '0' and (not meta['snapshot'] or not meta['checksum']):
                raise ValueError('index generation mismatch')
            if meta['checksum'] and self._checksum(connection) != meta['checksum']:
                raise ValueError('index checksum mismatch')
            if meta['snapshot'] != token:
                self._write_generation(connection, rows, bytes.fromhex(meta['salt']), token)
            terms = sorted(set(tokenize(query)))
            if len(terms)>100: raise ValueError('query exceeds term bound')
            matches = {}
            salt = bytes.fromhex(meta['salt'])
            for term in terms:
                for path,line in connection.execute('SELECT path,line FROM postings WHERE term=?',(_term_hash(salt,term),)):
                    matches[(path,line)] = matches.get((path,line),0)+1
            self.reader.revalidate_snapshot(token)
            if tuple(sorted(self.discover())) != tuple(sorted(name for name, _ in rows)):
                raise PermissionError('selected folder changed')
            self.authorize()
            connection.commit()
        except BaseException:
            if connection.in_transaction: connection.rollback()
            raise
        finally:
            connection.close()
        source_map = dict(rows)
        if not query.isascii():
            # The existing persisted term format is ASCII-only. Use the live
            # authorized reader for Unicode queries without changing the index
            # format or trusting old excerpts. Its lexical matching semantics
            # (casefolded substrings) differ from the ASCII index.
            if excerpt_window_bytes is None:
                items = self.reader.evidence(query, limit=limit)
            else:
                offset_evidence = getattr(self.reader, 'evidence_offset', None)
                if not callable(offset_evidence):
                    raise ValueError('offset evidence is unavailable')
                items = offset_evidence(query, limit=limit, excerpt_window_bytes=excerpt_window_bytes)
            if not isinstance(items, tuple) or len(items) > limit:
                raise ValueError('invalid live evidence')
            for item in items:
                if (not isinstance(item, EvidenceItem) or item.source.authority != self.authority
                        or item.source.resource_type != 'local.file'
                        or item.source.resource_id not in source_map
                        or not item.source.verifies(source_map[item.source.resource_id])):
                    raise ValueError('live evidence source mismatch')
            self.reader.revalidate_snapshot(token)
            if tuple(sorted(self.discover())) != tuple(sorted(source_map)):
                raise PermissionError('selected folder changed')
            self.authorize()
            return items
        best = {}
        for (path,line),count in matches.items():
            if path not in source_map: raise ValueError('index source mismatch')
            previous = best.get(path)
            if previous is None or (-count,line) < (-previous[0],previous[1]):
                best[path] = (count,line)
        items = []
        for path,(count,line) in sorted(best.items(), key=lambda item:(-item[1][0],item[0]))[:limit]:
            raw = source_map[path]
            lines = raw.decode('utf-8').splitlines()
            if not 1 <= line <= len(lines): raise ValueError('index line mismatch')
            if excerpt_window_bytes is None:
                excerpt = lines[line-1].encode('utf-8')[:MAX_EVIDENCE_ITEM_BYTES].decode('utf-8',errors='ignore')
                suffix = ''
            else:
                excerpt, start, end = _excerpt_window(lines[line-1], set(terms), excerpt_window_bytes,
                                                       matched_span=_indexed_match_span(lines[line-1], set(terms)))
                suffix = f'@B{start}-{end}'
            if not excerpt: continue
            digest = 'sha256:'+sha256(raw).hexdigest()
            ref = ResourceRef(authority=self.authority,resource_type='local.file',resource_id=path,version=digest,digest=digest)
            items.append(EvidenceItem(source=ref,locator=f'{quote(path,safe="/")}#L{line}{suffix}',excerpt=excerpt,relevance=count/max(len(terms),1)))
        self.reader.revalidate_snapshot(token)
        if tuple(sorted(self.discover())) != tuple(sorted(source_map)):
            raise PermissionError('selected folder changed')
        self.authorize()
        return tuple(items)

    def evidence(self, query: str, *, limit: int = 20, excerpt_window_bytes: int | None = None) -> tuple[EvidenceItem, ...]:
        return self.search(query, limit=limit, excerpt_window_bytes=excerpt_window_bytes)

    def snapshot_token(self) -> str:
        token, rows = self._capture()
        data = json.dumps([token, sorted(name for name, _ in rows)], separators=(',', ':')).encode()
        return 'sha256:' + sha256(data).hexdigest()

    def revalidate_snapshot(self, token: str) -> None:
        if not isinstance(token, str) or re.fullmatch(r'sha256:[a-f0-9]{64}', token) is None:
            raise ValueError('invalid snapshot token')
        if not hmac.compare_digest(token, self.snapshot_token()):
            raise PermissionError('selected sources changed')

    def get_resource(self, request):
        token = self.snapshot_token()
        result = self.reader.get_resource(request)
        self.revalidate_snapshot(token)
        return result

    def evidence_offset(self, query: str, *, limit: int, excerpt_window_bytes: int) -> tuple[EvidenceItem, ...]:
        """Explicitly request UTF-8 byte-offset evidence; legacy evidence is unchanged."""
        if type(excerpt_window_bytes) is not int or not 1 <= excerpt_window_bytes <= MAX_EVIDENCE_ITEM_BYTES:
            raise ValueError('invalid excerpt window size')
        return self.search(query, limit=limit, excerpt_window_bytes=excerpt_window_bytes)
