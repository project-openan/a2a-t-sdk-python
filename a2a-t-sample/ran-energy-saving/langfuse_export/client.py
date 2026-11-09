"""HTTP client: Basic auth, bounded retries, cursor pagination, error mapping."""

from __future__ import annotations

import base64
import os
import random
import time
import warnings
from contextlib import contextmanager
from typing import Any, Callable, Iterator

import requests
from urllib3.exceptions import InsecureRequestWarning

from . import __version__
from .config import proxy_label
from .models import RequestRecord

RETRY_STATUS = {429, 500, 502, 503, 504}
RETRY_AFTER_CAP = 60.0
USER_AGENT = f"langfuse-export/{__version__}"


class ExportError(Exception):
    exit_code = 1


class ConfigError(ExportError):
    exit_code = 1


class AuthError(ExportError):
    exit_code = 3


class NotFoundError(ExportError):
    exit_code = 2


class NetworkError(ExportError):
    exit_code = 4


class ApiError(ExportError):
    exit_code = 1

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class MediaTooLarge(ApiError):
    """The media file is bigger than --max-media-bytes; skip it, never retry."""


class LangfuseClient:
    def __init__(
        self,
        host: str,
        public_key: str,
        secret_key: str,
        connect_timeout: float = 5.0,
        read_timeout: float = 30.0,
        media_timeout: float = 120.0,
        max_attempts: int = 3,
        verbose: bool = False,
        log: Callable[[str], None] | None = None,
        proxy: str | None = None,
        no_proxy: bool = False,
        cacert: str | None = None,
        insecure: bool = False,
    ) -> None:
        self.host = host.rstrip("/")
        self.proxy = proxy
        # Explicit proxies are applied to every host and every request (API and
        # media). They are passed per request as well as stored on the session,
        # because requests merges env HTTP(S)_PROXY into the request-level map and
        # that would otherwise win over a session-level dict. `no_proxy`
        # (--no-proxy) is the only way to switch proxies off entirely.
        self.proxies: dict[str, str] | None = {"http": proxy, "https": proxy} if proxy else None
        self.no_proxy = no_proxy
        self.cacert = cacert
        self.insecure = insecure
        # verify: insecure > custom CA bundle > default trust store.
        self.verify: bool | str = False if insecure else (cacert or True)
        self.session = requests.Session()
        if no_proxy:
            # --no-proxy: drop env proxies AND env CA overrides; only --cacert can
            # supply a trust store in this mode (documented in README/design).
            self.session.trust_env = False
        if self.proxies:
            self.session.proxies.update(self.proxies)
        self.session.verify = self.verify
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self.session.headers.update(
            {
                "Authorization": f"Basic {token}",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            }
        )
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.media_timeout = media_timeout
        self.max_attempts = max_attempts
        self.verbose = verbose
        self.log = log
        self.records: list[RequestRecord] = []
        if insecure:
            # One ASCII notice replaces urllib3's per-request InsecureRequestWarning
            # (process-level filter, so media downloads through a fresh session obey it).
            warnings.filterwarnings("ignore", category=InsecureRequestWarning)

    def close(self) -> None:
        """Release the authenticated session (sample patch P2)."""
        self.session.close()

    # -- low level ---------------------------------------------------------
    def _sleep_for_attempt(self, attempt: int, retry_after: str | None) -> None:
        if retry_after:
            try:
                delay = min(float(retry_after), RETRY_AFTER_CAP)
            except ValueError:
                delay = 0.0
        else:
            delay = (2 ** (attempt - 1)) * random.uniform(0.8, 1.2)
        if delay > 0:
            time.sleep(delay)

    def get_json(self, path: str, params: dict[str, Any] | None = None, timeout: tuple[float, float] | None = None) -> dict[str, Any]:
        url = self.host + path
        timeout = timeout or (self.connect_timeout, self.read_timeout)
        started = time.monotonic()
        last_exc: Exception | None = None
        status: int | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                request_kwargs: dict[str, Any] = {"params": params, "timeout": timeout}
                if self.proxies:
                    # Explicit proxy must win over env HTTP(S)_PROXY. requests merges
                    # the environment into the *request-level* proxies (and that wins
                    # over a session-level dict), so pass them per request here too.
                    # trust_env stays True so --cacert/--no-proxy semantics are unchanged.
                    request_kwargs["proxies"] = dict(self.proxies)
                if self.verify is not True:
                    # A request-level verify beats an env REQUESTS_CA_BUNDLE, so
                    # --insecure/--cacert really win over the environment (a plain
                    # session.verify can be overridden while trust_env is on).
                    request_kwargs["verify"] = self.verify
                resp = self.session.get(url, **request_kwargs)
                status = resp.status_code
                if status in RETRY_STATUS and attempt < self.max_attempts:
                    if self.verbose:
                        self._log(f"retry {status} {path} attempt={attempt}")
                    self._sleep_for_attempt(attempt, resp.headers.get("Retry-After"))
                    continue
                self.records.append(
                    RequestRecord(endpoint=path, status=status, duration_ms=int((time.monotonic() - started) * 1000))
                )
                if status in RETRY_STATUS:
                    # The last attempt of a retryable status is a transport-level
                    # failure: exit code 4, message carries the final status code.
                    raise NetworkError(
                        f"retry exhausted for {path}: last HTTP status {status} "
                        f"after {self.max_attempts} attempts"
                    )
                if status == 407:
                    # A plain-HTTP proxy answered 407 itself. (An HTTPS target behind
                    # an HTTP proxy gets a CONNECT rejection instead, which requests
                    # raises as ProxyError -> NetworkError/exit 4.)
                    raise AuthError(
                        "proxy authentication failed (HTTP 407) via proxy "
                        f"{proxy_label(self.proxy) or 'from the environment'}; "
                        "check the proxy credentials or the proxy policy for this host"
                    )
                if status in (401, 403):
                    raise AuthError(f"authentication failed (HTTP {status}) for {self.host}; check keys and region")
                if status == 404:
                    raise NotFoundError(f"not found: {path}")
                if status >= 400:
                    snippet = resp.text[:300].replace("\n", " ")
                    raise ApiError(f"HTTP {status} for {path}: {snippet}", status=status)
                return resp.json()
            except (requests.Timeout, requests.ConnectionError, requests.exceptions.ChunkedEncodingError) as exc:
                last_exc = exc
                if attempt < self.max_attempts:
                    if self.verbose:
                        self._log(f"retry {type(exc).__name__} {path} attempt={attempt}")
                    self._sleep_for_attempt(attempt, None)
                    continue
                self.records.append(
                    RequestRecord(
                        endpoint=path,
                        status=None,
                        duration_ms=int((time.monotonic() - started) * 1000),
                        error=type(exc).__name__,
                    )
                )
                raise self._network_error(path, exc) from exc
        if last_exc is not None:
            raise self._network_error(path, last_exc) from last_exc
        raise NetworkError(f"request failed for {self.host}{path}")

    def _network_error(self, path: str, exc: BaseException) -> NetworkError:
        """Map a transport failure to exit code 4 without echoing proxy secrets."""
        label = proxy_label(self.proxy)
        via = f" (proxy={label})" if label else ""
        return NetworkError(f"network error for {self.host}{path}: {type(exc).__name__}{via}")

    @contextmanager
    def _open_media_stream(
        self,
        url: str,
        proxies: dict[str, str] | None,
        verify: bool | str,
    ) -> Iterator[Any]:
        """Open the stream for a presigned media URL, never the authenticated session.

        Only a User-Agent is sent. The same proxies/verify as the API client are
        applied explicitly here, because this request is deliberately not made
        through ``self.session``:

        * default: the plain module-level ``requests.get`` (0.1.0 call shape), so
          env proxies/CA behave exactly as before;
        * ``--no-proxy``: a fresh ``requests.Session`` with ``trust_env=False`` is
          required to make the media host ignore env proxies too (same CA rule as
          the API: only ``--cacert`` counts).
        """
        timeout = (self.connect_timeout, self.media_timeout)
        proxy_map = dict(proxies) if proxies else None
        if not self.no_proxy:
            with requests.get(
                url,
                stream=True,
                timeout=timeout,
                headers={"User-Agent": USER_AGENT},
                proxies=proxy_map,
                verify=verify,
            ) as resp:
                yield resp
            return
        session = requests.Session()
        session.trust_env = False  # requests.Session() takes no trust_env argument
        session.headers.clear()  # credentials-free: User-Agent only
        session.headers["User-Agent"] = USER_AGENT
        try:
            with session.get(
                url,
                stream=True,
                timeout=timeout,
                proxies=proxy_map,
                verify=verify,
            ) as resp:
                yield resp
        finally:
            session.close()

    def download(
        self,
        url: str,
        dest,
        max_bytes: int,
        label: str = "media-download",
        proxies: dict[str, str] | None = None,
        verify: bool | str | None = None,
    ) -> int:
        """Stream a presigned `url` to `dest`; returns bytes written.

        The request is deliberately made without the session that carries the
        Langfuse Basic Auth header: presigned storage URLs must never receive our
        credentials. Only a User-Agent is sent. Any failure removes `dest` so a
        half-written file can never reach the manifest.

        `proxies`/`verify` default to the client settings and are always applied
        to the media request explicitly (a proxy/TLS setting applies to the media
        host too; ``--no-proxy`` also applies here).

        Raises:
            MediaTooLarge: declared or streamed size exceeds `max_bytes`.
            ApiError: HTTP 401/403/4xx/5xx (status is preserved).
            NetworkError: transport failure (ProxyError included).
        """
        if proxies is None:
            proxies = self.proxies
        if verify is None:
            verify = self.verify
        started = time.monotonic()
        written = 0
        status: int | None = None
        error_name: str | None = None
        ok = False
        try:
            with self._open_media_stream(url, proxies, verify) as resp:
                status = resp.status_code
                if status >= 400:
                    raise ApiError(f"media download failed: HTTP {status}", status=status)
                declared = resp.headers.get("Content-Length")
                if declared and str(declared).isdigit() and int(declared) > max_bytes:
                    raise MediaTooLarge(f"media exceeds {max_bytes} bytes (declared {declared})")
                with open(dest, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        written += len(chunk)
                        if written > max_bytes:
                            raise MediaTooLarge(f"media exceeds {max_bytes} bytes")
                        fh.write(chunk)
            ok = True
            return written
        except requests.RequestException as exc:
            error_name = type(exc).__name__
            raise NetworkError(f"media download network error ({type(exc).__name__})") from exc
        except ExportError as exc:
            error_name = type(exc).__name__
            raise
        except BaseException:
            error_name = "Error"
            raise
        finally:
            if not ok:
                try:
                    os.unlink(dest)
                except OSError:
                    pass
            self.records.append(
                RequestRecord(
                    endpoint=label,
                    status=status,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    error=error_name,
                )
            )

    # -- pagination --------------------------------------------------------
    def iter_cursor_pages(
        self,
        path: str,
        params: dict[str, Any],
        max_pages: int,
        cursor_key: str = "cursor",
    ) -> Iterator[dict[str, Any]]:
        cursor: str | None = None
        for _ in range(max_pages):
            page_params = dict(params)
            if cursor:
                page_params["cursor"] = cursor
            body = self.get_json(path, page_params)
            yield body
            cursor = (body.get("meta") or {}).get(cursor_key)
            if not cursor:
                return

    def _log(self, message: str) -> None:
        if self.log:
            self.log(message)
