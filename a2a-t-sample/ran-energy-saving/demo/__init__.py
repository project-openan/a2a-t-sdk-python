"""Demo assets of the RAN energy-saving sample.

Everything under this package is data and wiring for the RAN energy-saving sample: the scenario
catalog, the business agents, the Web UI and the launcher. Production code in ``protocol`` and
``shared`` must not import it; the composition roots (the launcher, the Web UI entry point, the
gateway CLI and the NMS server entry point) wire it in explicitly.
"""
