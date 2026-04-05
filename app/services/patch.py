"""Patch/diff utilities."""

from __future__ import annotations
import difflib


def apply_patch(
    file_path: str,
    old_content: str,
    new_content: str,
) -> list[dict]:
    lines_before = old_content.splitlines(keepends=True)
    lines_after = new_content.splitlines(keepends=True)
    diff = difflib.unified_diff(
        lines_before,
        lines_after,
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        n=3,
        lineterm="",
    )
    return list(diff)
