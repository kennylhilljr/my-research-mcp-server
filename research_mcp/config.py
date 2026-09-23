"""Validated runtime configuration and deployment safety policy."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    data_root: Path
    dataset_root: Path
    remote_mode: bool = False
    enable_expert_sql: bool = False
    enable_crawler: bool = False
    allow_custom_paths: bool = False
    max_response_bytes: int = 65_536
    max_resource_bytes: int = 131_072
    max_download_bytes: int = 50 * 1024 * 1024
    max_pdf_pages: int = 500
    max_sql_rows: int = 1_000
    host: str = "127.0.0.1"
    port: int = 8080
    auth_token: str | None = None

    def __post_init__(self) -> None:
        self.data_root = Path(self.data_root).expanduser().resolve()
        self.dataset_root = Path(self.dataset_root).expanduser().resolve()
        if self.data_root == Path(self.data_root.anchor):
            raise ValueError("data_root must not be a filesystem root")
        if self.dataset_root == Path(self.dataset_root.anchor):
            raise ValueError("dataset_root must not be a filesystem root")
        if self.max_response_bytes < 1 or self.max_download_bytes < 1:
            raise ValueError("byte budgets must be positive")
        if not 1 <= self.max_pdf_pages <= 10_000:
            raise ValueError("max_pdf_pages must be between 1 and 10000")
        if self.remote_mode:
            # Free-form SQL and caller-selected paths are local expert features only.
            self.enable_expert_sql = False
            self.allow_custom_paths = False
        if self.host not in {"127.0.0.1", "::1", "localhost"} and not self.auth_token:
            raise ValueError("non-loopback binding requires RESEARCH_MCP_AUTH_TOKEN")

    @classmethod
    def from_env(cls) -> "Settings":
        data_root = Path(
            os.environ.get("RESEARCH_MCP_DATA_ROOT", os.path.expanduser("~/arxiv-papers"))
        )
        dataset_root = Path(
            os.environ.get(
                "RESEARCH_MCP_DATASET_ROOT", os.path.expanduser("~/research-datasets")
            )
        )
        return cls(
            data_root=data_root,
            dataset_root=dataset_root,
            remote_mode=_env_bool("RESEARCH_MCP_REMOTE_MODE"),
            enable_expert_sql=_env_bool("RESEARCH_MCP_ENABLE_EXPERT_SQL"),
            enable_crawler=_env_bool("RESEARCH_MCP_ENABLE_CRAWLER"),
            allow_custom_paths=_env_bool("RESEARCH_MCP_ALLOW_CUSTOM_PATHS"),
            max_response_bytes=int(os.environ.get("RESEARCH_MCP_MAX_RESPONSE_BYTES", "65536")),
            max_download_bytes=int(
                os.environ.get("RESEARCH_MCP_MAX_DOWNLOAD_BYTES", str(50 * 1024 * 1024))
            ),
            host=os.environ.get("RESEARCH_MCP_HOST", "127.0.0.1"),
            port=int(os.environ.get("RESEARCH_MCP_PORT", "8080")),
            auth_token=os.environ.get("RESEARCH_MCP_AUTH_TOKEN") or None,
        )

