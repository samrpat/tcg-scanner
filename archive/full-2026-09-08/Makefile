.DEFAULT_GOAL := help
COMPOSE := docker compose

.PHONY: help up down restart logs ps migrate revision sync prices seed test lint shell psql health hw backup clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

.env:
	@cp .env.example .env
	@echo "Created .env from .env.example — set POSTGRES_PASSWORD before going further."

up: .env ## Start the stack
	$(COMPOSE) up -d --build
	@echo "Waiting for services..."
	@$(MAKE) --no-print-directory health

down: ## Stop the stack
	$(COMPOSE) down

restart: ## Restart the stack
	$(COMPOSE) restart

logs: ## Tail all logs
	$(COMPOSE) logs -f --tail=100

ps: ## Show service status
	$(COMPOSE) ps

migrate: ## Apply database migrations
	@# Versions are bind-mounted, same as `make revision`. Migrations are baked into the image
	@# at build time, so without this a migration written since the last build is invisible to
	@# alembic and `upgrade head` silently reports it is already up to date.
	$(COMPOSE) run --rm \
		-v "$(CURDIR)/api/alembic/versions:/srv/alembic/versions" \
		api alembic upgrade head

revision: ## Create a migration: make revision m="add thing"
	@# The versions directory is bind-mounted for this one command so the generated file
	@# lands in the repo instead of vanishing with the container.
	$(COMPOSE) run --rm \
		-v "$(CURDIR)/api/alembic/versions:/srv/alembic/versions" \
		api alembic revision --autogenerate -m "$(m)"

seed: ## Seed reference data (idempotent)
	$(COMPOSE) run --rm api python -m app.cli seed $(ARGS)

sync: ## Sync card data from TCGdex
	$(COMPOSE) run --rm api python -m app.cli sync $(ARGS)

prices: ## Ingest prices from TCGdex
	$(COMPOSE) run --rm api python -m app.cli prices $(ARGS)

mark: ## Label a capture: make mark ARGS="CARD-000005 --no-card"
	$(COMPOSE) run --rm api python -m app.cli mark $(ARGS)

index-art: ## Compute perceptual hashes for card art (resumable)
	$(COMPOSE) run --rm api python -m app.cli index-art $(ARGS)

warm-art: ## Pre-download reference art: make warm-art ARGS="--set me04"
	$(COMPOSE) run --rm api python -m app.cli warm-art $(ARGS)

back-reference: ## Rebuild the card-back reference from stored captures
	$(COMPOSE) run --rm -v "$(CURDIR)/api/app/imaging/assets:/srv/app/imaging/assets" api python -m app.cli back-reference $(ARGS)

listings: ## Re-render listing images: make listings ARGS="--margin 8"
	$(COMPOSE) run --rm api python -m app.cli listings $(ARGS)

reconcile: ## Find captures whose processing job was lost and re-queue them
	$(COMPOSE) run --rm api python -m app.cli reconcile

sheet: ## Render every processed crop as one contact sheet to eyeball
	@./scripts/contact-sheet.sh

benchmark: ## Run detection over every stored original and score it
	$(COMPOSE) run --rm api python -m app.cli benchmark

test: ## Run the test suite
	@# Source is bind-mounted so editing a test does not require rebuilding the image.
	$(COMPOSE) run --rm -e TESTING=1 \
		-v "$(CURDIR)/api/app:/srv/app" \
		-v "$(CURDIR)/api/tests:/srv/tests" \
		api pytest -q $(ARGS)

photos-on: ## Share card photos publicly so eBay can fetch them
	@docker compose --profile photos up -d photos
	@# The tunnel is always recreated. A Cloudflare quick tunnel can lose its control stream
	@# and keep running with a hostname Cloudflare has already withdrawn — the container looks
	@# healthy while every picture URL points at nothing. Starting fresh costs a few seconds
	@# and rules that out.
	@docker compose --profile photos up -d --force-recreate tunnel
	@echo "Waiting for the tunnel (a fresh address can take a few minutes to publish)..."
	@for i in $$(seq 1 90); do \
		url=$$(curl -s http://localhost:$${WEB_PORT:-8080}/api/ebay/photo-host | sed -n 's/.*"url": *"\([^"]*\)".*/\1/p'); \
		if [ -n "$$url" ] && [ "$$url" != "null" ]; then \
			echo; echo "  Photos are public at: $$url"; \
			break; \
		fi; \
		sleep 2; \
	done
	@echo
	@echo "  Checking eBay can actually fetch them..."
	@curl -s "http://localhost:$${WEB_PORT:-8080}/api/ebay/photo-check?limit=1" \
		| python3 -c "import json,sys; d=json.load(sys.stdin); print('  ' + d['reason'])" || true
	@echo
	@echo "  Only card JPEGs are shared — no API, no writes. The app itself stays private,"
	@echo "  which matters because it has no password on it."
	@echo
	@echo "  Now: Inventory -> Download eBay upload file."
	@echo "  When the upload is done:  make photos-off"

photos-check: ## Verify eBay can fetch the listing photos right now
	@curl -s "http://localhost:$${WEB_PORT:-8080}/api/ebay/photo-check?limit=2" | python3 -c "\
import json,sys; d=json.load(sys.stdin); \
print(d['reason']); \
[print(f\"  {'ok  ' if c['ok'] else 'FAIL'} {c['status']} {c['url']}\") for c in d['checked']]"

photos-off: ## Stop sharing card photos
	@docker compose --profile photos stop photos tunnel >/dev/null 2>&1
	@docker compose --profile photos rm -f photos tunnel >/dev/null 2>&1
	@echo "Photos are no longer reachable from the internet."

smoke: ## Exercise every endpoint against the running stack
	@./scripts/smoke.sh

lint: ## Lint and type-check
	$(COMPOSE) run --rm \
		-v "$(CURDIR)/api/app:/srv/app" \
		-v "$(CURDIR)/api/tests:/srv/tests" \
		api ruff check app tests

shell: ## Shell into the api container
	$(COMPOSE) run --rm api bash

psql: ## Open psql
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-tcg} -d $${POSTGRES_DB:-tcg}

health: ## Check service health
	@./scripts/healthcheck.sh

cert: ## Generate a self-signed TLS cert so phones can use the camera
	@./scripts/make-cert.sh data/certs

hw: ## Detect hardware and recommend a topology
	@./scripts/detect-hardware.sh

backup: ## Dump database and images to data/backups
	@./scripts/backup.sh

clean: ## Stop and remove volumes. DESTRUCTIVE.
	@printf "This deletes the database and all images. Type 'yes' to continue: " && read a && [ "$$a" = yes ]
	$(COMPOSE) down -v
