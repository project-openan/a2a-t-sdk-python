import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
FIXTURES = HERE / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture()
def sample_rows() -> list[dict]:
    lines = [line for line in _load("sample/observations.jsonl").splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


@pytest.fixture()
def sample_scores() -> list[dict]:
    return json.loads(_load("sample/scores.json"))
