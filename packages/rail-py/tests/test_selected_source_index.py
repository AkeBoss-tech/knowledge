import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from krail.provider.v1 import GetResourceRequest
from rail.local_project_sources import LocalProjectSources, discover_text_files
from rail.selected_source_index import SelectedSourceIndex


def make_index(root, state, allowed=lambda: True):
    def auth(path=None):
        if not allowed():
            raise PermissionError('revoked')
    def discover():
        return discover_text_files(root)
    reader = LocalProjectSources(root=root, authority='local-project://fixture', selected_paths=discover(), authorize=auth)
    return SelectedSourceIndex(state_dir=state, source_root=root, reader=reader, discover=discover, authorize=auth, authority='local-project://fixture')


def test_restart_edits_add_delete_revocation_and_citations(tmp_path):
    root = tmp_path/'project'; root.mkdir()
    state = tmp_path/'state'; state.mkdir(mode=0o700)
    (root/'a.md').write_text('Atlas launch Monday.\n')
    (root/'b.txt').write_text('Atlas budget 500 dollars.\n')
    (root/'c.txt').write_text('Unrelated word.\n')
    idx = make_index(root, state)
    items = idx.evidence('Atlas')
    assert {i.locator for i in items} == {'a.md#L1', 'b.txt#L1'}
    assert all(i.source.verifies((root/i.source.resource_id).read_bytes()) for i in items)
    assert idx.get_resource(GetResourceRequest(ref=items[0].source)).resource.content
    old_token = idx.snapshot_token()
    assert b'Atlas launch Monday' not in idx.path.read_bytes()
    assert b'500 dollars' not in idx.path.read_bytes()
    assert idx.path.stat().st_mode & 0o077 == 0
    (root/'b.txt').write_text('Atlas budget 800 dollars.\n')
    (root/'c.txt').write_text('Atlas owner is Ada.\n')
    with pytest.raises(PermissionError): idx.revalidate_snapshot(old_token)
    with pytest.raises(PermissionError): idx.get_resource(GetResourceRequest(ref=next(i.source for i in items if i.source.resource_id=='b.txt')))
    updated = make_index(root,state).evidence('Atlas')
    assert {i.locator for i in updated} == {'a.md#L1','b.txt#L1','c.txt#L1'}
    assert all('500' not in i.excerpt for i in updated)
    (root/'a.md').unlink()
    (root/'d.md').write_text('Atlas deadline Friday.\n')
    with pytest.raises(PermissionError): idx.evidence('Atlas')
    fresh = make_index(root,state).evidence('Atlas')
    assert {i.source.resource_id for i in fresh} == {'b.txt','c.txt','d.md'}
    with pytest.raises(PermissionError): make_index(root,state,lambda:False).evidence('Atlas')
    # Reopen from a separate interpreter with no in-memory generation.
    code = '''from pathlib import Path\nfrom rail.local_project_sources import LocalProjectSources,discover_text_files\nfrom rail.selected_source_index import SelectedSourceIndex\nimport sys\nr=Path(sys.argv[1]); s=Path(sys.argv[2]); d=lambda:discover_text_files(r)\nx=LocalProjectSources(root=r,authority="local-project://fixture",selected_paths=d(),authorize=lambda p:None)\ni=SelectedSourceIndex(state_dir=s,source_root=r,reader=x,discover=d,authorize=lambda:None,authority="local-project://fixture")\nprint(",".join(sorted(e.source.resource_id for e in i.evidence("Atlas"))))'''
    result = subprocess.run([sys.executable,'-c',code,str(root),str(state)],capture_output=True,text=True,check=True)
    assert result.stdout.strip() == 'b.txt,c.txt,d.md'


def test_unrelated_and_corrupt_database_fail_closed_without_mutation(tmp_path):
    root=tmp_path/'project'; root.mkdir(); (root/'a.txt').write_text('atlas')
    state=tmp_path/'state'; state.mkdir(mode=0o700)
    path=state/'selected-source-index.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE important(data TEXT)'); db.execute("INSERT INTO important VALUES ('preserve')")
    path.chmod(0o600)
    before=path.read_bytes()
    with pytest.raises(ValueError): make_index(root,state).search('atlas')
    assert path.read_bytes()==before
    path.unlink()
    index=make_index(root,state); index.search('atlas')
    with sqlite3.connect(path) as db: db.execute("UPDATE metadata SET value='bad' WHERE key='checksum'")
    before=path.read_bytes()
    with pytest.raises(ValueError): index.search('atlas')
    assert path.read_bytes()==before


def test_mid_build_change_rolls_back_generation(tmp_path, monkeypatch):
    root=tmp_path/'project'; root.mkdir(); source=root/'a.txt'; source.write_text('atlas old')
    state=tmp_path/'state'; state.mkdir(mode=0o700)
    index=make_index(root,state); index.search('atlas')
    with sqlite3.connect(index.path) as db: before=dict(db.execute('SELECT key,value FROM metadata'))
    source.write_text('atlas new')
    original=SelectedSourceIndex._write_generation
    def mutate(db, rows, salt, token):
        original(db,rows,salt,token)
        source.write_text('atlas newer')
    monkeypatch.setattr(SelectedSourceIndex,'_write_generation',staticmethod(mutate))
    with pytest.raises(PermissionError): index.search('atlas')
    with sqlite3.connect(index.path) as db: after=dict(db.execute('SELECT key,value FROM metadata'))
    assert before==after


def test_folder_addition_during_answer_invalidates_snapshot(tmp_path):
    root=tmp_path/'project'; root.mkdir(); (root/'a.md').write_text('Atlas old')
    state=tmp_path/'state'; state.mkdir(mode=0o700)
    index=make_index(root,state)
    token=index.snapshot_token()
    (root/'new.md').write_text('Atlas additional')
    with pytest.raises(PermissionError): index.revalidate_snapshot(token)
    assert {e.source.resource_id for e in make_index(root,state).evidence('Atlas')} == {'a.md','new.md'}


def test_index_rejects_symlink_and_mutated_postings(tmp_path):
    root=tmp_path/'project'; root.mkdir(); (root/'a.md').write_text('Atlas old')
    state=tmp_path/'state'; state.mkdir(mode=0o700)
    target=tmp_path/'unrelated'; target.write_text('leave untouched')
    path=state/'selected-source-index.sqlite'; path.symlink_to(target)
    with pytest.raises(PermissionError): make_index(root,state).search('Atlas')
    assert target.read_text() == 'leave untouched'
    path.unlink()
    index=make_index(root,state); index.search('Atlas')
    with sqlite3.connect(path) as db: db.execute('DELETE FROM postings')
    with pytest.raises(ValueError,match='checksum'): index.search('Atlas')


def test_process_exit_mid_generation_never_returns_stale_output(tmp_path):
    root=tmp_path/'project'; root.mkdir(); source=root/'a.md'; source.write_text('Atlas original')
    state=tmp_path/'state'; state.mkdir(mode=0o700)
    make_index(root,state).search('Atlas')
    source.write_text('Atlas current')
    code='''from pathlib import Path\nfrom rail.local_project_sources import LocalProjectSources,discover_text_files\nfrom rail.selected_source_index import SelectedSourceIndex\nimport os,sys\nr=Path(sys.argv[1]); s=Path(sys.argv[2]); d=lambda:discover_text_files(r)\nx=LocalProjectSources(root=r,authority="local-project://fixture",selected_paths=d(),authorize=lambda p:None)\ni=SelectedSourceIndex(state_dir=s,source_root=r,reader=x,discover=d,authorize=lambda:None,authority="local-project://fixture")\nold=SelectedSourceIndex._write_generation\ndef crash(db,rows,salt,token):\n old(db,rows,salt,token); os._exit(23)\nSelectedSourceIndex._write_generation=staticmethod(crash)\ni.search("Atlas")'''
    result=subprocess.run([sys.executable,'-c',code,str(root),str(state)])
    assert result.returncode==23
    try:
        evidence=make_index(root,state).search('Atlas')
    except ValueError as exc:
        assert 'index unavailable or corrupt' in str(exc)
    else:
        assert [item.excerpt for item in evidence] == ['Atlas current']


@pytest.mark.parametrize('key,value', [
    ('salt', 'a'*64),
    ('snapshot', 'sha256:'+'b'*64),
    ('generation', '42'),
])
def test_valid_looking_metadata_corruption_fails_closed(tmp_path, key, value):
    root=tmp_path/'project'; root.mkdir(); (root/'a.md').write_text('Atlas current')
    state=tmp_path/'state'; state.mkdir(mode=0o700)
    index=make_index(root,state); assert index.search('Atlas')
    with sqlite3.connect(index.path) as db:
        db.execute('UPDATE metadata SET value=? WHERE key=?', (value,key))
    with pytest.raises(ValueError,match='checksum'):
        index.search('Atlas')


@pytest.mark.parametrize('separator', ['\r\n', '\r', '\u2028'])
def test_citation_lines_match_live_reader_on_all_supported_separators(tmp_path, separator):
    root=tmp_path/'project'; root.mkdir()
    (root/'a.md').write_bytes(('preface'+separator+'Atlas target'+separator+'tail').encode())
    state=tmp_path/'state'; state.mkdir(mode=0o700)
    index=make_index(root,state)
    indexed=index.evidence('Atlas')
    live=index.reader.evidence('Atlas')
    assert len(indexed)==len(live)==1
    assert indexed[0].locator==live[0].locator=='a.md#L2'
    assert indexed[0].excerpt==live[0].excerpt=='Atlas target'
    assert indexed[0].source==live[0].source


def test_older_index_format_fails_closed(tmp_path):
    root=tmp_path/'project'; root.mkdir(); (root/'a.md').write_text('Atlas current')
    state=tmp_path/'state'; state.mkdir(mode=0o700)
    index=make_index(root,state); index.search('Atlas')
    with sqlite3.connect(index.path) as db:
        db.execute("UPDATE metadata SET value='krail.selected-source-index.v1' WHERE key='marker'")
    with pytest.raises(ValueError,match='metadata'):
        index.search('Atlas')


def test_unicode_queries_use_live_reader_with_exact_citations_and_freshness(tmp_path):
    root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
    jp=root/'日本語.md';jp.write_text('予定\n日本語の計画は金曜日\n')
    fr=root/'café.md';fr.write_text('Résumé: réunion lundi\n')
    index=make_index(root,state)
    japanese=index.evidence('日本語');assert len(japanese)==1
    assert japanese[0].locator=='%E6%97%A5%E6%9C%AC%E8%AA%9E.md#L2'
    assert japanese[0].source.verifies(jp.read_bytes())
    accent=index.evidence('RÉSUMÉ');assert len(accent)==1
    assert accent[0].locator=='caf%C3%A9.md#L1' and 'Résumé' in accent[0].excerpt
    old=index.snapshot_token();fr.write_text('Résumé: réunion mardi\n')
    with pytest.raises(PermissionError): index.revalidate_snapshot(old)
    assert 'mardi' in index.evidence('RÉSUMÉ')[0].excerpt
    jp.unlink()
    assert index.evidence('日本語')==()
    assert make_index(root,state).evidence('日本語')==()


def test_unicode_live_fallback_fails_closed_on_revocation_and_midread_change(tmp_path,monkeypatch):
    root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
    source=root/'x.md';source.write_text('日本語 old\n');allowed={'yes':True}
    index=make_index(root,state,lambda:allowed['yes'])
    original=index.reader.evidence
    def changing(query,*,limit):
        items=original(query,limit=limit); source.write_text('日本語 changed\n'); return items
    monkeypatch.setattr(index.reader,'evidence',changing)
    with pytest.raises(PermissionError): index.evidence('日本語')
    allowed['yes']=False
    with pytest.raises(PermissionError): index.evidence('日本語')


def test_offset_window_binds_utf8_exact_bytes_for_index_and_unicode_fallback(tmp_path):
    import re
    root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
    source=root/'long file.md'
    source.write_text('header\r\n'+('é日本 ' * 5000)+'Straße and needle result\n')
    index=make_index(root,state)
    for reader,query in ((index,'needle'),(index,'STRAẞE'),(index.reader,'STRASSE')):
        item=reader.evidence_offset(query,limit=2,excerpt_window_bytes=128)[0]
        match=re.fullmatch(r'long%20file\.md#L2@B([0-9]+)-([0-9]+)',item.locator)
        assert match is not None
        start,end=map(int,match.groups()); raw=source.read_text().splitlines()[1].encode('utf-8')
        assert start>16384 and end-start<=128
        assert raw[start:end].decode('utf-8')==item.excerpt
        assert ('needle' if query=='needle' else 'Straße') in item.excerpt
        assert item.source.verifies(source.read_bytes())
    legacy=index.evidence('needle')[0]
    assert legacy.locator=='long%20file.md#L2' and 'needle' not in legacy.excerpt
    with pytest.raises(ValueError): index.evidence_offset('needle',limit=2,excerpt_window_bytes=2)
    before=index.snapshot_token();source.write_text('needle changed')
    with pytest.raises(PermissionError): index.revalidate_snapshot(before)
    assert index.evidence_offset('needle',limit=2,excerpt_window_bytes=16)[0].excerpt=='needle changed'


def test_index_offset_anchors_exact_token_not_earlier_substring(tmp_path):
    root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
    source=root/'long.md';source.write_text('needleword '+('x '*10000)+'needle actual result\n')
    index=make_index(root,state)
    evidence=index.evidence_offset('needle',limit=1,excerpt_window_bytes=128)[0]
    assert 'needle actual result' in evidence.excerpt and 'needleword' not in evidence.excerpt
    import re
    match=re.fullmatch(r'long\.md#L1@B([0-9]+)-([0-9]+)',evidence.locator)
    assert match
    start,end=map(int,match.groups())
    assert source.read_bytes().splitlines()[0][start:end].decode() == evidence.excerpt
    # A hyphen-delimited component is also an actual indexed term.
    source.write_text('needleword '+('x '*10000)+'pre-needle-post\n')
    assert 'pre-needle-post' in index.evidence_offset('needle',limit=1,excerpt_window_bytes=128)[0].excerpt
