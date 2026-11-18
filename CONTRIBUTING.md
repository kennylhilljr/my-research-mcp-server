# Contributing

Thanks for your interest in contributing to **my-research-mcp-server**!

## This repository is legacy (v1)

This is v1. It is read-only and kept running until it is replaced by [Research MCP v2](https://github.com/kennylhilljr/research-mcp) at cutover. Pick the right repository before you start:

| Change | Where it goes |
|--------|---------------|
| New features, new tools, new sources, refactors, dependency upgrades that aren't needed for a fix | **v2:** [kennylhilljr/research-mcp](https://github.com/kennylhilljr/research-mcp). PRs like these are not accepted here. |
| Critical v1 maintenance during the migration: a security fix, a crash or data-loss bug, or a broken upstream API that stops a tool from working for the existing MCP clients | **v1 (this repo).** Keep the change as small as possible and add a test that reproduces the problem. |

If you're unsure whether a fix counts as critical, open an issue first.

After cutover, this repository will be archived once v2 has had 14 days of daily use with no fallback to v1 and the owner has given an explicit go-ahead. After that it accepts no changes. See the status banner in the [README](README.md).

The rest of this guide applies to critical v1 fixes.

## Prerequisites

- Python 3.10+
- Git

## Development Setup

```bash
git clone https://github.com/kennylhilljr/my-research-mcp-server.git
cd my-research-mcp-server
make dev          # installs the package in editable mode with dev deps
```

## Running Tests

```bash
make test         # pytest -v --tb=short
```

## Linting

```bash
make lint         # ruff check .
make format       # ruff format .
```

## Commit Conventions

- Use clear, imperative-mood subjects (e.g. "Fix URL validation in fetch_cloud_doc_page").
- Keep commits focused — one logical change per commit.

## Pull Request Process

1. Fork the repo and create a feature branch from `main`.
2. Make your changes and add tests for new functionality.
3. Ensure `make lint` and `make test` pass.
4. Open a PR against `main` with a clear description of the change.
