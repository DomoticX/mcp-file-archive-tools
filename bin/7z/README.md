# bin/7z

This folder holds the 7-Zip command-line binaries used by
`mcp-file-archive-tools.py` for `.7z`, `.zip`, `.tar`, `.gz` and `.xz`
support. Binaries are not committed to the repository (see `.gitignore`) —
download them yourself.

## Download

Get the 7-Zip **standalone console version** ("7-Zip Extra") from the
official download page:

https://www.7-zip.org/download.html

Look for the entry named something like "7-Zip Extra: standalone console
version, 7z DLL, Plugin for Far Manager" (a `.7z` file itself, e.g.
`7z2500-extra.7z`) — not the regular installer.

## Extracting

1. Download the "7-Zip Extra" `.7z` package.
2. Since it's itself a `.7z` archive, extract it with 7-Zip (or any tool
   that already supports `.7z`, e.g. WinRAR — see
   [bin/winrar/README.md](../winrar/README.md)), or a fresh
   `python mcp-file-archive-tools.py` run once WinRAR is set up.
3. Copy the extracted files directly into this folder (`bin/7z/`, the
   same folder this README is in) — not into a further subfolder. The
   package also contains `x64/` and `arm64/` subfolders with 64-bit/ARM64
   builds of the same binaries; you can ignore those (the 32-bit build in
   the package root works fine) or swap them in if you prefer.

## Required files

- `7za.exe` — the 7-Zip standalone command-line archiver.

## Notes

- The standalone "Extra" package does **not** include the SFX stub modules
  (`7zCon.sfx` / `7z.sfx`). `sevenzip_create_sfx` needs one of those; if
  you want that feature, copy `7zCon.sfx` from a full 7-Zip install
  (typically `C:\Program Files\7-Zip\7zCon.sfx` — install the regular
  7-Zip installer from the same download page to get it) into this folder.
- `.gz`/`.xz` can only ever compress a single file. When packing a
  directory or multiple files into one of those, `mcp-file-archive-tools.py`
  automatically bundles them into a `.tar` first (the usual `.tar.gz` /
  `.tar.xz` two-step) — no extra setup needed for that.
