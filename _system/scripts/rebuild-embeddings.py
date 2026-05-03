#!/usr/bin/env python3
"""
Rebuild all entity embeddings.

Reads all live entities from SQLite, generates embeddings, and stores them
in the vec_entities virtual table. Safe to re-run (overwrites existing embeddings).

Usage: python3 _system/scripts/rebuild-embeddings.py [--batch-size N]

Requires: setup-search.py must have been run first.
"""

import os
import sys
import sqlite3
import argparse

SUBSTRATE_PATH = os.environ.get("SUBSTRATE_PATH", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DB_PATH = os.path.join(SUBSTRATE_PATH, "_system", "index", "substrate.db")

# Import embeddings module (handles venv path setup)
from embeddings import (
    is_search_available, load_vec_extension, init_vec_table,
    embed_entity_text, _get_model, _serialize_embedding, store_embedding
)


def main():
    parser = argparse.ArgumentParser(description="Rebuild all entity embeddings")
    parser.add_argument("--batch-size", type=int, default=64, help="Embedding batch size (default: 64)")
    args = parser.parse_args()

    if not is_search_available():
        print("Semantic search not set up.")
        print("Run: python3 _system/scripts/setup-search.py")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    if not load_vec_extension(conn):
        print("Failed to load sqlite-vec extension.")
        sys.exit(1)

    init_vec_table(conn)

    # Clear existing embeddings
    conn.execute("DELETE FROM vec_entities")
    conn.commit()

    # Read all live entities
    c = conn.cursor()
    c.execute("SELECT id, type, name, description FROM entities WHERE meta_status = 'live'")
    entities = c.fetchall()

    if not entities:
        print("No live entities found.")
        conn.close()
        return

    print(f"Embedding {len(entities)} entities...")

    # Build texts for batch embedding
    texts = []
    entity_ids = []
    for entity_id, entity_type, name, description in entities:
        text = embed_entity_text(entity_type, name, description)
        texts.append(text)
        entity_ids.append(entity_id)

    # Generate embeddings in batches using fastembed's built-in batching
    model = _get_model()
    embeddings = list(model.embed(texts, batch_size=args.batch_size))

    # Store embeddings
    stored = 0
    for entity_id, embedding in zip(entity_ids, embeddings):
        store_embedding(conn, entity_id, embedding.tolist())
        stored += 1
        if stored % 50 == 0:
            print(f"  Embedded {stored}/{len(entities)}...")
            conn.commit()

    conn.commit()
    conn.close()

    print(f"Done! Embedded {stored}/{len(entities)} entities.")


if __name__ == "__main__":
    main()
