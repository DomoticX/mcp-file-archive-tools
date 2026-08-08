# mcp-file-archive-tools

An MCP (Model Context Protocol) server for inspecting and manipulating archive
files: listing, extracting, creating, updating and more.

## Status

| Extension(s)                    | Backend               | Status        |
|----------------------------------|------------------------|---------------|
| `.rar`                           | `Rar.exe` / `UnRAR.exe` | **Implemented** |
| `.7z`, `.zip`, `.tar`, `.gz`, `.xz` | `7z.exe`              | Planned |
| `.arj`                           | `arj.exe`              | Planned |
| `.uha`                           | `uharc.exe`            | Planned |
| `.cab`                           | `makecab.exe` / `expand.exe` | Planned |
| `.lzh`, `.lha`                   | `lha.exe` / `lhasa.exe` | Planned |

Call the `list_supported_formats` tool at any time to get this table
programmatically.

## Requirements

- Python 3.10+
- The [`mcp`](https://modelcontextprotocol.io) Python SDK (`pip install mcp`)
- `bin/winrar/Rar.exe` and `bin/winrar/UnRAR.exe` (already bundled in this
  repo). `Rar.exe` is used for anything that writes to an archive (create,
  add, update, move, delete, rename, comment, lock, repair, convert-to-SFX,
  search). `UnRAR.exe` (freeware) is used for read-only operations (list,
  extract, test, print).

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

### Generic / roadmap

- `list_supported_formats()` — current format coverage.
- `extract_any_archive(archive_path, destination=None, password=None)` —
  dispatches to the right backend by file extension; raises a clear
  "not implemented yet" error for formats still on the roadmap.

## Notes

- All RAR subprocess calls are non-interactive: a missing/incorrect
  password fails fast (exit code 11) instead of hanging on a prompt.
- `Rar.exe` bundled here is an evaluation build (prints a nag banner on
  stdout); this doesn't affect functionality.
- Console output is decoded trying UTF-8, then `cp1252`/`cp850` as
  fallbacks — filenames with unusual characters may not round-trip
  perfectly depending on the system locale.
