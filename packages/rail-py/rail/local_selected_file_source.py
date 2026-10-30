"""Read one explicitly selected host-held file descriptor, without path access.

The trusted host owns the borrowed FD, source label and live authorization. A
file descriptor does not prove its original pathname still names the file: the
host must recheck named-path identity and context before releasing results.
Reads and authority checks are sequential and do not prevent later mutation.
"""
from __future__ import annotations

from hashlib import sha256
import hmac
import json
import os
import re
import stat
from urllib.parse import quote

from krail.provider.v1 import (
    EvidenceItem, GetResourceResult, MAX_EVIDENCE_ITEM_BYTES, MAX_RESOURCE_BYTES,
    ResourcePayload, ResourceRef,
)
from rail.local_project_sources import _excerpt_window


class LocalSelectedFileSource:
    """An explicitly selected regular file, read only through borrowed FD."""
    def __init__(self, *, file_fd: int, selected_name: str, authority: str, authorize):
        if (type(file_fd) is not int or file_fd < 0 or not callable(authorize)
                or not isinstance(selected_name,str) or not selected_name or len(selected_name)>2048
                or selected_name in ('.','..') or '/' in selected_name or '\\' in selected_name
                or any(ord(c)<32 or ord(c)==127 for c in selected_name)):
            raise ValueError('invalid selected file')
        ref = ResourceRef(authority=authority,resource_type='local.file',resource_id=selected_name,
                          version='sha256:'+'0'*64,digest='sha256:'+'0'*64)
        if ref.authority != authority or ref.resource_id != selected_name:
            raise ValueError('noncanonical selected file identity')
        self.file_fd,self.selected_name,self.authority,self.authorize=file_fd,selected_name,authority,authorize
        self.authorize(self.selected_name)
        info=os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('selected file must be a single-link regular file')
        self.identity=(info.st_dev,info.st_ino)
        self.authorize(self.selected_name)

    def _stat(self):
        info=os.fstat(self.file_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (info.st_dev,info.st_ino)!=self.identity:
            raise PermissionError('selected file identity changed')
        if info.st_size>MAX_RESOURCE_BYTES:
            raise ValueError('selected file exceeds byte bound')
        return info

    def _read(self):
        self.authorize(self.selected_name)
        before=self._stat();chunks=[];offset=0
        while True:
            chunk=os.pread(self.file_fd,min(65536,MAX_RESOURCE_BYTES+1-offset),offset)
            if not chunk: break
            chunks.append(chunk);offset+=len(chunk)
            if offset>MAX_RESOURCE_BYTES: raise ValueError('selected file exceeds byte bound')
        after=self._stat()
        fields=lambda value:(value.st_dev,value.st_ino,value.st_nlink,value.st_size,value.st_mtime_ns,value.st_ctime_ns)
        if fields(before)!=fields(after) or offset!=after.st_size:
            raise PermissionError('selected file changed')
        raw=b''.join(chunks)
        raw.decode('utf-8')
        self.authorize(self.selected_name)
        return raw

    def _ref(self,raw):
        digest='sha256:'+sha256(raw).hexdigest()
        return ResourceRef(authority=self.authority,resource_type='local.file',resource_id=self.selected_name,version=digest,digest=digest)

    def snapshot_token(self):
        first=self._read();second=self._read()
        if first!=second: raise PermissionError('selected file changed')
        data=json.dumps({'kind':'selected-file-fd-v1','authority':self.authority,'name':self.selected_name,
                         'device':self.identity[0],'inode':self.identity[1],'digest':sha256(first).hexdigest()},
                        ensure_ascii=True,sort_keys=True,separators=(',',':')).encode()
        return 'sha256:'+sha256(data).hexdigest()

    def revalidate_snapshot(self,token):
        if not isinstance(token,str) or not re.fullmatch(r'sha256:[a-f0-9]{64}',token):
            raise ValueError('invalid selected file snapshot')
        if not hmac.compare_digest(token,self.snapshot_token()): raise PermissionError('selected file changed')

    def evidence(self, query: str, *, limit: int = 20, excerpt_window_bytes: int | None = None):
        if not isinstance(query,str) or not query.strip() or len(query)>8192 or type(limit) is not int or not 1<=limit<=32:
            raise ValueError('invalid selected file query')
        if excerpt_window_bytes is not None and (type(excerpt_window_bytes) is not int or not 1<=excerpt_window_bytes<=MAX_EVIDENCE_ITEM_BYTES):
            raise ValueError('invalid excerpt window size')
        token=self.snapshot_token();raw=self._read();text=raw.decode('utf-8')
        terms=set(re.findall(r'\w+',query.casefold()))
        lines=text.splitlines()
        score=sum(term in text.casefold() for term in terms)/len(terms) if terms else 0
        result=()
        if score and lines:
            best=max(range(len(lines)),key=lambda i:sum(term in lines[i].casefold() for term in terms))
            line=lines[best]
            if excerpt_window_bytes is None:
                excerpt=line.encode('utf-8')[:MAX_EVIDENCE_ITEM_BYTES].decode('utf-8',errors='ignore');suffix=''
            else:
                excerpt,start,end=_excerpt_window(line,terms,excerpt_window_bytes);suffix=f'@B{start}-{end}'
            if excerpt:
                result=(EvidenceItem(source=self._ref(raw),locator=f'{quote(self.selected_name,safe="")}#L{best+1}{suffix}',excerpt=excerpt,relevance=score),)
        self.revalidate_snapshot(token)
        return result

    def evidence_offset(self, query: str, *, limit: int, excerpt_window_bytes: int):
        if type(excerpt_window_bytes) is not int or not 1<=excerpt_window_bytes<=MAX_EVIDENCE_ITEM_BYTES:
            raise ValueError('invalid excerpt window size')
        return self.evidence(query,limit=limit,excerpt_window_bytes=excerpt_window_bytes)

    def get_resource(self, request):
        if (request.ref.authority!=self.authority or request.ref.resource_type!='local.file'
                or request.ref.resource_id!=self.selected_name):
            raise PermissionError('selected source unavailable')
        token=self.snapshot_token();raw=self._read();ref=self._ref(raw)
        if request.ref.exact_key!=ref.exact_key: raise PermissionError('selected file changed')
        content=raw[:request.max_bytes].decode('utf-8',errors='ignore')
        self.revalidate_snapshot(token)
        return GetResourceResult(resource=ResourcePayload(ref=ref,media_type='text/plain',content=content,truncated=len(raw)>request.max_bytes))
