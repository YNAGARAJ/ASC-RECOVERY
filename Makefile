.PHONY: test lint eval security lock sbom

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

# Regenerates the pinned production dependency lockfile from pyproject.toml's
# runtime `dependencies` list. Commit the result -- this is what the
# Dockerfile installs from (see Dockerfile's builder stage), not
# pyproject.toml directly, so a build is reproducible byte-for-byte until
# this is deliberately re-run. CI checks this file for staleness rather
# than regenerating it on every run.
#
# pip-tools 7.6.0 reaches into pip's private internals and breaks under pip
# 25+ (ImportError: cannot import name 'stdlib_pkgs') -- if this fails with
# that error, `pip install "pip<25"` first, run `make lock`, then restore
# pip (`pip install --upgrade pip`). See .github/workflows/ci.yml's lint
# job for the same pin, scoped to just this step.
#
# pip-tools has no --universal/multi-platform mode (unlike `uv pip compile
# --universal`) -- it resolves against whoever's running it. CI runs on
# Linux (ubuntu-latest), so a lockfile regenerated on Windows or macOS
# will differ (Windows pulls in `colorama`/`tzdata`, which Linux doesn't
# need) and fail CI's freshness check even though nothing is actually
# wrong. Run this from a Linux shell/container when possible; if you must
# run it on Windows/macOS, diff the result against CI's own regenerated
# copy (the failed job's log shows the exact diff) and manually drop any
# platform-only lines before committing.
lock:
	pip-compile pyproject.toml --output-file=requirements.lock.txt --strip-extras

# Generates a CycloneDX SBOM describing exactly what's installed right now.
# Not committed (see docs/RUNBOOK.md) -- an SBOM describes one specific
# build's contents, so it's produced fresh per build/CI run as an artifact
# rather than kept as a static file that would drift from reality.
sbom:
	cyclonedx-py environment -o sbom.json
