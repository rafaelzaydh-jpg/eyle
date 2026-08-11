PYTHON ?= python3

.PHONY: install dev test test-core benchmark serve clean

install:
	$(PYTHON) -m pip install -r requirements.lock

dev:
	$(PYTHON) -m pip install -r requirements-dev.lock

test:
	$(PYTHON) -m pytest -q

test-core:
	$(PYTHON) -m pytest -q --ignore=tests/test_web_security.py

benchmark:
	$(PYTHON) main.py benchmark

serve:
	$(PYTHON) main.py serve

clean:
	find . -type d -name '__pycache__' -prune -exec rm -rf {} +
	rm -rf .pytest_cache .coverage htmlcov
