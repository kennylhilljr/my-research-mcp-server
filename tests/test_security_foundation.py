import ipaddress
from pathlib import Path

import pytest

from research_mcp.security import (
    DownloadPolicy,
    DownloadSecurityError,
    PathSecurityError,
    ScopedCredential,
    URLSecurityError,
    credential_headers_for_url,
    download_pdf_atomic,
    resolve_confined_path,
    validate_redirect_url,
    validate_url,
)


class FakeResponse:
    def __init__(self, status=200, headers=None, chunks=(), fail_after=None):
        self.status_code = status
        self.headers = headers or {}
        self._chunks = chunks
        self._fail_after = fail_after
        self.closed = False

    def iter_content(self, chunk_size=64 * 1024):
        del chunk_size
        for index, chunk in enumerate(self._chunks):
            if self._fail_after == index:
                raise OSError("connection lost")
            yield chunk

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)


def public_resolver(host, port):
    del host, port
    return [ipaddress.ip_address("93.184.216.34")]


def policy(**overrides):
    values = {
        "allowed_hosts": frozenset({"papers.example", "cdn.example"}),
        "max_bytes": 32,
        "max_redirects": 2,
    }
    values.update(overrides)
    return DownloadPolicy(**values)


def test_resolve_confined_path_accepts_relative_path(tmp_path):
    assert resolve_confined_path(tmp_path, "papers/item.pdf") == (
        tmp_path / "papers/item.pdf"
    ).resolve()


@pytest.mark.parametrize("unsafe", ["../escape.pdf", "/tmp/escape.pdf"])
def test_resolve_confined_path_rejects_traversal_and_absolute_paths(tmp_path, unsafe):
    with pytest.raises(PathSecurityError):
        resolve_confined_path(tmp_path, unsafe)


def test_resolve_confined_path_rejects_symlink_escape(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PathSecurityError):
        resolve_confined_path(tmp_path, "link/item.pdf")


@pytest.mark.parametrize(
    "url",
    [
        "http://papers.example/a.pdf",
        "https://user:pass@papers.example/a.pdf",
        "https://papers.example/a.pdf#fragment",
        "https://127.0.0.1/a.pdf",
        "https://169.254.169.254/latest/meta-data",
    ],
)
def test_validate_url_rejects_unsafe_urls(url):
    with pytest.raises(URLSecurityError):
        validate_url(url, policy(), resolver=public_resolver)


def test_validate_url_rejects_dns_resolving_to_private_address():
    def private_resolver(host, port):
        del host, port
        return [ipaddress.ip_address("10.0.0.2")]

    with pytest.raises(URLSecurityError):
        validate_url(
            "https://papers.example/a.pdf", policy(), resolver=private_resolver
        )


def test_redirect_is_joined_and_revalidated_per_hop():
    assert validate_redirect_url(
        "https://papers.example/a.pdf",
        "/next.pdf",
        policy(),
        resolver=public_resolver,
    ) == "https://papers.example/next.pdf"

    with pytest.raises(URLSecurityError):
        validate_redirect_url(
            "https://papers.example/a.pdf",
            "https://127.0.0.1/private",
            policy(),
            resolver=public_resolver,
        )


def test_credentials_are_scoped_to_exact_origin():
    credential = ScopedCredential(
        origin="https://papers.example", headers={"Authorization": "Bearer secret"}
    )

    assert credential_headers_for_url(
        "https://papers.example/file", credential
    ) == {"Authorization": "Bearer secret"}
    assert credential_headers_for_url("https://cdn.example/file", credential) == {}
    assert credential_headers_for_url("https://papers.example:444/file", credential) == {}


def test_downloader_streams_pdf_to_atomic_final_path(tmp_path):
    response = FakeResponse(
        headers={"Content-Type": "application/pdf", "Content-Length": "13"},
        chunks=[b"%PDF-1.7\n", b"body"],
    )
    session = FakeSession([response])
    destination = tmp_path / "paper.pdf"

    result = download_pdf_atomic(
        session,
        "https://papers.example/paper.pdf",
        destination,
        policy(),
        resolver=public_resolver,
    )

    assert destination.read_bytes() == b"%PDF-1.7\nbody"
    assert result.path == destination
    assert result.size_bytes == 13
    assert len(result.sha256) == 64
    assert response.closed
    assert session.calls[0][1]["allow_redirects"] is False
    assert list(tmp_path.glob(".*.part")) == []


def test_downloader_strips_credential_after_cross_origin_redirect(tmp_path):
    redirect = FakeResponse(status=302, headers={"Location": "https://cdn.example/p.pdf"})
    pdf = FakeResponse(headers={"Content-Type": "application/pdf"}, chunks=[b"%PDF-x"])
    session = FakeSession([redirect, pdf])
    credential = ScopedCredential(
        origin="https://papers.example", headers={"Authorization": "Bearer secret"}
    )

    result = download_pdf_atomic(
        session,
        "https://papers.example/start",
        tmp_path / "paper.pdf",
        policy(),
        credential=credential,
        resolver=public_resolver,
    )

    assert session.calls[0][1]["headers"] == {"Authorization": "Bearer secret"}
    assert session.calls[1][1]["headers"] == {}
    assert result.redirect_chain == ("https://cdn.example/p.pdf",)


@pytest.mark.parametrize(
    ("headers", "chunks"),
    [
        ({"Content-Type": "text/html"}, [b"%PDF-x"]),
        ({"Content-Type": "application/pdf"}, [b"not a pdf"]),
        ({"Content-Type": "application/pdf", "Content-Length": "100"}, [b"%PDF-x"]),
    ],
)
def test_invalid_or_oversized_download_leaves_no_files(tmp_path, headers, chunks):
    destination = tmp_path / "paper.pdf"

    with pytest.raises(DownloadSecurityError):
        download_pdf_atomic(
            FakeSession([FakeResponse(headers=headers, chunks=chunks)]),
            "https://papers.example/a.pdf",
            destination,
            policy(),
            resolver=public_resolver,
        )

    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_stream_limit_and_disconnect_cleanup_partial_file(tmp_path):
    destination = tmp_path / "paper.pdf"
    oversized = FakeResponse(
        headers={"Content-Type": "application/pdf"}, chunks=[b"%PDF-", b"x" * 30]
    )
    disconnected = FakeResponse(
        headers={"Content-Type": "application/pdf"},
        chunks=[b"%PDF-", b"rest"],
        fail_after=1,
    )

    with pytest.raises(DownloadSecurityError):
        download_pdf_atomic(
            FakeSession([oversized]),
            "https://papers.example/a.pdf",
            destination,
            policy(max_bytes=20),
            resolver=public_resolver,
        )
    with pytest.raises(OSError):
        download_pdf_atomic(
            FakeSession([disconnected]),
            "https://papers.example/a.pdf",
            destination,
            policy(),
            resolver=public_resolver,
        )

    assert list(tmp_path.iterdir()) == []


def test_validator_failure_leaves_no_final_or_partial_file(tmp_path):
    destination = tmp_path / "paper.pdf"

    def reject(_path: Path):
        raise ValueError("parser rejected PDF")

    with pytest.raises(ValueError):
        download_pdf_atomic(
            FakeSession(
                [FakeResponse(headers={"Content-Type": "application/pdf"}, chunks=[b"%PDF-x"])]
            ),
            "https://papers.example/a.pdf",
            destination,
            policy(),
            resolver=public_resolver,
            validator=reject,
        )

    assert list(tmp_path.iterdir()) == []
