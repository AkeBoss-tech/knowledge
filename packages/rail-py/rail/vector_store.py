from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


VECTOR_DIM = 384
CHUNK_CHARS = 1800
CHUNK_OVERLAP = 240
_TOKEN_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "what",
    "when",
    "where",
    "who",
    "why",
}


@dataclass(frozen=True)
class VectorChunk:
    path: str
    title: str
    chunk_index: int
    text: str
    vector: list[float]


def tokenize(text: str) -> list[str]:
    terms: list[str] = []
    for raw in _TOKEN_RE.findall(text):
        for part in [raw, *re.split(r"[_.-]+", raw)]:
            token = part.lower()
            if len(token) > 1 and token not in _STOPWORDS:
                terms.append(token)
    return terms


def embed_text(text: str, *, dim: int = VECTOR_DIM) -> list[float]:
    vector = [0.0] * dim
    for token in tokenize(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[bucket] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm:
        vector = [value / norm for value in vector]
    return vector


def cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def chunk_text(text: str, *, chunk_chars: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    if len(normalized) <= chunk_chars:
        return [normalized]
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + chunk_chars, len(normalized))
        chunks.append(normalized[start:end].strip())
        if end == len(normalized):
            break
        start = max(end - overlap, start + 1)
    return chunks


def title_for(path: Path, text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or path.stem
    return path.stem.replace("-", " ").replace("_", " ").strip() or path.name


class LocalVectorStore:
    def __init__(self, project_path: str | Path, db_path: str | Path | None = None):
        self.project_path = Path(project_path).resolve()
        self.db_path = Path(db_path).resolve() if db_path else self.project_path / ".krail" / "vector.sqlite"

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chunks (
              id TEXT PRIMARY KEY,
              path TEXT NOT NULL,
              title TEXT NOT NULL,
              chunk_index INTEGER NOT NULL,
              text TEXT NOT NULL,
              vector_json TEXT NOT NULL,
              content_sha TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path)")
        return conn

    def build(self, docs: list[Path]) -> dict[str, Any]:
        rows: list[VectorChunk] = []
        for path in docs:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            rel = path.relative_to(self.project_path).as_posix()
            title = title_for(path, text)
            for idx, chunk in enumerate(chunk_text(text)):
                rows.append(VectorChunk(rel, title, idx, chunk, embed_text(chunk)))

        conn = self._connect()
        with conn:
            conn.execute("DELETE FROM chunks")
            for row in rows:
                content_sha = hashlib.sha1(row.text.encode("utf-8")).hexdigest()
                chunk_id = f"{row.path}#{row.chunk_index}:{content_sha[:10]}"
                conn.execute(
                    """
                    INSERT INTO chunks (id, path, title, chunk_index, text, vector_json, content_sha)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk_id,
                        row.path,
                        row.title,
                        row.chunk_index,
                        row.text,
                        json.dumps(row.vector),
                        content_sha,
                    ),
                )
        conn.close()
        return {
            "status": "indexed",
            "database": str(self.db_path.relative_to(self.project_path)),
            "documents": len({row.path for row in rows}),
            "chunks": len(rows),
            "embedding": {"provider": "local_hash", "dimensions": VECTOR_DIM},
        }

    def search(self, query: str, *, limit: int = 10) -> dict[str, Any]:
        if not self.db_path.exists():
            return {
                "query": query,
                "hits": [],
                "database": str(self.db_path.relative_to(self.project_path)),
                "status": "missing_index",
            }
        query_vector = embed_text(query)
        conn = self._connect()
        rows = conn.execute("SELECT id, path, title, chunk_index, text, vector_json FROM chunks").fetchall()
        conn.close()
        hits: list[dict[str, Any]] = []
        for chunk_id, path, title, chunk_index, text, vector_json in rows:
            score = cosine(query_vector, json.loads(vector_json))
            if score <= 0:
                continue
            hits.append(
                {
                    "id": chunk_id,
                    "path": path,
                    "title": title,
                    "chunk_index": chunk_index,
                    "score": round(score, 4),
                    "snippet": text[:320],
                }
            )
        hits.sort(key=lambda item: (-item["score"], item["path"], item["chunk_index"]))
        return {
            "query": query,
            "hits": hits[:limit],
            "database": str(self.db_path.relative_to(self.project_path)),
            "embedding": {"provider": "local_hash", "dimensions": VECTOR_DIM},
        }
