#!/usr/bin/env python3
"""
Substrate semantic search module.

Provides embedding generation, storage, and search using fastembed (local model)
and sqlite-vec (SQLite extension). No API keys required.

Graceful degradation: if the venv isn't set up, all functions silently no-op.
Call is_search_available() to check before operations that need search.

Usage from other scripts:
    from embeddings import is_search_available, generate_and_store, search

    if is_search_available():
        generate_and_store(conn, entity_id, entity_type, name, description)
        results = search(conn, "what needs my attention", limit=10)
"""

import os
import sys
import struct
import sqlite3

SUBSTRATE_PATH = os.environ.get("SUBSTRATE_PATH", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
VENV_PATH = os.path.join(SUBSTRATE_PATH, "_system", "venv")
MODEL_CACHE_PATH = os.path.join(os.path.expanduser("~"), ".substrate", "model-cache")

# Find the venv site-packages and add to path
# Windows: Lib/site-packages (flat). Unix: lib/pythonX.Y/site-packages (versioned).
_venv_site = None
if sys.platform == "win32":
    candidate = os.path.join(VENV_PATH, "Lib", "site-packages")
    if os.path.exists(candidate):
        _venv_site = candidate
else:
    _venv_lib = os.path.join(VENV_PATH, "lib")
    if os.path.exists(_venv_lib):
        for d in os.listdir(_venv_lib):
            if d.startswith("python"):
                candidate = os.path.join(_venv_lib, d, "site-packages")
                if os.path.exists(candidate):
                    _venv_site = candidate
                    break

if _venv_site:
    sys.path.insert(0, _venv_site)

# Try importing search dependencies
try:
    import sqlite_vec
    from fastembed import TextEmbedding
    SEARCH_AVAILABLE = True
except ImportError:
    SEARCH_AVAILABLE = False

# Embedding model config
MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384

# Cached model singleton
_model = None


def is_search_available():
    """Check if semantic search dependencies are installed."""
    return SEARCH_AVAILABLE


def _get_model():
    """Get or create the embedding model (singleton)."""
    global _model
    if _model is None:
        os.makedirs(MODEL_CACHE_PATH, exist_ok=True)
        _model = TextEmbedding(MODEL_NAME, cache_dir=MODEL_CACHE_PATH)
    return _model


def embed_entity_text(entity_type, name, description):
    """Build the text representation for an entity.

    Format: "{type}: {name}. {description}"
    This captures both what the entity is and what it's about.
    """
    desc = description or ""
    if desc == "[awaiting context]":
        desc = ""
    text = f"{entity_type}: {name}"
    if desc:
        text += f". {desc}"
    return text


def generate_embedding(text):
    """Generate an embedding vector for a text string.

    Returns a list of floats (384 dimensions).
    """
    if not SEARCH_AVAILABLE:
        return None
    model = _get_model()
    embeddings = list(model.embed([text]))
    return embeddings[0].tolist()


def _serialize_embedding(embedding):
    """Serialize a float list to bytes for sqlite-vec."""
    return struct.pack(f'{len(embedding)}f', *embedding)


def init_vec_table(conn):
    """Create the vec_entities virtual table if it doesn't exist.

    Must be called on a connection that has sqlite-vec loaded.
    """
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_entities USING vec0(
            entity_id TEXT PRIMARY KEY,
            embedding FLOAT[384]
        )
    """)


def load_vec_extension(conn):
    """Load sqlite-vec into a connection. Returns True on success."""
    if not SEARCH_AVAILABLE:
        return False
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        return True
    except Exception:
        return False


def store_embedding(conn, entity_id, embedding):
    """Store an embedding in the vec_entities table.

    Handles upsert: deletes existing row first if present.
    """
    blob = _serialize_embedding(embedding)
    # sqlite-vec doesn't support ON CONFLICT for virtual tables
    conn.execute("DELETE FROM vec_entities WHERE entity_id = ?", (entity_id,))
    conn.execute(
        "INSERT INTO vec_entities (entity_id, embedding) VALUES (?, ?)",
        (entity_id, blob)
    )


def generate_and_store(conn, entity_id, entity_type, name, description):
    """Generate an embedding for an entity and store it.

    Convenience function combining embed_entity_text + generate_embedding + store_embedding.
    No-ops silently if search isn't available.
    """
    if not SEARCH_AVAILABLE:
        return
    text = embed_entity_text(entity_type, name, description)
    embedding = generate_embedding(text)
    if embedding:
        store_embedding(conn, entity_id, embedding)


def search(conn, query_text, limit=10, type_filter=None):
    """Search for entities by semantic similarity.

    Args:
        conn: SQLite connection with sqlite-vec loaded
        query_text: Natural language query
        limit: Max results (default 10)
        type_filter: Optional entity type to filter by

    Returns:
        List of dicts: [{id, name, type, description, distance}, ...]
        Lower distance = more similar.
    """
    if not SEARCH_AVAILABLE:
        return []

    embedding = generate_embedding(query_text)
    if not embedding:
        return []

    blob = _serialize_embedding(embedding)

    if type_filter:
        rows = conn.execute("""
            SELECT v.entity_id, v.distance, e.name, e.type, e.description
            FROM vec_entities v
            JOIN entities e ON v.entity_id = e.id
            WHERE v.embedding MATCH ? AND k = ?
            AND e.type = ? AND e.meta_status = 'live'
            ORDER BY v.distance
            LIMIT ?
        """, (blob, limit * 3, type_filter, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT v.entity_id, v.distance, e.name, e.type, e.description
            FROM vec_entities v
            JOIN entities e ON v.entity_id = e.id
            WHERE v.embedding MATCH ? AND k = ?
            AND e.meta_status = 'live'
            ORDER BY v.distance
            LIMIT ?
        """, (blob, limit * 3, limit)).fetchall()

    results = []
    for entity_id, distance, name, etype, desc in rows:
        results.append({
            "id": entity_id,
            "name": name,
            "type": etype,
            "description": desc,
            "distance": round(distance, 4),
        })

    return results


def remove_embedding(conn, entity_id):
    """Remove an embedding from the vec_entities table.

    Tolerant of the table not existing: on a fresh workspace (or one whose
    migrate-to-sqlite.py hasn't run since sqlite-vec was installed), the
    vec_entities virtual table is absent. Deleting an entity shouldn't fail
    just because there's no embedding to remove — the underlying entity
    delete has already happened.
    """
    try:
        conn.execute("DELETE FROM vec_entities WHERE entity_id = ?", (entity_id,))
    except sqlite3.OperationalError as e:
        if "no such table" in str(e).lower():
            return  # nothing to clean up — table not initialized
        raise
