"""Applying confirmed annotations to the case corpus.

The corpus chain is ``docs-local/cases-all.yaml`` → ``docs-local/_generate_cases.py`` →
``eval/osp/cases/*.yaml`` + ``index.yaml``. The generator copies each block of the source
*verbatim*, so an annotation is applied by editing that text, not by re-dumping YAML: a
``safe_dump`` of a whole block would reformat all 100 cases, drop the comments and make the review
diff unreadable.

A text edit is risky, so every patch is verified before anything is written: the patched block is
re-loaded and compared with the original document plus exactly the new keys. A mismatch aborts the
whole apply, and the source file is written through a temporary file with a timestamped backup.
"""

from __future__ import annotations

import copy
import difflib
import json
import re
import subprocess  # noqa: S404 - the corpus generator is a local, trusted script
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import yaml
from eval.osp.catalog import CaseCatalog
from eval.osp.expectations import (
    MESSAGE_DIMENSION,
    SLOT_DIMENSION,
    negotiation_message_expectation,
    slot_error_expectation,
)

#: The sample root (``a2a-t-sample/ran-energy-saving``).
SAMPLE_ROOT: Final[Path] = Path(__file__).resolve().parents[3]

#: The internal corpus source, the generator and the generated directory.
CASES_ALL: Final[Path] = SAMPLE_ROOT / "docs-local" / "cases-all.yaml"
GENERATOR: Final[Path] = SAMPLE_ROOT / "docs-local" / "_generate_cases.py"

#: Header comment that starts every case block in the source file.
_BLOCK_HEADER: Final[str] = "# Generated from OSP-Unified Evaluation Set"

#: How many diff lines the preview keeps; a longer patch is reported as truncated.
_DIFF_LIMIT: Final[int] = 400

_YAML_SENTINEL: Final[str] = "<annotation-preview>"


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of a preview or an apply."""

    ok: bool
    message: str
    diff: str = ""
    changed_cases: tuple[int, ...] = ()
    written: tuple[str, ...] = ()
    errors: tuple[str, ...] = field(default_factory=tuple)


def preview(payload: Mapping[str, object], *, source: Path = CASES_ALL) -> ApplyResult:
    """Patch the corpus source in memory and return the review diff."""
    try:
        original = source.read_text(encoding="utf-8")
    except OSError as exc:
        return ApplyResult(False, f"无法读取 {source}：{exc}")
    patched, changed, errors = patch_text(original, payload)
    if errors:
        return ApplyResult(False, "补丁校验失败", errors=errors)
    if not changed:
        return ApplyResult(True, "没有需要写入的期望值。")
    return ApplyResult(
        True,
        f"将修改 {len(changed)} 条用例。",
        diff=_diff(original, patched),
        changed_cases=changed,
    )


def apply(
    payload: Mapping[str, object],
    *,
    source: Path = CASES_ALL,
    generator: Path = GENERATOR,
    run_generator: bool = True,
) -> ApplyResult:
    """Write the patched corpus source and regenerate the case files."""
    try:
        original = source.read_text(encoding="utf-8")
    except OSError as exc:
        return ApplyResult(False, f"无法读取 {source}：{exc}")
    patched, changed, errors = patch_text(original, payload)
    if errors:
        return ApplyResult(False, "补丁校验失败，未写入任何文件", errors=errors)
    if not changed:
        return ApplyResult(True, "没有需要写入的期望值。")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = source.with_suffix(f".yaml.bak-{stamp}")
    temporary = source.with_suffix(".yaml.tmp")
    try:
        backup.write_text(original, encoding="utf-8")
        temporary.write_text(patched, encoding="utf-8", newline="\n")
        temporary.replace(source)
    except OSError as exc:
        return ApplyResult(False, f"写入 {source} 失败：{exc}")
    written: list[str] = [str(source), str(backup)]
    if run_generator:
        if not generator.is_file():
            return ApplyResult(
                False,
                f"已写入 {source}，但找不到生成器 {generator}；请手动重生成用例文件。",
                diff=_diff(original, patched),
                changed_cases=changed,
                written=tuple(written),
            )
        completed = subprocess.run(  # noqa: S603 - fixed local script, no user input
            [sys.executable, str(generator)],
            cwd=str(SAMPLE_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            return ApplyResult(
                False,
                f"已写入 {source}，但生成器失败（exit {completed.returncode}）：",
                diff=_diff(original, patched),
                changed_cases=changed,
                written=tuple(written),
                errors=(completed.stderr.strip() or completed.stdout.strip(),),
            )
        written.extend(
            [
                str(SAMPLE_ROOT / "eval" / "osp" / "cases"),
                str(SAMPLE_ROOT / "eval" / "osp" / "index.yaml"),
            ]
        )
        verification = verify_generated(changed)
        if verification:
            return ApplyResult(
                False,
                "已重生成用例文件，但生成后的期望无法解析：",
                diff=_diff(original, patched),
                changed_cases=changed,
                written=tuple(written),
                errors=verification,
            )
    return ApplyResult(
        True,
        f"已写入 {len(changed)} 条用例并重生成用例文件。",
        diff=_diff(original, patched),
        changed_cases=changed,
        written=tuple(written),
    )


def verify_generated(changed: Sequence[int], cases_dir: Path | None = None) -> tuple[str, ...]:
    """Parse the regenerated expectations of the changed cases.

    The patch is verified against the *source* text, but the generator rewrites the case files from
    that source; a block that the generator splits or normalizes differently would only surface at
    run time otherwise. Re-reading the generated corpus also proves the new keys survive the
    generation round trip, which is the whole reason the annotation is applied to the source.
    """
    try:
        catalog = CaseCatalog(cases_dir) if cases_dir is not None else CaseCatalog()
    except Exception as exc:  # noqa: BLE001 - a broken corpus is reported, not raised
        return (f"无法读取生成后的用例目录：{type(exc).__name__}: {exc}",)
    errors: list[str] = []
    for case_id in changed:
        case = catalog.get(case_id)
        if case is None:
            errors.append(f"case {case_id}: 生成后缺少该用例")
            continue
        try:
            slot_error_expectation(case)
            for protocol in ("a2a-t", "a2a"):
                negotiation_message_expectation(case, protocol)
        except ValueError as exc:
            errors.append(f"case {case_id}: {exc}")
    return tuple(errors)


def patch_text(
    text: str,
    payload: Mapping[str, object],
) -> tuple[str, tuple[int, ...], tuple[str, ...]]:
    """Return the patched corpus text, the changed case ids and any verification errors."""
    entries = payload.get("cases")
    if not isinstance(entries, list):
        return text, (), ("payload.cases 必须是数组",)
    wanted = {
        int(entry["id"]): entry
        for entry in entries
        if isinstance(entry, Mapping) and isinstance(entry.get("id"), int)
    }
    if not wanted:
        return text, (), ()
    blocks = _split_blocks(text)
    changed: list[int] = []
    errors: list[str] = []
    output: list[str] = []
    for left, right, case_id in blocks:
        block_lines = text.splitlines()[left:right]
        entry = wanted.get(case_id)
        if entry is None:
            output.extend(block_lines)
            continue
        original_document = _load_block(block_lines)
        patched_lines, expected_document, patch_errors = _patch_block(
            block_lines, entry, original_document
        )
        if patch_errors:
            errors.extend(f"case {case_id}: {error}" for error in patch_errors)
            output.extend(block_lines)
            continue
        verification = _verify(patched_lines, expected_document)
        if verification is not None:
            errors.append(f"case {case_id}: {verification}")
            output.extend(block_lines)
            continue
        output.extend(patched_lines)
        changed.append(case_id)
    trailing = text.splitlines()[max((right for _left, right, _id in blocks), default=0) :]
    output.extend(trailing)
    new_text = "\n".join(output)
    if text.endswith("\n"):
        new_text += "\n"
    return new_text, tuple(changed), tuple(errors)


def _patch_block(
    lines: list[str],
    entry: Mapping[str, object],
    original: dict[str, Any] | None,
) -> tuple[list[str], dict[str, Any] | None, list[str]]:
    """Insert or replace the expectation keys of one case block."""
    if original is None:
        return lines, None, ["该块不是合法的 YAML 文档"]
    expected = copy.deepcopy(original)
    errors: list[str] = []
    patched = list(lines)
    for protocol, block in entry.items():
        if protocol == "id" or not isinstance(block, Mapping):
            continue
        protocol_block = _ensure(expected, ("expect", "protocols", protocol))
        if not isinstance(protocol_block, dict):
            errors.append(f"expect.protocols.{protocol} 不是映射")
            continue
        if SLOT_DIMENSION in block:
            value = block[SLOT_DIMENSION]
            protocol_block[SLOT_DIMENSION] = copy.deepcopy(value)
            rendered = _render({SLOT_DIMENSION: value}, indent=6)
            anchor = (
                "validate_label"
                if _find_path(patched, ("expect", "protocols", protocol, "validate_label"))
                is not None
                else "validate"
            )
            patched = _upsert(
                patched,
                parent=("expect", "protocols", protocol),
                key=SLOT_DIMENSION,
                rendered=rendered,
                anchor=anchor,
            )
        if MESSAGE_DIMENSION in block:
            value = block[MESSAGE_DIMENSION]
            negotiation = _ensure(protocol_block, ("negotiation",))
            if not isinstance(negotiation, dict):
                errors.append(f"expect.protocols.{protocol}.negotiation 不是映射")
                continue
            negotiation[MESSAGE_DIMENSION] = copy.deepcopy(value)
            rendered = _render({MESSAGE_DIMENSION: value}, indent=8)
            patched = _upsert(
                patched,
                parent=("expect", "protocols", protocol, "negotiation"),
                key=MESSAGE_DIMENSION,
                rendered=rendered,
                anchor="rounds",
            )
    return patched, expected, errors


def _upsert(
    lines: list[str],
    *,
    parent: tuple[str, ...],
    key: str,
    rendered: list[str],
    anchor: str,
) -> list[str]:
    """Replace the key's block when present, else insert it after ``anchor`` or at the end."""
    path = (*parent, key)
    existing = _find_path(lines, path)
    if existing is not None:
        end = _block_end(lines, existing)
        return [*lines[:existing], *rendered, *lines[end:]]
    anchored = _find_path(lines, (*parent, anchor)) if anchor else None
    if anchored is not None:
        position = _block_end(lines, anchored)
    else:
        parent_index = _find_path(lines, parent)
        position = _block_end(lines, parent_index) if parent_index is not None else len(lines)
    return [*lines[:position], *rendered, *lines[position:]]


class _BlockDumper(yaml.SafeDumper):
    """Dump sequences indented under their key and multiline text as literal blocks.

    Both choices exist for the review diff: an indentless sequence and a folded quoted scalar are
    valid YAML but make a two-line message unreadable in the corpus source.
    """

    def increase_indent(
        self,
        flow: bool = False,
        indentless: bool = False,
    ) -> None:  # noqa: ARG002 - yaml API
        super().increase_indent(flow, False)


def _represent_text(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
    """Emit a multiline string as a ``|`` block scalar, any other string as usual."""
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_BlockDumper.add_representer(str, _represent_text)


def _render(value: object, *, indent: int) -> list[str]:
    """Render one YAML sub-document at the block indentation of the corpus source."""
    dumped = yaml.dump(
        value,
        Dumper=_BlockDumper,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=1000,
    )
    prefix = " " * indent
    return [f"{prefix}{line}" if line.strip() else "" for line in dumped.splitlines()]


def _verify(lines: Sequence[str], expected: dict[str, Any] | None) -> str | None:
    """Return why the patched block does not load as the expected document, or ``None``."""
    if expected is None:
        return "没有期望文档可比对"
    try:
        loaded = yaml.safe_load(_document_text(lines))
    except yaml.YAMLError as exc:
        return f"补丁后的块无法解析：{exc}"
    if loaded != expected:
        return "补丁后的块与预期文档不一致，已跳过该用例"
    return None


def _load_block(lines: Sequence[str]) -> dict[str, Any] | None:
    """Parse one case block, ``None`` when it is not a mapping."""
    try:
        document = yaml.safe_load(_document_text(lines))
    except yaml.YAMLError:
        return None
    return document if isinstance(document, dict) else None


def _document_text(lines: Sequence[str]) -> str:
    """Return one block as YAML text, dropping the document separators inside it.

    The internal source has a few ``---`` separators between cases; the generator strips them
    before parsing, so a single-document load here has to do the same.
    """
    return "\n".join(line for line in lines if line.strip() != "---")


def _ensure(document: dict[str, Any], path: tuple[str, ...]) -> object:
    """Return the nested mapping at ``path``, creating the missing levels."""
    current: Any = document
    for key in path:
        if not isinstance(current, dict):
            return None
        value = current.get(key)
        if not isinstance(value, dict):
            value = {}
            current[key] = value
        current = value
    return current


def _split_blocks(text: str) -> list[tuple[int, int, int]]:
    """Return ``(start, end, case_id)`` per case block of the corpus source."""
    lines = text.splitlines()
    starts = [
        index
        for index, line in enumerate(lines)
        if line.startswith(_BLOCK_HEADER)
    ]
    if not starts:
        return []
    blocks: list[tuple[int, int, int]] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        case_id = _block_case_id(lines[start:end])
        if case_id is not None:
            blocks.append((start, end, case_id))
    return blocks


def _block_case_id(lines: Sequence[str]) -> int | None:
    """Return the ``id:`` of one block, ``None`` when it is not a case block."""
    for line in lines:
        if line.startswith("id: "):
            value = line[4:].strip()
            if value.isdigit():
                return int(value)
            return None
    return None


_KEY_RE: Final[re.Pattern[str]] = re.compile(
    r"^(\s*)([A-Za-z0-9_][A-Za-z0-9_\-]*):(?:\s+(.*))?$"
)


def _find_path(lines: Sequence[str], path: tuple[str, ...]) -> int | None:
    """Return the line index that declares one nested key path.

    The corpus source is generated YAML with a fixed two-space nesting and no flow mappings at the
    levels this module touches, so a small indentation-aware scan is enough; the result is verified
    by reloading the patched block.
    """
    stack: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = _KEY_RE.match(line)
        if match is None:
            continue
        indent = len(match.group(1))
        key = match.group(2)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key))
        if tuple(name for _indent, name in stack) == path:
            return index
    return None


def _block_end(lines: Sequence[str], start: int) -> int:
    """Return the first line index after the block that starts at ``start``."""
    match = _KEY_RE.match(lines[start])
    if match is None:
        return start + 1
    indent = len(match.group(1))
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if not line.strip():
            continue
        stripped = len(line) - len(line.lstrip())
        if stripped <= indent:
            return index
    return len(lines)


def _diff(before: str, after: str) -> str:
    """Return a bounded unified diff of the two corpus texts."""
    lines = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile="cases-all.yaml（当前）",
            tofile="cases-all.yaml（应用后）",
            lineterm="",
            n=2,
        )
    )
    if len(lines) > _DIFF_LIMIT:
        lines = [*lines[:_DIFF_LIMIT], f"... （还有 {len(lines) - _DIFF_LIMIT} 行未显示）"]
    return "\n".join(lines)


def payload_json(payload: Mapping[str, object]) -> str:
    """Return the payload as pretty JSON (used by the export endpoint when not YAML)."""
    return json.dumps(payload, ensure_ascii=False, indent=2)


__all__ = [
    "CASES_ALL",
    "GENERATOR",
    "SAMPLE_ROOT",
    "ApplyResult",
    "apply",
    "patch_text",
    "payload_json",
    "preview",
    "verify_generated",
]
