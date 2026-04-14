from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.client.prompt_client import PromptClient


def default_env_path() -> Path:
    return PROJECT_ROOT / "package_data" / ".env"


def build_input(mode: str = "valid") -> str | dict[str, object]:
    if mode == "valid":
        return (
            "Please create an energy-saving analysis task for Shenzhen Nanshan Zone A equipment room. "
            "Time range: 2026-04-01 to 2026-04-07. "
            "Focus: power system, cooling system. "
            "Expected output: optimization suggestions with key risk notes. "
            "Additional notes: Focus on abnormal nighttime energy consumption."
        )
    if mode == "invalid-missing-field":
        return (
            "Please create an energy-saving analysis task for Shenzhen Nanshan Zone A equipment room. "
            "Focus: power system, cooling system. "
            "Expected output: optimization suggestions with key risk notes. "
            "Additional notes: Focus on abnormal nighttime energy consumption."
        )
    if mode == "invalid-value":
        return {
            "task_type": "energy_saving_analysis",
            "site": "Shenzhen Nanshan Zone A equipment room",
            "time_range": "2026-04-01 to 2026-04-07",
            "analysis_target": "banana",
            "expected_output": "optimization suggestions with key risk notes",
            "additional_notes": "Focus on abnormal nighttime energy consumption.",
        }
    raise ValueError(f"Unsupported mode: {mode}")


def run_generation(*, env_path: Path | None = None, mode: str = "valid") -> dict[str, object]:
    client = PromptClient(env_path=env_path or default_env_path())
    result = client.generate_a2a_t_prompt(build_input(mode))
    return result.to_dict()


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "valid"
    result = run_generation(mode=mode)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if bool(result.get("success")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
