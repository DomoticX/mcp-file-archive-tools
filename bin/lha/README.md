# bin/lha

This folder holds the LHA command-line binary used by
`mcp-file-archive-tools.py` for `.lzh`/`.lha` support. Binaries are not
committed to the repository (see `.gitignore`) — download it yourself.

## Download

Get the GnuWin32 port of LHA from:

https://gnuwin32.sourceforge.net/packages/lha.htm

Download the "Binaries" package (a `.zip`).

## Extracting

1. Download the binaries `.zip`.
2. Extract it — it contains a `bin/` folder (plus `doc/`, etc.).
3. Copy `lha.exe` from the zip's `bin/` folder directly into this folder
   (`bin/lha/`, the same folder this README is in).

## Required files

- `lha.exe` — the LHA command-line archiver.

## Known limitations of this binary

This specific build (LHa for UNIX v1.14i, GnuWin32 Windows port) has a
couple of bugs that shape what `mcp-file-archive-tools.py` does and doesn't
expose for it — see the top of `backends/lha.py` for the full detail:

- Its own `l`/`v` listing commands only ever report the first entry in an
  archive, no matter how many it actually contains. `lha_list_archive`
  works around this by extracting to a temp folder and reading the real
  file list back, rather than trusting the listing output.
- Its "delete entry" (`d`) command is destructively broken: deleting one
  file from a small multi-file archive can silently drop unrelated entries
  too, or delete the entire archive file outright. Because of this, there
  is **no delete tool** for LHA/LZH archives in this server — recreate the
  archive with `lha_add_to_archive` instead if you need to remove something.
- Extraction always overwrites existing files; without the force option
  this build loops forever re-printing its overwrite prompt instead of
  failing safely, so no non-overwrite mode is offered.

None of this reflects a problem with the LZH/LHA *format* — just this
particular compiled tool.
