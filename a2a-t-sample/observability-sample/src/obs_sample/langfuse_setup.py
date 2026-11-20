"""Optional Langfuse backend for the observability sample.

Set these env vars (or .env entries) to route A2A-T observability spans
into Langfuse cloud instead of the default Console exporter:

    LANGFUSE_PUBLIC_KEY=pk-lf-...
    LANGFUSE_SECRET_KEY=sk-lf-...
    LANGFUSE_BASE_URL=https://us.cloud.langfuse.com

Langfuse v5 sets a global TracerProvider with its own OTLP exporter
(Basic Auth to the Langfuse ingest endpoint). The A2A-T decorators
pick it up automatically because they use ``trace.get_tracer()``
which resolves to Langfuse's provider. No additional wiring needed.
"""

from __future__ import annotations

import os

#: Instrumentation scope of the A2A-T SDK decorators.
A2AT_INSTRUMENTATION_SCOPE = "a2at-observability"


def _should_export_span(span: object) -> bool:
    """Export the A2A-T decorator spans (Langfuse v5 filters unknown scopes by default)."""
    scope = getattr(span, "instrumentation_scope", None)
    return scope is not None and getattr(scope, "name", None) == A2AT_INSTRUMENTATION_SCOPE


def setup_langfuse_if_configured() -> bool:
    """Initialize Langfuse when keys are present; returns True if activated."""
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    if not public_key or not secret_key:
        return False

    from langfuse import Langfuse

    # Langfuse v5 sets the global TracerProvider on init and filters spans by scope; allow the
    # SDK's instrumentation scope so the decorator spans reach the dashboard.
    Langfuse(should_export_span=_should_export_span)
    return True
