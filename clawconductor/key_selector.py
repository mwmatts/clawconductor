"""LiteLLM virtual key selection.

Picks the correct LiteLLM virtual key based on the chosen lane/model so
that budget and rate-limit policies are applied per-lane.
"""

from __future__ import annotations

import os
from typing import Any, Dict

import yaml


def load_keys(config_path: str = "conductor.yaml") -> Dict[str, str]:
    """Load lane -> virtual-key mapping from config."""
    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        cfg = {}
    return cfg.get("litellm_keys", {})


def resolve_model(
    tier: str,
    *,
    tiers: Dict[str, str] | None = None,
    config_path: str = "conductor.yaml",
) -> str | None:
    """Resolve a tier name to a model string.

    Reads from the ``tiers`` section of conductor.yaml, or from a
    pre-loaded tier map.
    """
    if tiers is None:
        try:
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
        except FileNotFoundError:
            cfg = {}
        tiers = cfg.get("tiers", {})
    return tiers.get(tier)


def select_key(
    lane: str,
    *,
    keys: Dict[str, str] | None = None,
    config_path: str = "conductor.yaml",
) -> str | None:
    """Return the LiteLLM virtual key for the given lane.

    Parameters
    ----------
    lane:
        ``"routing"`` or ``"escalation"``.
    keys:
        Pre-loaded key map.  If None, loads from *config_path*.

    Returns the virtual key string, or None if not configured.
    """
    if keys is None:
        keys = load_keys(config_path)
    value = keys.get(lane)
    if value and value.startswith("os.environ/"):
        return os.environ.get(value[len("os.environ/"):])
    return value
