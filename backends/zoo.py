"""ZOO (.zoo) backend: unzoo.exe in bin/zoo/.

Extraction-only, matching the underlying tool: unzoo can list, test and
extract, but has no create/add/delete/rename/update/comment/password
support at all, so none of those are offered here.

No ZOO archive creator is available to produce genuine test data with (the
format is from the mid-1980s and long obsolete), so this backend was
verified by hand-building minimal, spec-correct ZOO archives byte-for-byte
in Python (from unzoo's own C source: header layout, magic words, CRC-16
algorithm) and round-tripping them through the real unzoo.exe - listing,
extracting and testing all match what the source predicts.

Two real findings from that process:
    - unzoo's own "-p" (print to stdout) prefixes each file's bytes with a
      "********\\n<name>\\n********\\n" banner directly on stdout, with no
      way to separate it from the real content - so
      zoo_print_file_from_archive is implemented via a temporary extraction
      instead, like a couple of the other backends.
    - A corrupted/malicious archive whose internal entry-chain pointers
      don't strictly advance can put unzoo.exe into a genuine CPU-bound
      infinite loop (not a stdin wait, so the usual closed-stdin safety net
      doesn't help) - it will only stop when the shared subprocess timeout
      (common.process.DEFAULT_TIMEOUT) kills it. This is inherent to the
      tool, not something worth adding special-case handling for here.

Extraction always uses -o (force overwrite): without it, an existing
destination file makes unzoo.exe prompt interactively; on the closed stdin
this server always uses that fails cleanly rather than hanging, but the
archive would then only be partially extracted, so forcing overwrite keeps
results deterministic.
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

ZOO_DIR = BASE_DIR / "bin" / "zoo"
ZOO_EXE = ZOO_DIR / "unzoo.exe"

# No documented exit code table; 0 is success, non-zero surfaces the
# tool's own error message as detail.
ZOO_EXIT_CODES: dict[int, str] = {0: "Success"}

_DASH_LINE_RE = re.compile(r"^[-\s]{5,}$")
_LIST_ROW_RE = re.compile(
    r"^\s*(?P<size>\d+)\s+(?P<ratio>\d+)%\s+(?P<compressed>\d+)\s+"
    r"(?P<day>\d+)\s+(?P<month>\w{3})\s+(?P<year>\d+)\s+"
    r"(?P<hour>\d+):(?P<min>\d+):(?P<sec>\d+)\s+(?P<name>.+?)\s*$"
)
_TEST_ROW_RE = re.compile(r"^(?P<name>\S.*?)\s*\t--\s*(?P<rest>.+?)\s*$")


def _parse_zoo_list(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    dash_indices = [i for i, line in enumerate(lines) if _DASH_LINE_RE.match(line)]
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
                "compressed_size": int(match.group("compressed")),
                "date": f"{match.group('year')}-{match.group('month')}-{int(match.group('day')):02d}",
                "time": f"{match.group('hour')}:{match.group('min')}:{match.group('sec')}",
            }
        )
    return entries


# ---------------------------------------------------------------------------
# MCP tools - ZOO
# ---------------------------------------------------------------------------


@mcp.tool()
def zoo_list_archive(archive_path: str) -> dict[str, Any]:
    """List the contents of a ZOO archive without extracting it.

    Args:
        archive_path: Path to the .zoo file.
    """
    archive = _require_file(archive_path)
    args = ["-l", str(archive)]
    rc, out, err = _run(ZOO_EXE, args, timeout=DEFAULT_TIMEOUT)
    _check(rc, out, err, "Listing archive", codes=ZOO_EXIT_CODES)
    entries = _parse_zoo_list(out)
    return {"archive": str(archive), "entry_count": len(entries), "entries": entries}


@mcp.tool()
def zoo_extract_archive(
    archive_path: str, destination: str | None = None, files: list[str] | None = None
) -> dict[str, Any]:
    """Extract a ZOO archive to a destination directory (always overwrites existing files).

    Args:
        archive_path: Path to the .zoo file.
        destination: Output directory. Defaults to a new folder named after
            the archive, next to it.
        files: Optional list of specific entries (paths as stored in the
            archive, wildcards allowed, see zoo_list_archive) to extract.
            Omit to extract everything.
    """
    archive = _require_file(archive_path)
    dest = Path(destination).resolve() if destination else archive.parent / archive.stem
    dest.mkdir(parents=True, exist_ok=True)

    args = ["-x", "-o", "-j", f"{dest}\\", str(archive)]
    if files:
        args.extend(files)

    rc, out, err = _run(ZOO_EXE, args, timeout=DEFAULT_TIMEOUT)
    _check(rc, out, err, "Extracting archive", codes=ZOO_EXIT_CODES)

    extracted = [str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()]
    return {"destination": str(dest), "file_count": len(extracted), "extracted_files": extracted}


@mcp.tool()
def zoo_test_archive(archive_path: str) -> dict[str, Any]:
    """Test a ZOO archive's integrity without extracting it to disk.

    Args:
        archive_path: Path to the .zoo file.
    """
    archive = _require_file(archive_path)
    args = ["-x", "-n", str(archive)]
    rc, out, err = _run(ZOO_EXE, args, timeout=DEFAULT_TIMEOUT)
    failed = []
    for line in out.splitlines():
        match = _TEST_ROW_RE.match(line)
        if match and not match.group("rest").startswith("tested"):
            failed.append(match.group("name"))
    return {
        "archive": str(archive),
        "ok": rc == 0 and not failed,
        "exit_code": rc,
        "failed_files": failed,
        "output": (out or err).strip(),
    }


@mcp.tool()
def zoo_print_file_from_archive(archive_path: str, file_path: str) -> dict[str, Any]:
    """Print a single file's contents from inside a ZOO archive without leaving it on disk afterwards.

    Args:
        archive_path: Path to the .zoo file.
        file_path: Entry path as stored in the archive (see zoo_list_archive).

    Returns text content directly, or base64 for files that aren't valid
    UTF-8 text. There's no clean print-to-stdout output (unzoo's own "-p"
    mixes a banner into the same stream), so this is implemented via a
    temporary extraction.
    """
    archive = _require_file(archive_path)
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        args = ["-x", "-o", "-j", f"{tmp}\\", str(archive), file_path]
        rc, out, err = _run(ZOO_EXE, args, timeout=DEFAULT_TIMEOUT)
        _check(rc, out, err, "Printing file from archive", codes=ZOO_EXIT_CODES)
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
