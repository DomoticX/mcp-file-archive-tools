"""
MCP File Archive Tools
=======================

An MCP server that exposes archive (de)compression tools to MCP clients.

Currently implemented:
    - RAR (.rar) via the bundled Rar.exe / UnRAR.exe in bin/winrar/
    - ARJ (.arj) via the bundled arj.exe in bin/arj/
    - 7-Zip (.7z/.zip/.tar/.gz/.xz) via the bundled 7za.exe in bin/7z/

Planned (see FORMAT_REGISTRY / list_supported_formats):
    - .uha                           -> uharc.exe
    - .cab                           -> makecab.exe / expand.exe
    - .lzh / .lha                    -> lha.exe / lhasa.exe

Run:
    python mcp-file-archive-tools.py
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

# ---------------------------------------------------------------------------
# Paths / configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
WINRAR_DIR = BASE_DIR / "bin" / "winrar"
RAR_EXE = WINRAR_DIR / "Rar.exe"
UNRAR_EXE = WINRAR_DIR / "UnRAR.exe"

ARJ_DIR = BASE_DIR / "bin" / "arj"
ARJ_EXE = ARJ_DIR / "arj.exe"

SEVENZIP_DIR = BASE_DIR / "bin" / "7z"
SEVENZIP_EXE = SEVENZIP_DIR / "7za.exe"

DEFAULT_TIMEOUT = 300  # seconds

# Known RAR / UnRAR process exit codes.
RAR_EXIT_CODES: dict[int, str] = {
    0: "Success",
    1: "Non-fatal warning(s) occurred",
    2: "Fatal error",
    3: "Invalid checksum (CRC error) - archive data is damaged",
    4: "Attempt to modify a locked archive",
    5: "Write error",
    6: "File open/create error",
    7: "Wrong command line option",
    8: "Not enough memory",
    9: "File create error",
    10: "No files matching the specified mask/options were found",
    11: "Wrong password",
    12: "Read error",
    13: "Bad archive / unknown or corrupt archive format",
    255: "User break / operation cancelled",
}

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

# Known 7-Zip (7za.exe) process exit codes (official, stable across versions).
SEVENZIP_EXIT_CODES: dict[int, str] = {
    0: "Success",
    1: "Warning (non-fatal error(s) occurred)",
    2: "Fatal error",
    7: "Command line error",
    8: "Not enough memory for the operation",
    255: "User stopped the process (e.g. a password was needed but stdin was closed)",
}

# Roadmap of archive formats and the external tool each one needs.
# ".rar", ".arj" and the 7-Zip formats are wired up to an implementation today.
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

mcp = MCPServer(
    name="file-archive-tools",
    version="0.1.0",
    instructions=(
        "Tools for inspecting and manipulating archive files. RAR archives "
        "(.rar, list_archive/extract_archive/... tools), ARJ archives "
        "(.arj, arj_* tools) and 7-Zip-backed formats (.7z/.zip/.tar/.gz/.xz, "
        "sevenzip_* tools) are fully supported: list, extract, create/add, "
        "update, delete, rename, test, print-file, plus format-specific "
        "extras (RAR lock/repair/comment, ARJ garble/recover/comment, 7-Zip "
        "SFX creation). Other formats are on the roadmap; call "
        "list_supported_formats() to see current coverage."
    ),
)

# ---------------------------------------------------------------------------
# Low-level process helpers
# ---------------------------------------------------------------------------


def _decode(data: bytes) -> str:
    for encoding in ("utf-8", "cp1252", "cp850"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _run_raw(
    exe: Path,
    args: list[str],
    cwd: Path | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    input_data: bytes | None = None,
) -> tuple[int, bytes, bytes]:
    if not exe.exists():
        raise FileNotFoundError(f"Required executable not found: {exe}")
    kwargs: dict[str, Any] = {"cwd": str(cwd) if cwd else None, "capture_output": True, "timeout": timeout}
    if input_data is not None:
        kwargs["input"] = input_data
    else:
        # No stdin needed: explicitly close it so a tool that unexpectedly
        # prompts fails fast instead of hanging the MCP server.
        kwargs["stdin"] = subprocess.DEVNULL
    try:
        proc = subprocess.run([str(exe), *args], **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"'{exe.name} {' '.join(args)}' timed out after {timeout}s") from exc
    return proc.returncode, proc.stdout, proc.stderr


def _run(
    exe: Path,
    args: list[str],
    cwd: Path | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    input_data: bytes | None = None,
) -> tuple[int, str, str]:
    rc, out, err = _run_raw(exe, args, cwd=cwd, timeout=timeout, input_data=input_data)
    return rc, _decode(out), _decode(err)


def _describe_exit_code(rc: int, codes: dict[int, str] = RAR_EXIT_CODES) -> str:
    return codes.get(rc, f"Unknown exit code {rc}")


def _check(rc: int, out: str, err: str, action: str, codes: dict[int, str] = RAR_EXIT_CODES) -> None:
    if rc != 0:
        detail = (err or out).strip()
        raise RuntimeError(f"{action} failed: {_describe_exit_code(rc, codes)} (exit {rc}).\n{detail}")


def _password_args(password: str | None) -> list[str]:
    # "-p-" explicitly disables the interactive password prompt so a
    # missing/wrong password fails fast instead of hanging the server.
    return [f"-p{password}"] if password else ["-p-"]


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


def _normalize_archive_path(archive_path: str, default_suffix: str = ".rar") -> Path:
    archive = Path(archive_path)
    if archive.suffix.lower() != default_suffix:
        archive = Path(str(archive) + default_suffix)
    archive = archive.resolve()
    archive.parent.mkdir(parents=True, exist_ok=True)
    return archive


# ---------------------------------------------------------------------------
# RAR listing parser (locale independent: relies on the "----" separator
# rows rather than the localized column headers)
# ---------------------------------------------------------------------------

_DASH_LINE_RE = re.compile(r"^[-\s]{5,}$")
_LIST_ROW_RE = re.compile(
    r"^\s*\*?\s*(?P<attrs>\S+)\s+(?P<size>\d+)\s+"
    r"(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<time>\d{2}:\d{2})\s+(?P<name>.+?)\s*$"
)


def _parse_list_output(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    dash_indices = [i for i, line in enumerate(lines) if _DASH_LINE_RE.match(line)]
    if len(dash_indices) < 2:
        return []
    entries: list[dict[str, Any]] = []
    for line in lines[dash_indices[0] + 1 : dash_indices[1]]:
        match = _LIST_ROW_RE.match(line)
        if not match:
            continue
        attrs = match.group("attrs")
        entries.append(
            {
                "name": match.group("name").strip(),
                "size": int(match.group("size")),
                "date": match.group("date"),
                "time": match.group("time"),
                "attributes": attrs,
                "is_directory": "d" in attrs.lower(),
                "encrypted": line.lstrip().startswith("*"),
            }
        )
    return entries


# ---------------------------------------------------------------------------
# MCP tools - RAR
# ---------------------------------------------------------------------------


@mcp.tool()
def list_archive(archive_path: str, password: str | None = None) -> dict[str, Any]:
    """List the contents of a RAR archive without extracting it.

    Args:
        archive_path: Path to the .rar file.
        password: Optional password. Encrypted entries are still listed
            (marked with encrypted=true) even without the correct password.
    """
    archive = _require_file(archive_path)
    args = ["l", "-y", *_password_args(password), str(archive)]
    rc, out, err = _run(UNRAR_EXE, args)
    _check(rc, out, err, "Listing archive")
    entries = _parse_list_output(out)
    return {"archive": str(archive), "entry_count": len(entries), "entries": entries}


@mcp.tool()
def extract_archive(
    archive_path: str,
    destination: str | None = None,
    files: list[str] | None = None,
    full_paths: bool = True,
    overwrite: bool = True,
    password: str | None = None,
) -> dict[str, Any]:
    """Extract a RAR archive to a destination directory.

    Args:
        archive_path: Path to the .rar file.
        destination: Output directory. Defaults to a new folder named after
            the archive, next to it.
        files: Optional list of specific entries (paths as stored in the
            archive, wildcards allowed) to extract. Omit to extract everything.
        full_paths: True extracts with the stored folder structure (x),
            False flattens everything into one directory (e).
        overwrite: Whether to overwrite existing files at the destination.
        password: Optional password for encrypted archives.
    """
    archive = _require_file(archive_path)
    dest = Path(destination).resolve() if destination else archive.parent / archive.stem
    dest.mkdir(parents=True, exist_ok=True)

    args = [
        "x" if full_paths else "e",
        "-y",
        "-o+" if overwrite else "-o-",
        *_password_args(password),
        str(archive),
    ]
    if files:
        args.extend(files)
    args.append(f"{dest}{os.sep}")

    rc, out, err = _run(UNRAR_EXE, args)
    _check(rc, out, err, "Extracting archive")

    extracted = [str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()]
    return {"destination": str(dest), "file_count": len(extracted), "extracted_files": extracted}


@mcp.tool()
def add_to_archive(
    archive_path: str,
    sources: list[str],
    recursive: bool = True,
    compression_level: int = 3,
    password: str | None = None,
    encrypt_headers: bool = False,
    comment: str | None = None,
) -> dict[str, Any]:
    """Create a new RAR archive, or add files/directories to an existing one.

    Args:
        archive_path: Path to the .rar file. Created if it doesn't exist,
            ".rar" is appended automatically if missing.
        sources: Files and/or directories to add.
        recursive: Recurse into subdirectories.
        compression_level: 0 (store, no compression) .. 5 (maximum).
        password: Optional password to encrypt the added file data.
        encrypt_headers: Also encrypt file names/headers (stronger, requires
            the password to list contents too). Only used when password is set.
        comment: Optional archive comment to store alongside the files.
    """
    if not 0 <= compression_level <= 5:
        raise ValueError("compression_level must be between 0 and 5")

    archive = _normalize_archive_path(archive_path)
    cwd, relative_sources = _prepare_sources(sources)

    args = ["a", "-y", f"-m{compression_level}", "-r" if recursive else "-r-"]
    if password:
        args.append(f"-hp{password}" if encrypt_headers else f"-p{password}")

    comment_file: Path | None = None
    if comment:
        fd, comment_file_str = tempfile.mkstemp(suffix=".txt", text=True)
        comment_file = Path(comment_file_str)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(comment)
        args.append(f"-z{comment_file}")

    args.append(str(archive))
    args.extend(relative_sources)

    try:
        rc, out, err = _run(RAR_EXE, args, cwd=cwd)
    finally:
        if comment_file and comment_file.exists():
            comment_file.unlink()

    _check(rc, out, err, "Adding files to archive")
    return {"archive": str(archive), "added_sources": relative_sources, "output": out.strip()}


@mcp.tool()
def update_archive(
    archive_path: str, sources: list[str], recursive: bool = True
) -> dict[str, Any]:
    """Update files in an existing RAR archive (refresh changed files, add new ones).

    Args:
        archive_path: Path to the .rar file.
        sources: Files and/or directories to update/add.
        recursive: Recurse into subdirectories.
    """
    archive = _require_file(archive_path)
    cwd, relative_sources = _prepare_sources(sources)
    args = ["u", "-y", "-r" if recursive else "-r-", str(archive), *relative_sources]
    rc, out, err = _run(RAR_EXE, args, cwd=cwd)
    _check(rc, out, err, "Updating archive")
    return {"archive": str(archive), "updated_sources": relative_sources, "output": out.strip()}


@mcp.tool()
def move_to_archive(
    archive_path: str, sources: list[str], recursive: bool = True, compression_level: int = 3
) -> dict[str, Any]:
    """Move files/directories into a RAR archive.

    WARNING: this deletes the original source files/directories on disk
    after they have been successfully added to the archive. Prefer
    add_to_archive unless you specifically want the originals removed.

    Args:
        archive_path: Path to the .rar file.
        sources: Files and/or directories to move into the archive.
        recursive: Recurse into subdirectories.
        compression_level: 0 (store) .. 5 (maximum).
    """
    if not 0 <= compression_level <= 5:
        raise ValueError("compression_level must be between 0 and 5")
    archive = _normalize_archive_path(archive_path)
    cwd, relative_sources = _prepare_sources(sources)
    args = ["m", "-y", f"-m{compression_level}", "-r" if recursive else "-r-", str(archive), *relative_sources]
    rc, out, err = _run(RAR_EXE, args, cwd=cwd)
    _check(rc, out, err, "Moving files into archive")
    return {"archive": str(archive), "moved_sources": relative_sources, "output": out.strip()}


@mcp.tool()
def delete_from_archive(archive_path: str, files: list[str]) -> dict[str, Any]:
    """Delete entries from a RAR archive.

    Args:
        archive_path: Path to the .rar file.
        files: Entry paths as stored in the archive (see list_archive).
    """
    archive = _require_file(archive_path)
    if not files:
        raise ValueError("At least one file to delete is required")
    args = ["d", "-y", str(archive), *files]
    rc, out, err = _run(RAR_EXE, args)
    _check(rc, out, err, "Deleting from archive")
    return {"archive": str(archive), "deleted": files, "output": out.strip()}


@mcp.tool()
def rename_in_archive(archive_path: str, renames: dict[str, str]) -> dict[str, Any]:
    """Rename entries inside a RAR archive.

    Args:
        archive_path: Path to the .rar file.
        renames: Mapping of {old_entry_path: new_entry_path} using paths as
            stored in the archive (see list_archive).
    """
    archive = _require_file(archive_path)
    if not renames:
        raise ValueError("At least one rename pair is required")
    args = ["rn", "-y", str(archive)]
    for old, new in renames.items():
        args.extend([old, new])
    rc, out, err = _run(RAR_EXE, args)
    _check(rc, out, err, "Renaming archive entries")
    return {"archive": str(archive), "renamed": renames, "output": out.strip()}


@mcp.tool()
def test_archive(archive_path: str, password: str | None = None) -> dict[str, Any]:
    """Test a RAR archive's integrity without extracting it to disk.

    Args:
        archive_path: Path to the .rar file.
        password: Optional password for encrypted archives.
    """
    archive = _require_file(archive_path)
    args = ["t", "-y", *_password_args(password), str(archive)]
    rc, out, err = _run(UNRAR_EXE, args)
    return {
        "archive": str(archive),
        "ok": rc == 0,
        "exit_code": rc,
        "message": _describe_exit_code(rc),
        "output": (out or err).strip(),
    }


@mcp.tool()
def repair_archive(archive_path: str) -> dict[str, Any]:
    """Attempt to repair a damaged RAR archive.

    Produces a new file named "rebuilt.<original name>" next to the
    original archive (the original is left untouched).

    Args:
        archive_path: Path to the .rar file.
    """
    archive = _require_file(archive_path)
    args = ["r", "-y", str(archive)]
    rc, out, err = _run(RAR_EXE, args, cwd=archive.parent)
    _check(rc, out, err, "Repairing archive")
    repaired = archive.parent / f"rebuilt.{archive.name}"
    return {
        "original": str(archive),
        "repaired": str(repaired) if repaired.exists() else None,
        "output": out.strip(),
    }


@mcp.tool()
def lock_archive(archive_path: str) -> dict[str, Any]:
    """Lock a RAR archive to protect it from further modification or deletion by RAR.

    Args:
        archive_path: Path to the .rar file.
    """
    archive = _require_file(archive_path)
    args = ["k", "-y", str(archive)]
    rc, out, err = _run(RAR_EXE, args)
    _check(rc, out, err, "Locking archive")
    return {"archive": str(archive), "locked": True, "output": out.strip()}


@mcp.tool()
def set_archive_comment(archive_path: str, comment: str) -> dict[str, Any]:
    """Set (or replace) the comment stored inside a RAR archive.

    Args:
        archive_path: Path to the .rar file.
        comment: Comment text to store.
    """
    archive = _require_file(archive_path)
    fd, comment_file_str = tempfile.mkstemp(suffix=".txt", text=True)
    comment_file = Path(comment_file_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(comment)
        args = ["c", "-y", f"-z{comment_file}", str(archive)]
        rc, out, err = _run(RAR_EXE, args)
    finally:
        if comment_file.exists():
            comment_file.unlink()
    _check(rc, out, err, "Setting archive comment")
    return {"archive": str(archive), "comment": comment}


@mcp.tool()
def get_archive_comment(archive_path: str) -> dict[str, Any]:
    """Read the comment stored inside a RAR archive, if any.

    Args:
        archive_path: Path to the .rar file.
    """
    archive = _require_file(archive_path)
    fd, comment_file_str = tempfile.mkstemp(suffix=".txt", text=True)
    os.close(fd)
    comment_file = Path(comment_file_str)
    try:
        args = ["cw", "-y", str(archive), str(comment_file)]
        rc, out, err = _run(RAR_EXE, args)
        comment = comment_file.read_text(encoding="utf-8", errors="replace") if comment_file.exists() else ""
    finally:
        if comment_file.exists():
            comment_file.unlink()
    _check(rc, out, err, "Reading archive comment")
    return {"archive": str(archive), "comment": comment.strip() or None}


@mcp.tool()
def convert_to_sfx(archive_path: str, sfx_name: str | None = None) -> dict[str, Any]:
    """Convert an existing RAR archive into a self-extracting executable (.exe).

    Args:
        archive_path: Path to the .rar file.
        sfx_name: Optional output name (without extension). Defaults to the
            archive's own name.
    """
    archive = _require_file(archive_path)
    command = f"s{sfx_name}" if sfx_name else "s"
    args = [command, "-y", str(archive)]
    rc, out, err = _run(RAR_EXE, args, cwd=archive.parent)
    _check(rc, out, err, "Converting archive to SFX")
    exe_path = archive.parent / f"{sfx_name or archive.stem}.exe"
    return {"archive": str(archive), "sfx": str(exe_path) if exe_path.exists() else None, "output": out.strip()}


@mcp.tool()
def search_archive(archive_path: str, search_string: str, password: str | None = None) -> dict[str, Any]:
    """Search for a text string inside the files contained in a RAR archive.

    Args:
        archive_path: Path to the .rar file.
        search_string: Text to search for.
        password: Optional password for encrypted archives.

    Note: results are returned as raw RAR output text (the console messages
    are localized, so this is not parsed into structured matches).
    """
    archive = _require_file(archive_path)
    args = [f"i={search_string}", "-y", *_password_args(password), str(archive)]
    rc, out, err = _run(RAR_EXE, args)
    _check(rc, out, err, "Searching archive")
    return {"archive": str(archive), "query": search_string, "raw_output": out.strip()}


@mcp.tool()
def print_file_from_archive(
    archive_path: str, file_path: str, password: str | None = None
) -> dict[str, Any]:
    """Print a single file's contents from inside a RAR archive without extracting it to disk.

    Args:
        archive_path: Path to the .rar file.
        file_path: Entry path as stored in the archive (see list_archive).
        password: Optional password for encrypted archives.

    Returns text content directly, or base64 for files that aren't valid UTF-8 text.
    """
    archive = _require_file(archive_path)
    args = ["p", "-y", *_password_args(password), "-inul", str(archive), file_path]
    rc, out_bytes, err_bytes = _run_raw(UNRAR_EXE, args)
    if rc != 0:
        raise RuntimeError(f"Printing file failed: {_describe_exit_code(rc)} (exit {rc}).\n{_decode(err_bytes)}")
    try:
        return {"archive": str(archive), "file": file_path, "encoding": "utf-8", "content": out_bytes.decode("utf-8")}
    except UnicodeDecodeError:
        return {
            "archive": str(archive),
            "file": file_path,
            "encoding": "base64",
            "content": base64.b64encode(out_bytes).decode("ascii"),
        }


# ---------------------------------------------------------------------------
# ARJ listing parser (verbose "v" listing: unlike RAR, ARJ's plain "l"
# listing only shows base filenames, so full paths require "v", which
# prints each entry as a "NNN) path" header line followed by a stats line)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 7-Zip (7za.exe) helpers and listing parser
# ---------------------------------------------------------------------------

_SEVENZIP_TYPE_BY_EXT: dict[str, str] = {
    ".7z": "7z",
    ".zip": "zip",
    ".zipx": "zip",
    ".jar": "zip",
    ".tar": "tar",
    ".gz": "gzip",
    ".tgz": "gzip",
    ".xz": "xz",
    ".txz": "xz",
}
_SEVENZIP_EXT_BY_TYPE: dict[str, str] = {"7z": ".7z", "zip": ".zip", "tar": ".tar", "gzip": ".gz", "xz": ".xz"}
# gzip/xz can only ever hold a single compressed stream, unlike 7z/zip/tar.
_SEVENZIP_SINGLE_FILE_TYPES = {"gzip", "xz"}

_SEVENZIP_ROW_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<time>\d{2}:\d{2}:\d{2})\s+(?P<attrs>\S+)\s+"
    r"(?P<size>\d+)\s+(?P<compressed>\d*)\s+(?P<name>.+?)\s*$"
)


def _parse_sevenzip_list(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    dash_indices = [i for i, line in enumerate(lines) if _DASH_LINE_RE.match(line)]
    if len(dash_indices) < 2:
        return []
    entries: list[dict[str, Any]] = []
    for line in lines[dash_indices[0] + 1 : dash_indices[1]]:
        match = _SEVENZIP_ROW_RE.match(line)
        if not match:
            continue
        attrs = match.group("attrs")
        compressed = match.group("compressed")
        entries.append(
            {
                "name": match.group("name").strip(),
                "size": int(match.group("size")),
                "compressed_size": int(compressed) if compressed else None,
                "date": match.group("date"),
                "time": match.group("time"),
                "attributes": attrs,
                "is_directory": attrs.startswith("D"),
            }
        )
    return entries


def _sevenzip_password_args(password: str | None) -> list[str]:
    return [f"-p{password}"] if password else []


def _sevenzip_resolve_type(archive: Path, archive_type: str | None) -> str:
    if archive_type:
        return archive_type
    resolved = _SEVENZIP_TYPE_BY_EXT.get(archive.suffix.lower())
    if not resolved:
        raise ValueError(
            f"Cannot infer 7-Zip archive type from extension '{archive.suffix}'; pass archive_type explicitly "
            "(one of: 7z, zip, tar, gzip, xz)."
        )
    return resolved


def _sevenzip_prepare_archive_path(archive_path: str, archive_type: str | None) -> tuple[Path, str]:
    archive = Path(archive_path)
    if archive.suffix.lower() in _SEVENZIP_TYPE_BY_EXT:
        resolved_type = archive_type or _SEVENZIP_TYPE_BY_EXT[archive.suffix.lower()]
    else:
        resolved_type = archive_type or "7z"
        archive = Path(str(archive) + _SEVENZIP_EXT_BY_TYPE[resolved_type])
    archive = archive.resolve()
    archive.parent.mkdir(parents=True, exist_ok=True)
    return archive, resolved_type


# ---------------------------------------------------------------------------
# MCP tools - 7-Zip (.7z / .zip / .tar / .gz / .xz)
# ---------------------------------------------------------------------------


@mcp.tool()
def sevenzip_list_archive(archive_path: str, password: str | None = None) -> dict[str, Any]:
    """List the contents of a 7z/zip/tar/gz/xz archive without extracting it.

    Args:
        archive_path: Path to the archive file.
        password: Optional password (only meaningful for encrypted .7z/.zip).

    Note: .gz/.xz archives only ever hold a single compressed stream. If
    that stream is itself a .tar (i.e. this is really a .tar.gz/.tar.xz),
    this transparently unwraps one level and lists the tar's real files
    instead of the single "whatever.tar" entry.
    """
    archive = _require_file(archive_path)
    args = ["l", str(archive), *_sevenzip_password_args(password)]
    rc, out, err = _run(SEVENZIP_EXE, args, timeout=DEFAULT_TIMEOUT)
    _check(rc, out, err, "Listing archive", codes=SEVENZIP_EXIT_CODES)
    entries = _parse_sevenzip_list(out)

    if len(entries) == 1 and not entries[0]["is_directory"] and entries[0]["name"].lower().endswith(".tar"):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            x_args = ["x", "-y", f"-o{tmp}", str(archive), *_sevenzip_password_args(password)]
            x_rc, x_out, x_err = _run(SEVENZIP_EXE, x_args, timeout=DEFAULT_TIMEOUT)
            _check(x_rc, x_out, x_err, "Unwrapping tar for listing", codes=SEVENZIP_EXIT_CODES)
            inner_tar = tmp / entries[0]["name"]
            if inner_tar.is_file():
                inner_rc, inner_out, inner_err = _run(SEVENZIP_EXE, ["l", str(inner_tar)], timeout=DEFAULT_TIMEOUT)
                _check(inner_rc, inner_out, inner_err, "Listing wrapped tar", codes=SEVENZIP_EXIT_CODES)
                return {
                    "archive": str(archive),
                    "unwrapped_tar": entries[0]["name"],
                    "entry_count": len(_parse_sevenzip_list(inner_out)),
                    "entries": _parse_sevenzip_list(inner_out),
                }

    return {"archive": str(archive), "entry_count": len(entries), "entries": entries}


@mcp.tool()
def sevenzip_extract_archive(
    archive_path: str,
    destination: str | None = None,
    files: list[str] | None = None,
    full_paths: bool = True,
    overwrite: bool = True,
    password: str | None = None,
) -> dict[str, Any]:
    """Extract a 7z/zip/tar/gz/xz archive to a destination directory.

    Args:
        archive_path: Path to the archive file.
        destination: Output directory. Defaults to a new folder named after
            the archive, next to it.
        files: Optional list of specific entries (paths as stored in the
            archive, wildcards allowed) to extract. Omit to extract everything.
        full_paths: True extracts with the stored folder structure (x),
            False flattens everything into one directory (e).
        overwrite: Whether to overwrite existing files at the destination.
        password: Optional password for encrypted archives.

    Note: a .tar.gz/.tar.xz needs two decompression passes (unwrap the
    gzip/xz stream, then extract the tar it contains); this is handled
    automatically, leaving only the final files behind.
    """
    archive = _require_file(archive_path)
    dest = Path(destination).resolve() if destination else archive.parent / archive.stem
    dest.mkdir(parents=True, exist_ok=True)

    args = [
        "x" if full_paths else "e",
        "-y",
        "-aoa" if overwrite else "-aos",
        f"-o{dest}",
        str(archive),
        *_sevenzip_password_args(password),
    ]
    if files:
        args.extend(files)

    rc, out, err = _run(SEVENZIP_EXE, args, timeout=DEFAULT_TIMEOUT)
    _check(rc, out, err, "Extracting archive", codes=SEVENZIP_EXIT_CODES)

    # Auto-unwrap: a plain gzip/xz of a single ".tar" member leaves that
    # intermediate tar sitting in the destination - expand it too.
    ext = archive.suffix.lower()
    if _SEVENZIP_TYPE_BY_EXT.get(ext) in _SEVENZIP_SINGLE_FILE_TYPES and not files:
        produced = list(dest.iterdir())
        if len(produced) == 1 and produced[0].is_file() and produced[0].suffix.lower() == ".tar":
            inner_tar = produced[0]
            inner_args = ["x" if full_paths else "e", "-y", "-aoa", f"-o{dest}", str(inner_tar)]
            inner_rc, inner_out, inner_err = _run(SEVENZIP_EXE, inner_args, timeout=DEFAULT_TIMEOUT)
            _check(inner_rc, inner_out, inner_err, "Extracting wrapped tar", codes=SEVENZIP_EXIT_CODES)
            inner_tar.unlink()

    extracted = [str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()]
    return {"destination": str(dest), "file_count": len(extracted), "extracted_files": extracted}


@mcp.tool()
def sevenzip_add_to_archive(
    archive_path: str,
    sources: list[str],
    archive_type: str | None = None,
    compression_level: int = 5,
    password: str | None = None,
    encrypt_headers: bool = False,
) -> dict[str, Any]:
    """Create a new archive, or add files/directories to an existing one.

    Args:
        archive_path: Path to the archive file. The type is inferred from
            its extension (.7z/.zip/.tar/.gz/.xz) unless archive_type is given.
        sources: Files and/or directories to add.
        archive_type: Optional explicit type override: "7z", "zip", "tar",
            "gzip" or "xz". Overrides extension-based detection.
        compression_level: 0 (store, no compression) .. 9 (ultra). Default 5
            (normal).
        password: Optional password to encrypt the archive (.7z and .zip only).
        encrypt_headers: Also encrypt file names/headers. Only supported for
            .7z archives; ignored for other types.

    Note: .gz/.xz can only hold a single compressed stream. If more than
    one source is given, or a source is a directory, the files are first
    bundled into a .tar and that tar is then compressed - the standard
    ".tar.gz"/".tar.xz" two-step, done transparently in one call.
    """
    if not 0 <= compression_level <= 9:
        raise ValueError("compression_level must be between 0 and 9")

    archive, resolved_type = _sevenzip_prepare_archive_path(archive_path, archive_type)
    cwd, relative_sources = _prepare_sources(sources)

    needs_tar_wrap = resolved_type in _SEVENZIP_SINGLE_FILE_TYPES and (
        len(relative_sources) > 1 or (cwd / relative_sources[0]).is_dir()
    )

    def _password_switches() -> list[str]:
        switches = _sevenzip_password_args(password)
        if password and encrypt_headers and resolved_type == "7z":
            switches.append("-mhe=on")
        return switches

    if needs_tar_wrap:
        tar_name = f"{archive.stem}.tar"
        tar_args = ["a", "-y", "-ttar", tar_name, *relative_sources]
        rc, out, err = _run(SEVENZIP_EXE, tar_args, cwd=cwd, timeout=DEFAULT_TIMEOUT)
        _check(rc, out, err, "Bundling files into tar", codes=SEVENZIP_EXIT_CODES)
        try:
            args = ["a", "-y", f"-t{resolved_type}", f"-mx{compression_level}", str(archive), tar_name, *_password_switches()]
            rc, out, err = _run(SEVENZIP_EXE, args, cwd=cwd, timeout=DEFAULT_TIMEOUT)
            _check(rc, out, err, "Compressing tar", codes=SEVENZIP_EXIT_CODES)
        finally:
            (cwd / tar_name).unlink(missing_ok=True)
    else:
        args = ["a", "-y", f"-t{resolved_type}", f"-mx{compression_level}", str(archive), *relative_sources, *_password_switches()]
        rc, out, err = _run(SEVENZIP_EXE, args, cwd=cwd, timeout=DEFAULT_TIMEOUT)
        _check(rc, out, err, "Adding files to archive", codes=SEVENZIP_EXIT_CODES)

    return {"archive": str(archive), "archive_type": resolved_type, "added_sources": relative_sources, "output": out.strip()}


@mcp.tool()
def sevenzip_update_archive(archive_path: str, sources: list[str]) -> dict[str, Any]:
    """Update files in an existing 7z/zip/tar archive (refresh changed files, add new ones).

    Not supported for .gz/.xz (single-stream formats) - recreate those with
    sevenzip_add_to_archive instead.

    Args:
        archive_path: Path to the archive file.
        sources: Files and/or directories to update/add.
    """
    archive = _require_file(archive_path)
    resolved_type = _sevenzip_resolve_type(archive, None)
    if resolved_type in _SEVENZIP_SINGLE_FILE_TYPES:
        raise ValueError(f"Updating a .{resolved_type} archive in place isn't supported; recreate it instead.")
    cwd, relative_sources = _prepare_sources(sources)
    args = ["u", "-y", str(archive), *relative_sources]
    rc, out, err = _run(SEVENZIP_EXE, args, cwd=cwd, timeout=DEFAULT_TIMEOUT)
    _check(rc, out, err, "Updating archive", codes=SEVENZIP_EXIT_CODES)
    return {"archive": str(archive), "updated_sources": relative_sources, "output": out.strip()}


@mcp.tool()
def sevenzip_delete_from_archive(archive_path: str, files: list[str]) -> dict[str, Any]:
    """Delete entries from a 7z/zip/tar archive.

    Not supported for .gz/.xz (single-stream formats).

    Args:
        archive_path: Path to the archive file.
        files: Entry paths as stored in the archive (see sevenzip_list_archive).
    """
    archive = _require_file(archive_path)
    resolved_type = _sevenzip_resolve_type(archive, None)
    if resolved_type in _SEVENZIP_SINGLE_FILE_TYPES:
        raise ValueError(f"Deleting entries from a .{resolved_type} archive isn't supported (single-stream format).")
    if not files:
        raise ValueError("At least one file to delete is required")
    args = ["d", "-y", str(archive), *files]
    rc, out, err = _run(SEVENZIP_EXE, args, timeout=DEFAULT_TIMEOUT)
    _check(rc, out, err, "Deleting from archive", codes=SEVENZIP_EXIT_CODES)
    return {"archive": str(archive), "deleted": files, "output": out.strip()}


@mcp.tool()
def sevenzip_rename_in_archive(archive_path: str, renames: dict[str, str]) -> dict[str, Any]:
    """Rename entries inside a 7z/zip/tar archive.

    Not supported for .gz/.xz (single-stream formats).

    Args:
        archive_path: Path to the archive file.
        renames: Mapping of {old_entry_path: new_entry_path} using paths as
            stored in the archive (see sevenzip_list_archive).
    """
    archive = _require_file(archive_path)
    resolved_type = _sevenzip_resolve_type(archive, None)
    if resolved_type in _SEVENZIP_SINGLE_FILE_TYPES:
        raise ValueError(f"Renaming entries in a .{resolved_type} archive isn't supported (single-stream format).")
    if not renames:
        raise ValueError("At least one rename pair is required")
    args = ["rn", "-y", str(archive)]
    for old, new in renames.items():
        args.extend([old, new])
    rc, out, err = _run(SEVENZIP_EXE, args, timeout=DEFAULT_TIMEOUT)
    _check(rc, out, err, "Renaming archive entries", codes=SEVENZIP_EXIT_CODES)
    return {"archive": str(archive), "renamed": renames, "output": out.strip()}


@mcp.tool()
def sevenzip_test_archive(archive_path: str, password: str | None = None) -> dict[str, Any]:
    """Test a 7z/zip/tar/gz/xz archive's integrity without extracting it to disk.

    Args:
        archive_path: Path to the archive file.
        password: Optional password for encrypted archives.
    """
    archive = _require_file(archive_path)
    args = ["t", str(archive), *_sevenzip_password_args(password)]
    rc, out, err = _run(SEVENZIP_EXE, args, timeout=DEFAULT_TIMEOUT)
    return {
        "archive": str(archive),
        "ok": rc == 0,
        "exit_code": rc,
        "message": _describe_exit_code(rc, SEVENZIP_EXIT_CODES),
        "output": (out or err).strip(),
    }


@mcp.tool()
def sevenzip_print_file_from_archive(
    archive_path: str, file_path: str, password: str | None = None
) -> dict[str, Any]:
    """Print a single file's contents from inside an archive without extracting it to disk.

    Args:
        archive_path: Path to the archive file.
        file_path: Entry path as stored in the archive (see sevenzip_list_archive).
        password: Optional password for encrypted archives.

    Returns text content directly, or base64 for files that aren't valid UTF-8 text.
    """
    archive = _require_file(archive_path)
    args = ["x", "-y", "-so", str(archive), file_path, *_sevenzip_password_args(password)]
    rc, out_bytes, err_bytes = _run_raw(SEVENZIP_EXE, args, timeout=DEFAULT_TIMEOUT)
    if rc != 0:
        raise RuntimeError(
            f"Printing file failed: {_describe_exit_code(rc, SEVENZIP_EXIT_CODES)} (exit {rc}).\n{_decode(err_bytes)}"
        )
    try:
        return {"archive": str(archive), "file": file_path, "encoding": "utf-8", "content": out_bytes.decode("utf-8")}
    except UnicodeDecodeError:
        return {
            "archive": str(archive),
            "file": file_path,
            "encoding": "base64",
            "content": base64.b64encode(out_bytes).decode("ascii"),
        }


@mcp.tool()
def sevenzip_create_sfx(
    archive_path: str, sources: list[str], sfx_module: str = "7zCon.sfx", compression_level: int = 5
) -> dict[str, Any]:
    """Create a self-extracting 7z executable (.exe) directly from source files/directories.

    Unlike RAR/ARJ, 7-Zip builds an SFX in one step from the source files
    rather than converting an already-built archive.

    Args:
        archive_path: Path for the resulting archive; ".exe" replaces
            whatever extension is given (or is appended if none).
        sources: Files and/or directories to add.
        sfx_module: SFX stub module name. Requires "7zCon.sfx" (console) or
            "7z.sfx" (GUI) to be present in bin/7z/ - these ship with the
            full 7-Zip installer but NOT the standalone 7za.exe package (see
            bin/7z/README.md).
        compression_level: 0 (store) .. 9 (ultra).
    """
    if not 0 <= compression_level <= 9:
        raise ValueError("compression_level must be between 0 and 9")
    exe_path = Path(archive_path).with_suffix(".exe").resolve()
    exe_path.parent.mkdir(parents=True, exist_ok=True)
    cwd, relative_sources = _prepare_sources(sources)
    args = ["a", "-y", f"-sfx{sfx_module}", f"-mx{compression_level}", str(exe_path), *relative_sources]
    rc, out, err = _run(SEVENZIP_EXE, args, cwd=cwd, timeout=DEFAULT_TIMEOUT)
    _check(rc, out, err, "Creating SFX archive", codes=SEVENZIP_EXIT_CODES)
    return {"sfx": str(exe_path) if exe_path.exists() else None, "added_sources": relative_sources, "output": out.strip()}


# ---------------------------------------------------------------------------
# MCP tools - generic / roadmap
# ---------------------------------------------------------------------------


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

    Currently .rar, .arj and the 7-Zip formats (.7z/.zip/.tar/.gz/.xz) are
    implemented; other known extensions raise a clear "not implemented yet"
    error naming the tool that will be used once support is added (see
    list_supported_formats).

    Args:
        archive_path: Path to the archive file.
        destination: Output directory. Defaults to a new folder named after
            the archive, next to it.
        password: Optional password for encrypted archives.
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
    return extract_archive(archive_path, destination=destination, password=password)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
