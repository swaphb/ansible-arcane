# Self-hosted Arcane with Ansible

## TL;DR

> This repository provides an Ansible playbook and `arcane` role that provisions a non-root `arcane` administrative user (with passwordless `sudo` and synced `root` SSH keys), hardens the host with Fail2ban, unattended security updates, and dynamic UFW rules, optionally installs Docker Engine and Compose v2, and deploys [Arcane](https://getarcane.app) (`v3.1.1`) for either a Proxmox LXC container or a standard Linux VM, with optional Pangolin Newt (`1.20.1`) tunnel isolation.

---

An Ansible role and playbook for deploying Arcane with Docker Compose on Debian, Ubuntu, or RHEL-family Linux hosts.

## Requirements

Control machine:

- [Nix](https://nixos.org) with flakes enabled (`nix develop` provides Ansible, Python with `PyYAML` and `Jinja2`, `openssh`, `make`, `gitleaks`, `pre-commit`, `openssl`, and `git`), or Ansible Core 2.15 or newer installed locally
- SSH access to the target (`root` for initial LXC bootstrap, then `arcane`)

Target host:

- Debian 12/13 or Ubuntu 22.04/24.04 when `arcane_install_docker: true` (RHEL-family hosts require Docker Engine and the Compose v2 plugin pre-installed with `arcane_install_docker: false`)
- For Proxmox LXC targets (`arcane_lxc_mode: true`): a container provisioned with nesting enabled (`features: nesting=1`)

## Quick start

1. Copy the example inventory and group variables, then set your host IP, `arcane_app_url`, and any firewall or Newt settings:

   ```bash
   cp inventory/hosts.yml.example inventory/hosts.yml
   cp group_vars/arcane.yml.example group_vars/arcane.yml
   $EDITOR inventory/hosts.yml
   $EDITOR group_vars/arcane.yml
   ```

2. If storing secrets (such as `arcane_newt_secret` or `arcane_encryption_key`) in `group_vars/arcane.yml`, encrypt the file with Ansible Vault:

   ```bash
   ansible-vault encrypt group_vars/arcane.yml
   ```

3. Run the initial bootstrap against a fresh LXC as `root` using the `Makefile` (`make bootstrap` or `BOOTSTRAP=1`). This installs `sudo`, creates the `arcane` user with passwordless `sudo`, syncs `/root/.ssh/authorized_keys`, hardens the host, and starts Arcane:

   ```bash
   make bootstrap VAULT=1
   # Or equivalently:
   make deploy BOOTSTRAP=1 VAULT=1
   ```

> [!NOTE]
> `BOOTSTRAP=1` (and `make bootstrap`) connects as `root` for initial provisioning. All subsequent `make` commands default to the `arcane` user.

4. Run subsequent checks and deployments as `arcane` (omit `VAULT=1` if `group_vars/arcane.yml` is not encrypted):

   ```bash
   make ping VAULT=1
   make check VAULT=1
   make deploy VAULT=1
   ```

5. Retrieve Arcane's generated initial admin password on first boot (username `arcane`):

   ```bash
   sudo docker compose -f /opt/arcane/compose.yml cp arcane:/app/data/initial-admin-password .
   ```

## Deployment modes

`arcane_lxc_mode` switches the generated Compose definition between Arcane's two supported host layouts:

- **LXC mode** (`arcane_lxc_mode: true`, default): mounts `/sys/fs/cgroup:/sys/fs/cgroup:ro` per Arcane's [LXC container guide](https://getarcane.app/docs/get-started/lxc-container).
- **Standard VM or bare-metal mode** (`arcane_lxc_mode: false`): sets `cgroup: host` on the `arcane` container per Arcane's [standard installation guide](https://getarcane.app/docs/get-started/installation).

When `arcane_encryption_key` is left empty, the role generates a 32-byte hex key on first run and persists it at `/opt/arcane/secrets/encryption-key` (`mode: 0600`). The container runs with `PUID` and `PGID` matched to the `arcane` user and waits for `/api/health` to return HTTP 200 before completing.

## Host hardening and dynamic UFW rules

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
ansible-playbook site.yml --tags ufw
```

## Optional Pangolin Newt exposure and private resource access

Set `arcane_newt_enabled: true` to run a Newt sidecar (`1.20.1`) alongside Arcane on an internal `arcane-newt` Docker bridge. In this mode, neither container publishes host ports, `arcane_ufw_allow_arcane_port` defaults to `false`, and Newt credentials are mounted as a root-only Compose secret (`/opt/arcane/secrets/newt-config.json`).

Create a Newt site in Pangolin, point your Pangolin HTTP resource at `arcane:3552`, and set:

```yaml
arcane_newt_enabled: true
arcane_newt_version: "1.20.1"
arcane_newt_pangolin_endpoint: https://pangolin.example.com
arcane_newt_id: site-id-from-pangolin
arcane_newt_secret: "{{ vault_arcane_newt_secret }}"
arcane_app_url: https://arcane.example.com
```

To let Arcane reach remote Pangolin private resources (such as remote Docker sockets or Arcane agents over zero-trust tunnels), enable the Pangolin machine client sidecar (`fosrl/pangolin-cli` pinned to `0.23.0`). Arcane attaches via `network_mode: "service:pangolin-client"` so it shares the WireGuard tunnel and private DNS directly without using host networking or publishing host ports (when Newt is enabled, `pangolin-client` joins `arcane-newt` with the `arcane` network alias so Newt still targets `arcane:3552`):

```yaml
arcane_pangolin_client_enabled: true
arcane_pangolin_cli_version: "0.23.0"
arcane_pangolin_client_endpoint: https://pangolin.example.com
arcane_pangolin_client_id: machine-client-id-from-pangolin
arcane_pangolin_client_secret: "{{ vault_arcane_pangolin_client_secret }}"
```

> [!IMPORTANT]
> The Pangolin machine client requires `/dev/net/tun` inside the target host. On Proxmox LXC, pass the device through from the Proxmox host with `pct set <CTID> --dev0 /dev/net/tun`.

## Key variables

| Variable | Default | Purpose |
|---|---|---|
| `arcane_lxc_mode` | `true` | `true` for LXC (`/sys/fs/cgroup:ro`), `false` for standard VM (`cgroup: host`) |
| `arcane_install_docker` | `true` | Install `docker.io` and `docker-compose-v2` on Debian/Ubuntu |
| `arcane_version` | `v3.1.1` | Pinned `ghcr.io/getarcaneapp/manager` image tag |
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

## Gitea CI lifecycle automation

- `.gitea/workflows/validate.yml` runs on pushes to `main`, pull requests targeting `main`, and manual `workflow_dispatch`. It validates YAML syntax, Ansible playbook syntax, all supported Compose variants, and the Nix flake (`flake.nix` and `flake.lock`) across all supported systems.
- `.gitea/workflows/gitleaks.yml` runs on pushes to `main`, pull requests targeting `main`, and manual `workflow_dispatch`. It scans the repository history with Gitleaks (`v8.30.1`) using `.gitleaks.toml`, which extends the default ruleset with custom checks for unencrypted `arcane_encryption_key`, `arcane_newt_secret`, and `arcane_database_url` values. For local pre-commit protection, install [pre-commit](https://pre-commit.com) and run `pre-commit install` (configured in `.pre-commit-config.yaml`).
- `.gitea/workflows/service-updates.yml` runs every Monday at 03:23 UTC (and on manual `workflow_dispatch`). It checks for newer stable Arcane, Newt, and `nixpkgs` (`flake.lock`) revisions, updates pinned values across tracked files, validates YAML syntax, Ansible syntax, rendered Compose variants, and the Nix flake, pulls the pinned images, and opens or refreshes the `automation/service-updates` pull request.
- `.gitea/workflows/release-on-main.yml` runs when a pull request is merged into `main` and publishes a sequentially versioned SemVer Gitea release with categorized release notes.
