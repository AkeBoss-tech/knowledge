# Authorized Context Packet v1

`rail.authorized_context.assemble_authorized_context` is the bounded read path
for callers that need a Context Brief under a live authorization decision. It
reuses `krail.provider.v1` exact `ResourceRef` values and the existing KRAIL
signed access authority; it does not own identity, grants, credentials,
scheduling, or OpenSaddle operational records.

The injected authorizer is checked before candidate shaping, before every exact
resource read, and immediately before returning. Search hits that fail the
decision are omitted without exposing their identity or count. The hosted
adapter requires the `context.read` action, an allowed source ID, and an exact
version-plus-digest grant. The authority is verified on every check, so context
or delegation revocation fails closed even after an earlier check succeeded.

The returned `AuthorizedContextPacket` binds the existing `ContextBrief` to the
authorizer's `authorization_digest` with `packet_digest`. OpenSaddle may carry
that packet and its KRAIL `ResourceRef` values alongside Project, Run, event,
artifact, and approval references; those operational records remain
OpenSaddle-owned. Packet construction and deserialization recompute and verify
the digest, so forged packet contents fail closed.

## Selected local project sources

`rail.local_project_sources.LocalProjectSources` is a bounded, read-only live
scan of explicitly selected UTF-8 files under a selected local directory. It
implements the existing provider-v1 `search` and `get_resource` request/result
shapes; `evidence` adds one-based line locators and exact SHA-256 references for
cited excerpts. Reads traverse directory descriptors without following symlinks,
reject hard-linked and special files, do not cross filesystem device boundaries,
and enforce file and aggregate byte limits. Search checks
selected files again before returning, and an exact ref cannot retrieve changed
or removed content. A change after the final check is still possible, so a
consumer must recheck citations when using them later.

For a match deep within a line, a trusted consumer can explicitly call
`evidence_offset` with a byte-window limit. Its locator appends
`@B<start>-<end>` to the line locator, identifying an exact UTF-8 byte slice of
that line; consumers must verify the slice and source digest before use. The
ordinary `evidence` method retains its legacy prefix locators by default.
Offset citations require explicit support throughout the consumer path; they
do not prove that a generated claim is entailed by the cited text.

`discover_text_files` returns a bounded, deterministic proposal of supported
text paths, excluding symlinks and hard links, hidden and generated directories, and common
secret-like names. Name filtering cannot detect all secrets inside otherwise
ordinary files; callers must present or otherwise constrain selection. Discovery
does not authorize access. The caller must
explicitly select paths and supply a live authorization callback; the adapter
owns no grants, identities or user interface. This is an in-memory live scan,
not a persistent index, a model-written answer, a hosted ingestion service, or
complete conversation/PDF import. It supports platforms with safe POSIX-style
no-follow file operations. `packages/rail-py/tests/test_local_project_sources.py`
exercises multiple sources, citations, update/removal, authorization, bounds,
symlink rejection and discovery.

`snapshot_token()` and `revalidate_snapshot(token)` cover every explicitly
selected path, including absent-file markers, using two live scans and the same
byte bounds and authorization callback. A token is an opaque digest, not an
authorization grant or atomic filesystem snapshot. An answer consumer should
revalidate it and every exact citation after inference, before returning the
answer. Newly created paths not in the selection are outside that token; a
folder consumer must rediscover and compare the folder selection as well.
The selected root must be a canonical absolute directory with no symbolic-link
ancestor. Descriptor traversal also rejects symlink substitution while opening
root components. File names alone cannot establish that content is non-secret.

A trusted local host can instead pass an already-open directory descriptor to
`LocalProjectSources` and `discover_text_files`. The descriptor is borrowed,
not an authority grant, and the supplied root path is only a display label;
reads and discovery reopen relative to the descriptor with no-follow traversal.
The host remains responsible for choosing the directory, binding the descriptor
to the current identity, and keeping it open. `read_selected()` returns bounded
selected bytes only to an authorized local consumer and rechecks the full
selection; persistence and withdrawal are that consumer's responsibility. The
checks remain sequential and do not prevent mutation after return.

## Rebuildable selected-source lexical index

`rail.selected_source_index.SelectedSourceIndex` is an opt-in local cache for
an explicitly selected source set. Its terms use the existing ASCII-oriented
KRAIL tokenizer; non-ASCII queries fall back to the authorized live reader's
case-folded substring search. This is lexical retrieval, not multilingual
semantic search, and Unicode normalization is not guaranteed. A caller supplies a private owner-only state
directory outside the selected source tree, a selection-bound reader, current
discovery, and live authorization callbacks. The SQLite cache stores relative
paths, content digests, line numbers and salted hashes of lexical terms, not
source excerpts. These hashes and paths are still sensitive and dictionary-
guessable: owner-only permissions are not encryption or secure deletion.

Search re-reads and reauthorizes the full selected corpus, atomically rebuilds
its postings when a snapshot changes, and returns exact current source lines
with digests. It detects inconsistent cache generations and fails closed rather
than using stale cached text. A new reader must be constructed when the selected
folder's file set changes; opening the index does not itself grant access.
Interrupted writes recover transactionally or fail closed; a damaged index may
require operator repair. Callers must protect the state directory from concurrent
same-account tampering, bind the matching reader authority, and treat sequential
checks as observations rather than an atomic filesystem snapshot. There is no
remote embedding provider, semantic search, automatic file watcher or UI in this
component. `packages/rail-py/tests/test_selected_source_index.py` covers restart,
update, removal, addition, revoked authority, corruption and interrupted writes.
Use a separate state directory per selection: the v2 cache is not bound to a
source root. An empty-folder CLI search returns no stale matches but can leave
the previous paths and hashed terms in the cache until refresh or removal.

The current selected-source index uses the v2 marker, with line splitting aligned
to the live reader for CRLF, bare CR and Unicode separators. A v1 prototype cache
is rejected rather than silently reused; choose a fresh private state directory
or explicitly manage the old cache. No automatic removal of user files occurs.
