.DEFAULT_GOAL := help

PLAYBOOK       ?= site.yml
INVENTORY      ?= inventory/hosts.yml
ANSIBLE_USER   ?= arcane
BOOTSTRAP      ?= 0
VAULT          ?= 0
EXTRA_ARGS     ?=

ifeq ($(BOOTSTRAP),1)
EFFECTIVE_USER := root
else
EFFECTIVE_USER := $(ANSIBLE_USER)
endif

ifeq ($(VAULT),1)
VAULT_FLAGS := --ask-vault-pass
else
VAULT_FLAGS :=
endif

ANSIBLE_FLAGS := -i $(INVENTORY) -u $(EFFECTIVE_USER) $(VAULT_FLAGS) $(EXTRA_ARGS)

.PHONY: help bootstrap bootstrap-user deploy check ping ufw hardening arcane validate secrets flake-check flake-update

help: ## Show available targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-16s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "Variables:"
	@echo "  BOOTSTRAP=1      Connect as root instead of $(ANSIBLE_USER) (first-time bootstrap)"
	@echo "  VAULT=1          Prompt for Ansible Vault password (--ask-vault-pass)"
	@echo "  ANSIBLE_USER=... Override default SSH user (default: $(ANSIBLE_USER))"
	@echo "  EXTRA_ARGS=...   Pass additional flags to ansible-playbook"

bootstrap: ## First-time full bootstrap as root (creates arcane user, hardens host, deploys Arcane)
	@$(MAKE) deploy BOOTSTRAP=1 VAULT=$(VAULT) EXTRA_ARGS="$(EXTRA_ARGS)"

bootstrap-user: ## First-time user-only bootstrap as root (creates arcane user, sudoers, and SSH keys)
	ansible-playbook $(PLAYBOOK) -i $(INVENTORY) -u root --tags base,user $(VAULT_FLAGS) $(EXTRA_ARGS)

deploy: ## Run full playbook (defaults to arcane user; pass BOOTSTRAP=1 to use root)
	ansible-playbook $(PLAYBOOK) $(ANSIBLE_FLAGS)

check: ## Dry-run playbook with diff (defaults to arcane user; pass BOOTSTRAP=1 to use root)
	ansible-playbook $(PLAYBOOK) $(ANSIBLE_FLAGS) --check --diff

ping: ## Test SSH connectivity (defaults to arcane user; pass BOOTSTRAP=1 to use root)
	ansible arcane -m ping $(ANSIBLE_FLAGS)

ufw: ## Apply only UFW firewall rules
	ansible-playbook $(PLAYBOOK) $(ANSIBLE_FLAGS) --tags ufw

hardening: ## Apply only host hardening tasks (fail2ban, unattended-upgrades, ufw)
	ansible-playbook $(PLAYBOOK) $(ANSIBLE_FLAGS) --tags hardening

arcane: ## Deploy or update only the Arcane and Newt stack
	ansible-playbook $(PLAYBOOK) $(ANSIBLE_FLAGS) --tags arcane

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
