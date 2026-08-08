"""LHA/LZH (.lzh / .lha) backend: lha.exe (GnuWin32 port of LHa for UNIX) in bin/lha/.

This specific lha.exe build (LHa for UNIX v1.14i) has significant bugs that
shape the implementation below - and one that isn't worked around:

1. Listing ("l"/"v") only ever reports the FIRST entry in the archive, even
   when more exist (verified: adding 3 files, extracting recovers all 3,
   but "l"/"v" always print "Total 1 file"). Worked around by extracting to
   a temporary directory and enumerating the real files instead of trusting
   the listing output. The "t" (test) command's per-file progress lines are
   reliable and used for test_archive.
2. Passing multiple file arguments to "a" (add) in one call silently drops
   all but the first. Worked around by invoking lha.exe once per file for
   add/update/move.
3. "d" (delete) is destructively broken and NOT exposed as a tool: deleting
   one entry from a 2+ file archive can silently discard other, unrelated
   entries too, and deleting down to what it miscounts as "empty" removes
   the entire archive file outright (reproduced with a plain 2-file, no
   password, no subdirectory archive - not an edge case). There is no safe
   way to offer entry deletion with this binary.

There is also no safe non-interactive way to decline an overwrite: without
the force option, an existing destination file makes lha.exe loop printing
its "OverWrite ?" prompt forever instead of failing on closed/EOF stdin (it
does not respect the same non-interactive stdin=DEVNULL convention the
other backends rely on). Extraction therefore always forces overwrite.
"""

from __future__ import annotations

import base64
import re
import tempfile
from pathlib import Path
from typing import Any

from common.paths import _normalize_archive_path, _prepare_sources, _require_file
from common.process import DEFAULT_TIMEOUT, _check, _decode, _describe_exit_code, _run, _run_raw
from common.server import BASE_DIR, mcp

LHA_DIR = BASE_DIR / "bin" / "lha"
LHA_EXE = LHA_DIR / "lha.exe"

# This build only distinguishes success from failure; no finer-grained codes.
LHA_EXIT_CODES: dict[int, str] = {
    0: "Success",
    1: "Error (archive not found, corrupt archive, or another fatal error)",
}

_PRINT_HEADER_RE = re.compile(rb"^::::::::\r?\n[^\n]*\r?\n::::::::\r?\n")
_TESTED_LINE_RE = re.compile(r"^(?P<name>.+?)\t- Tested\s*$")


def _expand_files(cwd: Path, relative_sources: list[str]) -> list[str]:
    """lha's "a" doesn't recurse into directories, so expand them ourselves."""
    files: list[str] = []
    for rel in relative_sources:
        path = cwd / rel
        if path.is_dir():
            for sub in sorted(path.rglob("*")):
                if sub.is_file():
                    files.append(str(sub.relative_to(cwd)))
        else:
            files.append(rel)
    return files


# ---------------------------------------------------------------------------
# MCP tools - LHA
# ---------------------------------------------------------------------------


@mcp.tool()
def lha_list_archive(archive_path: str) -> dict[str, Any]:
    """List the contents of an LHA/LZH archive without leaving files on disk.

    Args:
        archive_path: Path to the .lzh/.lha file.

    Note: implemented via a temporary extraction rather than lha.exe's own
    listing command, which has a bug in this build (see module docstring).
    """
    archive = _require_file(archive_path)
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        args = ["xf", f"-w={tmp}", str(archive)]
        rc, out, err = _run(LHA_EXE, args, timeout=DEFAULT_TIMEOUT)
        _check(rc, out, err, "Listing archive", codes=LHA_EXIT_CODES)
        entries = []
        for p in sorted(tmp.rglob("*")):
            rel = p.relative_to(tmp)
            if p.is_dir():
                entries.append({"name": f"{rel}/", "size": 0, "is_directory": True})
            else:
                entries.append({"name": str(rel), "size": p.stat().st_size, "is_directory": False})
    return {"archive": str(archive), "entry_count": len(entries), "entries": entries}


@mcp.tool()
def lha_extract_archive(
    archive_path: str,
    destination: str | None = None,
    full_paths: bool = True,
) -> dict[str, Any]:
    """Extract an LHA/LZH archive to a destination directory (always overwrites existing files).

    Args:
        archive_path: Path to the .lzh/.lha file.
        destination: Output directory. Defaults to a new folder named after
            the archive, next to it.
        full_paths: True extracts with the stored folder structure, False
            flattens everything into one directory.

    Note: always forces overwrite - without it, this lha.exe build loops
    forever re-printing its overwrite prompt instead of failing on closed
    stdin like the other backends do, so there is no safe way to offer a
    non-overwriting mode here.
    """
    archive = _require_file(archive_path)
    dest = Path(destination).resolve() if destination else archive.parent / archive.stem
    dest.mkdir(parents=True, exist_ok=True)

    command = "xf" + ("i" if not full_paths else "")
    args = [command, f"-w={dest}", str(archive)]
    rc, out, err = _run(LHA_EXE, args, timeout=DEFAULT_TIMEOUT)
    _check(rc, out, err, "Extracting archive", codes=LHA_EXIT_CODES)

    extracted = [str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()]
    return {"destination": str(dest), "file_count": len(extracted), "extracted_files": extracted}


@mcp.tool()
def lha_add_to_archive(archive_path: str, sources: list[str]) -> dict[str, Any]:
    """Create a new LHA/LZH archive, or add files/directories to an existing one.

    Args:
        archive_path: Path to the .lzh/.lha file. Created if it doesn't
            exist, ".lzh" is appended automatically if missing.
        sources: Files and/or directories to add (directories are always
            added recursively).

    Note: invoked once per file internally to work around a multi-file bug
    in this lha.exe build (see module docstring) - large trees will be
    slower than the other backends.
    """
    archive = _normalize_archive_path(archive_path, default_suffix=".lzh")
    cwd, relative_sources = _prepare_sources(sources)
    files = _expand_files(cwd, relative_sources)
    if not files:
        raise ValueError("No files found under the given sources")

    outputs: list[str] = []
    for f in files:
        rc, out, err = _run(LHA_EXE, ["a", str(archive), f], cwd=cwd, timeout=DEFAULT_TIMEOUT)
        _check(rc, out, err, f"Adding '{f}' to archive", codes=LHA_EXIT_CODES)
        outputs.append(out.strip())

    return {"archive": str(archive), "added_files": files, "output": "\n".join(outputs)}


@mcp.tool()
def lha_update_archive(archive_path: str, sources: list[str]) -> dict[str, Any]:
    """Update files in an existing LHA/LZH archive (refresh changed files, add new ones).

    Args:
        archive_path: Path to the .lzh/.lha file.
        sources: Files and/or directories to update/add (directories are
            always processed recursively).
    """
    archive = _require_file(archive_path)
    cwd, relative_sources = _prepare_sources(sources)
    files = _expand_files(cwd, relative_sources)
    if not files:
        raise ValueError("No files found under the given sources")

    outputs: list[str] = []
    for f in files:
        rc, out, err = _run(LHA_EXE, ["u", str(archive), f], cwd=cwd, timeout=DEFAULT_TIMEOUT)
        _check(rc, out, err, f"Updating '{f}' in archive", codes=LHA_EXIT_CODES)
        outputs.append(out.strip())

    return {"archive": str(archive), "updated_files": files, "output": "\n".join(outputs)}


@mcp.tool()
def lha_move_to_archive(archive_path: str, sources: list[str]) -> dict[str, Any]:
    """Move files/directories into an LHA/LZH archive.

    WARNING: this deletes the original source files/directories on disk
    after they have been successfully added to the archive. Prefer
    lha_add_to_archive unless you specifically want the originals removed.

    Args:
        archive_path: Path to the .lzh/.lha file.
        sources: Files and/or directories to move into the archive.
    """
    archive = _normalize_archive_path(archive_path, default_suffix=".lzh")
    cwd, relative_sources = _prepare_sources(sources)
    files = _expand_files(cwd, relative_sources)
    if not files:
        raise ValueError("No files found under the given sources")

    outputs: list[str] = []
    for f in files:
        rc, out, err = _run(LHA_EXE, ["m", str(archive), f], cwd=cwd, timeout=DEFAULT_TIMEOUT)
        _check(rc, out, err, f"Moving '{f}' into archive", codes=LHA_EXIT_CODES)
        outputs.append(out.strip())

    return {"archive": str(archive), "moved_files": files, "output": "\n".join(outputs)}


@mcp.tool()
def lha_test_archive(archive_path: str) -> dict[str, Any]:
    """Test an LHA/LZH archive's integrity without extracting it to disk.

    Args:
        archive_path: Path to the .lzh/.lha file.
    """
    archive = _require_file(archive_path)
    args = ["t", str(archive)]
    rc, out, err = _run(LHA_EXE, args, timeout=DEFAULT_TIMEOUT)
    tested_files = [m.group("name") for line in out.splitlines() if (m := _TESTED_LINE_RE.match(line))]
    return {
        "archive": str(archive),
        "ok": rc == 0,
        "exit_code": rc,
        "message": _describe_exit_code(rc, LHA_EXIT_CODES),
        "tested_files": tested_files,
        "output": (out or err).strip(),
    }


@mcp.tool()
def lha_print_file_from_archive(archive_path: str, file_path: str) -> dict[str, Any]:
    """Print a single file's contents from inside an LHA/LZH archive without extracting it to disk.

    Args:
        archive_path: Path to the .lzh/.lha file.
        file_path: Entry path as stored in the archive (see lha_list_archive).

    Returns text content directly, or base64 for files that aren't valid UTF-8 text.
    """
    archive = _require_file(archive_path)
    args = ["p", str(archive), file_path]
    rc, out_bytes, err_bytes = _run_raw(LHA_EXE, args, timeout=DEFAULT_TIMEOUT)
    if rc != 0:
        raise RuntimeError(
            f"Printing file failed: {_describe_exit_code(rc, LHA_EXIT_CODES)} (exit {rc}).\n{_decode(err_bytes)}"
        )
    # lha prefixes the actual bytes with a "::::::::\n<name>\n::::::::\n" banner.
    content_bytes = _PRINT_HEADER_RE.sub(b"", out_bytes, count=1)
    try:
        return {
            "archive": str(archive),
            "file": file_path,
            "encoding": "utf-8",
            "content": content_bytes.decode("utf-8"),
        }
    except UnicodeDecodeError:
        return {
            "archive": str(archive),
            "file": file_path,
            "encoding": "base64",
            "content": base64.b64encode(content_bytes).decode("ascii"),
        }


@mcp.tool()
def lha_recover_archive(archive_path: str, destination: str | None = None) -> dict[str, Any]:
    """Attempt to salvage files from a damaged LHA/LZH archive.

    Uses lha.exe's broken-archive extraction mode to recover whatever it
    can into a destination directory (there is no RAR-style "rebuild into a
    new archive" repair for this format here).

    Args:
        archive_path: Path to the .lzh/.lha file.
        destination: Output directory for recovered files. Defaults to a
            new folder next to the archive.
    """
    archive = _require_file(archive_path)
    dest = Path(destination).resolve() if destination else archive.parent / f"{archive.stem}_recovered"
    dest.mkdir(parents=True, exist_ok=True)
    args = ["xf", "--extract-broken-archive", f"-w={dest}", str(archive)]
    rc, out, err = _run(LHA_EXE, args, timeout=DEFAULT_TIMEOUT)
    recovered = [str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()]
    return {
        "original": str(archive),
        "destination": str(dest),
        "ok": rc == 0,
        "exit_code": rc,
        "message": _describe_exit_code(rc, LHA_EXIT_CODES),
        "recovered_files": recovered,
        "output": (out or err).strip(),
    }
