# MAESTRO — one command per thing you actually do.
#
# `make all` reproduces every artifact in the repo from source: the dataset,
# the benchmark suites, the trained intent model, the test run, the evaluation
# and the report. That is NFR-08 (reproducibility) as a command rather than a
# claim.

PY ?= python
VENV := .venv
ifeq ($(OS),Windows_NT)
	BIN := $(VENV)/Scripts
else
	BIN := $(VENV)/bin
endif
PYTHON := $(BIN)/python

.DEFAULT_GOAL := help
.PHONY: help venv install dataset benchmark train-intent test lint check eval \
        report demo doctor clean all reproduce prepare-lora

help:  ## show this help
	@echo "MAESTRO — make targets"
	@echo
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'
	@echo
	@echo "Start with:  make install && make demo"

venv:  ## create the virtualenv
	$(PY) -m venv $(VENV)

install: venv  ## install MAESTRO and its dev + nlp extras
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[nlp,dev]"
	@echo
	@echo "Installed. Optional extras when you need them:"
	@echo "  browser verbs : pip install -e '.[browser]' && playwright install chromium"
	@echo "  system metrics: pip install -e '.[system]'"
	@echo "  LoRA training : pip install -e '.[train]'"

# --------------------------------------------------------------------------- #
# artifacts
# --------------------------------------------------------------------------- #

dataset:  ## build DeskPlan and check its invariants
	$(PYTHON) data/build_dataset.py
	$(PYTHON) data/validate_dataset.py

benchmark:  ## build the 100-task and adversarial suites
	$(PYTHON) eval/build_benchmark.py

train-intent:  ## train the stage-1 intent classifier (seconds, no GPU)
	$(PYTHON) training/train_intent.py

baselines:  ## the docs/05 §4 comparison table, with a latency column
	$(PYTHON) training/train_intent.py --compare

prepare-lora:  ## turn DeskPlan into chatml/mlx fine-tuning files
	$(PYTHON) training/prepare_data.py

# --------------------------------------------------------------------------- #
# quality
# --------------------------------------------------------------------------- #

test:  ## run the test suite
	$(PYTHON) -m pytest

cov:  ## test suite with a coverage report
	$(PYTHON) -m pytest --cov=maestro --cov-report=term-missing

lint:  ## ruff
	$(PYTHON) -m ruff check maestro data eval training tests scripts

boundary:  ## enforce the cross-platform boundary (docs/02 §7)
	$(PYTHON) scripts/check_platform_boundary.py

check: lint boundary test  ## everything CI runs

# --------------------------------------------------------------------------- #
# evaluation
# --------------------------------------------------------------------------- #

eval:  ## run MAESTRO (B3) on both suites
	$(PYTHON) eval/harness.py --configs B3 --suite both

eval-matrix:  ## baselines + ablations — the numbers the report needs
	$(PYTHON) eval/harness.py \
	  --configs B0 B1 B2 B3 A1 A2 A3 A4 A5 A7 --suite both --out full_matrix

eval-full:  ## the same, three seeds, including desktop tasks (slow, side effects)
	$(PYTHON) eval/harness.py \
	  --configs B0 B1 B2 B3 A1 A2 A3 A4 A5 A7 --suite both \
	  --seeds 3 --allow-desktop --out full_matrix

report:  ## render the ten result tables from the newest run
	$(PYTHON) eval/report.py

planner-eval:  ## evaluate the planner against the DeskPlan test split
	$(PYTHON) training/evaluate_planner.py --by-difficulty --by-verb

# --------------------------------------------------------------------------- #
# running it
# --------------------------------------------------------------------------- #

demo:  ## the scripted end-to-end walkthrough
	$(PYTHON) -m maestro.cli demo

doctor:  ## what is installed, what is missing
	$(PYTHON) -m maestro.cli doctor

verbs:  ## print the closed verb registry
	$(PYTHON) -m maestro.cli verbs

audit:  ## verify the hash-chained audit log
	$(PYTHON) -m maestro.cli audit

# --------------------------------------------------------------------------- #
# the whole thing
# --------------------------------------------------------------------------- #

all: dataset benchmark train-intent check eval report  ## rebuild every artifact
	@echo
	@echo "Everything rebuilt from source."

reproduce: all  ## NFR-08: one command, pinned seeds, clean checkout
	@echo "See eval/results/ for the run and its Markdown report."

clean:  ## remove generated artifacts (keeps the venv and the dataset)
	rm -rf .pytest_cache .ruff_cache **/__pycache__ .coverage htmlcov
	rm -rf eval/results/raw eval/results/*.json eval/results/*.md
	rm -rf training/data
	@echo "cleaned. `make dataset` and `make eval` regenerate everything."

distclean: clean  ## also remove the venv, the dataset and the trained model
	rm -rf $(VENV) models/intent data/splits data/nlp data/generated \
	       data/seed data/adversarial data/stats.json data/DATASET_CARD.md
