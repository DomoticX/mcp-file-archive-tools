"""7-Zip (.7z / .zip / .tar / .gz / .xz) backend: 7za.exe in bin/7z/."""

from __future__ import annotations

import base64
import re
import tempfile
from pathlib import Path
from typing import Any

from common.paths import _prepare_sources, _require_file
from common.process import DEFAULT_TIMEOUT, _check, _decode, _describe_exit_code, _run, _run_raw
from common.server import BASE_DIR, mcp

SEVENZIP_DIR = BASE_DIR / "bin" / "7z"
SEVENZIP_EXE = SEVENZIP_DIR / "7za.exe"

# Known 7-Zip (7za.exe) process exit codes (official, stable across versions).
SEVENZIP_EXIT_CODES: dict[int, str] = {
    0: "Success",
    1: "Warning (non-fatal error(s) occurred)",
    2: "Fatal error",
    7: "Command line error",
    8: "Not enough memory for the operation",
    255: "User stopped the process (e.g. a password was needed but stdin was closed)",
}

# ---------------------------------------------------------------------------
# 7-Zip helpers and listing parser
# ---------------------------------------------------------------------------

_DASH_LINE_RE = re.compile(r"^[-\s]{5,}$")

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
