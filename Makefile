.PHONY: test lint eval security

test:
	pytest -q
	pytest --cov=src/domain --cov-branch --cov-report=term-missing -q
	coverage report --include="*/domain/variance.py" --fail-under=100 --show-missing

lint:
	ruff check .
	mypy --strict .

eval:
	python -m evals.run

security:
	bandit -r . -x ./tests,./evals
	pip-audit
	gitleaks detect --no-banner
