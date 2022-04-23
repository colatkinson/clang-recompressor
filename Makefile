all:
.PHONY: all

requirements: poetry.lock pyproject.toml
	poetry install
.PHONY: requirements

lint:
	poetry run isort --check main.py
	poetry run black --check main.py
	poetry run pylint main.py
	poetry run mypy main.py
.PHONY: lint

fmt:
	poetry run isort main.py
	poetry run black main.py
.PHONY: fmt

run:
	poetry run python main.py out
.PHONY: run
