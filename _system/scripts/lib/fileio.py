"""
Atomic, locked file writes for Substrate entity files + canonical YAML emission.

All file mutations in Substrate should go through safe_write() to prevent:
  1. Crash corruption (write interrupted mid-stream leaves truncated file)
  2. Lost updates (two agents read-modify-write the same file concurrently)

All dict-to-YAML emission for meta.yaml should go through dump_entity_meta()
to guarantee a single canonical format across the codebase. PyYAML's default
`yaml.dump` emits zero-indent block sequences (`- item` at parent's indent)
which has historically mixed with the two-space-indent style emitted by
create-entity.py (`  - item`). Both are valid YAML but the inconsistency
produced a class of bugs in line-based YAML editors that only recognized
one indent style. See delete-entity.py's _is_block_seq_item docstring for
the bug history.

Usage:
    from lib.fileio import safe_write, dump_entity_meta

    # Read-modify-write (most common — entity updates, cascades):
    with safe_write(meta_path) as (content, write):
        content = update_meta_attr(content, "focus", "idle")
        write(content)

    # Create new file:
    with safe_write(meta_path, create=True) as (_, write):
        write(meta_content)

    # Dict-to-canonical-YAML (maintenance scripts that load/edit/dump):
    yaml_text = dump_entity_meta(meta_dict)

The lock is held for the entire context block, so the read and write are
atomic with respect to other safe_write() callers on the same path.

Implementation:
  - fcntl.flock (LOCK_EX) on a .lock sidecar file for mutual exclusion
  - Write to a temp file in the same directory, then os.rename() for atomicity
  - Lock file is left in place after release (harmless, avoids delete races)
"""

import os
import re
import sys
import tempfile
from contextlib import contextmanager

import yaml

_IS_WINDOWS = sys.platform == "win32"

if not _IS_WINDOWS:
    import fcntl
    import signal
else:
    import threading
    _win_file_locks: dict = {}
    _win_registry_lock = threading.Lock()


# Timeout for acquiring file locks (seconds).
# If an agent holds a lock longer than this, something is wrong —
# a frozen-but-alive process shouldn't block all other writers indefinitely.
LOCK_TIMEOUT_SECONDS = 30


def _lock_timeout_handler(signum, frame):
    raise TimeoutError(f"safe_write: could not acquire lock within {LOCK_TIMEOUT_SECONDS}s")


@contextmanager
def safe_write(path, create=False):
    """Exclusive-locked, atomic file write.

    Yields (content, write_fn):
      - content: current file contents (empty string if create=True and file
        doesn't exist yet)
      - write_fn: call with new content to atomically replace the file

    Raises:
      FileNotFoundError: if path doesn't exist and create=False
      TimeoutError: if lock cannot be acquired within LOCK_TIMEOUT_SECONDS

    The write_fn may be called at most once. If the context exits without
    calling write_fn, no changes are made and the lock is released cleanly.

    Locking strategy:
      POSIX: fcntl.flock on a .lock sidecar — cross-process exclusive lock.
      Windows: threading.Lock keyed by path — in-process mutual exclusion.
        Cross-process locking on Windows requires external deps (filelock);
        single-user workspaces make this an acceptable trade-off.
    """
    if _IS_WINDOWS:
        with _win_registry_lock:
            if path not in _win_file_locks:
                _win_file_locks[path] = threading.Lock()
            lock = _win_file_locks[path]

        acquired = lock.acquire(timeout=LOCK_TIMEOUT_SECONDS)
        if not acquired:
            raise TimeoutError(f"safe_write: could not acquire lock within {LOCK_TIMEOUT_SECONDS}s")
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
            elif create:
                content = ""
            else:
                raise FileNotFoundError(f"safe_write: {path} does not exist (use create=True for new files)")

            written = False

            def write_fn(new_content):
                nonlocal written
                if written:
                    raise RuntimeError("safe_write: write_fn called more than once")
                written = True
                _atomic_write(path, new_content)

            yield content, write_fn
        finally:
            lock.release()

    else:
        lock_path = path + ".lock"
        lock_dir = os.path.dirname(lock_path)
        os.makedirs(lock_dir, exist_ok=True)

        lock_fd = open(lock_path, "w")
        try:
            # Acquire exclusive lock with timeout — a stuck process shouldn't
            # block all writers indefinitely. Crashed processes are fine (OS
            # releases flock on death); this catches the live-but-frozen case.
            prev_handler = signal.signal(signal.SIGALRM, _lock_timeout_handler)
            signal.alarm(LOCK_TIMEOUT_SECONDS)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, prev_handler)

            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
            elif create:
                content = ""
            else:
                raise FileNotFoundError(f"safe_write: {path} does not exist (use create=True for new files)")

            written = False

            def write_fn(new_content):
                nonlocal written
                if written:
                    raise RuntimeError("safe_write: write_fn called more than once")
                written = True
                _atomic_write(path, new_content)

            yield content, write_fn

        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()


def _atomic_write(path, content):
    """Write content to path atomically using temp file + rename.

    The temp file is created in the same directory as the target to
    guarantee same-filesystem rename (which is atomic on POSIX).
    """
    dir_path = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dir_path, prefix=".tmp_", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        # Clean up temp file on any error
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# --- Canonical meta.yaml emission ---
#
# Substrate's canonical YAML format for meta.yaml:
#   - Block style (no flow syntax)
#   - Block sequences indented TWO SPACES under parent mapping key:
#         relates_to:
#           - UUID
#     (NOT the PyYAML default of `- UUID` at parent's indent.)
#   - Key order preserved as-given by the caller (sort_keys=False)
#   - Unicode preserved
#   - Empty lists elided? PyYAML emits `key: []` — callers who want the key
#     dropped should remove it from the dict before dumping.
#
# Why this format:
#   create-entity.py's hand-rolled emitter uses two-space-indented lists.
#   Historically, yaml.dump()-based writers (e.g., migration scripts, agent
#   fix scripts) emitted zero-indent sequences. The mix of styles in existing
#   entity files broke line-based YAML editors (see delete-entity.py bug
#   history in its _is_block_seq_item docstring). This function ensures every
#   dict-to-YAML path produces the indented canonical form.


class _IndentedDumper(yaml.SafeDumper):
    """YAML dumper that indents block sequences under mapping keys.

    PyYAML's default behavior is `indentless=True` for sequences — sequence
    items appear at the SAME indent as the parent key. That produces:
        relates_to:
        - UUID
    We want items deeper than the key:
        relates_to:
          - UUID
    Overriding increase_indent with indentless=False achieves this.
    """
    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def _double_quote_str_representer(dumper, data):
    """Emit strings with double-quote style whenever quoting is needed.

    Canonical Substrate meta.yaml convention: plain when the scalar can be
    written bare and round-trip as a string, double-quoted otherwise. This
    matches create-entity.py's hand-rolled yaml_quote convention AND fixes
    its one under-safety (yaml_quote leaves type-ambiguous bare strings like
    "yes"/"null"/"2026-05-01" un-quoted, which round-trip as bool/None/date
    instead of str — a latent correctness bug that this representer closes).

    Rules:
      - Multi-line strings → literal block style (|). Better readable than
        PyYAML's default folded style and preserves line breaks verbatim.
      - Plain-safe scalars → plain (bare). "Plain-safe" means PyYAML's
        scalar-analysis allows plain AND PyYAML's resolver would infer
        'tag:yaml.org,2002:str' (not date/timestamp/int/bool/null).
      - Everything else → double-quoted.

    Why the resolver check: PyYAML's scalar-analysis (analyze_scalar) says
    plain is syntactically allowed for most strings, including "yes" and
    "2026-05-01". But PyYAML's emitter later overrides the style request if
    plain emission would resolve to a different type on reload — producing
    single-quoted output. Rather than letting the emitter's override fire,
    we detect the ambiguity up front and request double-quote explicitly.
    """
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    # Ask the resolver what tag PyYAML would infer for this string if emitted
    # bare. If anything other than str, plain would round-trip as a different
    # type — the string must be quoted to preserve identity.
    resolved = dumper.resolve(yaml.ScalarNode, data, (True, False))
    analysis = dumper.analyze_scalar(data)
    can_plain = (
        analysis.allow_flow_plain
        and analysis.allow_block_plain
        and resolved == "tag:yaml.org,2002:str"
    )
    if can_plain:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')


_IndentedDumper.add_representer(str, _double_quote_str_representer)


def dump_entity_meta(meta):
    """Serialize an entity meta dict to canonical meta.yaml text.

    This is the single canonical emitter for entity meta.yaml — every path
    that constructs a dict and persists it as meta.yaml routes through here.
    Output properties:
      - Two-space-indented block sequences (`  - item`) under mapping keys
      - Double-quoted strings when quoting is needed, plain otherwise
      - Block mappings for nested dicts (no Python-repr flow-style)
      - Literal (|) style for multi-line scalars
      - Trailing newline

    For hand-rolled emission that writes a single `key: value` line (regex
    replace, f-string assembly), use `quote_yaml_scalar(value)` — it applies
    the same resolver-aware quoting rules per scalar so output is consistent
    with this function's.

    Do NOT call `yaml.dump` directly on entity files. Its defaults produce
    zero-indent block sequences, which have caused silent bugs in line-based
    YAML editors (see delete-entity.py's _is_block_seq_item docstring).

    Args:
        meta: dict to serialize. Key order is preserved as-given.

    Returns:
        str: YAML text with trailing newline.
    """
    return yaml.dump(
        meta,
        Dumper=_IndentedDumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )


def quote_yaml_scalar(value):
    """Return canonical YAML-safe scalar text for `value`.

    Intended for hand-rolled emission paths — regex substitutions and
    f-string assembly that write a single `key: value` line. Guarantees
    the output matches what `dump_entity_meta` would emit for the same
    scalar, so the two paths don't drift on quoting rules.

    Examples:
        quote_yaml_scalar("plain") → 'plain'
        quote_yaml_scalar("has: colon") → '"has: colon"'
        quote_yaml_scalar("2026-04-22T12:00:00") → '"2026-04-22T12:00:00"'
        quote_yaml_scalar("yes") → '"yes"'  (would otherwise parse as bool True)
        quote_yaml_scalar(None) → ''  (empty — caller should check)
        quote_yaml_scalar(42) → '42'  (numbers emitted bare)

    Multi-line scalars (containing '\\n') are rejected — they can't be
    represented on a single line. Callers that need multi-line emission
    should use `dump_entity_meta` on a single-key dict.

    Why not call `yaml.dump(value)` directly? That would:
      - Use single quotes for type-ambiguous strings (different from the
        double-quote convention dump_entity_meta uses).
      - Add a trailing newline.
      - Emit document markers for some values.
    This helper strips those quirks and delegates to the same resolver-
    aware representer dump_entity_meta uses.
    """
    if value is None:
        return ""
    if isinstance(value, str) and "\n" in value:
        raise ValueError(
            "quote_yaml_scalar does not support multi-line strings. "
            "Use dump_entity_meta for block scalars."
        )
    # Reject non-scalar types — a list/dict/tuple would dump across multiple
    # lines and corrupt the caller's single-line `key: value` assembly (the
    # caller would get the `_: ` marker embedded in its output). Scalar types
    # (str, int, float, bool, None) produce single-line output safely.
    if not isinstance(value, (str, int, float, bool)):
        raise TypeError(
            f"quote_yaml_scalar expects a scalar (str/int/float/bool/None); "
            f"got {type(value).__name__}. Use dump_entity_meta for containers."
        )
    # Emit via the single-key-dict trick to get identical behavior to
    # dump_entity_meta's per-scalar output. The dumped form is `_: <scalar>\n`;
    # we strip the `_: ` prefix and trailing newline.
    dumped = dump_entity_meta({"_": value})
    prefix = "_: "
    if not dumped.startswith(prefix):
        # Defensive: shouldn't happen given the dict shape, but if it does
        # fall back to stripping any known-safe prefix patterns.
        return dumped.rstrip("\n")
    return dumped[len(prefix):].rstrip("\n")


def normalize_meta_yaml_text(content):
    """Surgically rewrite zero-indent list items to the indented canonical form.

    Walks the text line-by-line and rewrites `- item` under a top-level
    mapping key to `  - item`. Any continuation lines below a rewritten item
    — wrapped prose, blank paragraph breaks, or text that happens to begin
    with `-` — are shifted 2 spaces so YAML still parses the item as a
    single scalar.

    This is the minimal-diff normalizer: it only changes sequence-item
    indent and continuation indent, not any other formatting. Safe on any
    file that's already valid YAML. Files that are malformed (e.g., orphan
    bare UUIDs from the pre-fix delete-entity.py bug) should be repaired
    before calling this function.

    State machine:
      Phase A (in_seq_under_key, not yet rewriting):
        entered when we see `<key>:` at column 0. Lines that look like
        canonical items (`  - `) are kept as-is. The first zero-indent
        item (`- `) triggers Phase B.
      Phase B (rewriting_continuations):
        every indented line shifts 2 spaces. Next zero-indent item
        (`- ...`) is rewritten and Phase B continues. Blank lines are
        preserved in-place (YAML treats them as paragraph breaks within a
        block scalar, not as block terminators). Column-0 content exits
        both phases.

      Why Phase B takes precedence over the `  - ` canonical match: within
      a block whose first item was zero-indent, subsequent `  - ...` text
      is a continuation of the prior item (wrapped prose), not a new
      canonical sibling. Treating it as a continuation + shifting by 2
      preserves the scalar's content; treating it as a canonical sibling
      would silently change list cardinality.

    Why not just reparse via yaml.safe_load + dump_entity_meta? That round-
    trip would also change string quoting, line-wrap, and (in edge cases)
    preserve or reorder keys differently. For a normalization pass across
    ~1000 existing files we want a minimal diff, not a full reformat.

    Returns the normalized text. Input content is not modified.
    """
    lines = content.split("\n")
    out = []
    in_seq_under_key = False
    rewriting_continuations = False

    for line in lines:
        # A top-level mapping key (`<word>:` at column 0, nothing after the
        # colon) opens a potential sequence block. Reset both phases — any
        # continuation context from a prior block is finished.
        if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*:\s*$", line):
            in_seq_under_key = True
            rewriting_continuations = False
            out.append(line)
            continue

        if in_seq_under_key:
            # Phase B: inside continuations of a rewritten zero-indent item.
            if rewriting_continuations:
                if line.startswith("- "):
                    # Next zero-indent item in the same block — rewrite,
                    # stay in Phase B.
                    out.append("  " + line)
                    continue
                if line.strip() == "":
                    # Blank line — could be in-scalar paragraph break or block
                    # end. Preserve state; a column-0 non-blank line following
                    # it will terminate the block naturally.
                    out.append(line)
                    continue
                if line.lstrip().startswith("#"):
                    # Comment line. YAML ignores comments, so they do not
                    # terminate the block semantically. Preserve state so
                    # subsequent items continue to get rewritten.
                    out.append(line)
                    continue
                if line.startswith(" ") or line.startswith("\t"):
                    # Continuation of the current item (wrapped prose, nested
                    # content, text that happens to start with `- ` — all
                    # treated uniformly). Shift 2 spaces deeper so YAML still
                    # parses it as a continuation of the rewritten item.
                    out.append("  " + line)
                    continue
                # Column-0 non-key content — block ended.
                in_seq_under_key = False
                rewriting_continuations = False
                out.append(line)
                continue

            # Phase A: block opened, haven't seen a zero-indent item yet.
            if line.startswith("- "):
                # First zero-indent item → enter Phase B.
                out.append("  " + line)
                rewriting_continuations = True
                continue
            if line.startswith("  - "):
                # Canonical item under a canonical-styled block — keep as-is.
                out.append(line)
                continue
            if line.lstrip().startswith("#") or line.strip() == "":
                # Comment or blank line between the key and its first item —
                # doesn't terminate the block. Stay in Phase A.
                out.append(line)
                continue
            # Anything else (column-0 content, etc.) ends the block.
            in_seq_under_key = False
            out.append(line)
            continue

        out.append(line)

    return "\n".join(out)
