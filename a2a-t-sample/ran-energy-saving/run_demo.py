"""Compatibility launcher for the demonstration.

The implementation lives in :mod:`demo.run_demo`; this shim keeps the documented
``python run_demo.py`` command working from the sample directory.
"""

from __future__ import annotations

from demo.run_demo import main

if __name__ == "__main__":
    raise SystemExit(main())
