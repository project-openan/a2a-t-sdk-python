"""Command line interface and export orchestration."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from . import __version__
from .backend import Backend, select_backend
from .client import (
    ApiError,
    AuthError,
    ExportError,
    LangfuseClient,
    MediaTooLarge,
    NetworkError,
    NotFoundError,
)
from .config import (
    Config,
    ConfigError as SettingsError,
    load_config,
    network_notices,
    network_summary,
    proxy_label,
)
from .metrics import build_metrics, select_run_id
from .models import MediaAsset, MediaRef, NetworkInfo, TraceBundle
from .normalize import (
    apply_legacy_trace,
    build_tree,
    derive_summary,
    detect_metadata_truncation,
    normalize_scores,
)
from .render import media_extension, render_transcript
from .table import build_table
from .writer import build_manifest, make_zip, write_json, write_jsonl, write_text, write_xlsx

TRACE_ID_RE = re.compile(r"^[0-9A-Za-z_-]{1,128}$")
DEFAULT_MAX_MEDIA_BYTES = 20 * 1024 * 1024
ROUND_MODES = ("legacy", "decision")


def _round_mode(value: str) -> str:
    """Validate a round mode before it reaches argparse (sample patch P9c).

    ``argparse`` answers an invalid ``--round-mode`` with ``SystemExit``, which is a
    ``BaseException``: it escapes a caller's ``except Exception`` and can take down a
    background job. The output-free wrappers therefore reject the value first.
    """
    if value not in ROUND_MODES:
        raise ValueError(f"round_mode must be one of {', '.join(ROUND_MODES)}: {value!r}")
    return value


def safe_print(value: str, file: Any = None) -> None:
    try:
        print(value, file=file)
    except UnicodeEncodeError:  # GBK console
        print(value.encode("ascii", "replace").decode("ascii"), file=file)


def parse_trace_input(value: str) -> tuple[str, str | None]:
    text = (value or "").strip()
    if not text:
        raise SettingsError("empty trace id")
    if text.startswith("http://") or text.startswith("https://"):
        parts = urlsplit(text)
        segments = [unquote(segment) for segment in parts.path.split("/") if segment]
        trace_id = None
        for index, segment in enumerate(segments):
            if segment in ("traces", "trace") and index + 1 < len(segments):
                trace_id = segments[index + 1]
                break
        if trace_id is None and segments:
            trace_id = segments[-1]
        if not trace_id or not TRACE_ID_RE.match(trace_id):
            raise SettingsError(f"cannot parse trace id from URL: {value}")
        return trace_id, f"{parts.scheme}://{parts.netloc}"
    if not TRACE_ID_RE.match(text):
        raise SettingsError(f"invalid trace id: {value!r} (expected 1-128 of [0-9A-Za-z_-])")
    return text, None


def safe_slug(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]", "_", value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="langfuse-export",
        description="Export one Langfuse trace (raw JSON + manifest + transcript.md).",
    )
    parser.add_argument(
        "trace_id",
        nargs="+",
        help="one or more trace ids / Langfuse UI URLs (space or comma separated)",
    )
    parser.add_argument("--host", help="Langfuse host (default: env/.env, else EU cloud)")
    parser.add_argument("--public-key", help="public key (default: LANGFUSE_PUBLIC_KEY)")
    parser.add_argument("--secret-key", help="secret key (default: LANGFUSE_SECRET_KEY)")
    parser.add_argument(
        "--proxy",
        help="HTTP(S) proxy URL, e.g. http://proxy.example:8080 (default: LANGFUSE_PROXY, else env proxy vars)",
    )
    parser.add_argument(
        "--no-proxy",
        action="store_true",
        default=None,
        help="never use a proxy: ignore env HTTP(S)_PROXY/NO_PROXY (default: LANGFUSE_NO_PROXY)",
    )
    parser.add_argument(
        "--cacert",
        help="PEM bundle that replaces the default CA store (default: LANGFUSE_CACERT)",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        default=None,
        help="disable TLS verification (unsafe: prints a notice; default: LANGFUSE_INSECURE)",
    )
    parser.add_argument("--api-version", choices=["auto", "v4", "v3"], default="auto")
    parser.add_argument("--out", default="exports", help="output root directory (default: ./exports)")
    parser.add_argument("--from", dest="from_time", help="lower bound, ISO-8601 (startTime >= value)")
    parser.add_argument("--to", dest="to_time", help="upper bound, ISO-8601 (startTime < value, exclusive)")
    parser.add_argument("--expand-metadata", help="comma separated top-level metadata keys to expand")
    parser.add_argument("--no-media", action="store_true", help="skip media downloads")
    parser.add_argument("--max-media-bytes", type=int, default=DEFAULT_MAX_MEDIA_BYTES)
    parser.add_argument(
        "--with-session", action="store_true", help="also dump all observations of the session (raw only)"
    )
    parser.add_argument("--zip", action="store_true", help="also produce <export>.zip next to the directory")
    parser.add_argument("--strict", action="store_true", help="exit 1 when warnings were emitted")
    parser.add_argument(
        "--run-id",
        help="select one attempt when the trace contains both a blocked preflight and a retry",
    )
    parser.add_argument(
        "--metrics",
        action="store_true",
        help="also write metrics.json (run-level A2A metrics derived from observations)",
    )
    parser.add_argument(
        "--table",
        action="store_true",
        help="also write metrics.xlsx (Issue AF-AW table); implies --metrics",
    )
    parser.add_argument(
        "--round-mode",
        choices=["legacy", "decision"],
        default="legacy",
        help="negotiation round model: legacy (default) or decision (PROPOSE-DECISION pairs, for A2A/pure)",
    )
    parser.add_argument("--json", action="store_true", help="print a machine readable JSON result to stdout")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _client_from(config: Config, args: argparse.Namespace) -> LangfuseClient:
    connect = float(os.environ.get("LANGFUSE_CONNECT_TIMEOUT", "5"))
    read = float(os.environ.get("LANGFUSE_READ_TIMEOUT", "30"))
    log = (lambda message: print(message, file=sys.stderr)) if args.verbose else None
    return LangfuseClient(
        host=config.host,
        public_key=config.public_key,
        secret_key=config.secret_key,
        connect_timeout=connect,
        read_timeout=read,
        verbose=args.verbose,
        log=log,
        proxy=config.proxy,
        no_proxy=config.no_proxy,
        cacert=config.cacert,
        insecure=config.insecure,
    )


def _close_client(client: Any) -> None:
    """Close a client if it supports it (test doubles may not) — P2."""
    close = getattr(client, "close", None)
    if close is not None:
        close()


def _discard(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _media_network(client: Any) -> tuple[dict[str, str] | None, Any]:
    """Proxies/verify for media downloads: exactly the API client's settings.

    Media downloads must honour --proxy / --no-proxy / --cacert / --insecure too,
    otherwise the third-party storage host would silently use a different path
    (or a different trust store) than the API.
    """
    return getattr(client, "proxies", None), getattr(client, "verify", True)


def _download_media(
    backend: Backend,
    refs: list[MediaRef],
    media_dir: Path,
    max_bytes: int,
    warnings: list[str],
) -> list[MediaAsset]:
    assets: list[MediaAsset] = []
    seen: set[str] = set()
    proxies, verify = _media_network(backend.client)
    for ref in refs:
        if ref.media_id in seen:
            continue
        seen.add(ref.media_id)
        asset = MediaAsset(media_id=ref.media_id)
        try:
            info = backend.fetch_media(ref.media_id)
        except AuthError:
            raise
        except ExportError as exc:
            asset.status = "failed"
            asset.error = str(exc)
            warnings.append(f"media metadata failed for {ref.media_id}: {type(exc).__name__}")
            assets.append(asset)
            continue
        asset.content_type = info.get("contentType") or ref.mime_type
        length = info.get("contentLength")
        asset.content_length = int(length) if isinstance(length, int) else None
        asset.url_expiry = info.get("urlExpiry")
        url = info.get("url")
        if not url:
            asset.status = "failed"
            asset.error = "no url in media response"
            warnings.append(f"media url missing for {ref.media_id}")
            assets.append(asset)
            continue
        if asset.content_length is not None and asset.content_length > max_bytes:
            # Declared size already exceeds the cap: never request the presigned URL,
            # never refresh the metadata, never retry.
            asset.status = "skipped"
            asset.error = f"contentLength {asset.content_length} exceeds limit"
            warnings.append(f"media skipped for {ref.media_id}: exceeds {max_bytes} bytes")
            assets.append(asset)
            continue

        filename = safe_slug(ref.media_id) + media_extension(asset.content_type)
        dest = media_dir / filename
        written: int | None = 0
        refreshed = False
        while True:
            try:
                written = backend.client.download(
                    url,
                    dest,
                    max_bytes=max_bytes,
                    label=f"media/{ref.media_id}",
                    proxies=proxies,
                    verify=verify,
                )
                break
            except MediaTooLarge:
                # Too large: skip semantics, no refresh and no retry. The declared
                # Content-Length may be absent here, so report the cap only.
                _discard(dest)
                asset.status = "skipped"
                asset.error = f"streamed size exceeds {max_bytes} bytes"
                warnings.append(f"media skipped for {ref.media_id}: exceeds {max_bytes} bytes")
                written = None
                break
            except ApiError as exc:
                _discard(dest)
                # A 401/403 from a presigned URL usually means it expired: refresh the
                # media metadata exactly once and retry exactly once.
                if exc.status in (401, 403) and not refreshed:
                    refreshed = True
                    try:
                        refreshed_info = backend.fetch_media(ref.media_id)
                    except AuthError:
                        raise
                    except ExportError:
                        refreshed_info = None
                    if refreshed_info:
                        if refreshed_info.get("urlExpiry"):
                            asset.url_expiry = refreshed_info.get("urlExpiry")
                        refreshed_url = refreshed_info.get("url")
                        if refreshed_url:
                            url = refreshed_url
                            continue
                asset.status = "failed"
                asset.error = str(exc)
                warnings.append(f"media download failed for {ref.media_id}: {type(exc).__name__}")
                written = None
                break
            except NetworkError as exc:
                _discard(dest)
                asset.status = "failed"
                asset.error = str(exc)
                warnings.append(f"media download failed for {ref.media_id}: network error")
                written = None
                break

        if written:
            asset.status = "downloaded"
            asset.local_path = f"media/{filename}"
            if asset.content_length is None:
                asset.content_length = written
        else:
            _discard(dest)
            if asset.status == "pending":
                asset.status = "failed"
                asset.error = "empty media file"
                warnings.append(f"media download failed for {ref.media_id}: empty file")
        assets.append(asset)
    return assets


@dataclass
class _Collected:
    """Fetched and derived data shared by ``run_export`` and ``export_metrics``."""

    trace_id: str
    backend: Backend
    client: LangfuseClient
    rows: list[dict[str, Any]]
    legacy_trace: dict[str, Any] | None
    scores_rows: list[dict[str, Any]]
    roots: list[Any]
    observations: list[Any]
    scores: list[Any]
    summary: Any
    warnings: list[str]
    metadata_truncated: bool
    assets: list[MediaAsset]
    session_rows: list[dict[str, Any]]
    project_id: str | None
    project_name: str | None
    truncated: bool


def _collect(
    trace_id: str,
    config: Config,
    args: argparse.Namespace,
    *,
    url_host: str | None,
    export_dir: Path | None,
    emit: bool,
) -> _Collected:
    """Fetch a trace and derive everything the export and the metrics row share.

    ``export_dir`` is ``None`` for the metrics-only path: no directory is created
    and no media is downloaded, so nothing is written to disk.
    """
    warnings: list[str] = []
    if config.host.rstrip("/").endswith("/api/public"):
        config.host = config.host.rstrip("/")[: -len("/api/public")]
        warnings.append("host normalised: dropped /api/public suffix")
    if config.host_source == "default":
        warnings.append(f"host not configured; using default EU host {config.host} (set LANGFUSE_HOST or --host)")
    if url_host and url_host.rstrip("/") != config.host.rstrip("/"):
        warnings.append(f"URL host {url_host} differs from configured host {config.host}; using configured host")

    client = _client_from(config, args)
    if not emit:
        # P1: the verbose per-request logger is part of stdout/stderr output.
        client.log = None
    try:
        backend = select_backend(client, config.api_version)

        expand = [item.strip() for item in (args.expand_metadata or "").split(",") if item.strip()]
        rows = backend.fetch_observations(trace_id, args.from_time, args.to_time, expand or None)

        legacy_trace = None
        if backend.kind == "v3":
            legacy_trace = backend.fetch_legacy_trace(trace_id)
        elif not rows:
            raise NotFoundError(
                f"trace not found or no data yet: {trace_id} (v2 endpoints may lag 10-15 minutes for older SDK/OTel data)"
            )

        scores_rows: list[dict[str, Any]] = []
        try:
            scores_rows = backend.fetch_scores(trace_id)
        except AuthError:
            raise
        except ExportError as exc:
            warnings.append(f"scores fetch failed: {type(exc).__name__}")

        roots, tree_warnings = build_tree(rows, trace_id)
        warnings.extend(tree_warnings)
        scores = normalize_scores(scores_rows)
        observations = [obs for root in roots for obs in _walk(root)]
        summary, summary_warnings = derive_summary(observations, roots, len(scores), trace_id)
        warnings.extend(summary_warnings)
        if legacy_trace:
            apply_legacy_trace(summary, legacy_trace)
        metadata_truncated = detect_metadata_truncation(rows)
        if metadata_truncated:
            warnings.append("metadata may be truncated by the API; use --expand-metadata key1,key2 to expand")
        # shell rows are excluded by build_tree; keep raw rows intact for the archive
        media_refs: list[MediaRef] = []
        for obs in observations:
            media_refs.extend(obs.media_refs)

        assets: list[MediaAsset] = []
        if export_dir is not None:
            export_dir.mkdir(parents=True, exist_ok=False)
            (export_dir / "raw").mkdir()
            media_dir = export_dir / "media"
            if media_refs and not args.no_media:
                media_dir.mkdir()
            if media_refs and not args.no_media:
                assets = _download_media(backend, media_refs, media_dir, args.max_media_bytes, warnings)
                if not any(asset.status == "downloaded" for asset in assets):
                    try:
                        media_dir.rmdir()
                    except OSError:
                        pass
            elif media_refs and args.no_media:
                for ref in media_refs:
                    assets.append(MediaAsset(media_id=ref.media_id, content_type=ref.mime_type, status="skipped"))

        session_rows: list[dict[str, Any]] = []
        if args.with_session and summary.session_id:
            try:
                session_rows = backend.fetch_session_observations(summary.session_id)
            except AuthError:
                raise
            except ExportError as exc:
                warnings.append(f"session fetch failed: {type(exc).__name__}")

        project_id, project_name = backend.fetch_project()

        # Backend degradation warnings (project lookup, session fetch, ...) are appended
        # to `backend.warnings` during the calls above, so sync exactly once here, after
        # every backend call, to make them reach the bundle, manifest, transcript and --strict.
        warnings.extend(backend.warnings)

        truncated = bool(getattr(backend, "truncated", False))
        if truncated:
            warnings.append("page limit reached; the export may be incomplete")
    except BaseException:
        _close_client(client)
        raise

    return _Collected(
        trace_id=trace_id,
        backend=backend,
        client=client,
        rows=rows,
        legacy_trace=legacy_trace,
        scores_rows=scores_rows,
        roots=roots,
        observations=observations,
        scores=scores,
        summary=summary,
        warnings=warnings,
        metadata_truncated=metadata_truncated,
        assets=assets,
        session_rows=session_rows,
        project_id=project_id,
        project_name=project_name,
        truncated=truncated,
    )


def run_export(
    args: argparse.Namespace,
    exported_at: datetime | None = None,
    *,
    config: Config | None = None,
    emit: bool = True,
    print_result: bool = True,
) -> dict[str, Any]:
    raw_trace_id = args.trace_id
    if isinstance(raw_trace_id, (list, tuple)):
        raw_trace_id = raw_trace_id[0] if raw_trace_id else ""
    trace_id, url_host = parse_trace_input(raw_trace_id)
    if config is None:
        config = load_config(
            host=args.host,
            public_key=args.public_key,
            secret_key=args.secret_key,
            api_version=args.api_version,
            proxy=args.proxy,
            no_proxy=args.no_proxy,
            cacert=args.cacert,
            insecure=args.insecure,
        )
    # Network settings always reach the artifacts (defaults when nothing is set);
    # `proxy` is sanitised to host:port and `ca_bundle` keeps only the file name.
    network = NetworkInfo(
        proxy=proxy_label(config.proxy),
        tls_verify=not config.insecure,
        ca_bundle=Path(config.cacert).name if config.cacert else None,
        no_proxy=config.no_proxy,
    )
    notices = network_notices(config.insecure, config.cacert)
    if emit and config.insecure:
        # Never weaken TLS silently: one ASCII line on stderr (the remaining
        # notices stay in the artifacts). --quiet only trims stdout, so this
        # notice is still printed, and it is emitted exactly once per run.
        safe_print(
            "notice: TLS verification disabled (--insecure); exported data may be tampered with",
            file=sys.stderr,
        )
    if emit and args.verbose:
        # One credential-free line; request details follow on stderr as usual.
        safe_print(network_summary(config), file=sys.stderr)

    exported_at = exported_at or datetime.now(timezone.utc).astimezone()
    out_root = Path(args.out)
    export_dir = out_root / f"{safe_slug(trace_id)}_{exported_at.strftime('%Y%m%d-%H%M%S')}"

    collected: _Collected | None = None
    try:
        collected = _collect(trace_id, config, args, url_host=url_host, export_dir=export_dir, emit=emit)
        warnings = collected.warnings
        backend = collected.backend
        observations = collected.observations

        bundle = TraceBundle(
            trace_id=trace_id,
            backend=backend.kind,
            host=config.host,
            api_version=backend.api_version,
            host_source=config.host_source,
            summary=collected.summary,
            observations=observations,
            roots=collected.roots,
            scores=collected.scores,
            warnings=warnings,
            request_log=collected.client.records,
            raw_observations=collected.rows,
            raw_scores=collected.scores_rows,
            media_assets=collected.assets,
            session_rows=collected.session_rows,
            legacy_trace=collected.legacy_trace,
            project_id=collected.project_id,
            project_name=collected.project_name,
            metadata_truncated=collected.metadata_truncated,
            truncated=collected.truncated,
            network=network,
            notices=notices,
        )

        write_text(export_dir / "transcript.md", render_transcript(bundle, exported_at))
        write_jsonl(export_dir / "raw" / "observations.jsonl", collected.rows)
        write_json(export_dir / "raw" / "scores.json", collected.scores_rows)
        if collected.legacy_trace:
            write_json(export_dir / "raw" / "legacy-trace.json", collected.legacy_trace)
        if collected.session_rows:
            write_jsonl(export_dir / "raw" / "session-observations.jsonl", collected.session_rows)
        write_json(export_dir / "trace-summary.json", collected.summary.to_dict())
        selected_run_id = getattr(args, "run_id", None) or select_run_id(collected.rows)
        metrics = (
            build_metrics(
                collected.rows,
                trace_id=trace_id,
                summary=collected.summary,
                run_id=selected_run_id,
                round_mode=getattr(args, "round_mode", "legacy"),
            )
            if (getattr(args, "metrics", False) or getattr(args, "table", False))
            else None
        )
        if metrics is not None:
            write_json(export_dir / "metrics.json", metrics)
        table_path: str | None = None
        if getattr(args, "table", False) and metrics is not None:
            headers, table_rows = build_table(metrics)
            write_xlsx(export_dir / "metrics.xlsx", headers, table_rows)
            table_path = "metrics.xlsx"

        manifest = build_manifest(export_dir, bundle, exported_at, config.host_source)
        write_json(export_dir / "manifest.json", manifest)

        archive = make_zip(export_dir) if args.zip else None

        result = {
            "traceId": trace_id,
            "backend": backend.kind,
            "exportDir": str(export_dir),
            "observations": len(observations),
            "scores": len(collected.scores),
            "media": [asset.local_path for asset in collected.assets if asset.local_path],
            "network": network.to_dict(),
            "notices": notices,
            "metrics": metrics,
            "table": table_path,
            "warnings": warnings,
            "zip": str(archive) if archive else None,
        }
        if emit and print_result:
            if args.quiet:
                # --quiet means exactly one line: the export directory path. It wins over
                # --json so scripts can rely on `dir=$(langfuse-export ... --quiet)`.
                safe_print(str(export_dir))
            elif args.json:
                safe_print(json.dumps(result, ensure_ascii=True, indent=2))
            else:
                safe_print(f"exported {trace_id} -> {export_dir}")
                safe_print(
                    f"  observations={len(observations)} scores={len(collected.scores)} warnings={len(warnings)}"
                )
                for warning in warnings:
                    safe_print(f"  warning: {warning}")
                if archive:
                    safe_print(f"  zip: {archive}")
        return result
    finally:
        if collected is not None:
            _close_client(collected.client)


def export_metrics(
    config: Config,
    trace_id: str,
    *,
    run_id: str | None = None,
    round_mode: str = "legacy",
) -> dict[str, Any]:
    """Return ``{"metrics": ..., "warnings": [...]}`` without writing or printing (P3)."""
    round_mode = _round_mode(round_mode)
    trace_id, url_host = parse_trace_input(trace_id)
    args = build_parser().parse_args([trace_id])
    collected = _collect(trace_id, config, args, url_host=url_host, export_dir=None, emit=False)
    try:
        return {
            "metrics": build_metrics(
                collected.rows,
                trace_id=trace_id,
                summary=collected.summary,
                run_id=run_id or select_run_id(collected.rows),
                round_mode=round_mode,
            ),
            "warnings": collected.warnings,
        }
    finally:
        _close_client(collected.client)


def export_full(
    config: Config,
    trace_id: str,
    out_root: str | Path,
    *,
    zip_file: bool = True,
    run_id: str | None = None,
    round_mode: str = "legacy",
) -> dict[str, Any]:
    """Full export for the server: no output, caller-owned ``Config`` (P3)."""
    round_mode = _round_mode(round_mode)
    argv = [trace_id, "--out", str(out_root), "--metrics", "--no-media"]
    if run_id:
        argv.extend(["--run-id", run_id])
    if round_mode != "legacy":
        argv.extend(["--round-mode", round_mode])
    if zip_file:
        argv.append("--zip")
    args = build_parser().parse_args(argv)
    return run_export(args, config=config, emit=False)


def _trace_ids(raw: list[str] | str) -> list[str]:
    values = [raw] if isinstance(raw, str) else list(raw)
    ids: list[str] = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part and part not in ids:
                ids.append(part)
    return ids


def _error_exit_code(exc: BaseException) -> int:
    if isinstance(exc, AuthError):
        return 3
    if isinstance(exc, NotFoundError):
        return 2
    if isinstance(exc, NetworkError):
        return 4
    if isinstance(exc, SettingsError):
        return 1
    if isinstance(exc, ExportError):
        return 1
    return 1


def run_batch_export(
    args: argparse.Namespace,
    trace_ids: list[str],
    exported_at: datetime | None = None,
) -> dict[str, Any]:
    """Export several traces into one batch directory and (optionally) one table."""
    exported_at = exported_at or datetime.now(timezone.utc).astimezone()
    batch_dir = Path(args.out) / f"batch_{exported_at.strftime('%Y%m%d-%H%M%S')}"
    batch_dir.mkdir(parents=True, exist_ok=True)

    exports: list[str] = []
    metrics_list: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    warnings: list[str] = []
    exit_code = 0
    for trace_id in trace_ids:
        child = copy.copy(args)
        child.trace_id = trace_id
        child.out = str(batch_dir)
        # The batch owns stdout/stderr; per-trace results are summarised below.
        child.quiet = True
        child.json = False
        try:
            result = run_export(child, exported_at=exported_at, print_result=False)
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # report and continue so one bad trace cannot kill the table
            errors.append({"traceId": trace_id, "error": f"{type(exc).__name__}: {exc}"})
            code = _error_exit_code(exc)
            exit_code = exit_code or code
            continue
        exports.append(result["exportDir"])
        warnings.extend(result.get("warnings") or [])
        if result.get("metrics"):
            metrics_list.append(result["metrics"])

    table_path: str | None = None
    if getattr(args, "table", False) and metrics_list:
        headers, rows = build_table(metrics_list)
        write_xlsx(batch_dir / "table.xlsx", headers, rows)
        table_path = str(batch_dir / "table.xlsx")

    summary = {
        "batchDir": str(batch_dir),
        "table": table_path,
        "exports": exports,
        "errors": errors,
        "warnings": warnings,
        "exitCode": exit_code,
    }
    write_json(batch_dir / "batch.json", summary)

    if args.quiet:
        safe_print(str(batch_dir))
    elif args.json:
        safe_print(json.dumps(summary, ensure_ascii=True, indent=2))
    else:
        safe_print(f"exported {len(exports)}/{len(trace_ids)} traces -> {batch_dir}")
        for path in exports:
            safe_print(f"  export: {path}")
        for error in errors:
            safe_print(f"  error: {error['traceId']}: {error['error']}")
        if table_path:
            safe_print(f"  table: {table_path}")
    return summary


def _walk(obs) -> list:
    values = [obs]
    for child in obs.children:
        values.extend(_walk(child))
    return values


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    trace_ids = _trace_ids(args.trace_id)
    if not trace_ids:
        print("error: no trace id given", file=sys.stderr)
        return 1
    try:
        if len(trace_ids) == 1:
            args.trace_id = trace_ids[0]
            result = run_export(args)
        else:
            result = run_batch_export(args, trace_ids)
    except SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except NotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except NetworkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4
    except ExportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    if result.get("exitCode"):
        return int(result["exitCode"])
    if args.strict and result["warnings"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
