"""
Load markdown documents and split them into chunks.

Chunking: first by markdown heading (keeps a section together), then by size
with a small overlap. Every chunk keeps the document metadata, so we always
know where an answer came from (document attribution).
"""
import json
from pathlib import Path

import yaml
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from config.settings import settings
from src.utils.helpers import to_epoch

NAMESPACES = ["payments", "platform", "security", "hr", "products"]


def parse_document(path: Path) -> tuple[dict, str]:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        raise ValueError(f"{path.name} has no YAML header")
    _, header, body = raw.split("---", 2)
    meta = yaml.safe_load(header)
    meta["created_date"] = str(meta["created_date"])
    meta["source"] = str(path.relative_to(settings.DOCS_DIR)).replace("\\", "/")
    return meta, body.strip()


def chunk_document(meta: dict, body: str) -> list[dict]:
    header_splitter = MarkdownHeaderTextSplitter([("#", "h1"), ("##", "h2")], strip_headers=False)
    size_splitter = RecursiveCharacterTextSplitter(chunk_size=settings.CHUNK_SIZE,
                                                   chunk_overlap=settings.CHUNK_OVERLAP)
    chunks = []
    for section in header_splitter.split_text(body):
        section_name = section.metadata.get("h2") or section.metadata.get("h1") or "main"
        for piece in size_splitter.split_text(section.page_content):
            i = len(chunks)
            chunks.append({
                "id": f"{meta['doc_id']}#{i}",
                "text": f"{meta['title']}\n{piece}",  # title in every chunk helps BM25
                "metadata": {
                    "doc_id": meta["doc_id"],
                    "title": meta["title"],
                    "department": meta["department"],
                    "document_type": meta["document_type"],
                    "access_level": meta["access_level"],
                    "created_date": meta["created_date"],
                    "created_ts": to_epoch(meta["created_date"]),
                    "tags": [str(t) for t in meta.get("tags", [])],
                    "section": section_name,
                    "chunk_index": i,
                    "source": meta["source"],
                },
            })
    return chunks


def load_all_chunks() -> list[dict]:
    all_chunks = []
    for path in sorted(settings.DOCS_DIR.rglob("*.md")):
        meta, body = parse_document(path)
        all_chunks.extend(chunk_document(meta, body))
    return all_chunks


def save_chunk_cache(chunks: list[dict]) -> None:
    settings.CHUNKS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    settings.CHUNKS_CACHE.write_text(json.dumps(chunks, indent=1), encoding="utf-8")


def load_chunk_cache() -> list[dict]:
    if not settings.CHUNKS_CACHE.exists():
        chunks = load_all_chunks()
        save_chunk_cache(chunks)
        return chunks
    return json.loads(settings.CHUNKS_CACHE.read_text(encoding="utf-8"))


def document_catalog() -> list[dict]:
    """One row per document, metadata only (no text). The RLM agent explores this."""
    seen = {}
    for c in load_chunk_cache():
        m = c["metadata"]
        row = seen.setdefault(m["doc_id"], {
            "doc_id": m["doc_id"], "title": m["title"], "department": m["department"],
            "document_type": m["document_type"], "access_level": m["access_level"],
            "created_date": m["created_date"], "tags": m["tags"], "chunks": 0, "chars": 0,
        })
        row["chunks"] += 1
        row["chars"] += len(c["text"])
    return list(seen.values())
