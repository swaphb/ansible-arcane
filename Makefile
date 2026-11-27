.DEFAULT_GOAL := help

MANAGER_PLAYBOOK  ?= arcane-manager.yml
MANAGER_INVENTORY ?= inventory/manager.yml
AGENTS_PLAYBOOK   ?= arcane-agents.yml
AGENTS_INVENTORY  ?= inventory/agents.yml
ANSIBLE_USER      ?= arcane
AGENTS_USER       ?=
BOOTSTRAP         ?= 0
VAULT             ?= 0
LIMIT             ?=
EXTRA_ARGS        ?=

ifeq ($(BOOTSTRAP),1)
MANAGER_EFFECTIVE_USER := root
else
MANAGER_EFFECTIVE_USER := $(ANSIBLE_USER)
endif

ifeq ($(VAULT),1)
VAULT_FLAGS := --ask-vault-pass
else
VAULT_FLAGS :=
endif

ifneq ($(strip $(LIMIT)),)
LIMIT_FLAGS := --limit $(LIMIT)
else
LIMIT_FLAGS :=
endif

ifneq ($(strip $(AGENTS_USER)),)
AGENTS_USER_FLAGS := -u $(AGENTS_USER)
else
AGENTS_USER_FLAGS :=
endif

MANAGER_FLAGS := -i $(MANAGER_INVENTORY) -u $(MANAGER_EFFECTIVE_USER) -e ansible_user=$(MANAGER_EFFECTIVE_USER) $(VAULT_FLAGS) $(LIMIT_FLAGS) $(EXTRA_ARGS)
AGENTS_FLAGS  := -i $(AGENTS_INVENTORY) $(AGENTS_USER_FLAGS) $(VAULT_FLAGS) $(LIMIT_FLAGS) $(EXTRA_ARGS)

.PHONY: help \
	manager-bootstrap manager-bootstrap-user manager-deploy manager-check manager-ping manager-ufw manager-hardening manager-arcane \
	bootstrap bootstrap-user deploy check ping ufw hardening arcane \
	agents-deploy agents-check agents-ping \
	validate secrets flake-check flake-update

help: ## Show available targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-24s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "Variables:"
	@echo "  BOOTSTRAP=1              Connect as root instead of $(ANSIBLE_USER) for manager targets"
	@echo "  VAULT=1                  Prompt for Ansible Vault password (--ask-vault-pass)"
	@echo "  ANSIBLE_USER=...         Override manager SSH user (default: $(ANSIBLE_USER))"
	@echo "  AGENTS_USER=...          Override remote agent SSH user (default: inventory ansible_user)"
	@echo "  LIMIT=...                Limit execution to specific hosts or groups (--limit)"
	@echo "  EXTRA_ARGS=...           Pass additional flags to ansible-playbook / ansible"

manager-bootstrap: ## First-time manager bootstrap as root (user, hardening, Docker, Arcane)
	@$(MAKE) manager-deploy BOOTSTRAP=1 VAULT=$(VAULT) LIMIT="$(LIMIT)" EXTRA_ARGS="$(EXTRA_ARGS)"

manager-bootstrap-user: ## First-time manager user-only bootstrap as root (arcane user, sudoers, SSH keys)
	ansible-playbook $(MANAGER_PLAYBOOK) -i $(MANAGER_INVENTORY) -u root -e ansible_user=root --tags base,user $(VAULT_FLAGS) $(LIMIT_FLAGS) $(EXTRA_ARGS)

manager-deploy: ## Run manager playbook (defaults to arcane user; pass BOOTSTRAP=1 for root)
	ansible-playbook $(MANAGER_PLAYBOOK) $(MANAGER_FLAGS)

manager-check: ## Dry-run manager playbook with diff
	ansible-playbook $(MANAGER_PLAYBOOK) $(MANAGER_FLAGS) --check --diff

manager-ping: ## Test SSH connectivity to arcane_manager hosts
	ansible arcane_manager -m ping $(MANAGER_FLAGS)

manager-ufw: ## Apply only UFW firewall rules on manager hosts
	ansible-playbook $(MANAGER_PLAYBOOK) $(MANAGER_FLAGS) --tags ufw

manager-hardening: ## Apply only host hardening tasks on manager hosts (fail2ban, unattended-upgrades, ufw)
	ansible-playbook $(MANAGER_PLAYBOOK) $(MANAGER_FLAGS) --tags hardening

manager-arcane: ## Deploy or update only the Arcane Manager stack
	ansible-playbook $(MANAGER_PLAYBOOK) $(MANAGER_FLAGS) --tags arcane

bootstrap: manager-bootstrap ## Alias for manager-bootstrap
bootstrap-user: manager-bootstrap-user ## Alias for manager-bootstrap-user
deploy: manager-deploy ## Alias for manager-deploy
check: manager-check ## Alias for manager-check
ping: manager-ping ## Alias for manager-ping
ufw: manager-ufw ## Alias for manager-ufw
hardening: manager-hardening ## Alias for manager-hardening
arcane: manager-arcane ## Alias for manager-arcane

agents-deploy: ## Deploy or update Arcane Remote Agents on arcane_agents hosts
	ansible-playbook $(AGENTS_PLAYBOOK) $(AGENTS_FLAGS)

agents-check: ## Dry-run remote agent playbook with diff
	ansible-playbook $(AGENTS_PLAYBOOK) $(AGENTS_FLAGS) --check --diff

agents-ping: ## Test SSH connectivity to arcane_agents hosts
	ansible arcane_agents -m ping $(AGENTS_FLAGS)

validate: ## Run YAML, Ansible syntax, Compose variant, and Nix flake validation
	python3 scripts/validate-compose-variants.py

secrets: ## Run Gitleaks secret scan against the working tree
	@if command -v gitleaks >/dev/null 2>&1; then \
		gitleaks dir --config .gitleaks.toml --redact --verbose .; \
	else \
		docker run --rm -v "$(CURDIR):/repo:ro" -w /repo ghcr.io/gitleaks/gitleaks:v8.30.1 dir --config .gitleaks.toml --redact --verbose .; \
	fi

flake-check: ## Evaluate the Nix flake across all supported systems
	nix flake check --no-build --all-systems

flake-update: ## Update flake.lock to the latest pinned nixpkgs revision
	nix flake update
