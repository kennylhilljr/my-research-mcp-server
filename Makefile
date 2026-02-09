.PHONY: install dev test lint format run run-sse clean

install:
	pip install -e .

dev:
	pip install -e ".[dev]"

test:
	pytest -v --tb=short

test-cov:
	pytest -v --cov=server --cov-report=term-missing

lint:
	ruff check .

format:
	ruff format .

run:
	python server.py

run-sse:
	python server.py --transport sse --port 8080

clean:
	rm -rf __pycache__ .pytest_cache *.egg-info dist build
	find . -name "*.pyc" -delete
