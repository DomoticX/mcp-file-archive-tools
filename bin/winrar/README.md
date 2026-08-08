# bin/winrar

This folder holds the WinRAR/RAR command-line binaries used by
`mcp-file-archive-tools.py` for RAR (`.rar`) support. Binaries are not
committed to the repository (see `.gitignore`) — download them yourself.

## Download

Get WinRAR (which bundles the RAR/UnRAR command-line tools) from the
official site's pre-download page:

https://www.win-rar.com/predownload.html?&L=0

Pick the Windows 64-bit build unless you have a specific reason to use
32-bit.

## Extracting without installing

The WinRAR installer (`winrar-x64-*.exe`) is itself a self-extracting RAR
archive, so you don't have to run the installer:

1. Download the installer `.exe`.
2. Rename it to `.zip` (or any `.rar`/archive extension) — the file itself
   doesn't change, only its extension.
3. Open/extract it with any archive tool (WinRAR itself, 7-Zip, Windows'
   built-in zip support, etc.).
4. Copy the extracted files directly into this folder (`bin/winrar/`, the
   same folder this README is in) — not into a further subfolder.

Alternatively, just run the installer normally and copy the resulting files
from the WinRAR install directory (typically `C:\Program Files\WinRAR`)
into this folder.

## Required files

At minimum, `mcp-file-archive-tools.py` needs:

- `Rar.exe` — full RAR command-line tool (create/modify archives)
- `UnRAR.exe` — freeware extract-only tool (list/extract/test/print)

Copying the whole WinRAR folder (including the `.lng` language files, SFX
modules, etc.) also works and doesn't hurt.

## Notes

- The bundled `Rar.exe` from an unregistered WinRAR install is an
  evaluation build — it prints a nag banner to stdout but is otherwise
  fully functional for everything this MCP server does.
- The `.lng` language file next to `Rar.exe`/`UnRAR.exe` determines the
  console message language. This doesn't affect the MCP tools' parsing
  (see the main README's Notes section) but does affect raw text returned
  by `search_archive`, `add_to_archive`, etc.
