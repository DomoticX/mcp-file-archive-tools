"""Roadmap of archive formats and the external tool each one needs.

Used by backends/generic.py (list_supported_formats / extract_any_archive).
Each backend module is responsible for keeping its own entries' "implemented"
flag accurate.
"""

from __future__ import annotations

from typing import Any

FORMAT_REGISTRY: dict[str, dict[str, Any]] = {
    ".rar": {"tool": "Rar.exe / UnRAR.exe", "implemented": True},
    ".arj": {"tool": "arj.exe", "implemented": True},
    ".7z": {"tool": "7za.exe", "implemented": True},
    ".zip": {"tool": "7za.exe", "implemented": True},
    ".tar": {"tool": "7za.exe", "implemented": True},
    ".gz": {"tool": "7za.exe", "implemented": True},
    ".xz": {"tool": "7za.exe", "implemented": True},
    ".uha": {"tool": "uharc.exe", "implemented": False},
    ".cab": {"tool": "makecab.exe / expand.exe", "implemented": False},
    ".lzh": {"tool": "lha.exe / lhasa.exe", "implemented": False},
    ".lha": {"tool": "lha.exe / lhasa.exe", "implemented": False},
}
