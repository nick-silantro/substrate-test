#!/usr/bin/env python3
"""
Substrate semantic search module.

Provides embedding generation, storage, and search using a local ONNX model
(onnxruntime + tokenizers) and sqlite-vec (SQLite extension). No API keys required.

Graceful degradation: if the venv isn't set up, all functions silently no-op.
Call is_search_available() to check before operations that need search.

Usage from other scripts:
    from embeddings import is_search_available, generate_and_store, search

    if is_search_available():
        generate_and_store(conn, entity_id, entity_type, name, description)
        results = search(conn, "what needs my attention", limit=10)
"""

import os
import re
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
    import numpy as _np
    import onnxruntime as _ort
    import sqlite_vec
    from tokenizers import Tokenizer as _Tokenizer
    from huggingface_hub import hf_hub_download as _hf_download
    SEARCH_AVAILABLE = True
except ImportError:
    SEARCH_AVAILABLE = False

# Embedding model config
# Uses the ONNX version of BAAI/bge-small-en-v1.5 hosted by Qdrant on HuggingFace.
# This avoids fastembed's py-rust-stemmers dependency, which lacks wheels for
# newer Python versions (3.14+). onnxruntime and tokenizers have timely wheel releases.
_HF_ONNX_REPO = "Qdrant/fast-bge-small-en-v1.5"
MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384

# Cached model singleton
_model = None


class _TextEmbedding:
    """Minimal ONNX sentence embedder — same interface as fastembed.TextEmbedding."""

    def __init__(self, model_name: str, cache_dir: str):
        local_dir = os.path.join(cache_dir, _HF_ONNX_REPO.replace("/", "--"))
        os.makedirs(local_dir, exist_ok=True)

        tok_path = _hf_download(_HF_ONNX_REPO, "tokenizer.json", local_dir=local_dir)
        try:
            mdl_path = _hf_download(_HF_ONNX_REPO, "model_optimized.onnx", local_dir=local_dir)
        except Exception:
            mdl_path = _hf_download(_HF_ONNX_REPO, "model.onnx", local_dir=local_dir)

        self._tok = _Tokenizer.from_file(tok_path)
        self._tok.enable_padding(pad_id=0, pad_token="[PAD]")
        self._tok.enable_truncation(max_length=512)

        self._sess = _ort.InferenceSession(mdl_path, providers=["CPUExecutionProvider"])
        self._input_names = {inp.name for inp in self._sess.get_inputs()}

    def embed(self, texts):
        encoded = self._tok.encode_batch(texts)
        ids  = _np.array([e.ids            for e in encoded], dtype=_np.int64)
        mask = _np.array([e.attention_mask for e in encoded], dtype=_np.int64)

        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._input_names:
            feed["token_type_ids"] = _np.zeros_like(ids, dtype=_np.int64)

        out = self._sess.run(None, feed)[0]  # (batch, seq_len, dim)

        # Mean pool over non-padding tokens, then L2 normalize
        m = mask[:, :, None].astype(_np.float32)
        emb = (out * m).sum(1) / m.sum(1).clip(1e-9)
        return emb / _np.linalg.norm(emb, axis=1, keepdims=True).clip(1e-9)


def is_search_available():
    """Check if semantic search dependencies are installed."""
    return SEARCH_AVAILABLE


def _get_model():
    """Get or create the embedding model (singleton)."""
    global _model
    if _model is None:
        os.makedirs(MODEL_CACHE_PATH, exist_ok=True)
        _model = _TextEmbedding(MODEL_NAME, MODEL_CACHE_PATH)
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


_RRF_K = 60  # Standard RRF constant — dampens high-rank outliers


def _rrf_score(fts_rank=None, sem_rank=None):
    """Reciprocal Rank Fusion score. Higher = better match.
    Pass None for a system where the entity didn't appear.
    """
    score = 0.0
    if fts_rank is not None:
        score += 1.0 / (_RRF_K + fts_rank)
    if sem_rank is not None:
        score += 1.0 / (_RRF_K + sem_rank)
    return score


def _fts_search_raw(conn, query_text, limit, type_filter=None):
    """Run FTS5 BM25 keyword search.

    Returns list of (entity_id, fts_rank, name, type, description)
    where fts_rank is 1-indexed position in BM25 result order (1 = best).
    Returns [] on empty/short query or missing FTS table (old workspaces).
    """
    cleaned = re.sub(r'["\*\(\)\:\^\{\}\[\]]', ' ', query_text).strip()
    words = [w for w in cleaned.split() if len(w) >= 2]
    if not words:
        return []
    # Prefix-match each word: "watch*" matches watcher, watches, watching
    fts_query = ' '.join(f'"{w}"*' for w in words)

    type_clause = "AND e.type = ?" if type_filter else ""
    params = [fts_query]
    if type_filter:
        params.append(type_filter)
    params.append(limit)

    try:
        rows = conn.execute(f"""
            SELECT f.entity_id, rank, e.name, e.type, e.description
            FROM fts_entities f
            JOIN entities e ON f.entity_id = e.id
            WHERE fts_entities MATCH ?
            AND e.meta_status = 'live'
            {type_clause}
            ORDER BY rank
            LIMIT ?
        """, params).fetchall()
        # Attach 1-indexed position (ORDER BY rank gives BM25 order)
        return [(row[0], i + 1, row[2], row[3], row[4]) for i, row in enumerate(rows)]
    except Exception:
        return []  # FTS table absent on old workspaces — degrade silently


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


def hybrid_search(conn, query_text, limit=10, type_filter=None):
    """Hybrid FTS5 + semantic search blended via Reciprocal Rank Fusion (RRF).

    Falls back to FTS5-only when SEARCH_AVAILABLE is False (embeddings not set up)
    or when individual entities have no embedding yet.

    Returns: [{id, name, type, description, score}, ...] sorted by score desc.
    score is an RRF value — not normalized to [0,1]. Higher = better match.
    """
    pool = limit * 3  # oversample both systems before merging

    # --- FTS5 keyword results ---
    fts_rows = _fts_search_raw(conn, query_text, pool, type_filter)
    fts_ranks = {row[0]: row[1] for row in fts_rows}
    fts_meta  = {row[0]: {"name": row[2], "type": row[3], "description": row[4]}
                 for row in fts_rows}

    # --- Semantic vector results ---
    sem_ranks = {}
    sem_meta  = {}
    if SEARCH_AVAILABLE:
        try:
            emb = generate_embedding(query_text)
            if emb:
                blob = _serialize_embedding(emb)
                type_clause = "AND e.type = ?" if type_filter else ""
                params = [blob, pool]
                if type_filter:
                    params.append(type_filter)
                params.append(pool)
                rows = conn.execute(f"""
                    SELECT v.entity_id, e.name, e.type, e.description
                    FROM vec_entities v
                    JOIN entities e ON v.entity_id = e.id
                    WHERE v.embedding MATCH ? AND k = ?
                    AND e.meta_status = 'live'
                    {type_clause}
                    ORDER BY v.distance
                    LIMIT ?
                """, params).fetchall()
                for i, (eid, name, etype, desc) in enumerate(rows):
                    sem_ranks[eid] = i + 1
                    sem_meta[eid]  = {"name": name, "type": etype, "description": desc}
        except Exception:
            pass

    # --- RRF fusion ---
    all_ids = set(fts_ranks) | set(sem_ranks)
    if not all_ids:
        return []

    scored = []
    for eid in all_ids:
        score = _rrf_score(
            fts_rank=fts_ranks.get(eid),
            sem_rank=sem_ranks.get(eid),
        )
        meta = fts_meta.get(eid) or sem_meta.get(eid)
        scored.append({
            "id":          eid,
            "name":        meta["name"],
            "type":        meta["type"],
            "description": meta["description"],
            "score":       round(score, 6),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


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
