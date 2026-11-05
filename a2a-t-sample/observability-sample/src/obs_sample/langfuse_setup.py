"""Optional Langfuse backend for the observability sample.

Set these env vars (or .env entries) to route A2A-T observability spans
into Langfuse cloud instead of the default Console exporter:

    LANGFUSE_PUBLIC_KEY=pk-lf-...
    LANGFUSE_SECRET_KEY=sk-lf-...
    LANGFUSE_BASE_URL=https://us.cloud.langfuse.com

Langfuse v4 sets a global TracerProvider with its own OTLP exporter
(Basic Auth to the Langfuse ingest endpoint). The A2A-T decorators
pick it up automatically because they use ``trace.get_tracer()``
which resolves to Langfuse's provider. No additional wiring needed.
"""

from __future__ import annotations

import os


def setup_langfuse_if_configured() -> bool:
    """Initialize Langfuse when keys are present; returns True if activated."""
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    if not public_key or not secret_key:
        return False

    from langfuse import Langfuse

    langfuse = Langfuse()
    # Langfuse v4 sets the global TracerProvider on init — the A2A-T
    # decorators will automatically export spans to Langfuse cloud.
    langfuse.flush()
    return True
