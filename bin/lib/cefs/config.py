from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CefsConfig:
    mountpoint: Path
    image_root: Path
