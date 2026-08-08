"""
MCP File Archive Tools
=======================

An MCP server that exposes archive (de)compression tools to MCP clients.

Currently implemented (see backends/):
    - RAR (.rar) via the bundled Rar.exe / UnRAR.exe in bin/winrar/
    - ARJ (.arj) via the bundled arj.exe in bin/arj/
    - 7-Zip (.7z/.zip/.tar/.gz/.xz) via the bundled 7za.exe in bin/7z/
    - LHA/LZH (.lzh/.lha) via the bundled lha.exe in bin/lha/

Planned (see common/registry.py / list_supported_formats):
    - .uha                           -> uharc.exe
    - .cab                           -> makecab.exe / expand.exe

Layout:
    common/    - shared MCPServer instance, subprocess/path helpers, format registry
    backends/  - one module per archiver, each registering its own @mcp.tool() functions

Run:
    python mcp-file-archive-tools.py
"""

from __future__ import annotations

from common.server import mcp
from backends import arj, generic, lha, rar, sevenzip  # noqa: F401  (imported for @mcp.tool() registration)

if __name__ == "__main__":
    mcp.run()
