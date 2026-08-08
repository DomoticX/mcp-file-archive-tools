# mcp-file-archive-tools

An MCP (Model Context Protocol) server for inspecting and manipulating archive
files: listing, extracting, creating, updating and more.

## Status

| Extension(s)                    | Backend               | Status        |
|----------------------------------|------------------------|---------------|
| `.rar`                           | `Rar.exe` / `UnRAR.exe` | **Implemented** |
| `.arj`                           | `arj.exe`              | **Implemented** |
| `.7z`, `.zip`, `.tar`, `.gz`, `.xz` | `7z.exe`              | Planned |
| `.uha`                           | `uharc.exe`            | Planned |
| `.cab`                           | `makecab.exe` / `expand.exe` | Planned |
| `.lzh`, `.lha`                   | `lha.exe` / `lhasa.exe` | Planned |

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

## Running

```bash
python mcp-file-archive-tools.py
```

The server communicates over stdio, so add it to your MCP client config
(e.g. Claude Desktop) pointing at this script.

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

### Generic / roadmap

- `list_supported_formats()` — current format coverage.
- `extract_any_archive(archive_path, destination=None, password=None)` —
  dispatches to the right backend (RAR or ARJ today) by file extension;
  raises a clear "not implemented yet" error for formats still on the
  roadmap.

## Notes

- All RAR/ARJ subprocess calls are non-interactive by default (stdin is
  closed unless a command specifically needs it): a missing/incorrect
  password fails fast instead of hanging on a prompt.
- `Rar.exe` bundled here is an evaluation build (prints a nag banner on
  stdout); this doesn't affect functionality.
- Console output is decoded trying UTF-8, then `cp1252`/`cp850` as
  fallbacks — filenames with unusual characters may not round-trip
  perfectly depending on the system locale.
- ARJ's `arj_rename_in_archive` and `arj_search_archive` drive normally
  interactive ARJ prompts over stdin; this works reliably for the
  documented flows but is inherently more fragile than RAR's equivalents,
  which take all arguments on the command line.
