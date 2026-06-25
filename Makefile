SHELL := /bin/bash

.PHONY: help install run test lint format docker-build docker-run clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies
	uv sync --dev


env: ## Create .env from example if missing
	@if [ -e ./.env ]; then \
		echo -e "$(COLOR_YELLOW)$(ICON_WARN) .env already exists$(COLOR_RESET)"; \
	else \
		cp -v ./.env.example ./.env; \
		echo -e "$(COLOR_GREEN)$(ICON_OK) Created .env from .env.example$(COLOR_RESET)"; \
	fi

run: ## Run the server with hot reload
	source .env && uv run uvicorn app.main:app --reload --reload-exclude 'jobs.db' --reload-exclude '*.db' --host $${HOST:-0.0.0.0} --port $${PORT:-8000}

test: ## Run all tests
	uv run pytest app/tests/ -v

test-cov: ## Run tests with coverage
	uv run pytest app/tests/ -v --cov=app --cov-report=term-missing

lint: ## Run linting
	uv run ruff check app/ 

format: ## Format code
	uv run ruff format app/ 

docker-build: ## Build Docker image
	docker build -t job-queue .

docker-run: ## Run Docker container
	docker run -p $${PORT:-8000}:$${PORT:-8000} --env-file .env job-queue

clean: ## Remove build artifacts
	rm -rf __pycache__ .pytest_cache .coverage htmlcov .mypy_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -f jobs.db
