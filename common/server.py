"""Shared MCPServer instance and project-root path, imported by every backend."""

from __future__ import annotations

from pathlib import Path

from mcp.server.mcpserver import MCPServer

BASE_DIR = Path(__file__).resolve().parent.parent

mcp = MCPServer(
    name="file-archive-tools",
    version="0.1.0",
    instructions=(
        "Tools for inspecting and manipulating archive files. RAR archives "
        "(.rar, list_archive/extract_archive/... tools), ARJ archives "
        "(.arj, arj_* tools) and 7-Zip-backed formats (.7z/.zip/.tar/.gz/.xz, "
        "sevenzip_* tools) are fully supported: list, extract, create/add, "
        "update, delete, rename, test, print-file, plus format-specific "
        "extras (RAR lock/repair/comment, ARJ garble/recover/comment, 7-Zip "
        "SFX creation). Other formats are on the roadmap; call "
        "list_supported_formats() to see current coverage."
    ),
)
