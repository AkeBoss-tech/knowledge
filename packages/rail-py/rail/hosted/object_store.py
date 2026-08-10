"""Small immutable object-store interface and deterministic implementations."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Protocol


class ImmutableObjectStore(Protocol):
    def put_if_absent(self, key: str, value: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def contains(self, key: str) -> bool: ...


def _safe_key(root: Path, key: str) -> Path:
    candidate = (root / key).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("object key must stay inside the object-store root") from exc
    return candidate


class MemoryObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_if_absent(self, key: str, value: bytes) -> None:
        existing = self.objects.get(key)
        if existing is not None and existing != value:
            raise ValueError("immutable object key already contains different bytes")
        self.objects[key] = bytes(value)

    def get(self, key: str) -> bytes:
        return self.objects[key]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def contains(self, key: str) -> bool:
        return key in self.objects


class FileObjectStore:
    """Local content-addressed implementation with atomic publication."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put_if_absent(self, key: str, value: bytes) -> None:
        path = _safe_key(self.root, key)
        if path.exists():
            if path.read_bytes() != value:
                raise ValueError("immutable object key already contains different bytes")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != value:
                    raise ValueError("immutable object key already contains different bytes")
        finally:
            Path(temporary).unlink(missing_ok=True)

    def get(self, key: str) -> bytes:
        return _safe_key(self.root, key).read_bytes()

    def delete(self, key: str) -> None:
        _safe_key(self.root, key).unlink(missing_ok=True)

    def contains(self, key: str) -> bool:
        return _safe_key(self.root, key).is_file()


class S3ObjectStore:
    """S3-compatible immutable adapter using an injected client.

    The SDK is deployment-specific: local KRAIL never imports it.  Conditional
    publication prevents a concurrent writer from replacing an existing key.
    """

    def __init__(self, client: object, *, bucket: str, prefix: str = "krail") -> None:
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/")

    @classmethod
    def from_boto3(cls, *, bucket: str, prefix: str = "krail", **options: object) -> "S3ObjectStore":
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - deployment-only branch
            raise RuntimeError("install krail[hosted-s3] to use S3ObjectStore") from exc
        return cls(boto3.client("s3", **options), bucket=bucket, prefix=prefix)

    def _key(self, key: str) -> str:
        normalized = key.strip("/")
        if not normalized or ".." in normalized.split("/"):
            raise ValueError("object key must be a non-empty normalized path")
        return f"{self.prefix}/{normalized}" if self.prefix else normalized

    def put_if_absent(self, key: str, value: bytes) -> None:
        target = self._key(key)
        try:
            self.client.put_object(Bucket=self.bucket, Key=target, Body=value, IfNoneMatch="*")
        except Exception as exc:  # compatible SDKs expose different precondition classes
            response = getattr(exc, "response", {})
            status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            code = response.get("Error", {}).get("Code")
            if status not in {409, 412} and code not in {"409", "412", "ConditionalRequestConflict", "PreconditionFailed"}:
                raise
            if self.get(key) != value:
                raise ValueError("immutable object key already contains different bytes") from exc

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self._key(key))["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._key(key))

    def contains(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(key))
        except Exception as exc:  # compatible SDKs expose different not-found classes
            response = getattr(exc, "response", {})
            status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            code = response.get("Error", {}).get("Code")
            if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        return True
