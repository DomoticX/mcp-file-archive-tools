# mcp-file-archive-tools

An MCP (Model Context Protocol) server for inspecting and manipulating archive
files: listing, extracting, creating, updating and more.

All bundled tools/backends below have been tested running on Windows 11
64-bit.

## Status

| Extension(s)                    | Backend               | Status        |
|----------------------------------|------------------------|---------------|
| `.rar`                           | `Rar.exe` / `UnRAR.exe` | **Implemented** |
| `.arj`                           | `arj.exe`              | **Implemented** |
| `.7z`, `.zip`, `.tar`, `.gz`, `.xz` | `7za.exe`             | **Implemented** |
| `.lzh`, `.lha`                   | `lha.exe`              | **Implemented** |
| `.uha`                           | `UHARC.EXE`            | **Implemented** |
| `.cab`                           | `makecab.exe` / `expand.exe` | **Implemented** |
| `.ace`                           | `acefile.exe`          | **Implemented** (extract-only) |
| `.zoo`                           | `unzoo.exe`            | **Implemented** (extract-only) |

Call the `list_supported_formats` tool at any time to get this table
programmatically.

## Requirements

- Python 3.10+
- The [`mcp`](https://modelcontextprotocol.io) Python SDK (`pip install mcp`)
- `bin/winrar/Rar.exe` and `bin/winrar/UnRAR.exe` (see
  [bin/winrar/README.md](bin/winrar/README.md) for where to get them).
  `Rar.exe` is used for anything that writes to an archive (create, add,
  update, move, delete, rename, comment, lock, repair, convert-to-SFX,
  search). `UnRAR.exe` (freeware) is used for read-only operations (list,
  extract, test, print).
- `bin/arj/arj.exe` (see [bin/arj/README.md](bin/arj/README.md) for where
  to get it). Used for all ARJ operations, read and write alike.
- `bin/7z/7za.exe` (see [bin/7z/README.md](bin/7z/README.md) for where to
  get it). Used for all `.7z`/`.zip`/`.tar`/`.gz`/`.xz` operations.
- `bin/lha/lha.exe` (see [bin/lha/README.md](bin/lha/README.md) for where
  to get it). Used for all `.lzh`/`.lha` operations.
- `bin/uharc/UHARC.EXE` (see [bin/uharc/README.md](bin/uharc/README.md) for
  where to get it). Used for all `.uha` operations; `UHARCSFX.EXE`
  alongside it is also needed for `uharc_convert_to_sfx`.
- `makecab.exe` and `expand.exe` for `.cab` support — nothing to download,
  these ship with Windows and are normally already on `PATH`
  (`C:\Windows\System32`).
- `bin/ace/acefile.exe` (see [bin/ace/README.md](bin/ace/README.md) for
  where to get it). Used for all `.ace` operations (extraction only — see
  the ACE section below).
- `bin/zoo/unzoo.exe` (see [bin/zoo/README.md](bin/zoo/README.md) for
  where to get it). Used for all `.zoo` operations (extraction only — see
  the ZOO section below).

## Running

```bash
python mcp-file-archive-tools.py
```

The server communicates over stdio, so add it to your MCP client config
(e.g. Claude Desktop) pointing at this script.

## Layout

```
mcp-file-archive-tools.py   - thin entrypoint: imports backends, runs the server
common/
  server.py                 - the shared MCPServer instance + project BASE_DIR
  process.py                - subprocess helpers (_run, _check, exit-code lookup, ...)
  paths.py                  - path helpers (_require_file, _prepare_sources, ...)
  registry.py                - FORMAT_REGISTRY (extension -> backend tool + implemented?)
backends/
  rar.py                    - all RAR tools (list_archive, extract_archive, ...)
  arj.py                    - all ARJ tools (arj_list_archive, arj_extract_archive, ...)
  sevenzip.py                - all 7-Zip tools (sevenzip_list_archive, ...)
  lha.py                     - all LHA/LZH tools (lha_list_archive, ...)
  uharc.py                   - all UHARC tools (uharc_list_archive, ...)
  cab.py                     - all CAB tools (cab_list_archive, ...)
  ace.py                     - all ACE tools (ace_list_archive, ...)
  zoo.py                     - all ZOO tools (zoo_list_archive, ...)
  generic.py                 - list_supported_formats, extract_any_archive
```

Each backend module owns its executable path(s), exit-code table, output
parser and `@mcp.tool()` functions - adding a new archiver means adding one
new file under `backends/` without touching the others.

## Available tools

### RAR

- `list_archive(archive_path, password=None)` — list entries (name, size,
  date, attributes, directory/encrypted flags) without extracting.
- `extract_archive(archive_path, destination=None, files=None, full_paths=True, overwrite=True, password=None)` —
  extract everything or a subset of entries.
- `add_to_archive(archive_path, sources, recursive=True, compression_level=3, password=None, encrypt_headers=False, comment=None)` —
  create a new archive or add files/directories to an existing one.
- `update_archive(archive_path, sources, recursive=True)` — refresh changed
  files and add new ones.
- `move_to_archive(archive_path, sources, recursive=True, compression_level=3)` —
  like `add_to_archive`, but **deletes the original source files** once
  they're safely in the archive.
- `delete_from_archive(archive_path, files)` — remove entries.
- `rename_in_archive(archive_path, renames)` — rename entries
  (`{old_path: new_path}`).
- `test_archive(archive_path, password=None)` — verify archive integrity.
- `repair_archive(archive_path)` — attempt to rebuild a damaged archive into
  `rebuilt.<name>.rar`.
- `lock_archive(archive_path)` — mark an archive as locked (protects it from
  further changes via RAR).
- `set_archive_comment(archive_path, comment)` / `get_archive_comment(archive_path)` —
  read/write the archive comment.
- `convert_to_sfx(archive_path, sfx_name=None)` — convert to a
  self-extracting `.exe`.
- `search_archive(archive_path, search_string, password=None)` — search file
  contents inside the archive (returns raw RAR output).
- `print_file_from_archive(archive_path, file_path, password=None)` — read a
  single entry's contents without extracting to disk (UTF-8 text, or
  base64 for binary files).

### ARJ

All ARJ tools are prefixed `arj_` so they don't collide with the RAR ones.

- `arj_list_archive(archive_path)` — list entries (name, size, compressed
  size, date, CRC-32, directory/encrypted flags) without extracting.
- `arj_extract_archive(archive_path, destination=None, files=None, full_paths=True, password=None)` —
  extract everything or a subset of entries (always overwrites existing
  files — ARJ doesn't offer a reliable non-overwrite mode).
- `arj_add_to_archive(archive_path, sources, recursive=True, compression_level=1, password=None, comment=None)` —
  create a new archive or add files/directories to an existing one.
- `arj_update_archive(archive_path, sources, recursive=True)` — refresh
  changed files and add new ones.
- `arj_move_to_archive(archive_path, sources, recursive=True, compression_level=1)` —
  like `arj_add_to_archive`, but **deletes the original source files** once
  they're safely in the archive.
- `arj_delete_from_archive(archive_path, files)` — remove entries.
- `arj_rename_in_archive(archive_path, renames)` — rename entries
  (`{old_path: new_path}`; the new path fully replaces the old one).
- `arj_test_archive(archive_path, password=None)` — verify archive integrity.
- `arj_set_archive_comment(archive_path, comment)` / `arj_get_archive_comment(archive_path)` —
  read/write the archive comment.
- `arj_convert_to_sfx(archive_path, sfx_name=None)` — convert to a
  self-extracting `.exe`.
- `arj_garble_archive(archive_path, password)` — password-protect an
  **already existing** archive in place, without needing the original
  source files again (ARJ-specific; RAR has no equivalent).
- `arj_search_archive(archive_path, search_string, password=None, ignore_case=True)` —
  search file contents inside the archive (returns raw ARJ output).
- `arj_print_file_from_archive(archive_path, file_path, password=None)` —
  read a single entry's contents without leaving it on disk afterwards
  (UTF-8 text, or base64 for binary files).
- `arj_recover_archive(archive_path, destination=None)` — best-effort
  salvage of readable files from a damaged archive into a destination
  folder (ARJ has no RAR-style "rebuild into a new archive" repair).

### 7-Zip (.7z / .zip / .tar / .gz / .xz)

All 7-Zip tools are prefixed `sevenzip_`.

- `sevenzip_list_archive(archive_path, password=None)` — list entries
  (name, size, compressed size, date, directory flag). For `.tar.gz`/
  `.tar.xz`, transparently unwraps the tar and lists its real contents
  instead of the single wrapped `.tar` entry.
- `sevenzip_extract_archive(archive_path, destination=None, files=None, full_paths=True, overwrite=True, password=None)` —
  extract everything or a subset of entries; also auto-unwraps `.tar.gz`/
  `.tar.xz` in one call.
- `sevenzip_add_to_archive(archive_path, sources, archive_type=None, compression_level=5, password=None, encrypt_headers=False)` —
  create a new archive or add files/directories to an existing one. Type
  is inferred from the extension (`.7z`/`.zip`/`.tar`/`.gz`/`.xz`) unless
  `archive_type` overrides it. For `.gz`/`.xz` with multiple sources or a
  directory, files are bundled into a `.tar` first automatically (the
  standard `.tar.gz`/`.tar.xz` two-step).
- `sevenzip_update_archive(archive_path, sources)` — refresh changed files
  and add new ones. Not supported for `.gz`/`.xz` (single-stream formats).
- `sevenzip_delete_from_archive(archive_path, files)` — remove entries.
  Not supported for `.gz`/`.xz`.
- `sevenzip_rename_in_archive(archive_path, renames)` — rename entries
  (`{old_path: new_path}`). Not supported for `.gz`/`.xz`.
- `sevenzip_test_archive(archive_path, password=None)` — verify archive
  integrity.
- `sevenzip_print_file_from_archive(archive_path, file_path, password=None)` —
  read a single entry's contents without extracting to disk (UTF-8 text,
  or base64 for binary files).
- `sevenzip_create_sfx(archive_path, sources, sfx_module="7zCon.sfx", compression_level=5)` —
  build a self-extracting `.exe` directly from source files (7-Zip creates
  SFX archives in one step, unlike RAR/ARJ's convert-an-existing-archive
  approach). Requires an SFX module file in `bin/7z/` — see
  [bin/7z/README.md](bin/7z/README.md), it isn't included in the
  standalone package.

There's no `sevenzip_` equivalent of RAR/ARJ's lock/comment/search tools —
the 7z/zip/tar formats and the standalone `7za.exe` CLI don't expose
those features.

### LHA / LZH (.lzh / .lha)

All LHA tools are prefixed `lha_`. This format doesn't support passwords
via this tool, so none of the LHA functions take one.

- `lha_list_archive(archive_path)` — list entries (name, size, directory
  flag). Implemented via a temporary extraction rather than lha.exe's own
  listing command — see the caveat below.
- `lha_extract_archive(archive_path, destination=None, full_paths=True)` —
  extract everything (always overwrites — see caveat below).
- `lha_add_to_archive(archive_path, sources)` — create a new archive or add
  files/directories to an existing one (always recursive).
- `lha_update_archive(archive_path, sources)` — refresh changed files and
  add new ones.
- `lha_move_to_archive(archive_path, sources)` — like `lha_add_to_archive`,
  but **deletes the original source files** once they're safely in the
  archive.
- `lha_test_archive(archive_path)` — verify archive integrity.
- `lha_print_file_from_archive(archive_path, file_path)` — read a single
  entry's contents without extracting to disk (UTF-8 text, or base64 for
  binary files).
- `lha_recover_archive(archive_path, destination=None)` — best-effort
  salvage of readable files from a damaged archive.

**There's no `lha_delete_from_archive`.** The bundled `lha.exe` build's
delete command is destructively broken — deleting one entry from a small
multi-file archive can silently drop unrelated entries too, or wipe the
whole archive file. See [bin/lha/README.md](bin/lha/README.md) and the top
of `backends/lha.py` for the full detail (and the other workarounds this
backend applies for the same binary's listing bug and its interactive
overwrite-prompt loop).

### UHARC (.uha)

All UHARC tools are prefixed `uharc_`. UHARC's own command set is the
narrowest of the bunch: no delete, rename, update or comment support, and
no "print a single file" command exist in the tool itself.

- `uharc_list_archive(archive_path, password=None)` — list entries (name,
  size, date, attributes). **Requires the password up front for encrypted
  archives** — unlike the other backends, UHARC refuses to list an
  encrypted archive at all without it.
- `uharc_extract_archive(archive_path, destination=None, files=None, full_paths=True, password=None)` —
  extract everything or a subset of entries (always overwrites).
- `uharc_add_to_archive(archive_path, sources, recursive=True, compression_mode=None, password=None, encrypt_headers=False)` —
  create a new archive or add files/directories to an existing one.
- `uharc_move_to_archive(archive_path, sources, recursive=True, compression_mode=None)` —
  like `uharc_add_to_archive`, but **deletes the original source files**
  once they're safely in the archive.
- `uharc_test_archive(archive_path, password=None)` — verify archive
  integrity.
- `uharc_print_file_from_archive(archive_path, file_path, password=None)` —
  read a single entry's contents without leaving it on disk afterwards
  (UTF-8 text, or base64 for binary files); implemented via a temporary
  extraction since UHARC has no native print-to-stdout command.
- `uharc_convert_to_sfx(archive_path, sfx_name=None)` — build a
  self-extracting `.exe` by concatenating `UHARCSFX.EXE` with the archive
  bytes (UHARC's own SFX mechanism — there's no separate convert command).

### CAB (.cab)

All CAB tools are prefixed `cab_`, use `makecab.exe`/`expand.exe` (no
`bin/` folder or download needed — see Requirements above), and take no
password (the format/tools don't support it). This is the narrowest
backend of all: no delete, rename, update, comment or search, and no
native "test" or "print a single file" command either.

- `cab_list_archive(archive_path)` — list entries. **Names only** —
  `expand.exe`'s own listing doesn't expose any directory component, even
  though extraction does restore it (see the caveat below).
- `cab_extract_archive(archive_path, destination=None, files=None)` —
  extract everything or a subset of entries (always overwrites).
- `cab_add_to_archive(archive_path, sources, compress=True)` — create a
  cabinet from files/directories. `makecab.exe` has no append/update mode,
  so this always (re)creates the archive fresh — an existing file at
  `archive_path` gets overwritten, not merged into.
- `cab_move_to_archive(archive_path, sources, compress=True)` — like
  `cab_add_to_archive`, but **deletes the original source files** once
  they're safely in the archive (done by this server itself — create, then
  delete — since there's no native move mode to rely on).
- `cab_test_archive(archive_path)` — verify archive integrity via a full
  extract-and-discard to a temp folder (there's no dedicated test command).
- `cab_print_file_from_archive(archive_path, file_path)` — read a single
  entry's contents without leaving it on disk afterwards (UTF-8 text, or
  base64 for binary files); implemented via a temporary extraction.

**Caveat — single-file cabinets**: when a `.cab` holds exactly one file
total, `expand.exe`'s extraction misnames the output after the `.cab` file
itself instead of the real entry name (and drops any subdirectory
placement), regardless of how the cabinet was built or which file pattern
is requested. `cab_extract_archive` and `cab_print_file_from_archive` both
detect and correct this before returning. See the top of `backends/cab.py`
for the full detail.

### ACE (.ace)

All ACE tools are prefixed `ace_`. **Extraction only** — ACE archive
creation was only ever supported by the long-discontinued, commercial
WinAce; there is no `ace_add_to_archive` or equivalent.

- `ace_list_archive(archive_path, password=None)` — list entries (name,
  size, compressed size, date, directory flag).
- `ace_extract_archive(archive_path, destination=None, files=None, password=None, restore_attributes=False)` —
  extract everything or a subset of entries (always overwrites).
- `ace_test_archive(archive_path, password=None)` — verify archive
  integrity, returns any per-file failures.
- `ace_print_file_from_archive(archive_path, file_path, password=None)` —
  read a single entry's contents without leaving it on disk afterwards
  (UTF-8 text, or base64 for binary files); implemented via a temporary
  extraction since there's no native print-to-stdout command.
- `ace_show_headers(archive_path)` — dump raw technical header information
  (diagnostic).

Verified against real ACE 1.0/2.0 test archives. One limitation found in
this build: an entry whose filename needs a codepage the tool can't map to
the console's own codepage crashes the whole process with a Python
`UnicodeEncodeError` — a bug in `acefile.exe` itself, not something this
wrapper can work around. See [bin/ace/README.md](bin/ace/README.md) and
the top of `backends/ace.py` for the full detail.

### ZOO (.zoo)

All ZOO tools are prefixed `zoo_`. **Extraction only** — ZOO is a
mid-1980s format; the classic `zoo` compressor that could create archives
isn't packaged here, so there is no `zoo_add_to_archive` or equivalent, and
the format/tool has no password support either.

- `zoo_list_archive(archive_path)` — list entries (name, size, compressed
  size, date, time).
- `zoo_extract_archive(archive_path, destination=None, files=None)` —
  extract everything or a subset of entries (always overwrites).
- `zoo_test_archive(archive_path)` — verify archive integrity, returns any
  per-file failures.
- `zoo_print_file_from_archive(archive_path, file_path)` — read a single
  entry's contents without leaving it on disk afterwards (UTF-8 text, or
  base64 for binary files); implemented via a temporary extraction since
  `unzoo.exe`'s own print-to-stdout mixes a banner into the same stream.

No genuine `.zoo` test archive was available to verify this backend
against, so it was validated by hand-building minimal, spec-correct ZOO
archives byte-for-byte in Python (from `unzoo`'s own published C source)
and round-tripping them through the real `unzoo.exe` - listing, extraction
and integrity testing all matched what the source predicts. One thing to
be aware of: a corrupted/malicious archive whose entry chain doesn't
strictly advance can put `unzoo.exe` into a genuine CPU-bound infinite
loop, only stopped by the shared subprocess timeout. See
[bin/zoo/README.md](bin/zoo/README.md) and the top of `backends/zoo.py`
for the full detail.

### Generic / roadmap

- `list_supported_formats()` — current format coverage.
- `extract_any_archive(archive_path, destination=None, password=None)` —
  dispatches to the right backend (RAR, ARJ, 7-Zip, LHA, UHARC, CAB, ACE or
  ZOO today) by file extension; raises a clear "not implemented yet" error
  for formats still on the roadmap.

## Notes

- All subprocess calls are non-interactive by default (stdin is closed
  unless a command specifically needs it): a missing/incorrect password
  fails fast instead of hanging on a prompt. The one exception is
  `lha.exe`'s overwrite prompt, which ignores closed stdin and loops
  forever instead of failing — worked around by always forcing overwrite
  on LHA extraction rather than relying on that convention.
- `Rar.exe` bundled here is an evaluation build (prints a nag banner on
  stdout); this doesn't affect functionality.
- Console output is decoded trying UTF-8, then `cp1252`/`cp850` as
  fallbacks — filenames with unusual characters may not round-trip
  perfectly depending on the system locale.
- ARJ's `arj_rename_in_archive` and `arj_search_archive` drive normally
  interactive ARJ prompts over stdin; this works reliably for the
  documented flows but is inherently more fragile than RAR's equivalents,
  which take all arguments on the command line.
- `.gz` and `.xz` can only ever hold a single compressed stream (that's
  the format, not a limitation of this tool) — packing multiple
  files/directories always goes through an intermediate `.tar`, and
  extracting/listing a `.tar.gz`/`.tar.xz` involves two decompression
  passes under the hood. Both are handled automatically.
- Every other backend bundles its own executable(s) under `bin/`; CAB is
  the exception and relies on `makecab.exe`/`expand.exe` already being on
  `PATH`, which is the case on a stock Windows install.
- All subprocess calls share a timeout (`common/process.py`,
  `DEFAULT_TIMEOUT`, 300s) as a last-resort safety net against a tool that
  never returns - this matters most for `zoo_*`, where a corrupted archive
  can put `unzoo.exe` into a genuine CPU-bound infinite loop that closed
  stdin doesn't help with.
