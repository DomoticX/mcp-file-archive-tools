"""ARJ (.arj) backend: arj.exe in bin/arj/."""

from __future__ import annotations

import base64
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from common.paths import _normalize_archive_path, _prepare_sources, _require_file
from common.process import DEFAULT_TIMEOUT, _check, _describe_exit_code, _run
from common.server import BASE_DIR, mcp

ARJ_DIR = BASE_DIR / "bin" / "arj"
ARJ_EXE = ARJ_DIR / "arj.exe"

# Known ARJ32 process exit codes (source: FreeDOS/ARJ user manual).
ARJ_EXIT_CODES: dict[int, str] = {
    0: "Success",
    1: "Warning - files not found, or a prompt was answered negatively",
    2: "Fatal error",
    3: "CRC error (header or file CRC error, or bad password)",
    4: "ARJ-SECURITY error, or attempt to update an ARJ-SECURED archive",
    5: "Disk full or write error",
    6: "Cannot open archive or file",
    7: "Simple user error (bad parameters)",
    8: "Not enough memory",
    9: "Not an ARJ archive",
    10: "XMS memory error (read or write)",
    11: "User control break",
    12: "Too many chapters (over 250)",
}

# ---------------------------------------------------------------------------
# ARJ listing parser (verbose "v" listing: unlike RAR, ARJ's plain "l"
# listing only shows base filenames, so full paths require "v", which
# prints each entry as a "NNN) path" header line followed by a stats line)
# ---------------------------------------------------------------------------

_DASH_LINE_RE = re.compile(r"^[-\s]{5,}$")
_ARJ_ENTRY_HEADER_RE = re.compile(r"^\d+\)\s+(?P<name>.+?)\s*$")
_ARJ_STAT_LINE_RE = re.compile(
    r"^\s*\d+\s+\S+\s+(?P<size>\d+)\s+(?P<compressed>\d+)\s+(?P<ratio>[\d.]+)\s+"
    r"(?P<date>\d{2}-\d{2}-\d{2})\s+(?P<time>\d{2}:\d{2}:\d{2})\s+(?P<crc>[0-9A-Fa-f]{8})\s+(?P<attrs>.+?)\s*$"
)


def _parse_arj_verbose_list(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    dash_indices = [i for i, line in enumerate(lines) if _DASH_LINE_RE.match(line)]
    if len(dash_indices) < 2:
        return []
    entries: list[dict[str, Any]] = []
    pending_name: str | None = None
    for line in lines[dash_indices[0] + 1 : dash_indices[1]]:
        header_match = _ARJ_ENTRY_HEADER_RE.match(line)
        if header_match:
            pending_name = header_match.group("name")
            continue
        stat_match = _ARJ_STAT_LINE_RE.match(line)
        if stat_match and pending_name is not None:
            attrs = stat_match.group("attrs")
            # attrs looks like "A--W B+0" (file) or "---W D+0" (directory);
            # the digits after the type letter grow (e.g. "B+11") when the
            # entry is garbled (password protected).
            tokens = attrs.split()
            type_token = tokens[1] if len(tokens) >= 2 else ""
            flags_match = re.search(r"[+-](\d+)$", type_token)
            entries.append(
                {
                    "name": pending_name,
                    "size": int(stat_match.group("size")),
                    "compressed_size": int(stat_match.group("compressed")),
                    "date": stat_match.group("date"),
                    "time": stat_match.group("time"),
                    "crc32": stat_match.group("crc"),
                    "attributes": attrs,
                    "is_directory": type_token.startswith("D"),
                    "encrypted": bool(flags_match) and flags_match.group(1) != "0",
                }
            )
            pending_name = None
    return entries


def _arj_password_args(password: str | None) -> list[str]:
    return [f"-g{password}"] if password else []


# ---------------------------------------------------------------------------
# MCP tools - ARJ
# ---------------------------------------------------------------------------


@mcp.tool()
def arj_list_archive(archive_path: str) -> dict[str, Any]:
    """List the contents of an ARJ archive without extracting it.

    Args:
        archive_path: Path to the .arj file.
    """
    archive = _require_file(archive_path)
    args = ["v", "-y", str(archive)]
    rc, out, err = _run(ARJ_EXE, args, timeout=DEFAULT_TIMEOUT)
    _check(rc, out, err, "Listing archive", codes=ARJ_EXIT_CODES)
    entries = _parse_arj_verbose_list(out)
    return {"archive": str(archive), "entry_count": len(entries), "entries": entries}


@mcp.tool()
def arj_extract_archive(
    archive_path: str,
    destination: str | None = None,
    files: list[str] | None = None,
    full_paths: bool = True,
    password: str | None = None,
) -> dict[str, Any]:
    """Extract an ARJ archive to a destination directory (always overwrites existing files).

    Args:
        archive_path: Path to the .arj file.
        destination: Output directory. Defaults to a new folder named after
            the archive, next to it.
        files: Optional list of specific entries (paths as stored in the
            archive, wildcards allowed) to extract. Omit to extract everything.
        full_paths: True extracts with the stored folder structure (x),
            False flattens everything into one directory (e).
        password: Optional password for encrypted archives.
    """
    archive = _require_file(archive_path)
    dest = Path(destination).resolve() if destination else archive.parent / archive.stem
    dest.mkdir(parents=True, exist_ok=True)

    args = ["x" if full_paths else "e", "-y", "-r", f"-ht{dest}", *_arj_password_args(password), str(archive)]
    if files:
        args.extend(files)

    rc, out, err = _run(ARJ_EXE, args, timeout=DEFAULT_TIMEOUT)
    _check(rc, out, err, "Extracting archive", codes=ARJ_EXIT_CODES)

    extracted = [str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()]
    return {"destination": str(dest), "file_count": len(extracted), "extracted_files": extracted}


@mcp.tool()
def arj_add_to_archive(
    archive_path: str,
    sources: list[str],
    recursive: bool = True,
    compression_level: int = 1,
    password: str | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    """Create a new ARJ archive, or add files/directories to an existing one.

    Args:
        archive_path: Path to the .arj file. Created if it doesn't exist,
            ".arj" is appended automatically if missing.
        sources: Files and/or directories to add.
        recursive: Recurse into subdirectories.
        compression_level: 0 (store, no compression) .. 4 (fastest/least
            compression). 1 is ARJ's own default ("good compression").
        password: Optional password to encrypt ("garble") the added files.
        comment: Optional archive comment to store alongside the files.
    """
    if not 0 <= compression_level <= 4:
        raise ValueError("compression_level must be between 0 and 4")

    archive = _normalize_archive_path(archive_path, default_suffix=".arj")
    cwd, relative_sources = _prepare_sources(sources)

    args = ["a", "-y", f"-m{compression_level}", "-r" if recursive else "-r-", *_arj_password_args(password)]

    comment_file: Path | None = None
    if comment:
        fd, comment_file_str = tempfile.mkstemp(suffix=".txt", text=True)
        comment_file = Path(comment_file_str)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(comment if comment.endswith("\n") else comment + "\n")
        args.append(f"-z{comment_file}")

    args.append(str(archive))
    args.extend(relative_sources)

    try:
        rc, out, err = _run(ARJ_EXE, args, cwd=cwd, timeout=DEFAULT_TIMEOUT)
    finally:
        if comment_file and comment_file.exists():
            comment_file.unlink()

    _check(rc, out, err, "Adding files to archive", codes=ARJ_EXIT_CODES)
    return {"archive": str(archive), "added_sources": relative_sources, "output": out.strip()}


@mcp.tool()
def arj_update_archive(archive_path: str, sources: list[str], recursive: bool = True) -> dict[str, Any]:
    """Update files in an existing ARJ archive (refresh changed files, add new ones).

    Args:
        archive_path: Path to the .arj file.
        sources: Files and/or directories to update/add.
        recursive: Recurse into subdirectories.
    """
    archive = _require_file(archive_path)
    cwd, relative_sources = _prepare_sources(sources)
    args = ["u", "-y", "-r" if recursive else "-r-", str(archive), *relative_sources]
    rc, out, err = _run(ARJ_EXE, args, cwd=cwd, timeout=DEFAULT_TIMEOUT)
    _check(rc, out, err, "Updating archive", codes=ARJ_EXIT_CODES)
    return {"archive": str(archive), "updated_sources": relative_sources, "output": out.strip()}


@mcp.tool()
def arj_move_to_archive(
    archive_path: str, sources: list[str], recursive: bool = True, compression_level: int = 1
) -> dict[str, Any]:
    """Move files/directories into an ARJ archive.

    WARNING: this deletes the original source files/directories on disk
    after they have been successfully added to the archive. Prefer
    arj_add_to_archive unless you specifically want the originals removed.

    Args:
        archive_path: Path to the .arj file.
        sources: Files and/or directories to move into the archive.
        recursive: Recurse into subdirectories.
        compression_level: 0 (store) .. 4 (fastest/least compression).
    """
    if not 0 <= compression_level <= 4:
        raise ValueError("compression_level must be between 0 and 4")
    archive = _normalize_archive_path(archive_path, default_suffix=".arj")
    cwd, relative_sources = _prepare_sources(sources)
    args = ["m", "-y", f"-m{compression_level}", "-r" if recursive else "-r-", str(archive), *relative_sources]
    rc, out, err = _run(ARJ_EXE, args, cwd=cwd, timeout=DEFAULT_TIMEOUT)
    _check(rc, out, err, "Moving files into archive", codes=ARJ_EXIT_CODES)
    return {"archive": str(archive), "moved_sources": relative_sources, "output": out.strip()}


@mcp.tool()
def arj_delete_from_archive(archive_path: str, files: list[str]) -> dict[str, Any]:
    """Delete entries from an ARJ archive.

    Args:
        archive_path: Path to the .arj file.
        files: Entry paths as stored in the archive (see arj_list_archive).
    """
    archive = _require_file(archive_path)
    if not files:
        raise ValueError("At least one file to delete is required")
    args = ["d", "-y", str(archive), *files]
    rc, out, err = _run(ARJ_EXE, args, timeout=DEFAULT_TIMEOUT)
    _check(rc, out, err, "Deleting from archive", codes=ARJ_EXIT_CODES)
    return {"archive": str(archive), "deleted": files, "output": out.strip()}


@mcp.tool()
def arj_rename_in_archive(archive_path: str, renames: dict[str, str]) -> dict[str, Any]:
    """Rename entries inside an ARJ archive.

    Args:
        archive_path: Path to the .arj file.
        renames: Mapping of {old_entry_path: new_entry_path} using paths as
            stored in the archive (see arj_list_archive). Note: the new
            path fully replaces the old one (it isn't merged with the old
            directory prefix), so include the full desired path.
    """
    archive = _require_file(archive_path)
    if not renames:
        raise ValueError("At least one rename pair is required")
    renamed: list[dict[str, str]] = []
    # ARJ's "n" command is interactive per file, so each pair is run as its
    # own invocation with the new name fed over stdin rather than batching.
    for old, new in renames.items():
        args = ["n", "-y", str(archive), old]
        rc, out, err = _run(ARJ_EXE, args, timeout=DEFAULT_TIMEOUT, input_data=f"{new}\n".encode("utf-8"))
        _check(rc, out, err, f"Renaming '{old}' to '{new}'", codes=ARJ_EXIT_CODES)
        renamed.append({"old": old, "new": new})
    return {"archive": str(archive), "renamed": renamed}


@mcp.tool()
def arj_test_archive(archive_path: str, password: str | None = None) -> dict[str, Any]:
    """Test an ARJ archive's integrity without extracting it to disk.

    Args:
        archive_path: Path to the .arj file.
        password: Optional password for encrypted archives.
    """
    archive = _require_file(archive_path)
    args = ["t", "-y", *_arj_password_args(password), str(archive)]
    rc, out, err = _run(ARJ_EXE, args, timeout=DEFAULT_TIMEOUT)
    return {
        "archive": str(archive),
        "ok": rc == 0,
        "exit_code": rc,
        "message": _describe_exit_code(rc, ARJ_EXIT_CODES),
        "output": (out or err).strip(),
    }


@mcp.tool()
def arj_set_archive_comment(archive_path: str, comment: str) -> dict[str, Any]:
    """Set (or replace) the comment stored inside an ARJ archive.

    Args:
        archive_path: Path to the .arj file.
        comment: Comment text to store.
    """
    archive = _require_file(archive_path)
    fd, comment_file_str = tempfile.mkstemp(suffix=".txt", text=True)
    comment_file = Path(comment_file_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(comment if comment.endswith("\n") else comment + "\n")
        args = ["c", "-y", f"-z{comment_file}", str(archive)]
        rc, out, err = _run(ARJ_EXE, args, timeout=DEFAULT_TIMEOUT)
    finally:
        if comment_file.exists():
            comment_file.unlink()
    _check(rc, out, err, "Setting archive comment", codes=ARJ_EXIT_CODES)
    return {"archive": str(archive), "comment": comment}


@mcp.tool()
def arj_get_archive_comment(archive_path: str) -> dict[str, Any]:
    """Read the comment stored inside an ARJ archive, if any.

    Args:
        archive_path: Path to the .arj file.
    """
    archive = _require_file(archive_path)
    args = ["l", "-y", str(archive)]
    rc, out, err = _run(ARJ_EXE, args, timeout=DEFAULT_TIMEOUT)
    _check(rc, out, err, "Reading archive comment", codes=ARJ_EXIT_CODES)
    lines = out.splitlines()
    created_idx = next((i for i, l in enumerate(lines) if l.startswith("Archive created:")), None)
    header_idx = next((i for i, l in enumerate(lines) if l.startswith("Filename")), None)
    comment = None
    if created_idx is not None and header_idx is not None and header_idx > created_idx + 1:
        comment_lines = [l for l in lines[created_idx + 1 : header_idx] if l.strip()]
        if comment_lines:
            comment = "\n".join(comment_lines)
    return {"archive": str(archive), "comment": comment}


@mcp.tool()
def arj_convert_to_sfx(archive_path: str, sfx_name: str | None = None) -> dict[str, Any]:
    """Convert an existing ARJ archive into a self-extracting executable (.exe).

    Args:
        archive_path: Path to the .arj file.
        sfx_name: Optional output name (without extension). Defaults to the
            archive's own name.
    """
    archive = _require_file(archive_path)
    args = ["y", "-y", "-je", str(archive)]
    rc, out, err = _run(ARJ_EXE, args, cwd=archive.parent, timeout=DEFAULT_TIMEOUT)
    _check(rc, out, err, "Converting archive to SFX", codes=ARJ_EXIT_CODES)
    produced = archive.with_suffix(".exe")
    final_path = produced
    if sfx_name:
        final_path = archive.parent / (sfx_name if sfx_name.lower().endswith(".exe") else f"{sfx_name}.exe")
        if produced.exists() and produced != final_path:
            produced.replace(final_path)
    return {"archive": str(archive), "sfx": str(final_path) if final_path.exists() else None, "output": out.strip()}


@mcp.tool()
def arj_garble_archive(archive_path: str, password: str) -> dict[str, Any]:
    """Encrypt ("garble") an already-existing ARJ archive's contents in place.

    Unlike RAR, ARJ can add password protection to an archive after the
    fact without needing the original source files again.

    Args:
        archive_path: Path to the .arj file.
        password: Password to protect the archive contents with.
    """
    archive = _require_file(archive_path)
    if not password:
        raise ValueError("password is required")
    args = ["g", "-y", f"-g{password}", str(archive)]
    rc, out, err = _run(ARJ_EXE, args, timeout=DEFAULT_TIMEOUT)
    _check(rc, out, err, "Garbling (encrypting) archive", codes=ARJ_EXIT_CODES)
    return {"archive": str(archive), "output": out.strip()}


@mcp.tool()
def arj_search_archive(
    archive_path: str, search_string: str, password: str | None = None, ignore_case: bool = True
) -> dict[str, Any]:
    """Search for a text string inside the files contained in an ARJ archive.

    Args:
        archive_path: Path to the .arj file.
        search_string: Text to search for.
        password: Optional password for encrypted archives.
        ignore_case: Whether the search should be case-insensitive.

    Note: ARJ's search is an interactive, multi-step prompt under the hood;
    this automates it for a single search string and returns the raw ARJ
    output text (not parsed into structured matches) since results aren't
    locale-sensitive but are still free-form.
    """
    archive = _require_file(archive_path)
    args = ["w", "-y", *_arj_password_args(password), str(archive)]
    stdin_text = "\n".join(["y" if ignore_case else "n", "0", search_string, ""]) + "\n"
    rc, out, err = _run(ARJ_EXE, args, timeout=DEFAULT_TIMEOUT, input_data=stdin_text.encode("utf-8"))
    return {
        "archive": str(archive),
        "query": search_string,
        "exit_code": rc,
        "message": _describe_exit_code(rc, ARJ_EXIT_CODES),
        "raw_output": (out or err).strip(),
    }


@mcp.tool()
def arj_print_file_from_archive(
    archive_path: str, file_path: str, password: str | None = None
) -> dict[str, Any]:
    """Print a single file's contents from inside an ARJ archive without leaving it on disk afterwards.

    Args:
        archive_path: Path to the .arj file.
        file_path: Entry path as stored in the archive (see arj_list_archive).
        password: Optional password for encrypted archives.

    Returns text content directly, or base64 for files that aren't valid
    UTF-8 text. Implemented via a temporary extraction (ARJ's own "print to
    stdout" command interleaves progress text with the file bytes on the
    same stream, which isn't safely separable).
    """
    archive = _require_file(archive_path)
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        args = ["e", "-y", f"-ht{tmp}", *_arj_password_args(password), str(archive), file_path]
        rc, out, err = _run(ARJ_EXE, args, timeout=DEFAULT_TIMEOUT)
        _check(rc, out, err, "Printing file from archive", codes=ARJ_EXIT_CODES)
        extracted = tmp / Path(file_path).name
        if not extracted.is_file():
            raise FileNotFoundError(f"Entry not found in archive: {file_path}")
        data = extracted.read_bytes()
    try:
        return {"archive": str(archive), "file": file_path, "encoding": "utf-8", "content": data.decode("utf-8")}
    except UnicodeDecodeError:
        return {
            "archive": str(archive),
            "file": file_path,
            "encoding": "base64",
            "content": base64.b64encode(data).decode("ascii"),
        }


@mcp.tool()
def arj_recover_archive(archive_path: str, destination: str | None = None) -> dict[str, Any]:
    """Attempt to salvage files from a damaged ARJ archive.

    Unlike RAR's repair (which rebuilds a new archive file), ARJ's recovery
    extracts whatever it can read into a destination directory using its
    "badly broken archive" recovery mode.

    Args:
        archive_path: Path to the .arj file.
        destination: Output directory for recovered files. Defaults to a
            new folder next to the archive.
    """
    archive = _require_file(archive_path)
    dest = Path(destination).resolve() if destination else archive.parent / f"{archive.stem}_recovered"
    dest.mkdir(parents=True, exist_ok=True)
    args = ["x", "-y", "-jr1", f"-ht{dest}", str(archive)]
    rc, out, err = _run(ARJ_EXE, args, timeout=DEFAULT_TIMEOUT)
    recovered = [str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()]
    return {
        "original": str(archive),
        "destination": str(dest),
        "ok": rc == 0,
        "exit_code": rc,
        "message": _describe_exit_code(rc, ARJ_EXIT_CODES),
        "recovered_files": recovered,
        "output": (out or err).strip(),
    }
