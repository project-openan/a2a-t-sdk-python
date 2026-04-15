from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.prompt.common.a2a_t_task_prompt import A2ATTaskPromptMetadata, render_a2a_t_task_prompt
from a2a_t.server.prompt_handler import PromptHandler


def default_env_path() -> Path:
    return PROJECT_ROOT / "package_data" / ".env"


def build_prompt(mode: str = "valid") -> str:
    if mode == "valid":
        return render_a2a_t_task_prompt(
            body=(
                "Please create an energy-saving analysis task for Shenzhen Nanshan Zone A equipment room. "
                "Time range: 2026-04-01 to 2026-04-07. "
                "Focus: power system, cooling system. "
                "Expected output: optimization suggestions with key risk notes. "
                "Additional notes: Focus on abnormal nighttime energy consumption."
            ),
            metadata=A2ATTaskPromptMetadata(
                scenario_code="energy_saving",
                language="en-US",
                version="0.0.1",
                description="Used for energy saving analysis.",
            ),
        )
    if mode == "invalid-front-matter":
        return (
            "---\n"
            "scenario_code: energy_saving\n"
            "version: 0.0.1\n"
            "description: Used for energy saving analysis.\n"
            "---\n\n"
            "Please create an energy-saving analysis task for Shenzhen Nanshan Zone A equipment room."
        )
    if mode == "invalid-slots":
        return render_a2a_t_task_prompt(
            body=(
                "Please create an energy-saving analysis task for Shenzhen Nanshan Zone A equipment room. "
                "Focus: power system, cooling system. "
                "Expected output: optimization suggestions with key risk notes. "
                "Additional notes: Focus on abnormal nighttime energy consumption."
            ),
            metadata=A2ATTaskPromptMetadata(
                scenario_code="energy_saving",
                language="en-US",
                version="0.0.1",
                description="Used for energy saving analysis.",
            ),
        )
    raise ValueError(f"Unsupported mode: {mode}")


def build_sample_prompt() -> str:
    return build_prompt("valid")


def run_validation(*, env_path: Path | None = None, task_id: str = "server-live-demo", mode: str = "valid") -> dict[str, object]:
    handler = PromptHandler(env_path=env_path or default_env_path())
    return handler.process(
        task_id,
        {
            "processed_prompt_text": build_prompt(mode),
        },
    )


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "valid"
    result = run_validation(mode=mode)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if bool(result.get("passed")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
