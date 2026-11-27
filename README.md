# Self-hosted Arcane with Ansible

## TL;DR

> This repository provides two separate Ansible playbooks and roles with zero external Galaxy dependencies: `arcane-manager.yml` (`roles/arcane`) provisions a non-root `arcane` user, hardens the host with Fail2ban, unattended security updates, and dynamic UFW rules, optionally installs Docker, and deploys [Arcane Manager](https://getarcane.app) (`v3.2.0`) with optional Pangolin Newt (`1.20.1`) and machine client (`0.23.0`) sidecars; `arcane-agents.yml` (`roles/arcane_agent`) bootstraps remote Docker hosts with Arcane Remote Agents (`v3.2.0`) routed through a Pangolin CLI sidecar (`0.23.0`) with DNS override and no published host ports.

---

Ansible roles and playbooks for deploying Arcane Manager and Arcane Remote Agents with Docker Compose on Debian, Ubuntu, or RHEL-family Linux hosts.

## Requirements

Control machine:

- [Nix](https://nixos.org) with flakes enabled (`nix develop` provides Ansible, Python with `PyYAML` and `Jinja2`, `openssh`, `make`, `gitleaks`, `pre-commit`, `openssl`, and `git`), or Ansible Core 2.15 or newer installed locally
- SSH access to target hosts (`root` for initial manager LXC bootstrap, then `arcane`; existing SSH user with `sudo` for remote Docker hosts)

Target hosts:

- Debian 12/13 or Ubuntu 22.04/24.04 when Docker auto-installation is enabled (`arcane_install_docker: true` or `arcane_agent_install_docker: true`); RHEL-family hosts require Docker Engine and the Compose v2 plugin pre-installed with Docker auto-installation disabled
- For Proxmox LXC targets: nesting enabled (`features: nesting=1`), plus `/dev/net/tun` passed through (`pct set <CTID> --dev0 /dev/net/tun`) whenever a Pangolin machine client sidecar is enabled

## Quick start: Arcane Manager

1. Copy the example manager inventory and group variables, then set your host IP, `arcane_app_url`, and any firewall or Pangolin settings:

   ```bash
   cp inventory/manager.yml.example inventory/manager.yml
   cp group_vars/arcane_manager.yml.example group_vars/arcane_manager.yml
   $EDITOR inventory/manager.yml
   $EDITOR group_vars/arcane_manager.yml
   ```

2. If storing secrets (such as `arcane_newt_secret`, `arcane_pangolin_client_secret`, or `arcane_encryption_key`) in `group_vars/arcane_manager.yml`, encrypt the file with Ansible Vault:

   ```bash
   ansible-vault encrypt group_vars/arcane_manager.yml
   ```

3. Run the initial bootstrap against a fresh LXC as `root` using `make manager-bootstrap` (or shorthand `make bootstrap`). This installs `sudo`, creates the `arcane` user with passwordless `sudo`, syncs `/root/.ssh/authorized_keys`, hardens the host, and starts Arcane Manager:

   ```bash
   make manager-bootstrap VAULT=1
   # Or equivalently:
   make manager-deploy BOOTSTRAP=1 VAULT=1
   ```

> [!NOTE]
> `BOOTSTRAP=1` (and `make manager-bootstrap`) connects as `root` for initial provisioning. All subsequent manager `make` commands default to the `arcane` user.

4. Run subsequent checks and deployments as `arcane` (omit `VAULT=1` if `group_vars/arcane_manager.yml` is not encrypted):

   ```bash
   make manager-ping VAULT=1
   make manager-check VAULT=1
   make manager-deploy VAULT=1
   ```

5. Retrieve Arcane's generated initial admin password on first boot (username `arcane`):

   ```bash
   sudo docker compose -f /opt/arcane/compose.yml cp arcane:/app/data/initial-admin-password .
   ```

## Quick start: Arcane Remote Agents

`arcane-agents.yml` (`roles/arcane_agent`) is independent from the manager host-hardening role and targets existing remote Docker hosts in the `arcane_agents` group.

1. Copy the example agent inventory and group variables, then set your remote Docker hosts, `arcane_agent_manager_api_url`, `arcane_agent_token`, and Pangolin machine client credentials:

   ```bash
   cp inventory/agents.yml.example inventory/agents.yml
   cp group_vars/arcane_agents.yml.example group_vars/arcane_agents.yml
   $EDITOR inventory/agents.yml
   $EDITOR group_vars/arcane_agents.yml
   ```

2. Encrypt `group_vars/arcane_agents.yml` (and any `host_vars/*.yml` containing per-host tokens) with Ansible Vault:

   ```bash
   ansible-vault encrypt group_vars/arcane_agents.yml
   ```

3. Test connectivity, preview changes, and deploy agents (use `LIMIT=<host>` to target a single host, or `AGENTS_USER=<user>` to override the inventory `ansible_user`):

   ```bash
   make agents-ping VAULT=1
   make agents-check VAULT=1
   make agents-deploy VAULT=1
   ```

By default, `roles/arcane_agent` runs `docker.io/fosrl/pangolin-cli` (`0.23.0`) on a dedicated `arcane-agent-pangolin` Docker bridge network with DNS override enabled (`--override-dns=true` and `PANGOLIN_OVERRIDE_DNS: "true"`). `arcane-agent` attaches via `network_mode: "service:pangolin-client"` so all traffic to the Arcane Manager traverses the WireGuard tunnel and uses Pangolin's DNS resolver without publishing any host ports. The role persists the agent's encrypted key in the `arcane-agent-data` named volume and verifies that the `arcane-agent` container reaches and remains in the `running` state without restarting.

> [!IMPORTANT]
> When `arcane_agent_pangolin_enabled: true`, the target host must expose `/dev/net/tun`. On Proxmox LXC, pass the device through from the Proxmox host with `pct set <CTID> --dev0 /dev/net/tun`. For Proxmox LXC agent targets, set `arcane_agent_lxc_mode: true` to configure `pid: host` and mount `/proc:/proc`.

## Manager deployment modes

`arcane_lxc_mode` switches the manager Compose definition between Arcane's two supported host layouts:

- **LXC mode** (`arcane_lxc_mode: true`, default): mounts `/sys/fs/cgroup:/sys/fs/cgroup:ro` per Arcane's [LXC container guide](https://getarcane.app/docs/get-started/lxc-container).
- **Standard VM or bare-metal mode** (`arcane_lxc_mode: false`): sets `cgroup: host` on the `arcane` container per Arcane's [standard installation guide](https://getarcane.app/docs/get-started/installation).

When `arcane_encryption_key` is left empty, the role generates a 32-byte hex key on first run and persists it at `/opt/arcane/secrets/encryption-key` (`mode: 0600`). The container runs with `PUID` and `PGID` matched to the `arcane` user and waits for `/api/health` to return HTTP 200 before completing.

## Host hardening and dynamic UFW rules

These controls apply only to `roles/arcane` (manager hosts):

- **Fail2ban** (`arcane_fail2ban_enabled: true`): configures an SSH jail using the `systemd` backend, incremental ban time, and `ufw` banaction when UFW is enabled (or `nftables-multiport` otherwise).
- **Automatic security updates** (`arcane_unattended_upgrades_enabled: true`): installs and configures `unattended-upgrades` for Debian and Ubuntu security origins with automatic reboot disabled by default.
- **UFW** (`arcane_ufw_enabled: true`): sets default incoming and routed policies to `deny` and outgoing to `allow`, pre-allows SSH (`arcane_ssh_port`) before enabling the firewall, and allows `arcane_port/tcp` automatically only when Newt is disabled.

Supply additional firewall rules dynamically with `arcane_ufw_rules`:

```yaml
arcane_ufw_allow_arcane_port: false
arcane_ufw_rules:
  - rule: allow
    direction: in
    port: "3552"
    proto: tcp
    from_ip: 192.168.1.0/24
    comment: "Arcane UI from LAN"
  - rule: limit
    port: "22"
    proto: tcp
    from_ip: 192.168.1.0/24
    comment: "Rate-limited SSH from LAN"
  - rule: allow
    port: "8000:8010"
    proto: tcp
    from_ip: 10.0.0.0/8
    comment: "App range from internal network"
```

Re-apply only UFW rules with:

```bash
make manager-ufw VAULT=1
```

## Optional Pangolin Newt exposure and private resource access (Manager)

Set `arcane_newt_enabled: true` to run a Newt sidecar (`1.20.1`) alongside Arcane Manager on an internal `arcane-newt` Docker bridge. In this mode, neither container publishes host ports, `arcane_ufw_allow_arcane_port` defaults to `false`, and Newt credentials are mounted as a root-only Compose secret (`/opt/arcane/secrets/newt-config.json`).

Create a Newt site in Pangolin, point your Pangolin HTTP resource at `arcane:3552`, and set:

```yaml
arcane_newt_enabled: true
arcane_newt_version: "1.20.1"
arcane_newt_pangolin_endpoint: https://pangolin.example.com
arcane_newt_id: site-id-from-pangolin
arcane_newt_secret: "{{ vault_arcane_newt_secret }}"
arcane_app_url: https://arcane.example.com
```

To let Arcane Manager reach remote Pangolin private resources over zero-trust tunnels, enable the Pangolin machine client sidecar (`fosrl/pangolin-cli` pinned to `0.23.0`). Arcane attaches via `network_mode: "service:pangolin-client"` so it shares the WireGuard tunnel and private DNS directly without host networking or published host ports (when Newt is enabled, `pangolin-client` joins `arcane-newt` with the `arcane` network alias so Newt still targets `arcane:3552`):

```yaml
arcane_pangolin_client_enabled: true
arcane_pangolin_cli_version: "0.23.0"
arcane_pangolin_client_endpoint: https://pangolin.example.com
arcane_pangolin_client_id: machine-client-id-from-pangolin
arcane_pangolin_client_secret: "{{ vault_arcane_pangolin_client_secret }}"
```

## Key variables

### Manager role (`roles/arcane`)

| Variable | Default | Purpose |
|---|---|---|
| `arcane_lxc_mode` | `true` | `true` for LXC (`/sys/fs/cgroup:ro`), `false` for standard VM (`cgroup: host`) |
| `arcane_install_docker` | `true` | Install Docker Engine and the Compose v2 plugin from Docker's official APT repository on Debian/Ubuntu |
| `arcane_version` | `v3.2.0` | Pinned `ghcr.io/getarcaneapp/manager` image tag |
| `arcane_app_url` | `http://<host-ip>:3552` | Public or LAN URL for Arcane |
| `arcane_port` | `3552` | Host port in direct mode |
| `arcane_base_dir` | `/opt/arcane` | Root for Compose file and secrets |
| `arcane_projects_dir` | `/opt/docker` | Compose projects directory mounted into Arcane |
| `arcane_encryption_key` | auto-generated | 32+ char key (persisted at `/opt/arcane/secrets/encryption-key` if omitted) |
| `arcane_user` | `arcane` | Non-root user created with passwordless `sudo` and synced `root` SSH keys |
| `arcane_fail2ban_enabled` | `true` | Install and configure Fail2ban SSH jail |
| `arcane_unattended_upgrades_enabled` | `true` | Enable automatic Debian/Ubuntu security updates |
| `arcane_ufw_enabled` | `true` | Enable UFW with default deny incoming and dynamic `arcane_ufw_rules` |
| `arcane_ufw_rules` | `[]` | Dynamic list of user-supplied UFW rules |
| `arcane_newt_enabled` | `false` | Isolate Arcane behind a Pangolin Newt connector |
| `arcane_newt_version` | `1.20.1` | Pinned `docker.io/fosrl/newt` image tag |
| `arcane_newt_pangolin_endpoint` | `""` | Pangolin server URL |
| `arcane_newt_id` | `""` | Pangolin Newt site ID |
| `arcane_newt_secret` | `""` | Pangolin Newt site secret |
| `arcane_pangolin_client_enabled` | `false` | Run a Pangolin machine client sidecar so Arcane can reach private resources |
| `arcane_pangolin_cli_version` | `0.23.0` | Pinned `docker.io/fosrl/pangolin-cli` image tag |
| `arcane_pangolin_client_endpoint` | `arcane_newt_pangolin_endpoint` | Pangolin server URL for the machine client |
| `arcane_pangolin_client_id` | `""` | Pangolin machine client ID |
| `arcane_pangolin_client_secret` | `""` | Pangolin machine client secret |

### Remote Agent role (`roles/arcane_agent`)

| Variable | Default | Purpose |
|---|---|---|
| `arcane_agent_lxc_mode` | `false` | `true` for Proxmox LXC (`pid: host` and `/proc:/proc`), `false` for VM or bare-metal |
| `arcane_agent_install_docker` | `false` | Install Docker Engine and the Compose v2 plugin from Docker's official APT repository on Debian/Ubuntu |
| `arcane_agent_version` | `v3.2.0` | Pinned `ghcr.io/getarcaneapp/agent` image tag |
| `arcane_agent_base_dir` | `/opt/arcane-agent` | Root directory for the agent Compose file |
| `arcane_agent_transport` | `auto` | Manager connection transport (`auto`, `websocket`, or `grpc`) |
| `arcane_agent_token` | `""` | Enrollment token (`arc_etkn_...`) or agent API key (`arc_...`) |
| `arcane_agent_manager_api_url` | `""` | Arcane Manager API URL reachable from the agent |
| `arcane_agent_extra_env` | `{}` | Optional extra environment variables passed to `arcane-agent` |
| `arcane_agent_pangolin_enabled` | `true` | Route agent traffic through a Pangolin machine client sidecar |
| `arcane_agent_pangolin_cli_version` | `0.23.0` | Pinned `docker.io/fosrl/pangolin-cli` image tag |
| `arcane_agent_network_name` | `arcane-agent-pangolin` | Dedicated Docker bridge network name for the Pangolin sidecar |
| `arcane_agent_pangolin_endpoint` | `""` | Pangolin server URL |
| `arcane_agent_pangolin_client_id` | `""` | Pangolin machine client ID |
| `arcane_agent_pangolin_client_secret` | `""` | Pangolin machine client secret |
| `arcane_agent_pangolin_override_dns` | `true` | Override container DNS with Pangolin private DNS |
| `arcane_agent_pangolin_upstream_dns` | `""` | Optional comma-separated upstream DNS servers (`PANGOLIN_UPSTREAM_DNS`) |
| `arcane_agent_pangolin_match_domains` | `""` | Optional domain patterns for split DNS (`PANGOLIN_MATCH_DOMAINS`) |
| `arcane_agent_pangolin_tunnel_dns` | `false` | Route DNS queries through the Pangolin tunnel (`PANGOLIN_TUNNEL_DNS`) |

## Gitea CI lifecycle automation

- `.gitea/workflows/validate.yml` runs on pushes to `main`, pull requests targeting `main`, and manual `workflow_dispatch`. It runs CI guard tests and validates YAML syntax, Ansible syntax for both `arcane-manager.yml` and `arcane-agents.yml`, all supported Manager and Remote Agent Compose variants, and the Nix flake (`flake.nix` and `flake.lock`) across all supported systems.
- `.gitea/workflows/gitleaks.yml` runs on pushes to `main`, pull requests targeting `main`, and manual `workflow_dispatch`. It scans the repository history with Gitleaks (`v8.30.1`) using `.gitleaks.toml`, which extends the default ruleset with custom checks for unencrypted `arcane_encryption_key`, `arcane_newt_secret`, `arcane_pangolin_client_secret`, `arcane_agent_token`, `arcane_agent_pangolin_client_secret`, and `arcane_database_url` values. For local pre-commit protection, install [pre-commit](https://pre-commit.com) and run `pre-commit install` (configured in `.pre-commit-config.yaml`).
- `.gitea/workflows/service-updates.yml` runs every Monday at 03:23 UTC (and on manual `workflow_dispatch`). It checks for newer stable Arcane, Newt, Pangolin CLI, and `nixpkgs` (`flake.lock`) revisions, updates pinned values across tracked manager and agent files, validates YAML syntax, Ansible syntax, rendered Compose variants, and the Nix flake, pulls all pinned images, and opens or refreshes the `automation/service-updates` pull request.
- `.gitea/workflows/release-on-main.yml` runs when a pull request is merged into `main` and publishes a sequentially versioned SemVer Gitea release with categorized release notes.
