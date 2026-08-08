# bin/zoo

This folder holds the ZOO extraction binary used by
`mcp-file-archive-tools.py` for `.zoo` support. Binaries are not committed
to the repository (see `.gitignore`) — download it yourself.

## Download

Get `unzoo.exe` from the Universal Extractor 2 project:

https://github.com/Bioruebe/UniExtract2

Look under `UniExtract\bin\unzoo.exe` in that project's release/tools —
it's one of the extractors UniExtract2 itself relies on internally.

## Extracting

1. Download/locate `unzoo.exe` (`UniExtract\bin\unzoo.exe`).
2. Copy it directly into this folder (`bin/zoo/`, the same folder this
   README is in).

## Required files

- `unzoo.exe` — lists, tests and extracts ZOO archives.

## Notes

- **Extraction only.** ZOO is a mid-1980s format; the classic `zoo`
  compressor that could create archives isn't packaged here (and isn't
  needed for extraction) - there is no `zoo_add_to_archive` or similar.
- No genuine `.zoo` test archive was available to verify this backend
  against, so it was validated by hand-building minimal, spec-correct ZOO
  archives byte-for-byte (from unzoo's own published C source) and
  round-tripping them through the real `unzoo.exe`; listing, extraction
  and integrity testing all matched what the source predicts.
- A corrupted or maliciously crafted archive whose internal entry chain
  doesn't strictly advance can put `unzoo.exe` into a genuine CPU-bound
  infinite loop - this isn't a stdin-wait, so it only stops once the
  shared subprocess timeout (`common/process.py`) kills it. See the top of
  `backends/zoo.py` for the full detail.
