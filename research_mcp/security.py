"""Security boundaries for local paths and remote document acquisition."""

from __future__ import annotations

import hashlib
import ipaddress
import os
import socket
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit


class SecurityError(ValueError):
    """Base class for rejected security-sensitive input."""


class PathSecurityError(SecurityError):
    """A path crossed its configured filesystem boundary."""


class URLSecurityError(SecurityError):
    """A URL or resolved address violated the egress policy."""


class DownloadSecurityError(SecurityError):
    """A response violated the bounded document-download policy."""


Resolver = Callable[[str, int], Iterable[ipaddress.IPv4Address | ipaddress.IPv6Address]]


@dataclass(frozen=True)
class DownloadPolicy:
    allowed_hosts: frozenset[str]
    max_bytes: int
    media_types: frozenset[str] = field(
        default_factory=lambda: frozenset({"application/pdf"})
    )
    max_redirects: int = 3
    allow_private_ips: bool = False
    require_https: bool = True
    connect_timeout: float = 5.0
    read_timeout: float = 30.0

    def __post_init__(self) -> None:
        if self.max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        if self.max_redirects < 0:
            raise ValueError("max_redirects cannot be negative")
        normalized = frozenset(host.rstrip(".").lower() for host in self.allowed_hosts)
        if not normalized or "" in normalized:
            raise ValueError("allowed_hosts must contain valid hostnames")
        object.__setattr__(self, "allowed_hosts", normalized)
        object.__setattr__(
            self, "media_types", frozenset(item.lower() for item in self.media_types)
        )


@dataclass(frozen=True)
class ScopedCredential:
    origin: str
    headers: Mapping[str, str]

    def __post_init__(self) -> None:
        normalized = _origin(self.origin, require_origin_only=True)
        if not normalized.startswith("https://"):
            raise ValueError("credential origin must use HTTPS")
        object.__setattr__(self, "origin", normalized)
        object.__setattr__(self, "headers", dict(self.headers))


@dataclass(frozen=True)
class DownloadedDocument:
    path: Path
    sha256: str
    media_type: str
    size_bytes: int
    final_url: str
    redirect_chain: tuple[str, ...]


class ResponseLike(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def iter_content(self, chunk_size: int = ...) -> Iterable[bytes]: ...

    def raise_for_status(self) -> None: ...

    def close(self) -> None: ...


class SessionLike(Protocol):
    def get(self, url: str, **kwargs: Any) -> ResponseLike: ...


def resolve_confined_path(
    root: str | os.PathLike[str],
    relative_path: str | os.PathLike[str],
    *,
    must_exist: bool = False,
) -> Path:
    """Resolve a caller path while preventing absolute, traversal, and symlink escape."""
    root_path = Path(root).resolve(strict=True)
    supplied = Path(relative_path)
    if supplied.is_absolute():
        raise PathSecurityError("absolute paths are not allowed")

    try:
        candidate = (root_path / supplied).resolve(strict=must_exist)
        candidate.relative_to(root_path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise PathSecurityError("path escapes the configured root") from exc
    return candidate


def _default_resolver(
    hostname: str, port: int
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    try:
        records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise URLSecurityError("hostname could not be resolved") from exc
    return tuple(ipaddress.ip_address(record[4][0]) for record in records)


def validate_url(
    url: str,
    policy: DownloadPolicy,
    *,
    resolver: Resolver = _default_resolver,
) -> str:
    """Validate scheme, host allowlist, and every address returned by DNS."""
    try:
        parsed = urlsplit(url)
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise URLSecurityError("URL contains an invalid port") from exc

    scheme = parsed.scheme.lower()
    if policy.require_https and scheme != "https":
        raise URLSecurityError("HTTPS is required")
    if scheme not in {"http", "https"}:
        raise URLSecurityError("unsupported URL scheme")
    if parsed.username is not None or parsed.password is not None:
        raise URLSecurityError("embedded URL credentials are not allowed")
    if parsed.fragment:
        raise URLSecurityError("URL fragments are not allowed")
    if not parsed.hostname:
        raise URLSecurityError("URL hostname is required")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname not in policy.allowed_hosts:
        raise URLSecurityError("hostname is not allowed")

    literal = None
    try:
        literal = ipaddress.ip_address(hostname)
        addresses = (literal,)
    except ValueError:
        addresses = tuple(resolver(hostname, port))
    if not addresses:
        raise URLSecurityError("hostname did not resolve")
    if not policy.allow_private_ips and any(not address.is_global for address in addresses):
        raise URLSecurityError("hostname resolves to a non-public address")

    netloc = hostname
    if parsed.port is not None:
        netloc = f"[{hostname}]" if literal is not None and literal.version == 6 else hostname
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))


def validate_redirect_url(
    current_url: str,
    location: str,
    policy: DownloadPolicy,
    *,
    resolver: Resolver = _default_resolver,
) -> str:
    if not location or "\r" in location or "\n" in location:
        raise URLSecurityError("redirect location is invalid")
    return validate_url(urljoin(current_url, location), policy, resolver=resolver)


def _origin(url: str, *, require_origin_only: bool = False) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid origin") from exc
    if not parsed.scheme or not parsed.hostname or parsed.username is not None:
        raise ValueError("origin must contain only a scheme and hostname")
    if require_origin_only and (
        parsed.path not in {"", "/"} or parsed.query or parsed.fragment
    ):
        raise ValueError("origin must not contain a path, query, or fragment")
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.rstrip(".").lower()
    default_port = 443 if scheme == "https" else 80 if scheme == "http" else None
    port_suffix = "" if port is None or port == default_port else f":{port}"
    return f"{scheme}://{hostname}{port_suffix}"


def credential_headers_for_url(
    url: str, credential: ScopedCredential | None
) -> dict[str, str]:
    if credential is None or _origin(url) != credential.origin:
        return {}
    return dict(credential.headers)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def download_pdf_atomic(
    session: SessionLike,
    url: str,
    destination: str | os.PathLike[str],
    policy: DownloadPolicy,
    *,
    credential: ScopedCredential | None = None,
    resolver: Resolver = _default_resolver,
    validator: Callable[[Path], None] | None = None,
) -> DownloadedDocument:
    """Download a bounded PDF and atomically publish it after all validation passes."""
    destination_path = Path(destination)
    if not destination_path.parent.is_dir():
        raise PathSecurityError("destination directory does not exist")

    current_url = validate_url(url, policy, resolver=resolver)
    redirects: list[str] = []
    response: ResponseLike | None = None
    for hop in range(policy.max_redirects + 1):
        response = session.get(
            current_url,
            headers=credential_headers_for_url(current_url, credential),
            stream=True,
            allow_redirects=False,
            timeout=(policy.connect_timeout, policy.read_timeout),
        )
        if response.status_code not in {301, 302, 303, 307, 308}:
            break
        try:
            if hop >= policy.max_redirects:
                raise DownloadSecurityError("redirect limit exceeded")
            current_url = validate_redirect_url(
                current_url,
                _header(response.headers, "Location") or "",
                policy,
                resolver=resolver,
            )
            redirects.append(current_url)
        finally:
            response.close()

    assert response is not None
    temporary_path: Path | None = None
    try:
        response.raise_for_status()
        content_type = (_header(response.headers, "Content-Type") or "").split(";", 1)[
            0
        ].strip().lower()
        if content_type not in policy.media_types:
            raise DownloadSecurityError("response media type is not allowed")

        declared_length = _header(response.headers, "Content-Length")
        if declared_length is not None:
            try:
                length = int(declared_length)
            except ValueError as exc:
                raise DownloadSecurityError("invalid Content-Length") from exc
            if length < 0 or length > policy.max_bytes:
                raise DownloadSecurityError("declared response exceeds byte limit")

        digest = hashlib.sha256()
        size = 0
        magic = bytearray()
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination_path.name}.",
            suffix=".part",
            dir=destination_path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > policy.max_bytes:
                    raise DownloadSecurityError("streamed response exceeds byte limit")
                if len(magic) < 5:
                    magic.extend(chunk[: 5 - len(magic)])
                digest.update(chunk)
                temporary.write(chunk)
            if bytes(magic) != b"%PDF-":
                raise DownloadSecurityError("response does not have PDF magic bytes")
            temporary.flush()
            os.fsync(temporary.fileno())

        if validator is not None:
            validator(temporary_path)
        os.replace(temporary_path, destination_path)
        temporary_path = None
        return DownloadedDocument(
            path=destination_path,
            sha256=digest.hexdigest(),
            media_type=content_type,
            size_bytes=size,
            final_url=current_url,
            redirect_chain=tuple(redirects),
        )
    finally:
        response.close()
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
