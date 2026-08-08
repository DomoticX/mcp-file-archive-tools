# bin/ace

This folder holds the ACE extraction binary used by
`mcp-file-archive-tools.py` for `.ace` support. Binaries are not committed
to the repository (see `.gitignore`) — download it yourself.

## Download

Get `acefile.exe` (a standalone build of the open-source `acefile` Python
library's CLI) from the Universal Extractor 2 project:

https://github.com/Bioruebe/UniExtract2

Look in that project's releases/tools for `acefile.exe`, or check its
dependency/bundled-tools listing — it's one of the extractors UniExtract2
itself relies on internally.

## Extracting

1. Download `acefile.exe`.
2. Copy it directly into this folder (`bin/ace/`, the same folder this
   README is in).

## Required files

- `acefile.exe` — reads/lists/tests/extracts ACE 1.0 and 2.0 archives.

## Notes

- **Extraction only.** ACE archive creation was only ever supported by the
  long-discontinued, commercial WinAce, which isn't available here — there
  is no `ace_add_to_archive` or similar, matching what `acefile.exe`
  itself can do.
- Verified against real `.ace` test archives. One real limitation found:
  an entry whose filename needs a codepage the tool can't map to the
  console's own codepage crashes the whole process with a Python
  `UnicodeEncodeError` (reproduced with a cp437-encoded "café" filename) —
  a bug in this specific build, not something the MCP wrapper can work
  around. See the top of `backends/ace.py` for the full detail.
- On error (missing archive, etc.) this build prints a raw Python
  traceback rather than a clean message; it's surfaced as-is rather than
  cleaned up, since there's no reliable structured error to parse instead.
