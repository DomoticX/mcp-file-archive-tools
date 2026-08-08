"""Generic, format-agnostic tools that dispatch to the right backend by extension."""

from __future__ import annotations

from typing import Any

from backends.ace import ace_extract_archive
from backends.arj import arj_extract_archive
from backends.cab import cab_extract_archive
from backends.lha import lha_extract_archive
from backends.rar import extract_archive
from backends.sevenzip import _SEVENZIP_TYPE_BY_EXT, sevenzip_extract_archive
from backends.uharc import uharc_extract_archive
from common.paths import _require_file
from common.registry import FORMAT_REGISTRY
from common.server import mcp

_LHA_EXTENSIONS = {".lzh", ".lha"}


@mcp.tool()
def list_supported_formats() -> dict[str, Any]:
    """List archive formats this server knows about, and whether they're implemented yet."""
    return {
        ext: {"tool": info["tool"], "implemented": info["implemented"]}
        for ext, info in sorted(FORMAT_REGISTRY.items())
    }


@mcp.tool()
def extract_any_archive(
    archive_path: str, destination: str | None = None, password: str | None = None
) -> dict[str, Any]:
    """Extract any supported archive format, dispatching to the right backend by file extension.

    Currently .rar, .arj, .lzh/.lha, .uha, .cab, .ace and the 7-Zip formats
    (.7z/.zip/.tar/.gz/.xz) are implemented; other known extensions raise a
    clear "not implemented yet" error naming the tool that will be used
    once support is added (see list_supported_formats).

    Args:
        archive_path: Path to the archive file.
        destination: Output directory. Defaults to a new folder named after
            the archive, next to it.
        password: Optional password for encrypted archives. Ignored for
            .lzh/.lha and .cab, which don't support passwords via this tool.
    """
    archive = _require_file(archive_path)
    ext = archive.suffix.lower()
    info = FORMAT_REGISTRY.get(ext)
    if info is None:
        raise ValueError(f"Unrecognized archive extension: {ext}")
    if not info["implemented"]:
        raise NotImplementedError(
            f"{ext} archives are not implemented yet (planned via {info['tool']}). "
            "Use a format-specific tool, or check list_supported_formats()."
        )
    if ext == ".arj":
        return arj_extract_archive(archive_path, destination=destination, password=password)
    if ext in _SEVENZIP_TYPE_BY_EXT:
        return sevenzip_extract_archive(archive_path, destination=destination, password=password)
    if ext in _LHA_EXTENSIONS:
        return lha_extract_archive(archive_path, destination=destination)
    if ext == ".uha":
        return uharc_extract_archive(archive_path, destination=destination, password=password)
    if ext == ".cab":
        return cab_extract_archive(archive_path, destination=destination)
    if ext == ".ace":
        return ace_extract_archive(archive_path, destination=destination, password=password)
    return extract_archive(archive_path, destination=destination, password=password)
