# Contributing

Thanks for your interest in contributing to **my-research-mcp-server**!

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
