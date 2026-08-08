"""UHARC (.uha) backend: UHARC.EXE in bin/uharc/.

UHARC's own command set is narrower than the other backends: there is no
delete, rename, update or comment support in the tool itself, so none of
those are offered here either (a, m, l, x/e and t are all it has). There is
also no "print a single file to stdout" command, so uharc_print_file_from_archive
is implemented via a temporary extraction, same as the ARJ backend.

Quirks specific to this tool that the implementation works around:
    - Adding a bare directory (no wildcard) reports "Nothing to do!" and
      adds nothing; directories must be suffixed with "\\*" for -r+
      (recurse) to pick up their contents. _uharc_source_patterns() does
      this automatically.
    - Listing an encrypted archive requires the password up front (unlike
      RAR/ARJ/7z, which can list encrypted entries without one).
    - There's no dedicated SFX-conversion command; a self-extracting
      UHARCSFX.EXE + archive.uha (raw byte concatenation, the module ships
      a matching stub for this) - see uharc_convert_to_sfx.
"""

from __future__ import annotations

import base64
import re
import tempfile
from pathlib import Path
from typing import Any

from common.paths import _normalize_archive_path, _prepare_sources, _require_file
from common.process import DEFAULT_TIMEOUT, _check, _describe_exit_code, _run
from common.server import BASE_DIR, mcp

UHARC_DIR = BASE_DIR / "bin" / "uharc"
UHARC_EXE = UHARC_DIR / "UHARC.EXE"
UHARC_SFX_STUB = UHARC_DIR / "UHARCSFX.EXE"

# This tool only distinguishes success from failure; no finer-grained codes.
UHARC_EXIT_CODES: dict[int, str] = {
    0: "Success",
    255: "Error (archive not found, wrong/missing password, or another fatal error)",
}

_DASH_LINE_RE = re.compile(r"^-{5,}$")
_LIST_ROW_RE = re.compile(
    r"^(?P<name>\S.*?)\s{2,}(?P<size>\d+)\s+(?P<date>\d{2}-\w{3}-\d{4})\s+(?P<time>\d{2}:\d{2})\s+(?P<attr>\S+)\s*$"
)


def _parse_uharc_list(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    dash_indices = [i for i, line in enumerate(lines) if _DASH_LINE_RE.match(line.strip())]
    if len(dash_indices) < 2:
        return []
    entries: list[dict[str, Any]] = []
    for line in lines[dash_indices[0] + 1 : dash_indices[1]]:
        match = _LIST_ROW_RE.match(line)
        if not match:
            continue
        entries.append(
            {
                "name": match.group("name").strip(),
                "size": int(match.group("size")),
                "date": match.group("date"),
                "time": match.group("time"),
                "attributes": match.group("attr"),
            }
        )
    return entries


def _uharc_password_args(password: str | None) -> list[str]:
    return [f"-pw{password}"] if password else []


def _uharc_source_patterns(cwd: Path, relative_sources: list[str]) -> list[str]:
    # A bare directory adds nothing ("Nothing to do!"); it needs a trailing
    # wildcard for -r+ to actually recurse into it.
    patterns = []
    for rel in relative_sources:
        patterns.append(f"{rel}\\*" if (cwd / rel).is_dir() else rel)
    return patterns


# ---------------------------------------------------------------------------
# MCP tools - UHARC
# ---------------------------------------------------------------------------


@mcp.tool()
def uharc_list_archive(archive_path: str, password: str | None = None) -> dict[str, Any]:
    """List the contents of a UHARC archive without extracting it.

    Args:
        archive_path: Path to the .uha file.
        password: Required if the archive is encrypted - unlike the other
            backends, UHARC refuses to list an encrypted archive's contents
            without the correct password at all.
    """
    archive = _require_file(archive_path)
    args = ["l", *_uharc_password_args(password), str(archive)]
    rc, out, err = _run(UHARC_EXE, args, timeout=DEFAULT_TIMEOUT)
    _check(rc, out, err, "Listing archive", codes=UHARC_EXIT_CODES)
    entries = _parse_uharc_list(out)
    return {"archive": str(archive), "entry_count": len(entries), "entries": entries}


@mcp.tool()
def uharc_extract_archive(
    archive_path: str,
    destination: str | None = None,
    files: list[str] | None = None,
    full_paths: bool = True,
    password: str | None = None,
) -> dict[str, Any]:
    """Extract a UHARC archive to a destination directory (always overwrites existing files).

    Args:
        archive_path: Path to the .uha file.
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

    args = [
        "x" if full_paths else "e",
        "-y+",
        "-o+",
        f"-t{dest}",
        *_uharc_password_args(password),
        str(archive),
    ]
    if files:
        args.extend(files)

    rc, out, err = _run(UHARC_EXE, args, timeout=DEFAULT_TIMEOUT)
    _check(rc, out, err, "Extracting archive", codes=UHARC_EXIT_CODES)

    extracted = [str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()]
    return {"destination": str(dest), "file_count": len(extracted), "extracted_files": extracted}


@mcp.tool()
def uharc_add_to_archive(
    archive_path: str,
    sources: list[str],
    recursive: bool = True,
    compression_mode: str | None = None,
    password: str | None = None,
    encrypt_headers: bool = False,
) -> dict[str, Any]:
    """Create a new UHARC archive, or add files/directories to an existing one.

    Args:
        archive_path: Path to the .uha file. Created if it doesn't exist,
            ".uha" is appended automatically if missing.
        sources: Files and/or directories to add.
        recursive: Recurse into subdirectories.
        compression_mode: One of "0" (store), "1".."3" (ALZ fast..best),
            "x" (PPM) or "z" (LZP). Defaults to UHARC's own default (ALZ:2).
        password: Optional password to encrypt the archive contents.
        encrypt_headers: Also encrypt the archive headers (file names too).
            Only used when password is set.
    """
    archive = _normalize_archive_path(archive_path, default_suffix=".uha")
    cwd, relative_sources = _prepare_sources(sources)
    patterns = _uharc_source_patterns(cwd, relative_sources)

    args = ["a", "-y+", "-r+" if recursive else "-r-"]
    if compression_mode:
        args.append(f"-m{compression_mode}")
    if password:
        args.append(f"-pw{password}")
        if encrypt_headers:
            args.append("-ph+")
    args.append(str(archive))
    args.extend(patterns)

    rc, out, err = _run(UHARC_EXE, args, cwd=cwd, timeout=DEFAULT_TIMEOUT)
    _check(rc, out, err, "Adding files to archive", codes=UHARC_EXIT_CODES)
    return {"archive": str(archive), "added_sources": relative_sources, "output": out.strip()}


@mcp.tool()
def uharc_move_to_archive(
    archive_path: str, sources: list[str], recursive: bool = True, compression_mode: str | None = None
) -> dict[str, Any]:
    """Move files/directories into a UHARC archive.

    WARNING: this deletes the original source files/directories on disk
    after they have been successfully added to the archive. Prefer
    uharc_add_to_archive unless you specifically want the originals removed.

    Args:
        archive_path: Path to the .uha file.
        sources: Files and/or directories to move into the archive.
        recursive: Recurse into subdirectories.
        compression_mode: One of "0" (store), "1".."3" (ALZ fast..best),
            "x" (PPM) or "z" (LZP). Defaults to UHARC's own default (ALZ:2).
    """
    archive = _normalize_archive_path(archive_path, default_suffix=".uha")
    cwd, relative_sources = _prepare_sources(sources)
    patterns = _uharc_source_patterns(cwd, relative_sources)

    args = ["m", "-y+", "-r+" if recursive else "-r-"]
    if compression_mode:
        args.append(f"-m{compression_mode}")
    args.append(str(archive))
    args.extend(patterns)

    rc, out, err = _run(UHARC_EXE, args, cwd=cwd, timeout=DEFAULT_TIMEOUT)
    _check(rc, out, err, "Moving files into archive", codes=UHARC_EXIT_CODES)
    return {"archive": str(archive), "moved_sources": relative_sources, "output": out.strip()}


@mcp.tool()
def uharc_test_archive(archive_path: str, password: str | None = None) -> dict[str, Any]:
    """Test a UHARC archive's integrity without extracting it to disk.

    Args:
        archive_path: Path to the .uha file.
        password: Optional password for encrypted archives.
    """
    archive = _require_file(archive_path)
    args = ["t", *_uharc_password_args(password), str(archive)]
    rc, out, err = _run(UHARC_EXE, args, timeout=DEFAULT_TIMEOUT)
    return {
        "archive": str(archive),
        "ok": rc == 0,
        "exit_code": rc,
        "message": _describe_exit_code(rc, UHARC_EXIT_CODES),
        "output": (out or err).strip(),
    }


@mcp.tool()
def uharc_print_file_from_archive(
    archive_path: str, file_path: str, password: str | None = None
) -> dict[str, Any]:
    """Print a single file's contents from inside a UHARC archive without leaving it on disk afterwards.

    Args:
        archive_path: Path to the .uha file.
        file_path: Entry path as stored in the archive (see uharc_list_archive).
        password: Optional password for encrypted archives.

    Returns text content directly, or base64 for files that aren't valid
    UTF-8 text. UHARC has no "print to stdout" command, so this is
    implemented via a temporary extraction.
    """
    archive = _require_file(archive_path)
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        args = ["x", "-y+", "-o+", f"-t{tmp}", *_uharc_password_args(password), str(archive), file_path]
        rc, out, err = _run(UHARC_EXE, args, timeout=DEFAULT_TIMEOUT)
        _check(rc, out, err, "Printing file from archive", codes=UHARC_EXIT_CODES)
        extracted = tmp / file_path
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
def uharc_convert_to_sfx(archive_path: str, sfx_name: str | None = None) -> dict[str, Any]:
    """Convert an existing UHARC archive into a self-extracting executable (.exe).

    UHARC builds SFX archives by concatenating its SFX stub module with the
    archive bytes; there's no separate "convert" subcommand.

    Args:
        archive_path: Path to the .uha file.
        sfx_name: Optional output name (without extension). Defaults to the
            archive's own name.
    """
    if not UHARC_SFX_STUB.exists():
        raise FileNotFoundError(f"SFX stub not found: {UHARC_SFX_STUB}")
    archive = _require_file(archive_path)
    exe_path = archive.parent / f"{sfx_name or archive.stem}.exe"
    exe_path.write_bytes(UHARC_SFX_STUB.read_bytes() + archive.read_bytes())
    return {"archive": str(archive), "sfx": str(exe_path)}
