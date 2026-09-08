"""Opt-in signed retention of a reviewed source document from an external Git root."""
from __future__ import annotations
import fcntl, json, os, re, subprocess, selectors, time
from contextlib import contextmanager
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import rfc8785
from krail.provider.v1 import ResourceRef
from rail.bootstrap import bootstrap_future_project
from rail.hosted.access import AccessContextAuthority, InvalidAccessContext, SignedAccessContext, SignedPacketRequestBinding
from rail.integrity import ResearchIntegrityRepo, SourceCandidateRecord

_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
_HEX = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_MAX_BLOB_BYTES = 1_048_576
_MAX_MANIFEST_BYTES = 1_048_576

def registered_git_evidence_request_digest(action: str, ref: ResourceRef, target: str, state_root: str | Path) -> str:
    """Public canonical binding for caller-owned bridge delegations."""
    return "sha256:" + sha256(rfc8785.dumps({"action":action,"resource":ref.model_dump(mode="json"),"target":target,"state_root":Path(state_root).resolve().as_uri(),"purpose":"registered-git-evidence"})).hexdigest()

@dataclass(frozen=True)
class RetainedEvidence:
    capture_id: str
    review_id: str
    commit: str
    path: str
    content_digest: str
    captured_at: datetime
    reviewed_at: datetime
    content: bytes
    source_ref: ResourceRef

class RegisteredGitEvidenceBridge:
    """Signed, explicit bridge; construction never creates or mutates state."""
    def __init__(self, source_repo: str | Path, state_root: str | Path, *, authority: AccessContextAuthority, context_for: Callable[[str, str, ResourceRef], tuple[SignedAccessContext, SignedPacketRequestBinding] | None], tenant_id: str, project_id: str, capability_id: str, capability_version: str, capability_digest: str, clock: Callable[[], datetime]) -> None:
        self.source_repo = Path(source_repo).resolve(strict=True); self.state_root = Path(state_root).resolve()
        self.authority, self.context_for, self.clock = authority, context_for, clock
        self.tenant_id, self.project_id = tenant_id, project_id; self.capability_id, self.capability_version, self.capability_digest = capability_id, capability_version, capability_digest
        if self.state_root == self.source_repo or self.state_root.is_relative_to(self.source_repo) or self.source_repo.is_relative_to(self.state_root): raise ValueError("state_root must be separate from source_repo")
        if not ((self.source_repo / ".git").exists() or (self.source_repo / "HEAD").exists()): raise ValueError("source_repo must be Git")
        self._manifest = self.state_root / ".krail" / "registered-evidence-staging.json"
    def _digest(self, b: bytes) -> str: return "sha256:" + sha256(b).hexdigest()
    def _ref(self, commit: str, path: str, digest: str) -> ResourceRef: return ResourceRef(authority="git+" + self.source_repo.as_uri(), resource_type="git.file", resource_id=path, version=commit, digest=digest)
    def _request_digest(self, action: str, ref: ResourceRef, target: str) -> str:
        return registered_git_evidence_request_digest(action,ref,target,self.state_root)
    def _authorize(self, action: str, ref: ResourceRef, subject: str, target: str) -> tuple[SignedAccessContext, SignedPacketRequestBinding]:
        pair = self.context_for(subject, action, ref)
        if pair is None: raise PermissionError("evidence unavailable")
        context, signed = pair
        try:
            now = self.clock(); claims = self.authority.verify(context, as_of=now); binding = self.authority.verify_packet_request_binding(signed, access_context=context, as_of=now)
        except (InvalidAccessContext, AttributeError, KeyError, TypeError, ValueError) as exc: raise PermissionError("evidence unavailable") from exc
        if ((claims.tenant_id, claims.project_id, claims.subject) != (self.tenant_id,self.project_id,subject) or claims.capability_id != self.capability_id or claims.capability_version != self.capability_version or claims.capability_digest != self.capability_digest or action not in claims.actions or ref.resource_id not in claims.source_ids or (binding.tenant_id,binding.project_id)!=(self.tenant_id,self.project_id) or binding.capability_id != self.capability_id or binding.capability_version != self.capability_version or binding.capability_digest != self.capability_digest or binding.access_context_digest != context.context_digest or binding.request_digest != self._request_digest(action,ref,target) or binding.purpose != "registered-git-evidence" or binding.scope != ref.authority): raise PermissionError("evidence unavailable")
        return context, signed
    def _blob(self, commit: str, path: str) -> bytes:
        if not _HEX.fullmatch(commit) or not path or Path(path).is_absolute() or ".." in Path(path).parts or "\\" in path: raise ValueError("exact immutable Git ref required")
        size = subprocess.run(("git","-C",str(self.source_repo),"cat-file","-s",f"{commit}:{path}"),check=False,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5)
        if size.returncode or not size.stdout.strip().isdigit() or int(size.stdout) > _MAX_BLOB_BYTES: raise ValueError("Git blob exceeds byte limit")
        process = subprocess.Popen(("git","-C",str(self.source_repo),"show",f"{commit}:{path}"),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            selector=selectors.DefaultSelector(); selector.register(process.stdout,selectors.EVENT_READ,"out"); selector.register(process.stderr,selectors.EVENT_READ,"err")
            content=bytearray(); deadline=time.monotonic()+5
            while selector.get_map() and time.monotonic()<deadline:
                for key,_ in selector.select(max(0,deadline-time.monotonic())):
                    chunk=os.read(key.fileobj.fileno(),65536)
                    if not chunk: selector.unregister(key.fileobj); continue
                    if key.data=="out":
                        content.extend(chunk)
                        if len(content)>_MAX_BLOB_BYTES: raise ValueError("Git blob exceeds byte limit")
            if selector.get_map(): raise ValueError("Git blob read timed out")
            process.wait(timeout=1)
        except Exception:
            process.kill(); process.wait(); raise
        finally:
            selector.close(); process.stdout.close(); process.stderr.close()
        if process.returncode or len(content)>_MAX_BLOB_BYTES: raise ValueError("Git blob exceeds byte limit")
        return bytes(content)
    def _sidecar(self, record: dict) -> Path:
        raw=record.get("blob")
        if not isinstance(raw,str) or Path(raw).is_absolute() or ".." in Path(raw).parts: raise PermissionError("evidence unavailable")
        original=self.state_root/raw
        if original.is_symlink(): raise PermissionError("evidence unavailable")
        path=original.resolve()
        root=(self.state_root/"sources"/"registered").resolve()
        if not path.is_relative_to(root) or path.is_symlink() or not path.is_file(): raise PermissionError("evidence unavailable")
        return path
    def _read_local_bounded(self, path: Path, limit: int) -> bytes:
        flags=os.O_RDONLY | getattr(os,"O_NOFOLLOW",0)
        fd=os.open(path,flags)
        try:
            stat=os.fstat(fd)
            if not os.path.isfile(path) or stat.st_size>limit: raise PermissionError("evidence unavailable")
            data=os.read(fd,limit+1)
            if len(data)>limit: raise PermissionError("evidence unavailable")
            return data
        finally: os.close(fd)
    def _read_manifest(self) -> dict:
        if not self._manifest.exists(): return {}
        raw=self._read_local_bounded(self._manifest,_MAX_MANIFEST_BYTES)
        if len(raw)>_MAX_MANIFEST_BYTES: raise PermissionError("evidence unavailable")
        value=json.loads(raw)
        if not isinstance(value,dict): raise PermissionError("evidence unavailable")
        return value
    def _write_manifest(self, value: dict) -> None:
        self._manifest.parent.mkdir(parents=True,exist_ok=True); tmp=self._manifest.with_suffix(".tmp"); tmp.write_text(json.dumps(value,sort_keys=True,indent=2)+"\n",encoding="utf-8"); os.replace(tmp,self._manifest)
    @contextmanager
    def _locked_manifest(self):
        self._manifest.parent.mkdir(parents=True,exist_ok=True)
        with self._manifest.with_suffix(".lock").open("a+") as lock:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
            yield
    def setup(self, *, user_id: str, setup_id: str) -> Path:
        if not _ID.fullmatch(setup_id): raise ValueError("invalid setup id")
        setup_ref=ResourceRef(authority="git+"+self.source_repo.as_uri(),resource_type="git.repository",resource_id="knowledge-state/"+setup_id,version="setup",digest=self._digest(setup_id.encode()))
        self._authorize("capture.write",setup_ref,user_id,"setup:"+setup_id)
        bootstrap_future_project(self.state_root,name=f"Registered evidence {self.project_id}",slug=self.project_id)
        return self.state_root
    def capture(self, *, user_id: str, commit: str, path: str, capture_id: str) -> RetainedEvidence:
        if not _ID.fullmatch(capture_id): raise ValueError("invalid capture id")
        if not (self.state_root/"rail.yaml").exists(): raise RuntimeError("explicit setup required")
        content=self._blob(commit,path); digest=self._digest(content); ref=self._ref(commit,path,digest); target="capture:"+capture_id
        context,signed=self._authorize("capture.write",ref,user_id,target)
        with self._locked_manifest():
            if self._read_manifest().get(capture_id) is not None: raise ValueError("capture id already exists")
            self.authority.verify(context, as_of=self.clock())
            self.authority.verify_packet_request_binding(signed,access_context=context,as_of=self.clock())
            blob=self.state_root/"sources"/"registered"/(capture_id+".bin"); blob.parent.mkdir(parents=True,exist_ok=True); tmp=blob.with_suffix(".tmp"); tmp.write_bytes(content); os.replace(tmp,blob)
            candidate=SourceCandidateRecord(candidate_key="registered:"+capture_id,title=path,url_or_path=blob.relative_to(self.state_root).as_posix(),source_type_hint="document",discovered_in_paths=[path],snippet=f"git:{commit}:{path}:{digest}")
            ResearchIntegrityRepo(self.state_root).upsert_source_candidate(candidate)
            now=self.clock(); manifest=self._read_manifest(); manifest[capture_id]={"commit":commit,"path":path,"digest":digest,"blob":candidate.url_or_path,"captured_at":now.isoformat()}; self._write_manifest(manifest)
        return RetainedEvidence(capture_id,"",commit,path,digest,now,now,content,ref)
    def review(self, *, user_id: str, capture_id: str, review_id: str) -> RetainedEvidence:
        if not _ID.fullmatch(review_id): raise ValueError("invalid review id")
        with self._locked_manifest():
            record=self._read_manifest().get(capture_id); repo=ResearchIntegrityRepo(self.state_root)
            if not record: raise PermissionError("evidence unavailable")
            content=self._read_local_bounded(self._sidecar(record),_MAX_BLOB_BYTES); ref=self._ref(record["commit"],record["path"],record["digest"])
            if self._digest(content)!=record["digest"]: raise PermissionError("evidence unavailable")
            target=f"review:{capture_id}:{review_id}:{record['digest']}"
            context,_signed=self._authorize("shared_knowledge.review",ref,user_id,target)
            # The same context is checked after lock acquisition, immediately
            # before every canonical and manifest publication.
            self.authority.verify(context, as_of=self.clock())
            promoted=repo.promote_source_candidate("registered:"+capture_id,source_type="document",title=record["path"],origin=f"git:{record['commit']}:{record['path']}",access_method="signed-git-blob",freshness_status="fresh",quality_status="validated",provenance={"path":record["blob"],"gitCommit":record["commit"],"gitPath":record["path"],"retainedBlobDigest":record["digest"],"reviewId":review_id,"reviewer":user_id,"reviewedAt":self.clock().isoformat(),"signedContextDigest":context.context_digest,"captureId":capture_id})
            source_key=promoted["source"]["source_key"]
            repo.update_source(source_key, admissibility_status="observed", quality_status="validated")
            now=self.clock(); record.update(review_id=review_id,reviewed_at=now.isoformat(),source_key=source_key); m=self._read_manifest(); m[capture_id]=record; self._write_manifest(m)
        return RetainedEvidence(capture_id,review_id,record["commit"],record["path"],record["digest"],datetime.fromisoformat(record["captured_at"]),now,content,ref)
    def retrieve(self, *, user_id: str, capture_id: str, at: datetime | None = None) -> RetainedEvidence:
        record=self._read_manifest().get(capture_id); repo=ResearchIntegrityRepo(self.state_root)
        if not record or not record.get("source_key"): raise PermissionError("evidence unavailable")
        source=next((x for x in repo.load_sources() if x.source_key==record["source_key"]),None)
        provenance=source.provenance if source else {}
        if source is None or source.quality_status!="validated" or source.freshness_status not in {"fresh"} or source.admissibility_status in {"blocked","inadmissible"} or any(provenance.get(key)!=value for key,value in {"captureId":capture_id,"path":record["blob"],"gitCommit":record["commit"],"gitPath":record["path"],"retainedBlobDigest":record["digest"],"reviewId":record.get("review_id")}.items()): raise PermissionError("evidence unavailable")
        content=self._read_local_bounded(self._sidecar(record),_MAX_BLOB_BYTES); ref=self._ref(record["commit"],record["path"],record["digest"])
        if self._digest(content)!=record["digest"]: raise PermissionError("evidence unavailable")
        self._authorize("capture.read",ref,user_id,"retrieve:"+capture_id); self._authorize("shared_knowledge.read",ref,user_id,"retrieve:"+capture_id)
        reviewed=datetime.fromisoformat(source.provenance["reviewedAt"]); observed=at or self.clock()
        if observed<reviewed: raise PermissionError("evidence unavailable")
        return RetainedEvidence(capture_id,record["review_id"],record["commit"],record["path"],record["digest"],datetime.fromisoformat(record["captured_at"]),reviewed,content,ref)
