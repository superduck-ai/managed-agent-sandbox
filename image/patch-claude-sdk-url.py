#!/usr/bin/env python3
"""Disable Claude's --sdk-url endpoint validation with a length-preserving patch.

The patch targets the embedded, minified JavaScript validator, retains basic URL
parsing, and removes its host/scheme allowlist.  The replacement has exactly the
same byte length so offsets in the surrounding executable remain unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import struct
import sys


# Minified function and parameter names change between releases, so locating the
# validator must not depend on names such as H6_ or e.
FUNCTION_RE = re.compile(
    rb"function\s+[$A-Za-z_][$0-9A-Za-z_]*\s*"
    rb"\(\s*([$A-Za-z_][$0-9A-Za-z_]*)\s*\)\s*\{"
)
ANCHOR = b"not_allowlisted"
VALIDATOR_MARKERS = (
    b"new URL",
    b"bad_scheme",
    b"not_allowlisted",
    b".hostname",
    b".protocol",
)
SEARCH_BACK_BYTES = 16 * 1024
BUN_TRAILER = b"\n---- Bun! ----\n"
BUN_OFFSETS = struct.Struct("<Q6I")
BUN_MODULE = struct.Struct("<12I4B")
BUN_HAS_SOURCE_HASHES = 1 << 5


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_closing_brace(data: bytes, opening_brace: int) -> int:
    """Return the byte position just after a JavaScript block's closing brace."""
    depth = 1
    cursor = opening_brace + 1
    quote: int | None = None

    while cursor < len(data):
        byte = data[cursor]
        if quote is not None:
            if byte == 0x5C:  # Backslash escape.
                cursor += 2
                continue
            if byte == quote:
                quote = None
            cursor += 1
            continue

        if byte in (0x22, 0x27, 0x60):  # Double, single, or template quote.
            quote = byte
            cursor += 1
            continue
        if byte == 0x2F and cursor + 1 < len(data):
            following = data[cursor + 1]
            if following == 0x2F:  # Line comment.
                newline = data.find(b"\n", cursor + 2)
                cursor = len(data) if newline < 0 else newline + 1
                continue
            if following == 0x2A:  # Block comment.
                closing = data.find(b"*/", cursor + 2)
                if closing < 0:
                    raise RuntimeError("unterminated JavaScript block comment")
                cursor = closing + 2
                continue
        if byte == 0x7B:  # {
            depth += 1
        elif byte == 0x7D:  # }
            depth -= 1
            if depth == 0:
                return cursor + 1
        cursor += 1

    raise RuntimeError("unterminated JavaScript function")


def locate_validator(data: bytes) -> tuple[int, int, int, bytes]:
    """Find the validator using its behavior, independent of minified names."""
    candidates: dict[tuple[int, int], tuple[int, bytes]] = {}
    cursor = 0
    while True:
        anchor_at = data.find(ANCHOR, cursor)
        if anchor_at < 0:
            break
        window_start = max(0, anchor_at - SEARCH_BACK_BYTES)
        matches = list(FUNCTION_RE.finditer(data, window_start, anchor_at))
        for match in reversed(matches):
            opening_brace = match.end() - 1
            try:
                end = find_closing_brace(data, opening_brace)
            except RuntimeError:
                continue
            if not (match.start() <= anchor_at < end):
                continue
            block = data[match.start():end]
            if all(marker in block for marker in VALIDATOR_MARKERS):
                parameter = match.group(1)
                candidates[(match.start(), end)] = (match.end(), parameter)
                break
        cursor = anchor_at + len(ANCHOR)

    if len(candidates) != 1:
        raise RuntimeError(
            f"expected exactly one embedded sdk-url validator, found {len(candidates)}"
        )
    (start, end), (body_start, parameter) = next(iter(candidates.items()))
    return start, end, body_start, parameter


def make_patch(old: bytes, body_start: int, start: int, parameter: bytes) -> bytes:
    signature_length = body_start - start
    signature = old[:signature_length]
    core = (
        b"try{new URL("
        + parameter
        + b');return null}catch{return{code:"unparseable",'
        + b'reason:"could not parse --sdk-url as a URL"}}'
    )
    prefix = signature + core + b"/*"
    suffix = b"*/}"
    padding = len(old) - len(prefix) - len(suffix)
    if padding < 0:
        raise RuntimeError("validator is unexpectedly shorter than the replacement")
    replacement = prefix + (b" " * padding) + suffix
    if len(replacement) != len(old):
        raise AssertionError("internal error: replacement length differs")
    return replacement


def locate_bun_module(data: bytes, source_offset: int) -> tuple[int, int, int, str]:
    """Locate the standalone graph record that owns the patched source."""
    trailer_at = data.rfind(BUN_TRAILER)
    if trailer_at < BUN_OFFSETS.size:
        raise RuntimeError("Bun standalone graph trailer not found")
    offsets_at = trailer_at - BUN_OFFSETS.size
    byte_count, modules_offset, modules_length, _entry_id, _argv_offset, _argv_length, flags = (
        BUN_OFFSETS.unpack_from(data, offsets_at)
    )
    graph_at = offsets_at - byte_count
    if graph_at < 0 or graph_at + byte_count != offsets_at:
        raise RuntimeError("invalid Bun standalone graph bounds")
    if modules_length == 0 or modules_length % BUN_MODULE.size != 0:
        raise RuntimeError("invalid Bun standalone module table")

    modules_at = graph_at + modules_offset
    module_count = modules_length // BUN_MODULE.size
    matches: list[tuple[int, int, int, str]] = []
    for index in range(module_count):
        record_at = modules_at + index * BUN_MODULE.size
        values = BUN_MODULE.unpack_from(data, record_at)
        name_offset, name_length = values[0:2]
        contents_offset, contents_length = values[2:4]
        contents_at = graph_at + contents_offset
        if contents_at <= source_offset < contents_at + contents_length:
            name_at = graph_at + name_offset
            name = data[name_at:name_at + name_length].decode("utf-8", "replace")
            source_hash_at = (
                modules_at + modules_length + index * 4
                if flags & BUN_HAS_SOURCE_HASHES
                else -1
            )
            matches.append((record_at, source_hash_at, index, name))

    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one Bun module for sdk-url validator, found {len(matches)}"
        )
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "binary",
        nargs="?",
        type=Path,
        default=Path.home() / "claude",
        help="Claude executable to patch (default: ~/claude)",
    )
    parser.add_argument(
        "--backup",
        type=Path,
        help="backup path (default: BINARY.sdk-url.bak)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and describe the patch without writing files",
    )
    args = parser.parse_args()

    binary = args.binary.expanduser().resolve()
    backup = (args.backup or Path(f"{binary}.sdk-url.bak")).expanduser().resolve()
    data = binary.read_bytes()
    start, end, body_start, parameter = locate_validator(data)
    module_at, source_hash_at, module_index, module_name = locate_bun_module(data, start)
    old = data[start:end]
    new = make_patch(old, body_start, start, parameter)

    print(f"binary:       {binary}")
    print(f"sha256:       {sha256(binary)}")
    print(f"patch offset: {start}")
    print(f"patch length: {len(old)} bytes (unchanged)")
    print(f"parameter:    {parameter.decode('ascii')}")
    print(f"bun module:   {module_name} (index {module_index})")
    if args.dry_run:
        print("dry run: no files changed")
        return 0

    if backup.exists():
        raise FileExistsError(
            f"backup already exists: {backup}\n"
            "Refusing to overwrite it; pass --backup with a new path if intentional."
        )
    shutil.copy2(binary, backup)

    # Write in place so executable metadata and the file's total length remain intact.
    with binary.open("r+b") as stream:
        stream.seek(start)
        stream.write(new)
        # CompiledModuleGraphFile stores bytecode and module-info StringPointer
        # fields at byte offsets 24 and 32. Clear only this module so Bun parses
        # the patched source while every unrelated module keeps its bytecode.
        stream.seek(module_at + 24)
        stream.write(b"\0" * 16)
        if source_hash_at >= 0:
            stream.seek(source_hash_at)
            stream.write(b"\0" * 4)
        stream.flush()
        os.fsync(stream.fileno())

    if binary.stat().st_size != backup.stat().st_size:
        raise RuntimeError("patched file size changed unexpectedly")
    check = binary.read_bytes()
    if check[start:end] != new:
        raise RuntimeError("post-write verification failed")
    if check[module_at + 24:module_at + 40] != b"\0" * 16:
        raise RuntimeError("Bun module bytecode disable verification failed")
    if source_hash_at >= 0 and check[source_hash_at:source_hash_at + 4] != b"\0" * 4:
        raise RuntimeError("Bun module source hash reset verification failed")

    print(f"backup:       {backup}")
    print(f"new sha256:   {sha256(binary)}")
    print("patch written successfully")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
