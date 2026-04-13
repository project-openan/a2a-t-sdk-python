from __future__ import annotations

from pathlib import Path
import shutil
import unittest

from a2a_t.prompt.common.models import FetchResult


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def build_markdown(
    *,
    name: str,
    language: str | None,
    version: str,
    title: str,
    description: str,
    body: str,
) -> str:
    lines = [
        "---",
        f"name: {name}",
    ]
    if language is not None:
        lines.append(f"language: {language}")
    lines.extend(
        [
            f"version: {version}",
            f"title: {title}",
            f"description: {description}",
            "---",
            body,
        ]
    )

    return "\n".join(lines) + "\n"


class FakeRemoteProvider:
    def __init__(self, responses: list[FetchResult | Exception]) -> None:
        self._responses = responses
        self.calls = 0

    def fetch(self, locator: str) -> FetchResult:
        response = self._responses[self.calls]
        self.calls += 1
        if isinstance(response, Exception):
            raise response
        return response


class ManagedTempDirTestCase(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._managed_temp_dirs: list[Path] = []
        self.addCleanup(self.cleanup_temp_dirs)

    def make_temp_dir(self, name: str) -> Path:
        if not hasattr(self, "_managed_temp_dirs"):
            self._managed_temp_dirs = []
        temp_dir = PROJECT_ROOT / ".tmp_tests" / name / self._testMethodName
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        temp_dir.mkdir(parents=True, exist_ok=True)
        self._managed_temp_dirs.append(temp_dir)
        return temp_dir

    def cleanup_temp_dirs(self) -> None:
        for temp_dir in reversed(self._managed_temp_dirs):
            shutil.rmtree(temp_dir, ignore_errors=True)
            self._cleanup_empty_parents(temp_dir.parent)
        self._managed_temp_dirs.clear()

    def _cleanup_empty_parents(self, directory: Path) -> None:
        temp_root = PROJECT_ROOT / ".tmp_tests"
        current = directory
        while current.exists() and current != PROJECT_ROOT:
            try:
                current.rmdir()
            except OSError:
                break
            if current == temp_root:
                break
            current = current.parent
