#!/usr/bin/env python3
"""
Set up semantic search for Substrate.

Creates a managed venv at _system/venv/, installs sqlite-vec and fastembed,
downloads the embedding model, and verifies everything works.

Usage: python3 _system/scripts/setup-search.py

Run once. Safe to re-run (skips already-installed packages).
"""

import os
import sys
import subprocess
import venv

SUBSTRATE_PATH = os.environ.get("SUBSTRATE_PATH", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
VENV_PATH = os.path.join(SUBSTRATE_PATH, "_system", "venv")
MODEL_CACHE_PATH = os.path.join(os.path.expanduser("~"), ".substrate", "model-cache")

# Platform-aware venv paths: Windows uses Scripts/ and python.exe, Unix uses bin/ and python3
if sys.platform == "win32":
    VENV_PIP = os.path.join(VENV_PATH, "Scripts", "pip.exe")
    VENV_PYTHON = os.path.join(VENV_PATH, "Scripts", "python.exe")
else:
    VENV_PIP = os.path.join(VENV_PATH, "bin", "pip")
    VENV_PYTHON = os.path.join(VENV_PATH, "bin", "python3")

PACKAGES = ["sqlite-vec", "fastembed"]


def create_venv():
    """Create the managed venv if it doesn't exist."""
    if os.path.exists(VENV_PYTHON):
        print(f"  Venv already exists at {VENV_PATH}")
        return

    print(f"  Creating venv at {VENV_PATH}...")
    venv.create(VENV_PATH, with_pip=True)
    print(f"  Venv created.")


def install_packages():
    """Install required packages into the venv."""
    for pkg in PACKAGES:
        print(f"  Installing {pkg}...")
        result = subprocess.run(
            [VENV_PIP, "install", pkg],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"  ERROR installing {pkg}:")
            print(result.stderr)
            sys.exit(1)
        # Check if it was already installed
        if "already satisfied" in result.stdout.lower():
            print(f"  {pkg} already installed.")
        else:
            print(f"  {pkg} installed.")


def verify_imports():
    """Verify that the installed packages can be imported."""
    print("  Verifying imports...")
    result = subprocess.run(
        [VENV_PYTHON, "-c", "import sqlite_vec; from fastembed import TextEmbedding; print('OK')"],
        capture_output=True, text=True
    )
    if result.returncode != 0 or "OK" not in result.stdout:
        print("  ERROR: Package imports failed.")
        print(result.stderr)
        sys.exit(1)
    print("  Imports verified.")


def verify_sqlite_vec():
    """Verify sqlite-vec can be loaded as a SQLite extension."""
    print("  Verifying sqlite-vec extension...")
    result = subprocess.run(
        [VENV_PYTHON, "-c", """
import sqlite3
import sqlite_vec
conn = sqlite3.connect(':memory:')
conn.enable_load_extension(True)
sqlite_vec.load(conn)
conn.execute("SELECT vec_version()")
print('OK')
"""],
        capture_output=True, text=True
    )
    if result.returncode != 0 or "OK" not in result.stdout:
        print("  ERROR: sqlite-vec extension failed to load.")
        print(result.stderr)
        sys.exit(1)
    print("  sqlite-vec extension works.")


def download_model():
    """Pre-download the embedding model so first search isn't slow."""
    print("  Downloading embedding model (BAAI/bge-small-en-v1.5)...")
    print("  This may take a minute on first run (~50MB download).")
    result = subprocess.run(
        [VENV_PYTHON, "-c", f"""
import os
os.makedirs("{MODEL_CACHE_PATH}", exist_ok=True)
from fastembed import TextEmbedding
model = TextEmbedding("BAAI/bge-small-en-v1.5", cache_dir="{MODEL_CACHE_PATH}")
embeddings = list(model.embed(["test"]))
print(f"OK dim={{len(embeddings[0])}}")
"""],
        capture_output=True, text=True,
        timeout=300  # 5 minute timeout for model download
    )
    if result.returncode != 0:
        print("  ERROR: Model download/test failed.")
        print(result.stderr)
        sys.exit(1)

    # Extract dimension info
    for line in result.stdout.strip().split('\n'):
        if line.startswith("OK"):
            print(f"  Model ready. {line[3:]}")
            break


def main():
    print("Setting up semantic search for Substrate")
    print("=" * 50)

    print("\n1. Creating managed venv...")
    create_venv()

    print("\n2. Installing packages...")
    install_packages()

    print("\n3. Verifying packages...")
    verify_imports()
    verify_sqlite_vec()

    print("\n4. Downloading embedding model...")
    download_model()

    print("\n" + "=" * 50)
    print("Semantic search is ready!")
    print(f"  Venv: {VENV_PATH}")
    print(f"  Next: python3 _system/scripts/rebuild-embeddings.py")
    print(f"  Then: python3 _system/scripts/query.py search \"your query\"")


if __name__ == "__main__":
    main()
