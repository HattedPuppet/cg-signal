"""Small, GET-only HTTP client with DNS-rebinding-safe address pinning.

The desktop application consumes URLs configured by the user and URLs found in
RSS documents.  This transport deliberately avoids :mod:`urllib.request` so
that proxy environment variables and a second hostname lookup cannot bypass
the address policy.  Resolver and connection-factory seams are intentionally
small so tests can exercise the policy without opening network sockets.
"""

from __future__ import annotations

from dataclasses import dataclass
import email.message
import http.client
import inspect
import ipaddress
import queue
import socket
import ssl
import threading
import time
import urllib.parse
from typing import Any, Callable, Mapping


MAX_REDIRECTS = 5


class SafeHttpError(OSError):
    """Raised when a URL, address, redirect, or response violates policy."""


class ResponseTooLargeError(SafeHttpError):
    """The response body is larger than the caller's explicit cap."""


@dataclass(frozen=True)
class SafeHttpResponse:
    status: int
    headers: email.message.Message
    body: bytes
    final_url: str


Resolver = Callable[..., list[tuple[int, int, int, str, tuple[Any, ...]]]]
ConnectionFactory = Callable[..., Any]


def _is_global_address(value: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return whether *value* is a globally routable address.

    ``is_global`` already excludes private, loopback, link-local, multicast,
    unspecified, reserved, documentation, and CGNAT ranges on supported
    Python versions.  IPv4-mapped IPv6 addresses need an explicit check: an
    IPv6 object may otherwise inherit surprising classification behaviour.
    """

    mapped = getattr(value, "ipv4_mapped", None)
    if mapped is not None:
        return bool(mapped.is_global)
    return bool(value.is_global)


def _authority_parts(url: str) -> tuple[urllib.parse.SplitResult, str, int, bool]:
    """Parse and validate URL syntax, returning split URL/host/port/explicit-port."""

    if not isinstance(url, str) or len(url) > 8192:
        raise SafeHttpError("URL must be a bounded HTTP or HTTPS URL.")
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise SafeHttpError("Malformed URL authority.") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise SafeHttpError("Only HTTP and HTTPS URLs are supported.")
    try:
        username, password = parsed.username, parsed.password
    except ValueError as exc:
        raise SafeHttpError("Malformed URL authority.") from exc
    if not parsed.netloc or username is not None or password is not None:
        raise SafeHttpError("URL userinfo and missing authorities are not supported.")
    if any(ord(character) < 32 or character.isspace() for character in parsed.netloc):
        raise SafeHttpError("Malformed URL authority.")
    if parsed.netloc.endswith(":"):
        raise SafeHttpError("Malformed URL authority or invalid port.")
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise SafeHttpError("Malformed URL authority or invalid port.") from exc
    if not hostname:
        raise SafeHttpError("URL host is required.")
    # Zone-scoped IPv6 literals are local-interface selectors, not routable
    # authorities, and should never reach a resolver or dialer.
    if "%" in hostname:
        raise SafeHttpError("Zone-scoped IPv6 addresses are not supported.")
    try:
        host_idna = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise SafeHttpError("Malformed URL hostname.") from exc
    if not host_idna or any(character in host_idna for character in "/\\?#@"):
        raise SafeHttpError("Malformed URL hostname.")
    if port is None:
        port = 443 if parsed.scheme.lower() == "https" else 80
        explicit_port = False
    else:
        explicit_port = True
        if not 1 <= port <= 65535:
            raise SafeHttpError("URL port is out of range.")
    # An unbracketed IPv6 literal is rejected by urlsplit.hostname in many
    # cases, but this explicit check also catches unusual malformed forms.
    if ":" in host_idna and not (parsed.netloc.startswith("[") and "]" in parsed.netloc):
        raise SafeHttpError("IPv6 authorities must be bracketed.")
    return parsed, host_idna, port, explicit_port


def _host_header(parsed: urllib.parse.SplitResult, hostname: str, port: int, explicit_port: bool) -> str:
    """Build the original authority for the HTTP Host header."""

    # ``hostname`` is the normalized lowercase ASCII/IDNA form returned by
    # ``_authority_parts``.  Use that same value for the wire authority so the
    # Host header cannot contain Unicode or redirect-controlled spelling.
    # IPv6 literals retain the required brackets.
    original = f"[{hostname}]" if ":" in hostname else hostname
    if explicit_port:
        return f"{original}:{port}"
    return original


def _request_target(parsed: urllib.parse.SplitResult) -> str:
    target = parsed.path or "/"
    if parsed.query:
        target += f"?{parsed.query}"
    return target


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection whose socket has already been dialled by the client."""

    def __init__(self, sock: socket.socket, host: str, port: int, timeout: float):
        super().__init__(host, port, timeout=timeout)
        self.sock = sock

    def connect(self) -> None:  # pragma: no cover - defensive; sock is pre-set
        if self.sock is None:
            raise SafeHttpError("Pinned connection was not initialized.")


class SafeHttpClient:
    """GET-only transport enforcing global-address resolution and pinning.

    ``resolver`` receives the same arguments as ``socket.getaddrinfo``.  A
    supplied ``connection_factory`` is called once for each approved address
    with keyword arguments ``scheme``, ``hostname``, ``port``, ``address``
    (the numeric sockaddr returned by the resolver), and ``timeout``.  It must
    return an object implementing ``request``, ``getresponse``, and ``close``
    like :class:`http.client.HTTPConnection`.  This seam keeps policy tests
    deterministic while the production path uses direct sockets.
    """

    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        connection_factory: ConnectionFactory | None = None,
        clock: Callable[[], float] | None = None,
        max_redirects: int = MAX_REDIRECTS,
    ):
        self.resolver = resolver or socket.getaddrinfo
        self.connection_factory = connection_factory
        self.clock = clock or time.monotonic
        self.max_redirects = max(0, int(max_redirects))

    def _remaining(self, deadline: float, wall_deadline: float) -> float:
        return min(deadline - self.clock(), wall_deadline - time.monotonic())

    def _run_with_deadline(
        self,
        function: Callable[[], Any],
        *,
        deadline: float,
        wall_deadline: float,
    ) -> Any:
        """Run a potentially blocking stdlib operation under one wall clock.

        ``getaddrinfo`` has no portable timeout argument.  A daemon worker
        prevents an OS resolver stall from hanging the dashboard process; all
        socket operations additionally receive the remaining timeout directly.
        """

        remaining = self._remaining(deadline, wall_deadline)
        if remaining <= 0:
            raise TimeoutError("Safe HTTP request deadline exceeded.")
        result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

        def run() -> None:
            try:
                result_queue.put((True, function()))
            except BaseException as exc:  # propagate operation failures to caller
                result_queue.put((False, exc))

        threading.Thread(target=run, name="cg-signal-safe-http", daemon=True).start()
        try:
            completed, value = result_queue.get(timeout=remaining)
        except queue.Empty as exc:
            raise TimeoutError("Safe HTTP request deadline exceeded.") from exc
        if not completed:
            raise value
        return value

    def _resolve(
        self,
        hostname: str,
        port: int,
        *,
        deadline: float,
        wall_deadline: float,
    ) -> list[tuple[int, int, int, str, tuple[Any, ...]]]:
        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            literal = None
        if literal is not None and not _is_global_address(literal):
            raise SafeHttpError("Target is a non-global literal address.")
        def resolve() -> list[tuple[int, int, int, str, tuple[Any, ...]]]:
            try:
                results = list(self.resolver(hostname, port, type=socket.SOCK_STREAM))
            except TypeError:
                # A concise deterministic test seam may expose the positional
                # getaddrinfo shape instead of accepting the keyword.
                results = list(self.resolver(hostname, port, 0, socket.SOCK_STREAM))
            return results

        try:
            results = self._run_with_deadline(resolve, deadline=deadline, wall_deadline=wall_deadline)
        except SafeHttpError:
            raise
        except (OSError, socket.gaierror) as exc:
            raise SafeHttpError(f"Unable to resolve {hostname}.") from exc
        if not results:
            raise SafeHttpError("Hostname resolved to no addresses.")
        approved: list[tuple[int, int, int, str, tuple[Any, ...]]] = []
        for result in results:
            try:
                family, socktype, proto, _canonname, sockaddr = result
                numeric = sockaddr[0]
                # A resolver can return an IPv6 scope identifier even when the
                # textual hostname was not scoped.  Reject it as non-global.
                if len(sockaddr) >= 4 and sockaddr[3]:
                    raise SafeHttpError("Scoped addresses are not supported.")
                address = ipaddress.ip_address(numeric.split("%", 1)[0])
            except (ValueError, IndexError, TypeError) as exc:
                raise SafeHttpError("Resolver returned a malformed address.") from exc
            if not _is_global_address(address):
                raise SafeHttpError("Target resolves to a non-global address.")
            approved.append((family, socktype, proto, _canonname, sockaddr))
        return approved

    @staticmethod
    def _call_factory(factory: ConnectionFactory, **values: Any) -> Any:
        """Call a test seam while tolerating concise positional fake factories."""

        try:
            signature = inspect.signature(factory)
        except (TypeError, ValueError):
            return factory(**values)
        parameters = signature.parameters
        if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
            return factory(**values)
        aliases = {
            "scheme": ("scheme",),
            "hostname": ("hostname", "host", "server_hostname", "sni"),
            "port": ("port",),
            "address": ("address", "addr", "sockaddr"),
            "timeout": ("timeout",),
        }
        kwargs: dict[str, Any] = {}
        for key, names in aliases.items():
            for name in names:
                if name in parameters:
                    kwargs[name] = values[key]
                    break
        if kwargs or not parameters:
            return factory(**kwargs)
        positional = [values[key] for key in ("scheme", "hostname", "port", "address", "timeout")]
        if not any(parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in parameters.values()):
            positional = positional[: len(parameters)]
        return factory(*positional)

    def _default_connection(
        self,
        *,
        scheme: str,
        hostname: str,
        port: int,
        address: tuple[Any, ...],
        timeout: float,
        family: int = socket.AF_UNSPEC,
        socktype: int = socket.SOCK_STREAM,
        proto: int = 0,
    ) -> http.client.HTTPConnection:
        if family == socket.AF_UNSPEC:
            family = socket.AF_INET6 if ":" in str(address[0]) else socket.AF_INET
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(timeout)
        try:
            sock.connect(address)
            if scheme == "https":
                context = ssl.create_default_context()
                sock = context.wrap_socket(sock, server_hostname=hostname)
                connection: http.client.HTTPConnection = _PinnedHTTPConnection(sock, hostname, port, timeout)
            else:
                connection = _PinnedHTTPConnection(sock, hostname, port, timeout)
            return connection
        except Exception:
            try:
                sock.close()
            except OSError:
                pass
            raise

    def _request_once(
        self,
        url: str,
        request_headers: Mapping[str, str],
        *,
        deadline: float,
        wall_deadline: float,
        max_bytes: int,
    ) -> SafeHttpResponse:
        parsed, hostname, port, explicit_port = _authority_parts(url)
        addresses = self._resolve(
            hostname,
            port,
            deadline=deadline,
            wall_deadline=wall_deadline,
        )
        last_error: Exception | None = None
        for family, socktype, proto, _canonname, sockaddr in addresses:
            remaining = self._remaining(deadline, wall_deadline)
            if remaining <= 0:
                raise TimeoutError("Safe HTTP request deadline exceeded.")
            connection: Any = None
            try:
                values = {
                    "scheme": parsed.scheme.lower(), "hostname": hostname, "port": port,
                    "address": sockaddr, "timeout": remaining,
                    "family": family, "socktype": socktype, "proto": proto,
                }
                if self.connection_factory is not None:
                    connection = self._run_with_deadline(
                        lambda: self._call_factory(self.connection_factory, **values),
                        deadline=deadline,
                        wall_deadline=wall_deadline,
                    )
                else:
                    connection = self._run_with_deadline(
                        lambda: self._default_connection(**values),
                        deadline=deadline,
                        wall_deadline=wall_deadline,
                    )
                headers = dict(request_headers)
                # A caller-supplied Host could otherwise survive a redirect;
                # every hop gets the authority that was actually validated.
                headers["Host"] = _host_header(parsed, hostname, port, explicit_port)
                self._set_transport_timeout(connection, self._remaining(deadline, wall_deadline))
                self._run_with_deadline(
                    lambda: connection.request("GET", _request_target(parsed), headers=headers),
                    deadline=deadline,
                    wall_deadline=wall_deadline,
                )
                self._set_transport_timeout(connection, self._remaining(deadline, wall_deadline))
                response = self._run_with_deadline(
                    connection.getresponse,
                    deadline=deadline,
                    wall_deadline=wall_deadline,
                )
                # Always read one byte beyond the cap, even when a
                # Content-Length header advertises a larger value.  This
                # handles chunked and dishonest responses uniformly.
                body = self._read_body(
                    connection,
                    response,
                    max_bytes=max_bytes,
                    deadline=deadline,
                    wall_deadline=wall_deadline,
                )
                if len(body) > max_bytes:
                    raise ResponseTooLargeError("HTTP response exceeds the configured size cap.")
                return SafeHttpResponse(
                    status=int(response.status),
                    headers=response.headers,
                    body=body,
                    final_url=url,
                )
            except ResponseTooLargeError:
                raise
            except (OSError, TimeoutError, ssl.SSLError) as exc:
                last_error = exc
                continue
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except OSError:
                        pass
        if last_error is not None:
            raise SafeHttpError(str(last_error)) from last_error
        raise SafeHttpError("Unable to connect to resolved address.")

    @staticmethod
    def _set_transport_timeout(connection: Any, remaining: float) -> None:
        if remaining <= 0:
            raise TimeoutError("Safe HTTP request deadline exceeded.")
        candidates = [connection, getattr(connection, "sock", None)]
        for candidate in candidates:
            setter = getattr(candidate, "settimeout", None)
            if callable(setter):
                try:
                    setter(remaining)
                except OSError:
                    pass

    def _read_body(
        self,
        connection: Any,
        response: Any,
        *,
        max_bytes: int,
        deadline: float,
        wall_deadline: float,
    ) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while total <= max_bytes:
            remaining = self._remaining(deadline, wall_deadline)
            if remaining <= 0:
                raise TimeoutError("Safe HTTP request deadline exceeded.")
            self._set_transport_timeout(connection, remaining)
            amount = min(64 * 1024, max_bytes + 1 - total)
            chunk = self._run_with_deadline(
                lambda amount=amount: response.read(amount),
                deadline=deadline,
                wall_deadline=wall_deadline,
            )
            if not chunk:
                break
            if not isinstance(chunk, (bytes, bytearray)):
                raise SafeHttpError("HTTP response body was not bytes.")
            chunks.append(bytes(chunk))
            total += len(chunk)
            if total > max_bytes:
                break
        return b"".join(chunks)

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 22.0,
        max_bytes: int = 6_000_000,
    ) -> SafeHttpResponse:
        """Fetch *url* with bounded redirects and one overall deadline."""

        if max_bytes < 0:
            raise ValueError("max_bytes must be non-negative.")
        timeout = max(0.001, float(timeout))
        deadline = self.clock() + timeout
        wall_deadline = time.monotonic() + timeout
        current = url
        request_headers = dict(headers or {})
        for redirect_count in range(self.max_redirects + 1):
            response = self._request_once(
                current,
                request_headers,
                deadline=deadline,
                wall_deadline=wall_deadline,
                max_bytes=max_bytes,
            )
            if response.status not in {301, 302, 303, 307, 308}:
                return response
            location = response.headers.get("Location", "")
            if not location:
                raise SafeHttpError("Redirect response did not include a Location.")
            if redirect_count >= self.max_redirects:
                raise SafeHttpError("Too many HTTP redirects.")
            try:
                current = urllib.parse.urljoin(current, location)
                _authority_parts(current)
            except (ValueError, SafeHttpError) as exc:
                raise SafeHttpError("Redirect target is not a valid HTTP or HTTPS URL.") from exc
        raise SafeHttpError("Too many HTTP redirects.")


__all__ = [
    "MAX_REDIRECTS",
    "ResponseTooLargeError",
    "SafeHttpClient",
    "SafeHttpError",
    "SafeHttpResponse",
    "SafeHTTPClient",
    "SafeHTTPError",
    "SafeHTTPResponse",
    "_is_global_address",
]

# Naming aliases keep the public seam readable to callers that use the common
# HTTP acronym capitalization while the implementation follows the project's
# ``SafeHttp*`` naming convention.
SafeHTTPClient = SafeHttpClient
SafeHTTPError = SafeHttpError
SafeHTTPResponse = SafeHttpResponse
