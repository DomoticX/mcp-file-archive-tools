"""CAB (.cab) backend: the built-in Windows makecab.exe / expand.exe (usually already on PATH).

These are basic, old Microsoft tools with a much narrower feature set than
the other backends here:
    - No password/encryption support.
    - No adjustable compression level, just on/off (MSZIP) via -Compress.
    - makecab.exe only ever creates a brand new cabinet - there's no
      append/update mode, so cab_add_to_archive always (re)creates the
      archive from the given sources.
    - No native delete, rename, update, test, comment or search command.
      cab_move_to_archive is implemented by creating the archive and then
      deleting the sources from disk ourselves (safe - we control that
      deletion, unlike relying on a buggy native "move" command). There's
      no cab_test_archive beyond a full extract-and-discard integrity
      check, and no delete/rename/update/comment/search tools at all.
    - Directories aren't recursed automatically; multi-file/directory
      cabinets are built via a temporary MakeCAB "directive file" (.ddf)
      with the source tree walked in Python.
    - expand.exe's own listing (-D) only ever reports bare file names, no
      directory component - even though extraction *does* correctly
      restore any subdirectory structure baked in via the DDF.
    - When a cabinet holds exactly ONE file total, expand.exe's
      directory-destination extraction mode misnames the output after the
      .cab file itself instead of the real entry name (and drops any
      subdirectory placement). Verified independent of how the file was
      added or which -F: pattern is used - it only depends on the archive
      having a single entry. Worked around by renaming the output back
      using the name from cab_list_archive.
"""

from __future__ import annotations

import base64
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from common.paths import _prepare_sources, _require_file
from common.process import DEFAULT_TIMEOUT, _check, _decode, _describe_exit_code
from common.server import mcp

MAKECAB_EXE = Path("makecab.exe")
EXPAND_EXE = Path("expand.exe")

CAB_EXIT_CODES: dict[int, str] = {0: "Success"}


def _run_tool(exe: Path, args: list[str], cwd: Path | None = None, timeout: int = DEFAULT_TIMEOUT):
    # makecab.exe/expand.exe are resolved via PATH (no bundled bin/ folder
    # for this backend), so skip common.process's "exe must exist as a
    # file" check and let subprocess do PATH lookup itself.
    try:
        proc = subprocess.run(
            [str(exe), *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"'{exe}' not found on PATH. It ships with Windows (System32) - "
            "check your PATH if this is missing."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"'{exe} {' '.join(args)}' timed out after {timeout}s") from exc
    return proc.returncode, _decode(proc.stdout), _decode(proc.stderr)


def _normalize_cab_path(archive_path: str) -> Path:
    archive = Path(archive_path)
    if archive.suffix.lower() != ".cab":
        archive = Path(str(archive) + ".cab")
    archive = archive.resolve()
    archive.parent.mkdir(parents=True, exist_ok=True)
    return archive


_LIST_LINE_RE = re.compile(r"\.cab:\s+(?P<name>\S.*)$", re.IGNORECASE)


def _list_entry_names(archive: Path) -> list[str]:
    rc, out, err = _run_tool(EXPAND_EXE, ["-D", str(archive)])
    _check(rc, out, err, "Listing archive", codes=CAB_EXIT_CODES)
    # expand.exe echoes back whatever path we passed it as the line prefix
    # (and lowercases an absolute path's drive letter), so match generically
    # on "...cab: <name>" rather than predicting the exact prefix text.
    entries = []
    for line in out.splitlines():
        match = _LIST_LINE_RE.search(line)
        if match:
            entries.append(match.group("name").strip())
    return entries


def _expand_files_with_dest(cwd: Path, relative_sources: list[str]) -> list[tuple[Path, str]]:
    """Returns (absolute_source_path, relative_dest_dir) pairs; dest_dir is "" for root."""
    result: list[tuple[Path, str]] = []
    for rel in relative_sources:
        path = cwd / rel
        candidates = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
        for p in candidates:
            rel_to_cwd = p.relative_to(cwd)
            dest_dir = str(rel_to_cwd.parent) if str(rel_to_cwd.parent) != "." else ""
            result.append((p, dest_dir))
    return result


def _build_cabinet(archive: Path, files_with_dest: list[tuple[Path, str]], compress: bool) -> None:
    lines = [
        ".OPTION EXPLICIT",
        f".Set CabinetNameTemplate={archive.name}",
        f".Set DiskDirectory1={archive.parent}",
        ".Set Cabinet=on",
        f".Set Compress={'on' if compress else 'off'}",
    ]
    last_dest_dir: str | None = None
    for src, dest_dir in files_with_dest:
        if dest_dir != last_dest_dir:
            lines.append(f".Set DestinationDir={dest_dir}")
            last_dest_dir = dest_dir
        lines.append(f'"{src}"')

    # Run from a scratch directory: besides the .ddf itself, makecab.exe
    # unconditionally drops "setup.inf"/"setup.rpt" report files into its
    # *current working directory* (not next to the .ddf) - isolating cwd
    # here means those land somewhere disposable instead of littering
    # wherever this process happened to be started from.
    with tempfile.TemporaryDirectory() as scratch_dir:
        scratch = Path(scratch_dir)
        ddf_path = scratch / "build.ddf"
        ddf_path.write_text("\n".join(lines), encoding="utf-8")
        rc, out, err = _run_tool(MAKECAB_EXE, ["/F", str(ddf_path)], cwd=scratch)
        _check(rc, out, err, "Creating cabinet", codes=CAB_EXIT_CODES)


def _fix_single_entry_name(dest: Path, archive: Path, entries: list[str]) -> None:
    if len(entries) != 1:
        return
    misnamed = dest / archive.name
    correct = dest / entries[0]
    if misnamed.exists() and misnamed != correct:
        correct.parent.mkdir(parents=True, exist_ok=True)
        misnamed.replace(correct)


# ---------------------------------------------------------------------------
# MCP tools - CAB
# ---------------------------------------------------------------------------


@mcp.tool()
def cab_list_archive(archive_path: str) -> dict[str, Any]:
    """List the contents of a CAB archive without extracting it.

    Args:
        archive_path: Path to the .cab file.

    Note: only reports bare file names (no directory component) - that's
    all expand.exe's own listing exposes, even though extraction does
    restore any subdirectory structure the cabinet was built with.
    """
    archive = _require_file(archive_path)
    entries = _list_entry_names(archive)
    return {"archive": str(archive), "entry_count": len(entries), "entries": [{"name": n} for n in entries]}


@mcp.tool()
def cab_extract_archive(
    archive_path: str, destination: str | None = None, files: list[str] | None = None
) -> dict[str, Any]:
    """Extract a CAB archive to a destination directory (always overwrites existing files).

    Args:
        archive_path: Path to the .cab file.
        destination: Output directory. Defaults to a new folder named after
            the archive, next to it.
        files: Optional list of specific entry names (see cab_list_archive)
            to extract. Omit to extract everything.
    """
    archive = _require_file(archive_path)
    dest = Path(destination).resolve() if destination else archive.parent / archive.stem
    dest.mkdir(parents=True, exist_ok=True)

    entries = _list_entry_names(archive)
    pattern = ",".join(files) if files else "*"
    rc, out, err = _run_tool(EXPAND_EXE, [str(archive), f"-F:{pattern}", str(dest)])
    _check(rc, out, err, "Extracting archive", codes=CAB_EXIT_CODES)
    _fix_single_entry_name(dest, archive, entries)

    extracted = [str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()]
    return {"destination": str(dest), "file_count": len(extracted), "extracted_files": extracted}


@mcp.tool()
def cab_add_to_archive(archive_path: str, sources: list[str], compress: bool = True) -> dict[str, Any]:
    """Create a CAB archive from files/directories.

    makecab.exe has no append/update mode, so this always (re)creates the
    archive fresh from the given sources - if archive_path already exists,
    it's overwritten, not merged into.

    Args:
        archive_path: Path for the archive. ".cab" is appended
            automatically if missing.
        sources: Files and/or directories to add (directories are always
            added recursively).
        compress: Whether to compress (MSZIP) or just store the files.
    """
    archive = _normalize_cab_path(archive_path)
    cwd, relative_sources = _prepare_sources(sources)
    files_with_dest = _expand_files_with_dest(cwd, relative_sources)
    if not files_with_dest:
        raise ValueError("No files found under the given sources")

    _build_cabinet(archive, files_with_dest, compress)
    added = [str(src.relative_to(cwd)) for src, _ in files_with_dest]
    return {"archive": str(archive), "added_files": added}


@mcp.tool()
def cab_move_to_archive(archive_path: str, sources: list[str], compress: bool = True) -> dict[str, Any]:
    """Move files/directories into a CAB archive.

    WARNING: this deletes the original source files/directories on disk
    after they have been successfully added to the archive. Prefer
    cab_add_to_archive unless you specifically want the originals removed.
    Unlike the other backends' "move", this is done by this server (create
    then delete) rather than a native makecab move mode, since none exists.

    Args:
        archive_path: Path for the archive. ".cab" is appended
            automatically if missing.
        sources: Files and/or directories to move into the archive.
        compress: Whether to compress (MSZIP) or just store the files.
    """
    archive = _normalize_cab_path(archive_path)
    cwd, relative_sources = _prepare_sources(sources)
    files_with_dest = _expand_files_with_dest(cwd, relative_sources)
    if not files_with_dest:
        raise ValueError("No files found under the given sources")

    _build_cabinet(archive, files_with_dest, compress)
    moved = [str(src.relative_to(cwd)) for src, _ in files_with_dest]

    for src, _ in files_with_dest:
        src.unlink()
    for rel in relative_sources:
        candidate = cwd / rel
        if candidate.is_dir():
            shutil.rmtree(candidate, ignore_errors=True)

    return {"archive": str(archive), "moved_files": moved}


@mcp.tool()
def cab_test_archive(archive_path: str) -> dict[str, Any]:
    """Test a CAB archive's integrity without leaving files on disk afterwards.

    Args:
        archive_path: Path to the .cab file.

    Note: there's no dedicated "test" command for CAB via these tools, so
    this does a full extraction to a temporary directory and discards it -
    if every file decompresses without error, the archive is considered OK.
    """
    archive = _require_file(archive_path)
    with tempfile.TemporaryDirectory() as tmp_dir:
        rc, out, err = _run_tool(EXPAND_EXE, [str(archive), "-F:*", tmp_dir])
    return {
        "archive": str(archive),
        "ok": rc == 0,
        "exit_code": rc,
        "message": _describe_exit_code(rc, CAB_EXIT_CODES),
        "output": (out or err).strip(),
    }


@mcp.tool()
def cab_print_file_from_archive(archive_path: str, file_path: str) -> dict[str, Any]:
    """Print a single file's contents from inside a CAB archive without leaving it on disk afterwards.

    Args:
        archive_path: Path to the .cab file.
        file_path: Entry name as stored in the archive (see cab_list_archive).

    Returns text content directly, or base64 for files that aren't valid
    UTF-8 text. There's no native print-to-stdout command, so this is
    implemented via a temporary extraction.
    """
    archive = _require_file(archive_path)
    entries = _list_entry_names(archive)
    if file_path not in entries:
        raise FileNotFoundError(f"Entry not found in archive: {file_path}")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        rc, out, err = _run_tool(EXPAND_EXE, [str(archive), f"-F:{file_path}", str(tmp)])
        _check(rc, out, err, "Printing file from archive", codes=CAB_EXIT_CODES)
        _fix_single_entry_name(tmp, archive, entries)
        # cab_list_archive only exposes bare names (see module docstring),
        # but extraction restores any subdirectory the entry was built
        # with, so the file may not land directly under tmp - search for it.
        matches = list(tmp.rglob(file_path))
        if not matches:
            raise FileNotFoundError(f"Entry not found in archive: {file_path}")
        data = matches[0].read_bytes()
    try:
        return {"archive": str(archive), "file": file_path, "encoding": "utf-8", "content": data.decode("utf-8")}
    except UnicodeDecodeError:
        return {
            "archive": str(archive),
            "file": file_path,
            "encoding": "base64",
            "content": base64.b64encode(data).decode("ascii"),
        }
