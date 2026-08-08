# bin/arj

This folder is where the ARJ command-line binaries should go for future
`.arj` support in `mcp-file-archive-tools.py` (see `list_supported_formats`
— ARJ is on the roadmap, not implemented yet). Binaries are not committed
to the repository (see `.gitignore`) — download them yourself.

## Download

The Windows binaries are distributed as `arj-3.10.22-bin.zip` via the
GnuWin32 project on SourceForge:

- File listing: https://sourceforge.net/projects/gnuwin32/files/arj/3.10.22/
- Package info page: https://gnuwin32.sourceforge.net/packages/arj.htm

(The original ARJ Software project page, for reference/source code, is
https://sourceforge.net/projects/arj/)

## Extracting

1. Download `arj-3.10.22-bin.zip`.
2. Extract it — it contains several folders (`bin`, `doc`, `man`, ...).
3. Copy the **contents of the zip's `bin` folder** directly into this
   folder (`bin/arj/`, the same folder this README is in), so you end up
   with e.g. `bin/arj/arj.exe`.

## Required files

- `arj.exe` — the ARJ command-line archiver.

## Notes

- ARJ handling in the MCP server hasn't been wired up yet. Once
  implemented, it will follow the same pattern as the RAR tools
  (`WINRAR_DIR` / `RAR_EXE` in `mcp-file-archive-tools.py`), pointing at
  this folder.
