"""Public API contract for a2a_t.observability (spec §5.1) + no-a2a-import guard.

Two invariants:

1. Export completeness: the package exports exactly the spec §5.1 public API —
   every expected symbol is present, and ``__all__`` set-equals the expected
   set, so a stray export (nothing more) or a missing one (nothing less) fails.

2. No-a2a-import guard: the observability tree is structural — modules must not
   import ``a2a``/``a2a.*`` (``a2a_t`` is fine). v3 architecture decision: the
   decorator runtime modules (``client/factory.py``, ``server/handler_decorator.py``)
   are the documented exemption — they wrap a2a objects structurally and may
   import a2a if a future change needs real types (e.g. push-sender ABC checks).
   The core modules (attributes/span/current_span/propagation/logs/config/
   setup/sdk_trace and everything else) are prohibited unconditionally. Today
   the entire tree is a2a-import-free, which is what makes
   ``import a2a_t.observability`` work without a2a-sdk installed — verified in
   a subprocess with ``a2a`` import-blocked (guard test 3).

The source tree is anchored to THIS test file (``Path(__file__)``), not to the
process cwd, and the anchor is validated before use so the AST walk cannot pass
vacuously against an empty/nonexistent directory.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import a2a_t.observability as observability

_PACKAGE_DIR = Path(__file__).resolve().parents[2] / "src" / "a2a_t" / "observability"

#: Decorator runtime modules allowed to import a2a (v3 architecture exemption).
_A2A_IMPORT_EXEMPT = {
    "client/factory.py",
    "server/handler_decorator.py",
}

#: spec §5.1: 12 APIs + 17 A2A-T constants + 6 GenAI constants.
EXPECTED_EXPORTS: frozenset[str] = frozenset(
    {
        # OTel configuration
        "setup",
        "is_otel_configured",
        # Integration
        "register_client_factory",
        "A2ATRequestHandlerDecorator",
        # Current span
        "current_span",
        "A2ATCurrentSpan",
        # Manual span
        "A2ATSpan",
        # Facade tracing
        "trace_facade",
        # Propagation
        "inject_traceparent",
        "extract_trace_context",
        "TRACEPARENT_HEADER",
        # Configuration
        "A2ATObservabilityConfig",
        # A2A-T attribute constants (17)
        "ATTR_EXTENSION_NAME",
        "ATTR_TASK_ID",
        "ATTR_TASK_STATUS",
        "ATTR_TASK_TYPE",
        "ATTR_NEGOTIATION_ID",
        "ATTR_NEGOTIATION_ROUND",
        "ATTR_NEGOTIATION_MAX_ROUNDS",
        "ATTR_NEGOTIATION_PERFORMATIVE",
        "ATTR_NEGOTIATION_TOTAL_ROUNDS",
        "ATTR_NOTIFICATION_TOPIC",
        "ATTR_STREAMING_EVENT_KIND",
        "ATTR_PUSH_NOTIFICATION_URL",
        "ATTR_AUTHORIZATION_POLICY_OPERATION_TYPE",
        "ATTR_GEN_AI_OPERATION_NAME",
        "ATTR_GEN_AI_CONVERSATION_ID",
        "ATTR_STREAMING",
        # GenAI attribute constants (6)
        "ATTR_GEN_AI_USAGE_INPUT_TOKENS",
        "ATTR_GEN_AI_USAGE_OUTPUT_TOKENS",
        "ATTR_GEN_AI_REQUEST_MODEL",
        "ATTR_GEN_AI_RESPONSE_MODEL",
        "ATTR_GEN_AI_TOKEN_TYPE",
        "ATTR_GEN_AI_PROVIDER_NAME",
    }
)


def test_export_set_equals_spec_5_1() -> None:
    """``__all__`` carries exactly the spec §5.1 symbols — nothing more, nothing less."""
    exported = set(observability.__all__)
    extra = exported - EXPECTED_EXPORTS
    missing = EXPECTED_EXPORTS - exported
    assert not extra, f"exports not in spec §5.1: {sorted(extra)}"
    assert not missing, f"spec §5.1 exports missing from __all__: {sorted(missing)}"
    assert len(observability.__all__) == len(exported), "__all__ contains duplicates"


def test_exported_symbols_are_resolvable() -> None:
    """Every expected name is a real attribute of the package (not just listed)."""
    unresolvable = [name for name in EXPECTED_EXPORTS if not hasattr(observability, name)]
    assert not unresolvable, f"__all__ names not importable from a2a_t.observability: {sorted(unresolvable)}"


def _import_roots(node: ast.AST) -> list[tuple[str, int]]:
    """(imported-root-module, lineno) pairs for one Import/ImportFrom node."""
    if isinstance(node, ast.Import):
        return [(alias.name, node.lineno) for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        if node.level:
            return []  # relative import — never an a2a.* absolute import
        return [(node.module or "", node.lineno)]
    return []


def test_no_a2a_imports_outside_decorator_exemption() -> None:
    """AST-walk the whole observability tree; ``a2a``/``a2a.*`` only in exempt decorators."""
    # Anchor sanity: fail loudly (never vacuously) if the tree is missing/misanchored.
    assert _PACKAGE_DIR.is_dir(), f"anchored package dir not found: {_PACKAGE_DIR}"
    assert (_PACKAGE_DIR / "__init__.py").is_file(), f"not the observability package: {_PACKAGE_DIR}"
    assert (_PACKAGE_DIR / "span.py").is_file(), f"core module span.py missing under {_PACKAGE_DIR}"

    violations: list[str] = []
    py_files = sorted(_PACKAGE_DIR.rglob("*.py"))
    assert len(py_files) >= 10, f"unexpectedly few files under {_PACKAGE_DIR}: {len(py_files)}"
    for py_file in py_files:
        rel = py_file.relative_to(_PACKAGE_DIR).as_posix()
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            for root, lineno in _import_roots(node):
                if root.split(".")[0] == "a2a" and rel not in _A2A_IMPORT_EXEMPT:
                    violations.append(f"{rel}:{lineno} imports {root!r}")
    assert not violations, f"a2a imports outside the decorator exemption {_A2A_IMPORT_EXEMPT}: {violations}"


def test_package_imports_without_a2a_sdk() -> None:
    """``import a2a_t.observability`` succeeds with a2a import-blocked (subprocess)."""
    code = (
        "import sys\n"
        "sys.modules['a2a'] = None\n"  # None entry → any a2a import raises ImportError
        "import a2a_t.observability as obs\n"
        "assert len(obs.__all__) == 34, obs.__all__\n"
        "assert obs.setup is not None and obs.current_span() is None\n"
        "print('a2a-blocked import ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, (
        f"import a2a_t.observability failed without a2a-sdk:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "a2a-blocked import ok" in result.stdout
