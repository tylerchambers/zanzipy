.PHONY: lint test build run check precommit

lint:
	ruff check src tests
	ruff format --check src tests
	@! grep -R -n -E "\\bpragma\\b" src tests || (echo "Forbidden word 'pragma' found. Remove any occurrences (e.g., '# pragma: ...')." >&2; exit 1)

fix:
	ruff check src tests --fix
	@! grep -R -n -E "\\bpragma\\b" src tests || (echo "Forbidden word 'pragma' found. Remove any occurrences (e.g., '# pragma: ...')." >&2; exit 1)

typecheck:
	ty check src tests

test:
	PYTHONPATH=src pytest tests

make precommit:
	make lint
	make typecheck

build:
	uv build

publish:
	make clean
	make build
	uv publish

clean:
	rm -rf dist
	rm -rf build
	rm -rf .eggs
	rm -rf .pytest_cache
	rm -rf .ruff_cache
	rm -rf .mypy_cache
	rm -rf .ty_cache
