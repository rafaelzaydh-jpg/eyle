PYTHON ?= python3

.PHONY: install dev test test-core test-adapter benchmark serve verify clean

install:
	$(PYTHON) -m pip install -r requirements.lock

dev:
	$(PYTHON) -m pip install -r requirements-dev.lock
	$(PYTHON) -m pip install -r server/requirements.txt

test:
	$(PYTHON) -m pytest -q

test-core:
	$(PYTHON) -m pytest -q --ignore=tests/test_web_security.py

test-adapter:
	$(PYTHON) -m pytest -q server/tests

benchmark:
	$(PYTHON) main.py benchmark

serve:
	$(PYTHON) main.py serve

verify:
	$(PYTHON) -B -m eyle.devtools.release_identity
	$(PYTHON) -m compileall -q eyle llm web server main.py
	$(PYTHON) -m pytest -q
	$(PYTHON) -m pytest -q server/tests
	node --check web/static/app.js

clean:
	find . -type d -name '__pycache__' -prune -exec rm -rf {} +
	rm -rf .pytest_cache .coverage htmlcov
	find context memory workspace -mindepth 1 -type f ! -name '.gitkeep' -delete 2>/dev/null || true
