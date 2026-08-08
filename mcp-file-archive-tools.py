"""
MCP File Archive Tools
=======================

An MCP server that exposes archive (de)compression tools to MCP clients.

Currently implemented:
    - RAR (.rar) via the bundled Rar.exe / UnRAR.exe in bin/winrar/

Planned (see FORMAT_REGISTRY / list_supported_formats):
    - .7z / .zip / .tar / .gz / .xz  -> 7z.exe
    - .arj                           -> arj.exe
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

# Roadmap of archive formats and the external tool each one needs.
# Only ".rar" is wired up to an implementation today.
FORMAT_REGISTRY: dict[str, dict[str, Any]] = {
    ".rar": {"tool": "Rar.exe / UnRAR.exe", "implemented": True},
    ".7z": {"tool": "7z.exe", "implemented": False},
    ".zip": {"tool": "7z.exe", "implemented": False},
    ".tar": {"tool": "7z.exe", "implemented": False},
    ".gz": {"tool": "7z.exe", "implemented": False},
    ".xz": {"tool": "7z.exe", "implemented": False},
    ".arj": {"tool": "arj.exe", "implemented": False},
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
        "(.rar) are fully supported: list, extract, create/add, update, "
        "delete, rename, test, repair, lock, comment, convert-to-SFX, "
        "search and print-file. Other formats are on the roadmap; call "
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
    exe: Path, args: list[str], cwd: Path | None = None, timeout: int = DEFAULT_TIMEOUT
) -> tuple[int, bytes, bytes]:
    if not exe.exists():
        raise FileNotFoundError(f"Required executable not found: {exe}")
    try:
        proc = subprocess.run(
            [str(exe), *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"'{exe.name} {' '.join(args)}' timed out after {timeout}s") from exc
    return proc.returncode, proc.stdout, proc.stderr


def _run(
    exe: Path, args: list[str], cwd: Path | None = None, timeout: int = DEFAULT_TIMEOUT
) -> tuple[int, str, str]:
    rc, out, err = _run_raw(exe, args, cwd=cwd, timeout=timeout)
    return rc, _decode(out), _decode(err)


def _describe_exit_code(rc: int) -> str:
    return RAR_EXIT_CODES.get(rc, f"Unknown exit code {rc}")


def _check(rc: int, out: str, err: str, action: str) -> None:
    if rc != 0:
        detail = (err or out).strip()
        raise RuntimeError(f"{action} failed: {_describe_exit_code(rc)} (exit {rc}).\n{detail}")


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

    Currently only .rar is implemented; other known extensions raise a
    clear "not implemented yet" error naming the tool that will be used
    once support is added (see list_supported_formats).

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
    return extract_archive(archive_path, destination=destination, password=password)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
