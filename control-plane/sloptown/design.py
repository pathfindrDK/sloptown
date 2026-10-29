"""Design systems ("styles") and colour palettes of webapp Vibes.

The catalog is template-design/catalog.json, the same file vibe-agentd applies
from; the control plane image carries a copy at `design_catalog` (falls back to
the repo copy for tests and local runs). The studio reads it via GET /api/design.
"""

import json
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import get_settings

REPO_CATALOG = Path(__file__).resolve().parents[2] / "template-design" / "catalog.json"


@lru_cache
def catalog() -> dict[str, Any]:
    path = Path(get_settings().design_catalog)
    return json.loads((path if path.is_file() else REPO_CATALOG).read_text())


def style_ids() -> list[str]:
    return [s["id"] for s in catalog()["styles"]]


def palette_ids() -> list[str]:
    return [p["id"] for p in catalog()["palettes"]]


def random_palette() -> str:
    """For a Vibe started without a palette: a random one, fixed from then on."""
    return secrets.choice(palette_ids())
