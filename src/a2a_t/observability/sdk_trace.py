"""trace_facade: opt-in instance-level wrapping of SDK/business facades (L4 spans).

Wraps public methods on the INSTANCE (setattr on the object, not the class) so neither the
class definition nor the SDK source is modified. Coroutine functions get an async wrapper
(the span ends only after await); sync functions a plain wrapper. Spans carry no custom
attributes by design (spec §3.2). The master switch is honored via _otel_compat.get_tracer(),
which degrades to a NoOp tracer when disabled.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Iterable
from typing import Any

from a2a_t.observability import _otel_compat


def _guess_role(obj: Any) -> str:
    class_name = type(obj).__name__
    if "Client" in class_name:
        return "client"
    if "Server" in class_name:
        return "server"
    return "custom"


def _wrap_sync(name: str, role: str, original: Any) -> Any:
    @functools.wraps(original)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        tracer: Any = _otel_compat.get_tracer()
        with tracer.start_as_current_span(f"a2at.sdk.{role}.{name}"):
            return original(*args, **kwargs)

    return wrapper


def _wrap_async(name: str, role: str, original: Any) -> Any:
    @functools.wraps(original)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        tracer: Any = _otel_compat.get_tracer()
        with tracer.start_as_current_span(f"a2at.sdk.{role}.{name}"):
            return await original(*args, **kwargs)

    return wrapper


def trace_facade(obj: Any, *, role: str | None = None, methods: Iterable[str] | None = None) -> Any:
    """Wrap public methods of obj with a2at.sdk.{role}.{method} INTERNAL spans; returns obj."""
    resolved_role = role or _guess_role(obj)
    cls = type(obj)
    if methods is None:
        names = [name for name in dir(cls) if not name.startswith("_") and callable(getattr(cls, name))]
    else:
        names = list(methods)
    for name in names:
        original = getattr(obj, name)
        if inspect.iscoroutinefunction(original):
            setattr(obj, name, _wrap_async(name, resolved_role, original))
        elif callable(original):
            setattr(obj, name, _wrap_sync(name, resolved_role, original))
    return obj
