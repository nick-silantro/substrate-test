"""Shared helpers for loading overlay.yaml and resolving aliases in sys.argv."""

import os
from pathlib import Path

import yaml


def load_overlay_aliases(substrate_path: str | None = None) -> dict:
    """Return the aliases dict from overlay.yaml, or empty dict if absent/unreadable."""
    if substrate_path is None:
        substrate_path = os.environ.get("SUBSTRATE_PATH", ".")
    overlay_path = Path(substrate_path) / "_system" / "overlay.yaml"
    if not overlay_path.exists():
        return {}
    try:
        data = yaml.safe_load(overlay_path.read_text()) or {}
        return data.get("aliases", {})
    except Exception:
        return {}


def resolve_args_aliases(args: list[str], attr_aliases: dict, rel_aliases: dict) -> list[str]:
    """Replace --ALIAS_NAME with --CANONICAL_NAME for known attribute and relationship aliases.

    Attributes are checked before relationships. If the same alias target exists in both
    namespaces, the attribute mapping wins — this is intentional because attribute aliases
    are more common and the namespaces are independent by design.
    """
    attr_reverse = {v: k for k, v in attr_aliases.items() if isinstance(v, str)}
    rel_reverse = {v: k for k, v in rel_aliases.items() if isinstance(v, str)}
    result = []
    for arg in args:
        if arg.startswith("--"):
            flag = arg[2:]
            if flag in attr_reverse:
                result.append("--" + attr_reverse[flag])
            elif flag in rel_reverse:
                result.append("--" + rel_reverse[flag])
            else:
                result.append(arg)
        else:
            result.append(arg)
    return result
