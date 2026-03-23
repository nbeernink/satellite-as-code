# Satellite as Code

Ansible project that fully automates the deployment and configuration of a
Red Hat Satellite 6 infrastructure, from initial RHEL registration through
content management, provisioning setup, and compliance scanning.

Every aspect of the Satellite configuration is expressed as code: playbooks
handle orchestration, while all environment-specific values live in
`host_vars` files, enabling clean separation between logic and data.

The most credits of this work are going to [https://github.com/sscheib/ansible_satellite](https://github.com/sscheib/ansible_satellite). 
Without this, most of the additions would not be possible.

## Documentation

Detailed guides for specific topics live in the [`docs/`](docs/) folder:

- [HTTP Boot Provisioning](docs/httpboot.md) -- configuring UEFI HTTP
  Boot with Satellite and Capsule (Ansible version)
- [HTTP Boot Manual Setup](docs/httpboot-manual.md) -- same steps using
  `hammer` CLI, API calls, and UI (no Ansible required)
- [Housekeeping](docs/housekeeping.md) -- detecting and removing
  orphaned resources (configuration drift)

## Getting Started

### 1. Install Ansible on the Control Node

The control node is the machine you run the playbooks from (your laptop, a
jump host, or an AAP controller). Install Ansible on RHEL 9 or 10:

```bash
sudo dnf install -y ansible-core python3-pip
```

Verify the installation:

```bash
ansible --version
```

### 2. Clone the Repository

```bash
git clone <repository-url> satellite-as-code
cd satellite-as-code
```

### 3. Configure the Automation Hub Token

Several collections (`redhat.satellite`, `redhat.satellite_operations`,
`redhat.rhel_system_roles`) are hosted on Red Hat Automation Hub and require
an authentication token.

1. Go to
   [console.redhat.com/ansible/automation-hub/token](https://console.redhat.com/ansible/automation-hub/token)
2. Click **Load token** and copy it
3. Export it as an environment variable (add to `~/.bashrc` or `~/.zshrc`):

```bash
export ANSIBLE_GALAXY_SERVER_AUTOMATION_HUB_TOKEN='your-token-here'
```

The `ansible.cfg` in this project is pre-configured with the Automation Hub
URL and auth endpoint. The token is intentionally **not** stored in
`ansible.cfg` to avoid committing sensitive data to Git.

### 4. Install Required Collections

The project depends on several Ansible collections defined in
`collections/requirements.yml`. Install them into the project-local
`collections/` directory (matching `collections_path` in `ansible.cfg`):

```bash
ansible-galaxy collection install -r collections/requirements.yml \
  -p ./collections
```

If the installation fails due to version resolution, add `--pre` to allow
pre-release versions:

```bash
ansible-galaxy collection install -r collections/requirements.yml \
  -p ./collections --pre
```

### 5. Set Up the Vault Password

All secrets (RHSM credentials, SSH keys, API tokens) are vault-encrypted in
the repository. Ansible needs the vault password to decrypt them.

Create a vault password file **outside the project directory**. Storing it
inside the project would cause problems with AAP (which manages vault
credentials separately) and risks accidental commits:

```bash
echo 'your-vault-password' > ~/.vault-pass-satellite
chmod 600 ~/.vault-pass-satellite
```

Then tell Ansible where to find it. The recommended approach is to set the
environment variable in your shell profile (`~/.bashrc` or `~/.zshrc`):

```bash
export ANSIBLE_VAULT_PASSWORD_FILE=~/.vault-pass-satellite
```

Alternatively, pass it on every run:

```bash
ansible-playbook 03_satellite_installer.yml \
  --vault-password-file ~/.vault-pass-satellite
```

Do **not** configure `vault_password_file` in `ansible.cfg` if you plan to
run this project on AAP. AAP still reads `ansible.cfg` and will attempt to
use the configured path, which will not exist inside the execution
environment and cause the job to fail.

### 6. Configure Credentials

All sensitive values live in `host_vars/<satellite-fqdn>/00a_secrets.yml`.
To create or update vault-encrypted values, use `ansible-vault
encrypt_string`:

**Encrypt a simple string:**

```bash
ansible-vault encrypt_string 'my-secret-password' --name 'variable_name'
```

**Encrypt the contents of a file (e.g. an SSH key or certificate):**

```bash
ansible-vault encrypt_string "$(cat ~/.ssh/id_ed25519.pub)" \
  --name 'sat_remote_execution_ssh_public_key'
```

**Encrypt a private key file:**

```bash
ansible-vault encrypt_string "$(cat /path/to/private-key.pem)" \
  --name 'sat_cert_private_key_content'
```

Paste the output into `00a_secrets.yml`. The file already contains all the
variables used by the playbooks -- replace the existing vault blocks with
your own.

To edit the secrets file interactively:

```bash
ansible-vault edit host_vars/<satellite-fqdn>/00a_secrets.yml
```

The `vault-pki.sh` helper script automates vault-encrypting all certificate
files from a directory:

```bash
./vault-pki.sh .pki/
```

### 7. Prepare the Satellite Target Host

The playbooks connect to the Satellite server via SSH. The target host must
be configured to allow access for the Ansible user **before** running any
playbooks.

**On the target RHEL host** (as root or via console access):

1. Create the Ansible user (matching `ansible_user` in the inventory):

```bash
useradd -m cloud-user
```

2. Grant passwordless sudo:

```bash
echo 'cloud-user ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/cloud-user
chmod 440 /etc/sudoers.d/cloud-user
```

3. Deploy your SSH public key:

```bash
mkdir -p /home/cloud-user/.ssh
chmod 700 /home/cloud-user/.ssh
cat >> /home/cloud-user/.ssh/authorized_keys << 'EOF'
ssh-ed25519 AAAA... your-key-comment
EOF
chmod 600 /home/cloud-user/.ssh/authorized_keys
chown -R cloud-user:cloud-user /home/cloud-user/.ssh
```

4. Verify SSH access from the control node:

```bash
ssh cloud-user@<satellite-fqdn> sudo whoami
# should print: root
```

### 8. Update the Inventory

Edit `inventory` to match your environment:

```ini
[satellite-dev]
satellite.example.com ansible_user=cloud-user

[satellite:children]
satellite-dev
```

### 9. Customize Host Variables

Create or update the `host_vars/<satellite-fqdn>/` directory. At minimum,
review and adjust:

- `00a_secrets.yml` -- RHSM credentials, manifest UUID, SSH keys
- `00b_register_satellite.yml` -- activation key, repositories, RHEL version
- `01a_satellite_installer_certificates.yml` -- certificate paths
- `01c_satellite_installer_configuration.yml` -- installer options

### 10. Run the Playbooks

Execute playbooks in numbered order for a full deployment, or run individual
steps as needed:

```bash
ansible-playbook 01_register_satellite.yml
ansible-playbook 02_satellite_software_install.yml
ansible-playbook 03_satellite_installer.yml
# ... continue with 05-22
```

## Project Structure

```
.
├── 01_register_satellite.yml         # RHEL registration via RHC
├── 02_satellite_software_install.yml # Install Satellite/Capsule packages and prerequisites
├── 03_satellite_installer.yml        # Run the Satellite installer
├── 04_capsule_installer.yml          # Run the Capsule installer
├── 05_satellite_manifest.yml         # Manifest download and upload
├── 06_satellite_content_credentials.yml
├── 07_satellite_products_and_repositories.yml
├── 08_satellite_sync_repositories.yml
├── 09_satellite_sync_plans.yml
├── 10_satellite_lifecycle_environments.yml
├── 11_satellite_domains.yml
├── 12_satellite_subnets.yml
├── 13_satellite_content_views.yml
├── 14_satellite_content_view_publish.yml
├── 15_satellite_settings.yml
├── 16_satellite_operating_systems.yml
├── 17_satellite_activation_keys.yml
├── 18_satellite_host_groups.yml
├── 19_satellite_global_parameters.yml
├── 20_satellite_openscap.yml
├── 21_satellite_role.yml
├── 22_satellite_users.yml
├── 23_satellite_template_deploy.yml  # Build PXE default boot configuration
├── 24_satellite_housekeeping.yml     # Detect and remove orphaned resources
├── inventory                         # Static inventory
├── ansible.cfg
├── collections/
│   ├── requirements.yml              # Collection dependencies
│   └── ansible_collections/          # Installed collections
├── host_vars/
│   └── <satellite-fqdn>/            # All host-specific configuration
│       ├── 00a_secrets.yml           # Vault-encrypted credentials
│       ├── 00b_register_satellite.yml
│       ├── 00c_satellite_software_install.yml  # fapolicyd toggle and rules
│       ├── 01a_satellite_installer_certificates.yml
│       ├── 01b_satellite_firewall_rules.yml
│       ├── 01c_satellite_installer_configuration.yml
│       ├── 02_general.yml
│       ├── 03_cloud_connector.yml
│       ├── 04_manifest.yml
│       ├── 05_content_credentials.yml
│       ├── 06a_products.yml          # Red Hat products/repos
│       ├── 06b_custom_products.yml   # Custom products (EPEL)
│       ├── 06c_combined_products.yml # Merge of 06a + 06b
│       ├── 07_sync_plans.yml
│       ├── 08_lifecycle_environments.yml
│       ├── 09_domains.yml
│       ├── 10_subnets.yml
│       ├── 11a_content_views_custom_products.yml
│       ├── 11b_content_views.yml
│       ├── 11c_composite_content_views.yml
│       ├── 11d_rolling_content_views.yml
│       ├── 11e_combined_content_views.yml  # Merge of 11a-11d
│       ├── 12a-12g_settings_*.yml    # Satellite settings (split by area)
│       ├── 13_operating_systems.yml
│       ├── 14_activation_keys.yml
│       ├── 15a-15d_host_groups_*.yml # Host groups (layered hierarchy)
│       ├── 16a-16e_global_parameters_*.yml
├── docs/
│   ├── httpboot.md                  # HTTP Boot provisioning guide (Ansible)
│   ├── httpboot-manual.md           # HTTP Boot manual setup (hammer/API/UI)
│   └── housekeeping.md              # Configuration drift detection
│       ├── 17_openscap.yml
│       ├── 18_roles.yml
│       └── 19_users
└── group_vars/
    └── satellite-dev.yml             # Group-level overrides
```

## Playbook Execution Order

The playbooks are numbered to indicate their intended execution order. Each
playbook can be executed individually:

```bash
ansible-playbook 15_satellite_settings.yml
```

### Execution Flow

```
01  Register Satellite host with Red Hat (RHC)
02  Install Satellite/Capsule software and prerequisites (fapolicyd, packages)
03  Run the Satellite installer (certificates, firewall, installer)
04  Run the Capsule installer (certificates, firewall, certs archive, installer)
        │
05  Download and upload subscription manifest
06  Create content credentials (GPG keys)
07  Enable products and repositories
08  Synchronize repositories (async)
09  Create sync plans
        │
10  Create lifecycle environments
11  Create domains
12  Create subnets
        │
13  Create content views (regular, composite, rolling)
14  Publish and promote content views (async)
        │
15  Apply Satellite settings
16  Configure operating systems
17  Create activation keys
18  Create host groups (hierarchical)
19  Set global parameters
        │
20  Configure OpenSCAP (compliance policies)
21  Create Satellite roles
22  Create Satellite users
        │
23  Build PXE default boot configuration (deploys GRUB2/PXELinux files to proxies)
        │
24  Housekeeping (detect and remove orphaned host groups, subnets, domains)
```

### Capsule Installation

To install a Satellite Capsule, add the Capsule host to the inventory under
a `[capsule]` group and provide appropriate `host_vars`. The Capsule uses
the same software installation step as the Satellite:

```bash
ansible-playbook 01_register_satellite.yml --limit lab-capsule-1.example.com
ansible-playbook 02_satellite_software_install.yml --limit lab-capsule-1.example.com
ansible-playbook 04_capsule_installer.yml --limit lab-capsule-1.example.com
```

The Capsule installer expects the same firewall and certificate variables as
the Satellite installer, plus `satellite_installer_scenario: capsule` and
a `satellite_installer_certs_tar_file` pointing to the certificate archive
generated on the Satellite server.

## Variable Organization

All environment-specific configuration lives in `host_vars`. The files use a
numbered naming convention that mirrors the playbook they feed into.

### Naming Convention

| Prefix | Area | Example |
|--------|------|---------|
| `00` | Secrets, registration, software install | `00a_secrets.yml` |
| `01` | Installer (certs, firewall, options) | `01c_satellite_installer_configuration.yml` |
| `02` | General / shared variables | `02_general.yml` |
| `03-04` | Cloud connector, manifest | `04_manifest.yml` |
| `05-06` | Content credentials, products | `06a_products.yml` |
| `07-08` | Sync plans, lifecycle environments | `08_lifecycle_environments.yml` |
| `09-10` | Domains, subnets | `10_subnets.yml` |
| `11` | Content views (regular, composite, rolling) | `11b_content_views.yml` |
| `12` | Satellite settings (split by area) | `12c_settings_remote_execution.yml` |
| `13` | Operating systems | `13_operating_systems.yml` |
| `14` | Activation keys | `14_activation_keys.yml` |
| `15` | Host groups (layered hierarchy) | `15a_host_groups_base.yml` |
| `16` | Global parameters | `16b_global_parameters_remote_execution.yml` |
| `17` | OpenSCAP compliance | `17_openscap.yml` |
| `18-19` | Roles, users | `18_roles.yml` |

### Merge Files

Several variable groups are split across multiple files for readability and
then merged in a dedicated file:

- `06c_combined_products.yml` merges `06a` (Red Hat) + `06b` (custom)
- `11e_combined_content_views.yml` merges `11a` through `11d`
- `12g_merge_settings.yml` merges `12a` through `12f`
- `15d_host_groups_merged.yml` merges `15a` through `15c`
- `16e_merge_global_parameters.yml` merges `16a` through `16d`

The playbooks consume only the merged variable (e.g. `satellite_products`,
`satellite_content_views`, `satellite_settings`, `satellite_hostgroups`,
`satellite_global_parameters`).

## Making Changes

### Adding a New Repository

1. Add the product and repository set to `06a_products.yml`
2. Run `07_satellite_products_and_repositories.yml` to enable it
3. Run `08_satellite_sync_repositories.yml` to synchronize

### Adding a Content View

1. Add the content view definition to `11b_content_views.yml` (or `11a` for
   custom products)
2. If composite, add to `11c_composite_content_views.yml`
3. Run `13_satellite_content_views.yml` to create it
4. Run `14_satellite_content_view_publish.yml` to publish and promote

### Adding a New RHEL Version

This requires changes across multiple files:

1. **Products** (`06a`): Add BaseOS, AppStream, Kickstart, and Satellite
   Client repositories for the new version
2. **Content views** (`11b`): Create a base content view with the new
   repositories
3. **Composite content views** (`11c`): Create a composite content view
   referencing the base CV
4. **Operating systems** (`13`): Add the OS definition
5. **Activation keys** (`14`): Create dev and prod activation keys
6. **Host groups** (`15a-15c`): Add OS-level and lifecycle-environment-level
   host groups
7. **OpenSCAP** (`17`): Add compliance policies for the new version

Then run the playbooks from step 07 onward.

### Changing Satellite Settings

1. Edit the appropriate `12x_settings_*.yml` file
2. Run `15_satellite_settings.yml`

### Updating Secrets

All sensitive values are vault-encrypted in `00a_secrets.yml`. See the
Getting Started section for details on creating vault-encrypted values.

### Adding a Capsule

1. Add the Capsule host to `inventory` under a `[capsule]` group
2. Create `host_vars/<capsule-fqdn>/` with the required variable files
   (certificates, firewall rules, installer configuration)
3. On the Satellite server, generate the Capsule certificate archive:

```bash
capsule-certs-generate --foreman-proxy-fqdn <capsule-fqdn> \
  --certs-tar /root/<capsule-fqdn>-certs.tar
```

4. Run the Capsule installer playbook:

```bash
ansible-playbook 04_capsule_installer.yml --limit <capsule-fqdn>
```

## Host Group Hierarchy

Host groups follow a layered structure for maximum reusability:

```
hg-base                                # Architecture, Ansible roles
├── hg-rhel-8                          # OS, PXE loader (PXELinux UEFI), ptable
│   ├── hg-ansible_automation_platform-rhel-8
│   │   ├── ...-dev                    # Lifecycle env, content view, activation key
│   │   └── ...-prod
│   └── hg-default-rhel-8
│       ├── ...-dev
│       └── ...-prod
├── hg-rhel-9
│   └── (same pattern)
├── hg-rhel-10
│   └── (same pattern)
├── hg-httpboot-rhel-9                 # OS, PXE loader (Grub2 UEFI HTTP), ptable
│   ├── hg-httpboot-rhel-9-dev         # deploy.crazy.lab, Capsule as content source
│   └── hg-httpboot-rhel-9-prod
└── hg-httpboot-rhel-10
    ├── hg-httpboot-rhel-10-dev
    └── hg-httpboot-rhel-10-prod
```

See [HTTP Boot Provisioning](docs/httpboot.md) for details on the
HTTP Boot host group configuration.

## Running on Ansible Automation Platform (AAP)

This project is designed to run on AAP without modification:

1. **Project**: Point to this Git repository
2. **Credentials**:
   - **Vault credential**: Provide the vault password
   - **Machine credential**: SSH access to the Satellite host
3. **Job Templates**: Create one per playbook, or a Workflow Template that
   chains them in the numbered order

Secrets (RHSM credentials, SSH keys, certificates) are vault-encrypted in
the repository and travel with the project -- no external secret store is
required.

Do not set `vault_password_file` in `ansible.cfg`. AAP manages vault
credentials through its own credential system, but still reads
`ansible.cfg` -- a configured path that does not exist inside the execution
environment will cause the job to fail.

## License

MIT
