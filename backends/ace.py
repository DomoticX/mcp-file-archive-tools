"""ACE (.ace) backend: acefile.exe (the "acefile" Python library's bundled CLI) in bin/ace/.

Extraction-only, matching the underlying tool: acefile.exe can list, test
and extract, but has no create/add/delete/rename/update/comment support at
all, so none of those are offered here.

Verified against real ACE 1.0/2.0 test archives (from the acefile project's
public testdata repo, github.com/droe/acefile-testdata):
    - Listing, testing and extracting (whole archive or specific entries)
      all work cleanly and don't hang on closed stdin.
    - On error (missing archive, etc.) this build prints a raw Python
      traceback rather than a clean message - not pretty, but harmless;
      the traceback text is surfaced as-is in the raised error.
    - One real limitation found: archive entries whose filename requires
      a codepage the tool can't map to the console's own codepage crash
      the whole process with an UnicodeEncodeError (reproduced with a
      cp437-encoded "café" filename) - a bug in this specific build, not
      something this wrapper can work around.

There's no native "print a single file to stdout" command, so
ace_print_file_from_archive is implemented via a temporary extraction, same
as a couple of the other backends.
"""

from __future__ import annotations

import base64
import re
import tempfile
from pathlib import Path
from typing import Any

from common.paths import _require_file
from common.process import DEFAULT_TIMEOUT, _check, _run
from common.server import BASE_DIR, mcp

ACE_DIR = BASE_DIR / "bin" / "ace"
ACE_EXE = ACE_DIR / "acefile.exe"

# This build has no documented exit code table; 0 is success, anything
# else surfaces the tool's own (often a raw traceback) message as detail.
ACE_EXIT_CODES: dict[int, str] = {0: "Success"}

_LIST_ROW_RE = re.compile(
    r"^\S+\s+(?P<type>[df])\s+(?P<size>\d+)\s+(?P<compressed>\d+)\s+(?P<ratio>\d+)%\s+"
    r"(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<time>\d{2}:\d{2}:\d{2})\s+(?P<name>.+?)\s*$"
)
_TEST_ROW_RE = re.compile(r"^(?P<status>success|failed)\s+(?P<name>.+?)\s*$")


def _ace_password_args(password: str | None) -> list[str]:
    return ["-p", password] if password else []


def _parse_ace_list(text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = _LIST_ROW_RE.match(line)
        if not match:
            continue
        entries.append(
            {
                "name": match.group("name"),
                "size": int(match.group("size")),
                "compressed_size": int(match.group("compressed")),
                "date": match.group("date"),
                "time": match.group("time"),
                "is_directory": match.group("type") == "d",
            }
        )
    return entries


# ---------------------------------------------------------------------------
# MCP tools - ACE
# ---------------------------------------------------------------------------


@mcp.tool()
def ace_list_archive(archive_path: str, password: str | None = None) -> dict[str, Any]:
    """List the contents of an ACE archive without extracting it.

    Args:
        archive_path: Path to the .ace file.
        password: Optional password for encrypted archives.
    """
    archive = _require_file(archive_path)
    args = ["--list", "-v", "-b", *_ace_password_args(password), str(archive)]
    rc, out, err = _run(ACE_EXE, args, timeout=DEFAULT_TIMEOUT)
    _check(rc, out, err, "Listing archive", codes=ACE_EXIT_CODES)
    entries = _parse_ace_list(out)
    return {"archive": str(archive), "entry_count": len(entries), "entries": entries}


@mcp.tool()
def ace_extract_archive(
    archive_path: str,
    destination: str | None = None,
    files: list[str] | None = None,
    password: str | None = None,
    restore_attributes: bool = False,
) -> dict[str, Any]:
    """Extract an ACE archive to a destination directory (always overwrites existing files).

    Args:
        archive_path: Path to the .ace file.
        destination: Output directory. Defaults to a new folder named after
            the archive, next to it.
        files: Optional list of specific entries (paths as stored in the
            archive, see ace_list_archive) to extract. Omit to extract everything.
        password: Optional password for encrypted archives.
        restore_attributes: Restore original mtime/atime, file attributes
            and NT security info on extraction.
    """
    archive = _require_file(archive_path)
    dest = Path(destination).resolve() if destination else archive.parent / archive.stem
    dest.mkdir(parents=True, exist_ok=True)

    args = ["--extract", "-b", "-d", str(dest), *_ace_password_args(password)]
    if restore_attributes:
        args.append("-r")
    args.append(str(archive))
    if files:
        args.extend(files)

    rc, out, err = _run(ACE_EXE, args, timeout=DEFAULT_TIMEOUT)
    _check(rc, out, err, "Extracting archive", codes=ACE_EXIT_CODES)

    extracted = [str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()]
    return {"destination": str(dest), "file_count": len(extracted), "extracted_files": extracted}


@mcp.tool()
def ace_test_archive(archive_path: str, password: str | None = None) -> dict[str, Any]:
    """Test an ACE archive's integrity without extracting it to disk.

    Args:
        archive_path: Path to the .ace file.
        password: Optional password for encrypted archives.
    """
    archive = _require_file(archive_path)
    args = ["--test", "-b", *_ace_password_args(password), str(archive)]
    rc, out, err = _run(ACE_EXE, args, timeout=DEFAULT_TIMEOUT)
    failed = [m.group("name") for line in out.splitlines() if (m := _TEST_ROW_RE.match(line)) and m.group("status") == "failed"]
    return {
        "archive": str(archive),
        "ok": rc == 0 and not failed,
        "exit_code": rc,
        "failed_files": failed,
        "output": (out or err).strip(),
    }


@mcp.tool()
def ace_print_file_from_archive(
    archive_path: str, file_path: str, password: str | None = None
) -> dict[str, Any]:
    """Print a single file's contents from inside an ACE archive without leaving it on disk afterwards.

    Args:
        archive_path: Path to the .ace file.
        file_path: Entry path as stored in the archive (see ace_list_archive).
        password: Optional password for encrypted archives.

    Returns text content directly, or base64 for files that aren't valid
    UTF-8 text. There's no native print-to-stdout command, so this is
    implemented via a temporary extraction.
    """
    archive = _require_file(archive_path)
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        args = ["--extract", "-b", "-d", str(tmp), *_ace_password_args(password), str(archive), file_path]
        rc, out, err = _run(ACE_EXE, args, timeout=DEFAULT_TIMEOUT)
        _check(rc, out, err, "Printing file from archive", codes=ACE_EXIT_CODES)
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
def ace_show_headers(archive_path: str) -> dict[str, Any]:
    """Dump raw technical header information for an ACE archive (diagnostic).

    Args:
        archive_path: Path to the .ace file.
    """
    archive = _require_file(archive_path)
    args = ["--headers", "-b", str(archive)]
    rc, out, err = _run(ACE_EXE, args, timeout=DEFAULT_TIMEOUT)
    _check(rc, out, err, "Reading archive headers", codes=ACE_EXIT_CODES)
    return {"archive": str(archive), "headers": out.strip()}
