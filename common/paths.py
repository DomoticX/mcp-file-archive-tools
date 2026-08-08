"""Filesystem path helpers shared by every backend."""

from __future__ import annotations

import os
from pathlib import Path


def _require_file(path_str: str, label: str = "Archive") -> Path:
    path = Path(path_str).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    return path


def _prepare_sources(sources: list[str]) -> tuple[Path, list[str]]:
    if not sources:
        raise ValueError("At least one source file or directory is required")
    abs_sources = [Path(s).resolve() for s in sources]
    for p in abs_sources:
        if not p.exists():
            raise FileNotFoundError(f"Source path not found: {p}")
    common = Path(os.path.commonpath([str(p.parent) for p in abs_sources]))
    relative = [str(p.relative_to(common)) for p in abs_sources]
    return common, relative


def _normalize_archive_path(archive_path: str, default_suffix: str) -> Path:
    archive = Path(archive_path)
    if archive.suffix.lower() != default_suffix:
        archive = Path(str(archive) + default_suffix)
    archive = archive.resolve()
    archive.parent.mkdir(parents=True, exist_ok=True)
    return archive
