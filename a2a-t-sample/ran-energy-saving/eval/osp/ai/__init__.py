"""AI-driven OSP evaluation: instance profile, headless batches, evidence and reports.

The package is the deterministic half of the ``osp-ai-eval`` procedure. It never owns protocol
logic and never changes ``/tester``: it drives the existing HTTP API, reads the artifacts the
sample already writes, and leaves the root-cause judgement to the agent.
"""

from __future__ import annotations

__all__: list[str] = []
