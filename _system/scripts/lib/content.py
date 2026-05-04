"""
lib/content.py — On-demand decryption of registry content from the local cache.

Content is stored in $SUBSTRATE_PATH/_system/cache/ as AES-256-GCM encrypted blobs
(nonce(12 bytes) || ciphertext+tag). Nothing is ever written to disk in plaintext.

The session key is sourced from (in priority order):
  1. SUBSTRATE_CONTENT_KEY env var (hex string) — set by Surface when spawning subprocesses
  2. ~/.substrate/session.key — written by 'substrate sync' with mode 0600

Call decrypt_cache_file() to read and decrypt any cached content file by its
registry-relative name (e.g. "skills/entity-management/SKILL.md").
"""

import os
from pathlib import Path

SESSION_KEY_PATH = Path("~/.substrate/session.key").expanduser()


def load_session_key() -> bytes:
    """Return the 32-byte AES session key, or raise if unavailable."""
    env_key = os.environ.get("SUBSTRATE_CONTENT_KEY")
    if env_key:
        return bytes.fromhex(env_key)
    if SESSION_KEY_PATH.exists():
        return SESSION_KEY_PATH.read_bytes()
    raise RuntimeError(
        "No session key found. Run 'substrate sync' to download registry content."
    )


def _decrypt_blob(key: bytes, blob: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(blob) < 12:
        raise ValueError("Encrypted blob is too short (< 12 bytes)")
    return AESGCM(key).decrypt(blob[:12], blob[12:], None)


def decrypt_cache_file(cache_dir: "str | Path", relative_name: str) -> bytes:
    """
    Read and decrypt a single cached blob.

    Args:
        cache_dir: Path to the instance's _system/cache/ directory.
        relative_name: Registry-relative file path, e.g. "schema/types.yaml"

    Returns:
        Plaintext bytes.

    Raises:
        FileNotFoundError: if the blob is not in cache (run 'substrate sync').
        RuntimeError: if no session key is available.
    """
    blob_path = Path(cache_dir) / relative_name
    if not blob_path.exists():
        raise FileNotFoundError(
            f"Cache miss: {relative_name}. Run 'substrate sync' to download content."
        )
    key = load_session_key()
    return _decrypt_blob(key, blob_path.read_bytes())


def decrypt_cache_text(cache_dir: "str | Path", relative_name: str) -> str:
    """Convenience wrapper — returns plaintext as a UTF-8 string."""
    return decrypt_cache_file(cache_dir, relative_name).decode("utf-8")
