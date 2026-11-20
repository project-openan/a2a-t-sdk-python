"""Demo assets of the RAN energy-saving sample.

Everything under this package exists to make the interaction reproducible offline: the scenario
catalog, the deterministic models, the mock registry, the Web UI and the launcher. Production code
in ``protocol`` and ``shared`` must not import it; the composition roots (the launcher, the Web UI
entry point, the gateway CLI and the NMS server entry point) wire it in explicitly.
"""
