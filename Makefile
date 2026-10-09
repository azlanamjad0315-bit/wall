# DemoSites AI — sab kaam yahin se. `make` likhein, list aa jayegi.
SHELL   := /bin/bash
COMPOSE := docker compose
PY      := python3
VENV    := gateway/.venv
PIPSTAMP:= $(VENV)/.stamp

.PHONY: help install dirs up up-full down restart logs logs-waha status build validate test crm waha \
        tunnel dryrun live-test pages report tune reset check clean all

help:            ## (default) sab targets ki list
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: $(PIPSTAMP) ## local python env (tests + scripts ke liye)
$(PIPSTAMP): gateway/requirements.txt tools/requirements-dev.txt
	@command -v python3 >/dev/null || { echo "python3 chahiye"; exit 1; }
	@test -d $(VENV) || python3 -m venv $(VENV)
	$(VENV)/bin/pip -q install -r gateway/requirements.txt -r tools/requirements-dev.txt
	@touch $(PIPSTAMP)

dirs:            ## data folders banao (permission bugs se bachne ke liye)
	@bash scripts/dirs.sh

up: dirs         ## sab services up (default set)
	$(COMPOSE) up -d --build
	@sleep 3 && $(COMPOSE) ps

up-full: dirs    ## + Baserow + Ollama + tunnel
	$(COMPOSE) --profile baserow --profile local-ai --profile tunnel up -d --build
	@$(COMPOSE) ps

down:            ## sab band (data safe rahega)
	$(COMPOSE) down

restart:         ## config ke baad restart
	$(COMPOSE) restart n8n ai-gateway waha
	@$(COMPOSE) ps

logs:            ## live logs (n8n + gateway)
	$(COMPOSE) logs -f --tail=60 n8n ai-gateway

logs-waha:       ## WhatsApp ke logs (ban/login issues)
	$(COMPOSE) logs --tail=80 waha

status:          ## poora health report: kaun chal raha hai, kaun nahi
	@bash scripts/status.sh

check: validate test status ## ek command me sab QA

build:           ## n8n workflow JSON dobara generate karein (prompts/tools badle to)
	$(PY) tools/build_workflows.py

validate:        ## workflow JSON ki shape + $env keys check
	$(PY) tools/validate_workflows.py

test: install    ## gateway ke unit tests (network nahi chahiye)
	@cd gateway && ../$(VENV)/bin/python -m pytest -q

crm:             ## Baserow database/table/fields + .env me table id
	@bash scripts/crm.sh

waha:            ## WhatsApp QR login
	@bash scripts/waha-login.sh

tunnel:          ## free public HTTPS URL (WAHA webhook ke liye)
	@bash scripts/tunnel.sh

dryrun:          ## pipeline test: scan → AI site → deploy → message (kuch send NAHI hota)
	@bash scripts/dryrun.sh

live-test:       ## generated message khud ke number pe bhej kar asli test
	@bash scripts/dryrun.sh --to $(shell grep -E '^CONTACT_PHONE=' .env | cut -d= -f2- | tr -cd '0-9')

pages:           ## GitHub Pages status + live link probe
	@bash scripts/pages-check.sh

reset:           ## ⚠️ CRM ke saare rows delete (table/fields safe)
	@bash scripts/reset.sh

report:          ## aaj ke numbers (Baserow se) terminal me
	@bash scripts/report.sh

tune:            ## funnel dekh kar wording/limit suggestions
	@bash scripts/tune.sh

clean:           ## containers + images hatao (DATA bhi!) — soch samajh kar
	$(COMPOSE) down -v --rmi local || true
	@echo "note: ./n8n ./baserow ./waha ./gateway/data folders khud delete karne honge agar chahiye to"

all: install build validate test status ## install → build → validate → test → status
