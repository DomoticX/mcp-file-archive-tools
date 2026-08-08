# bin/uharc

This folder holds the UHARC command-line binaries used by
`mcp-file-archive-tools.py` for `.uha` support. Binaries are not committed
to the repository (see `.gitignore`) — download them yourself.

## Download

Two options, either works:

- **Installer**: https://sam.gleske.net/uharc/
- **Standalone package** (background/history on the format, with links to
  the original archives): http://justsolve.archiveteam.org/wiki/UHARC

## Extracting

1. Download the installer or standalone package from one of the links above.
2. Run the installer (or extract the standalone package/archive).
3. Copy `UHARC.EXE` from wherever it ends up into this folder (`bin/uharc/`,
   the same folder this README is in).

Copying the whole set (`UHARC.EXE`, `UHARCD.EXE`, `UHARCSFX.EXE`,
`UNUHARC.EXE`, `UNUHARCD.EXE`, docs) also works and doesn't hurt —
`uharc_convert_to_sfx` specifically needs `UHARCSFX.EXE` alongside
`UHARC.EXE` to build self-extracting archives.

## Required files

- `UHARC.EXE` — the UHARC command-line archiver (all read/write operations).
- `UHARCSFX.EXE` — SFX stub, only needed for `uharc_convert_to_sfx`.

## Notes

- UHARC 0.6b is old freeware (2005) with a narrower command set than the
  other backends here: no delete, rename, update or comment support, and
  no "print a single file" command. See the top of `backends/uharc.py` for
  what's implemented and how the missing print command is worked around.
