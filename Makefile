# One command: `make film` (or, without make, `python -m pipeline film`).
PY ?= python

.PHONY: film radio smoke validate test script costs kaggle-push kaggle-status kaggle-pull install

install:
	$(PY) -m pip install -r requirements.txt

validate:
	$(PY) -m pipeline validate

smoke:
	$(PY) -m pipeline smoke

script:
	$(PY) -m pipeline script

radio:
	$(PY) -m pipeline radio

film:
	$(PY) -m pipeline film

kaggle-push:
	$(PY) -m pipeline kaggle-push

kaggle-status:
	$(PY) -m pipeline kaggle-status

kaggle-pull:
	$(PY) -m pipeline kaggle-pull

costs:
	$(PY) -m pipeline costs

test:
	$(PY) -m pytest -q
