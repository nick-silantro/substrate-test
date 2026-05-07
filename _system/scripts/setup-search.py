#!/usr/bin/env python3
"""
Set up semantic search for Substrate.

Creates a managed venv at _system/venv/, installs sqlite-vec and the
embedding stack (onnxruntime + tokenizers + huggingface-hub + numpy),
downloads the ONNX model, and verifies everything works.

Usage: python3 _system/scripts/setup-search.py [--skip-model]

  --skip-model   Create venv and install packages but skip model download.
                 The model is downloaded automatically on first search use.

Run once. Safe to re-run (skips already-installed packages).
"""

import argparse
import os
import sys
import subprocess
import venv

SUBSTRATE_PATH = os.environ.get("SUBSTRATE_PATH", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
VENV_PATH = os.path.join(SUBSTRATE_PATH, "_system", "venv")
MODEL_CACHE_PATH = os.path.join(os.path.expanduser("~"), ".substrate", "model-cache")
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

# Platform-aware venv paths: Windows uses Scripts/ and python.exe, Unix uses bin/ and python3
if sys.platform == "win32":
    VENV_PIP = os.path.join(VENV_PATH, "Scripts", "pip.exe")
    VENV_PYTHON = os.path.join(VENV_PATH, "Scripts", "python.exe")
else:
    VENV_PIP = os.path.join(VENV_PATH, "bin", "pip")
    VENV_PYTHON = os.path.join(VENV_PATH, "bin", "python3")

# sqlite-vec: vector extension for SQLite (always required)
# onnxruntime + tokenizers + huggingface-hub + numpy: embedding stack
# All have pre-built binary wheels for Python 3.11+ on Mac, Linux, Windows.
PACKAGES = ["sqlite-vec", "onnxruntime", "tokenizers", "huggingface-hub", "numpy"]

_search_available = True


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
    global _search_available
    search_pkgs = {"onnxruntime", "tokenizers", "huggingface-hub", "numpy"}
    for pkg in PACKAGES:
        print(f"  Installing {pkg}...")
        result = subprocess.run(
            [VENV_PIP, "install", pkg],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            if pkg in search_pkgs:
                print(f"  Warning: {pkg} install failed — semantic search will not be available.")
                print(f"  (Run 'substrate search setup' to retry after fixing the issue.)")
                _search_available = False
            else:
                print(f"  ERROR installing {pkg}:")
                print(result.stderr)
                sys.exit(1)
            continue
        if "already satisfied" in result.stdout.lower():
            print(f"  {pkg} already installed.")
        else:
            print(f"  {pkg} installed.")


def verify_imports():
    """Verify that the installed packages can be imported."""
    if not _search_available:
        return
    print("  Verifying imports...")
    result = subprocess.run(
        [VENV_PYTHON, "-c",
         "import sqlite_vec; import onnxruntime; from tokenizers import Tokenizer; import numpy; print('OK')"],
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
    """Pre-download the ONNX embedding model so first search isn't slow."""
    if not _search_available:
        return
    print("  Downloading ONNX embedding model...")
    print("  This may take a minute on first run (~50MB download).")
    # Pass cache path via env var — embedding Windows paths directly in a -c string
    # causes SyntaxError because backslashes are parsed as unicode escapes (\U...).
    result = subprocess.run(
        [VENV_PYTHON, "-c", """
import os, sys, numpy as np, onnxruntime as ort
from tokenizers import Tokenizer
from huggingface_hub import hf_hub_download

sys.path.insert(0, os.environ["SUBSTRATE_SCRIPTS_DIR"])
from embeddings import _HF_ONNX_REPO as HF_REPO

cache_dir = os.environ["SUBSTRATE_MODEL_CACHE"]
os.makedirs(cache_dir, exist_ok=True)
local_dir = os.path.join(cache_dir, HF_REPO.replace("/", "--"))
os.makedirs(local_dir, exist_ok=True)

tok_path = hf_hub_download(HF_REPO, "tokenizer.json", local_dir=local_dir)
try:
    mdl_path = hf_hub_download(HF_REPO, "model_optimized.onnx", local_dir=local_dir)
except Exception:
    mdl_path = hf_hub_download(HF_REPO, "model.onnx", local_dir=local_dir)

tok = Tokenizer.from_file(tok_path)
tok.enable_padding(pad_id=0, pad_token="[PAD]")
tok.enable_truncation(max_length=512)

sess = ort.InferenceSession(mdl_path, providers=["CPUExecutionProvider"])
inp_names = {i.name for i in sess.get_inputs()}

enc = tok.encode_batch(["test"])
ids = np.array([e.ids for e in enc], dtype=np.int64)
mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
feed = {"input_ids": ids, "attention_mask": mask}
if "token_type_ids" in inp_names:
    feed["token_type_ids"] = np.zeros_like(ids)

out = sess.run(None, feed)[0]
m = mask[:, :, None].astype(np.float32)
emb = (out * m).sum(1) / m.sum(1).clip(1e-9)
emb = emb / np.linalg.norm(emb, axis=1, keepdims=True).clip(1e-9)
print(f"OK dim={emb.shape[1]}")
"""],
        capture_output=True, text=True,
        timeout=300,  # 5 minute timeout for model download
        env={**os.environ, "SUBSTRATE_MODEL_CACHE": MODEL_CACHE_PATH, "SUBSTRATE_SCRIPTS_DIR": SCRIPTS_DIR},
    )
    if result.returncode != 0:
        print("  ERROR: Model download/test failed.")
        print(result.stderr)
        sys.exit(1)

    for line in result.stdout.strip().split('\n'):
        if line.startswith("OK"):
            print(f"  Model ready. {line[3:]}")
            break


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--skip-model", action="store_true",
                   help="Skip model download; model will be fetched on first search use")
    args = p.parse_args()

    print("Setting up semantic search for Substrate")
    print("=" * 50)

    print("\n1. Creating managed venv...")
    create_venv()

    print("\n2. Installing packages...")
    install_packages()

    print("\n3. Verifying packages...")
    verify_imports()
    verify_sqlite_vec()

    if args.skip_model:
        print("\n4. Skipping model download (will fetch on first search use).")
    else:
        print("\n4. Downloading embedding model...")
        download_model()

    print("\n" + "=" * 50)
    if _search_available:
        print("Semantic search is ready!")
    else:
        print("Search venv ready (semantic search unavailable — see warning above).")
    print(f"  Venv: {VENV_PATH}")
    if _search_available and not args.skip_model:
        print(f"  Next: python3 _system/scripts/rebuild-embeddings.py")
        print(f"  Then: python3 _system/scripts/query.py search \"your query\"")


if __name__ == "__main__":
    main()
