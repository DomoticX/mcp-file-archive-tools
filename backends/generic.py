"""Generic, format-agnostic tools that dispatch to the right backend by extension."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from backends.ace import ACE_EXE, ace_extract_archive
from backends.arj import ARJ_EXE, arj_extract_archive
from backends.cab import cab_extract_archive
from backends.lha import LHA_EXE, lha_extract_archive
from backends.rar import RAR_EXE, UNRAR_EXE, extract_archive
from backends.sevenzip import _SEVENZIP_TYPE_BY_EXT, SEVENZIP_EXE, sevenzip_extract_archive
from backends.uharc import UHARC_EXE, UHARC_SFX_STUB, uharc_extract_archive
from backends.zoo import ZOO_EXE, zoo_extract_archive
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
def backend_status() -> dict[str, Any]:
    """Check which backend executables are actually present and usable on this machine.

    Unlike list_supported_formats() (which only reflects what the code
    knows how to drive), this checks the real filesystem/PATH: whether each
    required .exe actually exists where the corresponding backend expects
    it. Useful for an agent to check readiness before attempting an
    operation, and to get an exact path to point to in a bin/*/README.md if
    something is missing.
    """

    def _exe(path) -> dict[str, Any]:
        return {"path": str(path), "found": path.is_file()}

    def _on_path(name: str) -> dict[str, Any]:
        # subprocess.run() on Windows resolves a bare "name.exe" via
        # CreateProcess's native search order, which checks System32
        # *before* the PATH environment variable. shutil.which() doesn't
        # replicate that - it just walks PATH in order - so in a shell
        # whose PATH happens to list e.g. Git's coreutils "expand.exe"
        # before System32, it reports the wrong tool as "the one that will
        # run". Check the canonical System32 copy first since that's what
        # actually gets invoked regardless of PATH content, and only fall
        # back to shutil.which() (a real PATH search) if it's absent.
        system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / name
        if system32.is_file():
            return {"path": str(system32), "found": True}
        found = shutil.which(name)
        return {"path": found or name, "found": found is not None}

    backends: dict[str, dict[str, Any]] = {
        "rar": {"Rar.exe": _exe(RAR_EXE), "UnRAR.exe": _exe(UNRAR_EXE)},
        "arj": {"arj.exe": _exe(ARJ_EXE)},
        "sevenzip": {"7za.exe": _exe(SEVENZIP_EXE)},
        "lha": {"lha.exe": _exe(LHA_EXE)},
        "uharc": {
            "UHARC.EXE": _exe(UHARC_EXE),
            "UHARCSFX.EXE (optional, for uharc_convert_to_sfx)": _exe(UHARC_SFX_STUB),
        },
        "cab": {"makecab.exe": _on_path("makecab.exe"), "expand.exe": _on_path("expand.exe")},
        "ace": {"acefile.exe": _exe(ACE_EXE)},
        "zoo": {"unzoo.exe": _exe(ZOO_EXE)},
    }

    result: dict[str, Any] = {}
    for name, executables in backends.items():
        required = {k: v for k, v in executables.items() if "optional" not in k}
        result[name] = {"ready": all(v["found"] for v in required.values()), "executables": executables}
    return result


@mcp.tool()
def extract_any_archive(
    archive_path: str, destination: str | None = None, password: str | None = None
) -> dict[str, Any]:
    """Extract any supported archive format, dispatching to the right backend by file extension.

    Currently .rar, .arj, .lzh/.lha, .uha, .cab, .ace, .zoo and the 7-Zip
    formats (.7z/.zip/.tar/.gz/.xz) are implemented; other known extensions
    raise a clear "not implemented yet" error naming the tool that will be
    used once support is added (see list_supported_formats).

    Args:
        archive_path: Path to the archive file.
        destination: Output directory. Defaults to a new folder named after
            the archive, next to it.
        password: Optional password for encrypted archives. Ignored for
            .lzh/.lha, .cab and .zoo, which don't support passwords via
            this tool.
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
    if ext == ".zoo":
        return zoo_extract_archive(archive_path, destination=destination)
    return extract_archive(archive_path, destination=destination, password=password)
