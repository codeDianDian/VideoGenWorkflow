PYTHON ?= python3.13
VENV   ?= .venv
PIP    := $(VENV)/bin/pip
BIN    := $(VENV)/bin

SCRIPT ?= examples/demo_script.md
PORT   ?= 8501

.PHONY: install browser dev ui watch auto run-demo run-rag clean install-launchd uninstall-launchd

install:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install -e .

browser:
	$(BIN)/playwright install chromium

dev: install browser
	@echo "\nNext: cp .env.example .env && set ANTHROPIC_API_KEY or OPENAI_API_KEY"

ui:
	$(BIN)/vidforge ui --port $(PORT)

watch:
	$(BIN)/vidforge watch

auto:
	$(BIN)/vidforge auto $(SCRIPT)

run-demo:
	$(BIN)/python examples/seed_demo.py --render

run-rag:
	$(BIN)/python examples/seed_rag.py --render

clean:
	rm -rf build data/*.mp4 data/*.mp3 data/_pw runs/*

install-launchd:
	@./scripts/install-launchd.sh

uninstall-launchd:
	@./scripts/uninstall-launchd.sh
