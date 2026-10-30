"""Live selected-source read contract: no source cache, exact citations."""
import os
from pathlib import Path

import pytest
from krail.provider.v1 import GetResourceRequest, SearchRequest
from rail.local_project_sources import LocalProjectSources


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'selected'
    (root / 'docs').mkdir(parents=True)
    (root / 'docs' / 'launch.md').write_text('# Launch\nAtlas launches on Monday.\n', encoding='utf-8')
    (root / 'budget.txt').write_text('Atlas launch budget: 500 dollars.\n', encoding='utf-8')
    (root / 'private.txt').write_text('Atlas launch private secret.\n', encoding='utf-8')
    allowed = {'docs/launch.md', 'budget.txt'}
    def authorize(path):
        if path not in allowed:
            raise PermissionError('access revoked')
    reader = LocalProjectSources(root=root, authority='local-project://fixture',
        selected_paths=('docs/launch.md', 'budget.txt'), authorize=authorize)
    return root, reader, allowed


def test_multifile_citations_update_delete_and_reopen(project):
    root, reader, allowed = project
    evidence = reader.evidence('Atlas launch', limit=5)
    assert {e.source.resource_id for e in evidence} == {'docs/launch.md', 'budget.txt'}
    assert {e.locator for e in evidence} == {'docs/launch.md#L2', 'budget.txt#L1'}
    assert all(e.source.verifies((root / e.source.resource_id).read_bytes()) for e in evidence)
    assert {e.excerpt for e in evidence} == {'Atlas launches on Monday.', 'Atlas launch budget: 500 dollars.'}
    old = next(e.source for e in evidence if e.source.resource_id == 'budget.txt')
    assert reader.get_resource(GetResourceRequest(ref=old)).resource.content == 'Atlas launch budget: 500 dollars.\n'
    (root / 'budget.txt').write_text('Atlas launch budget: 800 dollars.\n', encoding='utf-8')
    with pytest.raises(PermissionError, match='changed'):
        reader.get_resource(GetResourceRequest(ref=old))
    updated = reader.evidence('Atlas launch')
    assert 'Atlas launch budget: 800 dollars.' in [item.excerpt for item in updated]
    assert all('500 dollars' not in item.excerpt for item in updated)
    (root / 'docs' / 'launch.md').unlink()
    reopened = LocalProjectSources(root=root, authority='local-project://fixture',
        selected_paths=('docs/launch.md', 'budget.txt'), authorize=lambda path: None)
    hits = reopened.search(SearchRequest(query='Atlas launch'))
    assert [hit.ref.resource_id for hit in hits.hits] == ['budget.txt']
    with pytest.raises(PermissionError):
        reader.get_resource(GetResourceRequest(ref=evidence[0].source if evidence[0].source.resource_id == 'docs/launch.md' else evidence[1].source))
    assert all('private' not in hit.ref.resource_id for hit in hits.hits)


def test_revocation_and_foreign_ref_fail_closed(project):
    _, reader, allowed = project
    ref = reader.search(SearchRequest(query='budget')).hits[0].ref
    allowed.remove('budget.txt')
    with pytest.raises(PermissionError):
        reader.get_resource(GetResourceRequest(ref=ref))
    with pytest.raises(PermissionError):
        reader.search(SearchRequest(query='Atlas'))
    with pytest.raises(PermissionError):
        reader.get_resource(GetResourceRequest(ref=ref.model_copy(update={'resource_id': 'private.txt'})))


def test_nested_symlinks_fifo_and_outside_paths_are_not_read(tmp_path):
    root = tmp_path / 'root'; root.mkdir()
    outside = tmp_path / 'outside'; outside.mkdir()
    (outside / 'secret.txt').write_text('unique secret')
    (root / 'escape').symlink_to(outside, target_is_directory=True)
    reader = LocalProjectSources(root=root, authority='local-project://test', selected_paths=('escape/secret.txt',), authorize=lambda p: None)
    with pytest.raises(OSError):
        reader.search(SearchRequest(query='secret'))
    with pytest.raises(ValueError):
        LocalProjectSources(root=root, authority='local-project://test', selected_paths=('../outside/secret.txt',), authorize=lambda p: None)
    os.link(outside / 'secret.txt', root / 'hardlink.txt')
    linked = LocalProjectSources(root=root, authority='local-project://test', selected_paths=('hardlink.txt',), authorize=lambda p: None)
    with pytest.raises(ValueError, match='hard-linked'):
        linked.search(SearchRequest(query='secret'))
    os.mkfifo(root / 'pipe')
    pipe = LocalProjectSources(root=root, authority='local-project://test', selected_paths=('pipe',), authorize=lambda p: None)
    with pytest.raises(ValueError, match='regular file'):
        pipe.search(SearchRequest(query='secret'))


def test_read_limits_and_utf8_truncation(project):
    root, reader, _ = project
    (root / 'budget.txt').write_text('Atlas café ☕\n', encoding='utf-8')
    ref = reader.search(SearchRequest(query='Atlas')).hits[0].ref
    data = reader.get_resource(GetResourceRequest(ref=ref, max_bytes=8)).resource
    assert data.truncated and len(data.content.encode()) <= 8
    small = LocalProjectSources(root=root, authority='local-project://fixture', selected_paths=('budget.txt',), authorize=lambda p: None, max_file_bytes=2)
    with pytest.raises(ValueError, match='byte bound'):
        small.search(SearchRequest(query='Atlas'))


def test_detect_change_during_search(project):
    root, _, _ = project
    calls = 0
    def authorize(path):
        nonlocal calls
        calls += 1
        if calls == 3:
            (root / 'docs' / 'launch.md').write_text('Atlas launches Tuesday.\n')
    reader = LocalProjectSources(root=root, authority='local-project://fixture', selected_paths=('docs/launch.md', 'budget.txt'), authorize=authorize)
    with pytest.raises(ValueError, match='changed during search'):
        reader.search(SearchRequest(query='Atlas'))


def test_folder_discovery_is_bounded_and_is_not_authorization(project):
    from rail.local_project_sources import discover_text_files
    root, _, _ = project
    (root / '.git').mkdir(); (root / '.git' / 'config.txt').write_text('secret')
    (root / 'node_modules').mkdir(); (root / 'node_modules' / 'x.txt').write_text('generated')
    (root / 'credentials.json').write_text('{}')
    (root / 'service-account.json').write_text('{}')
    (root / 'outside.md').symlink_to(root / 'private.txt')
    paths = discover_text_files(root)
    assert paths == ('budget.txt', 'docs/launch.md')
    (root / 'new.md').write_text('Atlas launch new plan')
    (root / 'budget.txt').unlink()
    assert discover_text_files(root) == ('docs/launch.md', 'new.md')
    with pytest.raises(ValueError, match='file bound'):
        discover_text_files(root, max_files=1)
    denied = LocalProjectSources(root=root, authority='local-project://fixture', selected_paths=paths,
        authorize=lambda path: (_ for _ in ()).throw(PermissionError('not granted')))
    with pytest.raises(PermissionError):
        denied.search(SearchRequest(query='Atlas'))


def test_citation_locator_escapes_fragment_in_source_filename(tmp_path):
    root = tmp_path / 'root'; root.mkdir()
    (root / 'a#b café.md').write_text('Unique needle\n', encoding='utf-8')
    reader = LocalProjectSources(root=root, authority='local-project://fixture',
        selected_paths=('a#b café.md',), authorize=lambda path: None)
    cited = reader.evidence('needle')[0]
    assert cited.source.resource_id == 'a#b café.md'
    assert cited.locator == 'a%23b%20caf%C3%A9.md#L1'


def test_snapshot_token_rechecks_entire_selection_and_missing_sources(project):
    root, reader, allowed = project
    token = reader.snapshot_token()
    assert token.startswith('sha256:') and len(token) == 71
    assert 'Atlas' not in token and 'budget' not in token
    reader.revalidate_snapshot(token)
    # This was not a match for a previous query, but must still invalidate an
    # in-flight answer when it becomes relevant.
    assert reader.search(SearchRequest(query='Monday')).hits[0].ref.resource_id == 'docs/launch.md'
    (root / 'budget.txt').write_text('Monday: budget changed.\n')
    with pytest.raises(PermissionError, match='changed'):
        reader.revalidate_snapshot(token)
    token = reader.snapshot_token()
    (root / 'budget.txt').unlink()
    with pytest.raises(PermissionError, match='changed'):
        reader.revalidate_snapshot(token)
    missing = reader.snapshot_token()
    (root / 'budget.txt').write_text('It reappeared.\n')
    with pytest.raises(PermissionError, match='changed'):
        reader.revalidate_snapshot(missing)
    token = reader.snapshot_token()
    allowed.remove('budget.txt')
    with pytest.raises(PermissionError, match='revoked'):
        reader.revalidate_snapshot(token)
    with pytest.raises(ValueError, match='invalid'):
        reader.revalidate_snapshot('sha256:bad')



def test_root_with_symlink_ancestor_is_not_accepted(tmp_path):
    from rail.local_project_sources import discover_text_files
    actual = tmp_path / 'actual'; (actual / 'child').mkdir(parents=True)
    (actual / 'child' / 'ok.md').write_text('ok')
    (tmp_path / 'alias').symlink_to(actual, target_is_directory=True)
    root = tmp_path / 'alias' / 'child'
    with pytest.raises(ValueError, match='no symlink ancestors'):
        LocalProjectSources(root=root, authority='local-project://fixture', selected_paths=('ok.md',), authorize=lambda path: None)
    with pytest.raises(ValueError, match='no symlink ancestors'):
        discover_text_files(root)
