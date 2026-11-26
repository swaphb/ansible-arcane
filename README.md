# Self-hosted Arcane with Ansible

This project deploys [Arcane](https://getarcane.app) — the GitOps and Docker
management platform — with Ansible and Docker Compose. It is built for
Proxmox LXC containers (with nesting enabled) and also supports standard VMs
and bare-metal Linux hosts via a single boolean toggle (`arcane_lxc_mode`).

## What the role configures

- Non-root administrative user (`arcane`) with passwordless `sudo`, optional
  `docker` group membership, and SSH `authorized_keys` synced from `root`
- Host security hardening:
  - **Fail2ban** protecting SSH (`systemd` backend, incremental ban time,
    UFW/nftables banaction)
  - **Automatic security updates** via `unattended-upgrades` on Debian/Ubuntu
  - **UFW firewall** with safe SSH pre-allow rules and dynamic user-supplied
    rule sets (`arcane_ufw_rules`)
- Optional Docker Engine and Docker Compose v2 bootstrap on Debian/Ubuntu
  (`arcane_install_docker`)
- Arcane Manager container pinned to `v3.1.1`:
  - **LXC mode** (`arcane_lxc_mode: true`): mounts `/sys/fs/cgroup:/sys/fs/cgroup:ro`
    per Arcane's [LXC guide](https://getarcane.app/docs/get-started/lxc-container)
  - **Standard VM / host mode** (`arcane_lxc_mode: false`): sets `cgroup: host`
    per Arcane's [standard installation guide](https://getarcane.app/docs/get-started/installation)
  - Persistent encryption key auto-generated at `/opt/arcane/secrets/encryption-key`
    (or supplied via `arcane_encryption_key`)
  - Built-in container health check and playbook `/api/health` readiness check
- Optional **Pangolin Newt** sidecar (`arcane_newt_enabled: true`) pinned to
  `1.20.1`, isolating Arcane on an internal Docker bridge with zero published
  host ports

## Requirements

Control machine:

- Ansible Core 2.15 or newer
- SSH access to the target (`root` for initial LXC bootstrap, then `arcane`)

Target host:

- Debian 12/13 or Ubuntu 22.04/24.04 when `arcane_install_docker: true`
  (RHEL-family hosts are supported with `arcane_install_docker: false` and
  Docker + Compose v2 pre-installed)
- For Proxmox LXC: container provisioned with **Nesting** enabled
  (`features: nesting=1`)

## Quick start

1. Copy the example inventory and group variables:

   ```bash
   cp inventory/hosts.yml.example inventory/hosts.yml
   cp group_vars/arcane.yml.example group_vars/arcane.yml
   $EDITOR inventory/hosts.yml
   $EDITOR group_vars/arcane.yml
   ```

2. If storing secrets (such as `arcane_newt_secret` or `arcane_encryption_key`)
   in `group_vars/arcane.yml`, encrypt the file with Ansible Vault:

   ```bash
   ansible-vault encrypt group_vars/arcane.yml
   ```

3. Run the initial bootstrap against a fresh LXC as `root` (this creates the
   `arcane` user, syncs `/root/.ssh/authorized_keys`, enables passwordless
   sudo, hardens the host, and starts Arcane):

   ```bash
   ansible-playbook site.yml -u root --ask-vault-pass
   ```

4. All subsequent runs can use the `arcane` user configured in `inventory/hosts.yml`:

   ```bash
   ansible arcane -m ping --ask-vault-pass
   ansible-playbook site.yml --ask-vault-pass --check --diff
   ansible-playbook site.yml --ask-vault-pass
   ```

5. Retrieve Arcane's initial admin password on first boot (username `arcane`):

   ```bash
   sudo docker compose -f /opt/arcane/compose.yml cp arcane:/app/data/initial-admin-password .
   ```

## LXC vs standard VM mode

- **Proxmox / LXC** (`arcane_lxc_mode: true`, default): mounts
  `/sys/fs/cgroup:/sys/fs/cgroup:ro` so Arcane can read host/container metrics
  inside an LXC container.
- **Standard VM / bare-metal** (`arcane_lxc_mode: false`): sets `cgroup: host`
  on the Arcane container instead of mounting `/sys/fs/cgroup`.

## Dynamic UFW rules

UFW defaults to denying incoming and routed traffic and allowing outgoing
traffic. SSH (`arcane_ssh_port`) is allowed before UFW is enabled so you do
not lock yourself out. When `arcane_newt_enabled: false`, port `arcane_port`
(`3552/tcp`) is also allowed automatically unless you set
`arcane_ufw_allow_arcane_port: false`.

Define additional rules dynamically using `arcane_ufw_rules`:

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

You can re-apply only firewall rules at any time with:

```bash
ansible-playbook site.yml --tags ufw
```

## Optional Pangolin Newt exposure

Set `arcane_newt_enabled: true` to run a Newt sidecar alongside Arcane. In
this mode:

- Arcane and Newt attach to the internal `arcane-newt` Docker bridge.
- No host ports are published for Arcane or Newt.
- `arcane_ufw_allow_arcane_port` automatically defaults to `false`.
- Newt credentials are written to `/opt/arcane/secrets/newt-config.json`
  (`mode: 0600`) and mounted as a Compose secret.

Create a Newt site in Pangolin, point your Pangolin HTTP resource at
`arcane:3552`, and set:

```yaml
arcane_newt_enabled: true
arcane_newt_version: "1.20.1"
arcane_newt_pangolin_endpoint: https://pangolin.example.com
arcane_newt_id: site-id-from-pangolin
arcane_newt_secret: "{{ vault_arcane_newt_secret }}"
arcane_app_url: https://arcane.example.com
```

## Important variables

| Variable | Default | Meaning |
|---|---|---|
| `arcane_lxc_mode` | `true` | `true` for LXC (`/sys/fs/cgroup:ro`), `false` for standard VM (`cgroup: host`) |
| `arcane_install_docker` | `true` | Install `docker.io` and `docker-compose-v2` on Debian/Ubuntu |
| `arcane_version` | `v3.1.1` | Pinned `ghcr.io/getarcaneapp/manager` image tag |
| `arcane_app_url` | `http://<host-ip>:3552` | Public or LAN URL for Arcane |
| `arcane_port` | `3552` | Host port in direct mode |
| `arcane_base_dir` | `/opt/arcane` | Root for Compose file and secrets |
| `arcane_projects_dir` | `/opt/docker` | Compose projects directory mounted into Arcane |
| `arcane_encryption_key` | auto-generated | 32+ char key (persisted at `/opt/arcane/secrets/encryption-key` if omitted) |
| `arcane_user` | `arcane` | Non-root user created with passwordless sudo and synced root SSH keys |
| `arcane_fail2ban_enabled` | `true` | Install and configure Fail2ban SSH jail |
| `arcane_unattended_upgrades_enabled` | `true` | Enable automatic Debian/Ubuntu security updates |
| `arcane_ufw_enabled` | `true` | Enable UFW with default deny incoming and dynamic `arcane_ufw_rules` |
| `arcane_ufw_rules` | `[]` | Dynamic list of user-supplied UFW rules |
| `arcane_newt_enabled` | `false` | Isolate Arcane behind a Pangolin Newt connector |
| `arcane_newt_version` | `1.20.1` | Pinned `docker.io/fosrl/newt` image tag |
| `arcane_newt_pangolin_endpoint` | `""` | Pangolin server URL |
| `arcane_newt_id` | `""` | Pangolin Newt site ID |
| `arcane_newt_secret` | `""` | Pangolin Newt site secret |

## Gitea CI lifecycle automation

- **`.gitea/workflows/service-updates.yml`** runs every Monday at 03:23 UTC
  (and on manual `workflow_dispatch`). It discovers the latest stable Arcane
  and Newt releases, updates pinned values across tracked files, validates all
  YAML files, Ansible syntax, and supported Compose variants, pulls the pinned
  container images, and opens or refreshes the `automation/service-updates` PR.
- **`.gitea/workflows/release-on-main.yml`** runs when a pull request is
  merged into `main` and publishes a sequentially versioned SemVer Gitea
  release with categorized release notes.
