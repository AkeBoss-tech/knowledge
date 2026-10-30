import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from krail.provider.v1 import GetResourceRequest,ResourceRef
from rail.local_selected_file_source import LocalSelectedFileSource


def test_one_fd_excludes_sibling_and_supports_exact_offsets_and_freshness(tmp_path):
    selected=tmp_path/'chosen résumé.md';selected.write_text('Header\n'+('é '*9000)+'Straße deadline Friday\n')
    sibling=tmp_path/'sibling.md';sibling.write_text('PRIVATE SIBLING CONTENT')
    fd=os.open(selected,os.O_RDONLY|os.O_NOFOLLOW);allowed={'yes':True}
    def auth(name):
        if not allowed['yes'] or name!='chosen résumé.md':raise PermissionError('revoked')
    try:
        reader=LocalSelectedFileSource(file_fd=fd,selected_name=selected.name,authority='local-project://fixture',authorize=auth)
        item=reader.evidence_offset('STRASSE',limit=1,excerpt_window_bytes=128)[0]
        assert item.locator.startswith('chosen%20r%C3%A9sum%C3%A9.md#L2@B') and 'Straße' in item.excerpt
        bounds=item.locator.split('@B')[1].split('-'); start,end=map(int,bounds)
        assert selected.read_text().splitlines()[1].encode()[start:end].decode()==item.excerpt
        assert item.source.verifies(selected.read_bytes())
        ref=item.source
        foreign=ResourceRef(authority=reader.authority,resource_type='local.file',resource_id='sibling.md',version=ref.version,digest=ref.digest)
        with pytest.raises(PermissionError):reader.get_resource(GetResourceRequest(ref=foreign))
        token=reader.snapshot_token();selected.write_text('Straße deadline Monday\n')
        with pytest.raises(PermissionError):reader.revalidate_snapshot(token)
        with pytest.raises(PermissionError):reader.get_resource(GetResourceRequest(ref=ref))
        assert 'Monday' in reader.evidence('STRASSE')[0].excerpt
        allowed['yes']=False
        with pytest.raises(PermissionError):reader.evidence('STRASSE')
    finally:os.close(fd)


def test_concurrent_pread_does_not_change_or_depend_on_shared_offset(tmp_path):
    path=tmp_path/'one.txt';path.write_text('Atlas launch Friday\n');fd=os.open(path,os.O_RDONLY)
    try:
        reader=LocalSelectedFileSource(file_fd=fd,selected_name=path.name,authority='local-project://fixture',authorize=lambda _:None)
        os.lseek(fd,7,os.SEEK_SET)
        with ThreadPoolExecutor(max_workers=6) as executor:
            results=list(executor.map(lambda _:reader.evidence('Atlas')[0].excerpt,range(18)))
        assert results==['Atlas launch Friday']*18 and os.lseek(fd,0,os.SEEK_CUR)==7
    finally:os.close(fd)


def test_invalid_utf8_hardlink_unlink_and_special_descriptors_fail_closed(tmp_path):
    path=tmp_path/'one.txt';path.write_bytes(b'\xff')
    fd=os.open(path,os.O_RDONLY)
    try:
        reader=LocalSelectedFileSource(file_fd=fd,selected_name=path.name,authority='local-project://fixture',authorize=lambda _:None)
        with pytest.raises(UnicodeDecodeError):reader.evidence('Atlas')
        link=tmp_path/'hardlink';os.link(path,link)
        with pytest.raises(PermissionError):reader.snapshot_token()
        with pytest.raises(ValueError):LocalSelectedFileSource(file_fd=fd,selected_name=path.name,authority='local-project://fixture',authorize=lambda _:None)
        link.unlink();path.unlink()
        with pytest.raises(PermissionError):reader.snapshot_token()
    finally:os.close(fd)
    directory=os.open(tmp_path,os.O_RDONLY|os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError):LocalSelectedFileSource(file_fd=directory,selected_name='a',authority='local-project://fixture',authorize=lambda _:None)
    finally:os.close(directory)


def test_midread_mutation_fails_and_invalid_label_cannot_expand_scope(tmp_path,monkeypatch):
    path=tmp_path/'one.txt';path.write_text('Atlas original');fd=os.open(path,os.O_RDONLY)
    try:
        with pytest.raises(ValueError): LocalSelectedFileSource(file_fd=fd,selected_name='../sibling.txt',authority='local-project://fixture',authorize=lambda _:None)
        reader=LocalSelectedFileSource(file_fd=fd,selected_name=path.name,authority='local-project://fixture',authorize=lambda _:None)
        original=os.pread;called={'yes':False}
        def mutate(descriptor,count,offset):
            data=original(descriptor,count,offset)
            if not called['yes']:
                called['yes']=True;path.write_text('Atlas altered')
            return data
        monkeypatch.setattr(os,'pread',mutate)
        with pytest.raises(PermissionError):reader.evidence('Atlas')
    finally:os.close(fd)
