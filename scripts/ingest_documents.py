"""
Ingest documents: load -> chunk -> embed (dense + sparse) -> upsert to Pinecone.

    python scripts/ingest_documents.py            # Pinecone (needs PINECONE_API_KEY)
    python scripts/ingest_documents.py --local    # only build data/processed/chunks.json
"""
import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from src.rag.loader import load_all_chunks, save_chunk_cache  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true", help="skip Pinecone")
    args = parser.parse_args()

    chunks = load_all_chunks()
    save_chunk_cache(chunks)
    docs = len({c["metadata"]["doc_id"] for c in chunks})
    print(f"Chunked {docs} documents into {len(chunks)} chunks -> data/processed/chunks.json")

    if args.local:
        return
    from src.rag.vector_store import PineconeStore
    store = PineconeStore()
    store.ensure_index()
    store.upsert_chunks(chunks)
    print("Upserted to Pinecone (namespaces = departments)")


if __name__ == "__main__":
    main()
