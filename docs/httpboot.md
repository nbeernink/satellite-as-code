# HTTP Boot Provisioning with Satellite and Capsule

This guide covers the end-to-end configuration required to provision hosts
using UEFI HTTP Boot through a Red Hat Satellite Capsule.

> **Looking for the non-Ansible version?** See
> [httpboot-manual.md](httpboot-manual.md) for the same steps using
> `hammer` CLI, Satellite API, and web UI.

## Overview

HTTP Boot is a UEFI firmware feature that replaces traditional PXE (TFTP)
with HTTP for downloading boot files. This brings several advantages:

- **Reliability**: HTTP uses TCP (vs. TFTP's UDP), eliminating packet loss
  issues common on congested or routed networks.
- **Speed**: HTTP transfers are significantly faster than TFTP, especially
  for large boot images.
- **Simplicity**: No need for TFTP relay agents when booting across
  subnets -- standard HTTP proxying or routing is sufficient.
- **Security**: HTTPS can be used to encrypt the boot process.

In a Satellite/Capsule topology, the Capsule serves the HTTP Boot files
on port 8000 (HTTP) or 8443 (HTTPS). The UEFI firmware fetches
`shim.efi`, which loads GRUB2, which then downloads its configuration
from the Satellite template proxy.

### Boot Sequence

```
UEFI Firmware
  │
  ├─ DHCP Discover (gets IP + next-server + boot-file-url)
  │
  ├─ HTTP GET http://<capsule>:8000/EFI/grub2/shim.efi
  │
  ├─ shim.efi loads grubx64.efi
  │
  ├─ GRUB2 fetches grub.cfg from template proxy
  │     http://<capsule>:8000/tftp/grub2/grub.cfg-<MAC or UUID>
  │
  ├─ GRUB2 loads kernel + initrd from kickstart repo
  │
  └─ Anaconda installer runs kickstart from Satellite
```

## Prerequisites

- Red Hat Satellite 6.16+ with a configured Capsule
- UEFI-capable target hardware or VMs (HTTP Boot support in firmware)
- A DHCP server that can serve UEFI HTTP Boot options (the Capsule's
  ISC DHCP or an external DHCP server with option 59/60 configured)
- Network connectivity between the target host and the Capsule on
  ports 8000 (HTTP Boot) and 9090 (Smart Proxy)

## Configuration Steps

The configuration spans both the Capsule (installer options) and the
Satellite (templates, subnets, host groups). Each section below
references the specific variable files in this project.

### Step 1: Capsule Installer -- Enable HTTP Boot

The Capsule must be installed with TFTP, HTTP Boot, and template
proxying enabled. These are set as installer options in
`host_vars/<capsule-fqdn>/01c_capsule_installer_configuration.yml`:

```yaml
satellite_installer_options:
  # ... other options ...

  # TFTP and HTTP Boot
  - '--foreman-proxy-tftp true'
  - '--foreman-proxy-tftp-servername {{ inventory_hostname }}'
  - '--foreman-proxy-httpboot true'
  - '--foreman-proxy-http true'
  - '--foreman-proxy-templates true'
```

| Option | Purpose |
|--------|---------|
| `--foreman-proxy-tftp true` | Enables TFTP (still needed for fallback and file serving) |
| `--foreman-proxy-tftp-servername` | FQDN the TFTP/HTTPBoot service binds to |
| `--foreman-proxy-httpboot true` | Enables the HTTP Boot endpoint on port 8000 |
| `--foreman-proxy-http true` | Serves content via HTTP (not just HTTPS) |
| `--foreman-proxy-templates true` | Enables the template proxy (serves `grub.cfg`) |

Run the Capsule installer playbook to apply:

```bash
ansible-playbook 04_capsule_installer.yml --limit <capsule-fqdn>
```

#### DNS Recursion for the Deploy Subnet

By default, the `satellite-installer` configures BIND with
`allow_recursion: ['none']`. This means BIND is authoritative for
`deploy.crazy.lab` but **denies** all recursive queries. Clients on the
deploy subnet that use the Capsule as their nameserver (via DHCP) will
fail to resolve external names (e.g. `cert-api.access.redhat.com`).

The fix is a custom Hiera override in
`host_vars/<capsule-fqdn>/01c_capsule_installer_configuration.yml`:

```yaml
satellite_installer_custom_hiera:
  dns::allow_recursion:
    - 'localhost'
    - '172.16.80.0/24'
```

The Capsule installer playbook (`04_capsule_installer.yml`) deploys this
to `/etc/foreman-installer/custom-hiera.yaml` before the installer runs.
Custom Hiera is the Red Hat supported mechanism for tuning Puppet class
parameters that have no dedicated installer CLI flag -- it persists
across future `satellite-installer` runs.

### Step 2: Firewall -- Open Port 8000

The Capsule firewall must allow TCP port 8000 for HTTP Boot. Configure
this in `host_vars/<capsule-fqdn>/01b_capsule_firewall_rules.yml`:

```yaml
firewall:
  - port:
      - '8000/tcp'    # HTTP Boot
      - '8443/tcp'    # HTTPS for Smart Proxy
      - '9090/tcp'    # Smart Proxy API
      - '53/tcp'      # DNS
      - '53/udp'      # DNS
      - '67/udp'      # DHCP
      - '69/udp'      # TFTP (fallback)
    service:
      - 'http'
      - 'https'
    zone: 'public'
    state: 'enabled'
    permanent: true
    immediate: true
```

### Step 3: Operating System Templates -- Custom PXEGrub2

HTTP Boot uses GRUB2 as the bootloader. The `PXEGrub2` template kind
must be associated with each operating system that will use HTTP Boot.

The stock `Kickstart default PXEGrub2` template hardcodes
`set default=0`, which always selects the plain PXE (TFTP) menu entry.
For HTTP Boot clients this is wrong -- they need the HTTP entry selected
by default. The `23_satellite_template_deploy.yml` playbook automates
this by:

1. Fetching the stock `Kickstart default PXEGrub2` template from the
   Satellite API.
2. Replacing `set default=0` with ERB logic that selects the correct
   default based on `@host.pxe_loader` (HTTP, HTTPS, or plain PXE).
3. Creating (or updating) a custom template named
   `pvt-kickstart_default_pxegrub2`.

In `host_vars/<satellite-fqdn>/13_operating_systems.yml`, reference the
custom template:

```yaml
satellite_operatingsystems:
  - name: 'RedHat'
    major: 9
    minor: 6
    # ... other fields ...
    default_templates:
      # ... other templates ...

      - template_kind: 'PXEGrub2'
        provisioning_template: 'pvt-kickstart_default_pxegrub2'
        state: 'present'
```

Repeat for every OS version you intend to provision via HTTP Boot.

Apply the changes:

```bash
# Create the custom template and build PXE defaults
ansible-playbook 23_satellite_template_deploy.yml --limit <satellite-fqdn>

# Associate the template with the operating systems
ansible-playbook 16_satellite_operating_systems.yml
```

> **Why is this needed?**
> Without a `PXEGrub2` template associated to the OS, Satellite will not
> generate the GRUB2 configuration files (`grub.cfg`) that the client
> requests after loading `shim.efi`. This manifests as HTTP 404 errors
> during boot and the message "This system was not recognized by
> Foreman." Additionally, the stock template always defaults to the
> TFTP-based boot entry, which fails for HTTP Boot clients.

### Step 4: Subnet -- Assign Capsule as HTTP Boot Proxy

The subnet where HTTP Boot clients reside must point its boot-related
proxies to the Capsule (not the Satellite). Configure this in
`host_vars/<satellite-fqdn>/10_subnets.yml`:

```yaml
satellite_subnets:
  - name: 'sn-172_16_80_0'
    network: '172.16.80.0'
    mask: '255.255.255.0'
    gateway: '172.16.80.254'
    boot_mode: 'DHCP'
    ipam: 'DHCP'
    tftp_proxy: '<capsule-fqdn>'
    httpboot_proxy: '<capsule-fqdn>'
    template_proxy: '<capsule-fqdn>'
    discovery_proxy: '<capsule-fqdn>'
    remote_execution_proxies:
      - '<capsule-fqdn>'
    # ... dns, domains, org, location ...
```

The `boot_mode` and `ipam` settings control how Anaconda configures
networking during installation:

| Setting | Value | Meaning |
|---------|-------|---------|
| `boot_mode: 'DHCP'` | Anaconda uses DHCP to obtain its IP (`ip=...::dhcp` on kernel cmdline) |
| `boot_mode: 'Static'` | Anaconda uses a static IP that must be assigned to the host record |
| `ipam: 'DHCP'` | Satellite queries the DHCP server for lease information |
| `ipam: 'None'` | Satellite does not manage IP allocation (manual assignment required) |

> **Important**: With `boot_mode: 'Static'` and `ipam: 'None'`, every
> host must have an IP address explicitly assigned. If the IP is missing,
> the kernel `ip=` parameter will have an empty client address and
> Anaconda will fail with "missing inst.stage2 or inst.repo".

The critical proxy assignments:

| Proxy | Purpose |
|-------|---------|
| `tftp_proxy` | Serves boot files (also used as fallback for non-HTTP Boot clients) |
| `httpboot_proxy` | Serves boot files over HTTP on port 8000 |
| `template_proxy` | Generates and serves `grub.cfg` and kickstart templates |

Apply the change:

```bash
ansible-playbook 12_satellite_subnets.yml
```

### Step 5: Host Groups -- Use the Grub2 UEFI HTTP PXE Loader

HTTP Boot requires a different PXE loader than traditional PXE. Create
dedicated host groups with `pxe_loader: 'Grub2 UEFI HTTP'`.

This project uses a flat two-level hierarchy for HTTP Boot host groups
(no intermediate service level):

```
hg-base
├── hg-httpboot-rhel-9          # OS level (pxe_loader: Grub2 UEFI HTTP)
│   ├── hg-httpboot-rhel-9-dev  # Lifecycle: dev
│   └── hg-httpboot-rhel-9-prod # Lifecycle: prod
└── hg-httpboot-rhel-10
    ├── hg-httpboot-rhel-10-dev
    └── hg-httpboot-rhel-10-prod
```

**OS-level groups** in `15a_host_groups_base.yml`:

```yaml
sat_operating_system_host_groups:
  # ... existing PXELinux groups ...

  - name: 'hg-httpboot-rhel-9'
    parent: 'hg-base'
    operatingsystem: 'RedHat 9.6'
    pxe_loader: 'Grub2 UEFI HTTP'
    ptable: 'Kickstart default'
    root_pass: !vault |
          ...
```

**Lifecycle groups** in `15c_host_groups_lifecycle_environments.yml`:

```yaml
sat_lifecycle_environment_host_groups:
  # ... existing groups ...

  - name: 'hg-httpboot-rhel-9-dev'
    parent: 'hg-base/hg-httpboot-rhel-9'
    lifecycle_environment: 'lce-default-dev'
    content_view: 'ccv-default-rhel-9'
    domain: 'deploy.crazy.lab'
    subnet: 'sn-172_16_80_0'
    content_source: '<capsule-fqdn>'
    # ... activation_keys, kickstart_repository, etc.
```

Key differences from PXELinux host groups:

| Field | PXELinux | HTTP Boot |
|-------|----------|-----------|
| `pxe_loader` | `PXELinux UEFI` | `Grub2 UEFI HTTP` |
| `subnet` | Main network | Capsule-managed network |
| `content_source` | Satellite | Capsule |
| `domain` | `crazy.lab` | `deploy.crazy.lab` |

Apply the change:

```bash
ansible-playbook 18_satellite_host_groups.yml
```

### Step 6: Build PXE Defaults and Fix the grub.cfg Path

After all template and host group configuration is in place, Satellite
must generate the global PXE boot files and push them to the smart
proxies. This creates the GRUB2 directory tree (`shim.efi`, `grub.cfg`,
module `.lst` files) on each proxy's HTTP Boot root.

The `23_satellite_template_deploy.yml` playbook performs six tasks:

1. **Creates `pvt-kickstart_default_pxegrub2`** (PXEGrub2 template) --
   fetches the stock `Kickstart default PXEGrub2`, patches the
   `set default` logic to auto-select the HTTP/HTTPS/PXE entry based
   on `@host.pxe_loader`, and uploads it as a new template.
2. **Creates `snt-kickstart_rhsm`** (snippet) -- fetches the stock
   `kickstart_rhsm` snippet and prepends a `%pre` script that downloads
   the Satellite CA certificate before the `rhsm` Anaconda addon runs.
   Without this, RHSM registration fails with an SSL certificate
   verification error because the Anaconda environment does not trust
   the Satellite's internal CA.
3. **Creates `snt-post_insights_registration`** (snippet) -- registers
   the host with Red Hat Insights in kickstart `%post`. At this stage
   RHSM is fully configured, so `insights-client --register` with
   `auto_config=True` correctly detects the Satellite/Capsule and
   routes Insights data through it instead of trying Red Hat CDN
   directly. This replaces Anaconda's built-in `ConnectToInsightsTask`
   which cannot auto-detect the Satellite route and fails with 401.
   The global parameter `host_registration_insights=false` disables the
   broken built-in task.
4. **Creates `pvt-kickstart_default`** (provision template) -- fetches
   the stock `Kickstart default`, replaces the `kickstart_rhsm`
   snippet call with `snt-kickstart_rhsm`, and injects the
   `snt-post_insights_registration` snippet before `touch
   /tmp/foreman_built`.
5. **Builds PXE defaults** via the Satellite API, generating the global
   boot files on all smart proxies.
6. **Creates symlinks** on TFTP servers to bridge the path mismatch
   between where DHCP tells clients to look (`/EFI/grub2/`) and where
   "Build PXE Defaults" places `grub.cfg` (`/grub2/`).

All custom templates are associated with the operating systems defined
in `satellite_operatingsystems`. They follow the `pvt-`/`snt-` naming
convention used by the template sync mechanism and do not modify any
locked stock templates.

**Important path mismatch**: Satellite's DHCP `httpclients` class tells
HTTP Boot clients to load `shim.efi` from `/EFI/grub2/` (e.g.,
`http://<capsule>:8000/EFI/grub2/shim.efi`). GRUB2 then looks for
`grub.cfg` relative to this same path. However, "Build PXE Defaults"
generates `grub.cfg` at `/var/lib/tftpboot/grub2/grub.cfg` -- a
different directory. Without a symlink bridging these two paths, GRUB2
cannot find its configuration and falls back to searching local EFI
directories.

Run it against the Satellite first, then against the Capsule:

```bash
ansible-playbook 23_satellite_template_deploy.yml --limit <satellite-fqdn>
ansible-playbook 23_satellite_template_deploy.yml --limit <capsule-fqdn>
```

On the Satellite, the playbook creates the three custom templates,
calls the API endpoint
`POST /api/v2/provisioning_templates/build_pxe_default` (equivalent to
clicking **Build PXE Defaults** in the web UI under *Hosts > Templates
> Provisioning Templates*), and creates the EFI symlinks. On the
Capsule, it creates the symlinks only (the API tasks are skipped
because the Satellite API variables are not defined).

> **This step must be repeated** whenever you change PXE-related
> provisioning templates or add new operating system template
> associations.

### Step 7: Capsule Content Sync

The Capsule must have content synchronized for the lifecycle environments
used by the HTTP Boot host groups. If you haven't already:

```bash
ansible-playbook 04b_capsule_content.yml --limit <capsule-fqdn>
```

## Complete Playbook Execution Order

For a from-scratch setup of HTTP Boot on a new Capsule:

```bash
# 1. Register and install the Capsule
ansible-playbook 01_register_satellite.yml --limit <capsule-fqdn>
ansible-playbook 02_satellite_software_install.yml --limit <capsule-fqdn>
ansible-playbook 04_capsule_installer.yml --limit <capsule-fqdn>

# 2. Create custom templates and deploy PXE boot configuration
ansible-playbook 23_satellite_template_deploy.yml --limit <satellite-fqdn>

# 3. Configure Satellite (OS templates, subnets, host groups)
ansible-playbook 16_satellite_operating_systems.yml --limit <satellite-fqdn>
ansible-playbook 12_satellite_subnets.yml
ansible-playbook 18_satellite_host_groups.yml

# 4. Deploy symlinks on the Capsule
ansible-playbook 23_satellite_template_deploy.yml --limit <capsule-fqdn>

# 5. Sync content to the Capsule
ansible-playbook 04b_capsule_content.yml --limit <capsule-fqdn>
```

## Verification

### On the Capsule

Verify the HTTP Boot directory tree is populated:

```bash
ls -la /var/lib/tftpboot/grub2/
# Should contain: shim.efi, grubx64.efi, grub.cfg

ls -la /var/lib/tftpboot/grub2/EFI/redhat/x86_64-efi/
# Should contain: command.lst, fs.lst, crypto.lst, terminal.lst
```

Verify the HTTP Boot service is listening:

```bash
curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/tftp/grub2/grub.cfg
# Should return 200
```

### On the Satellite

Verify the PXE loader is set correctly for your host group:

```bash
hammer hostgroup info --name 'hg-base/hg-httpboot-rhel-9/hg-httpboot-rhel-9-dev'
```

Look for `PXE Loader: Grub2 UEFI HTTP` in the output.

Verify the subnet proxies:

```bash
hammer subnet info --name 'sn-172_16_80_0'
```

Confirm that `TFTP Proxy`, `HTTPBoot Proxy`, and `Template Proxy` all
point to the Capsule FQDN.

## Troubleshooting

### "This system was not recognized by Foreman"

**Cause**: The host is not registered in Satellite, or the host's MAC
address / IP doesn't match any known host record.

**Fix**: Create the host in Satellite first (manually or via the API)
and assign it to an HTTP Boot host group. Alternatively, enable host
discovery.

### HTTP 404 for boot files after shim.efi loads

**Cause**: PXE defaults have not been built, or the `PXEGrub2` template
is not associated with the operating system.

**Fix**:
1. Associate `Kickstart default PXEGrub2` with the OS (Step 3)
2. Run `ansible-playbook 23_satellite_template_deploy.yml` (Step 6)

### GRUB2 searches local /EFI/\<distro\>/grub.cfg and finds nothing

```
Trying /EFI/fedora/grub.cfg
error: .../../grub-core/commands/search.c:471:
  no such device: /EFI/fedora/grub.cfg.
Trying /EFI/redhat/grub.cfg
...
```

**Cause**: GRUB2 loaded successfully but could not fetch `grub.cfg`
from the HTTP server. It fell back to searching local devices. This
happens when the DHCP `httpclients` class points shim.efi to
`/EFI/grub2/` but `grub.cfg` only exists at `/grub2/grub.cfg` (the
path used by "Build PXE Defaults").

**Fix**: Run `ansible-playbook 23_satellite_template_deploy.yml` against
the Capsule to create the symlink from
`/var/lib/tftpboot/EFI/grub2/grub.cfg` to
`/var/lib/tftpboot/grub2/grub.cfg`.

### GRUB2 module .lst files not found

```
error: .../../grub-core/net/efi/http.c:315:
  file '/EFI/redhat/x86_64-efi/command.lst' not found.
```

**Cause**: Same as above -- the GRUB2 module directory tree hasn't been
generated on the Capsule.

**Fix**: Run `ansible-playbook 23_satellite_template_deploy.yml`.

### Client gets DHCP but no boot file URL

**Cause**: The DHCP server is not providing the HTTP Boot URL (DHCP
option 59 / vendor class).

**Fix**: Ensure the Capsule's DHCP is configured with
`--foreman-proxy-dhcp true` and `--foreman-proxy-dhcp-managed true`.
The Capsule's ISC DHCP automatically serves the correct boot file URL
when managed mode is enabled and the host group uses `Grub2 UEFI HTTP`
as PXE loader.

### RHSM registration fails with SSL CERTIFICATE_VERIFY_FAILED

**Cause**: The `rhsm` Anaconda addon (RHEL 9+) runs before `%post`, so
the Satellite CA certificate is not yet installed in the Anaconda
environment. The addon cannot verify the Satellite/Capsule's SSL
certificate when attempting to register the host.

**Fix**: Ensure the custom `pvt-kickstart_default` provision template
is associated with the OS. This template calls the `snt-kickstart_rhsm`
snippet which includes a `%pre` script that downloads the CA certificate
from `http://<content_source>/pub/katello-server-ca.crt` before
registration. Run:

```bash
ansible-playbook 23_satellite_template_deploy.yml --limit <satellite-fqdn>
ansible-playbook 16_satellite_operating_systems.yml --limit <satellite-fqdn>
```

### GRUB2 menu defaults to PXE entry instead of HTTP Boot

**Cause**: The stock `Kickstart default PXEGrub2` template hardcodes
`set default=0`, which always selects the first (TFTP-based) menu entry
regardless of the host's PXE loader setting.

**Fix**: Ensure the custom template `pvt-kickstart_default_pxegrub2` is
being used. Run `ansible-playbook 23_satellite_template_deploy.yml` to
create/update it, then verify the OS template association in
`13_operating_systems.yml` references `pvt-kickstart_default_pxegrub2`.
Rebuild the host config afterwards:

```bash
hammer host update --name "<host-fqdn>" --build true
```

### Anaconda fails with "missing inst.stage2 or inst.repo"

**Cause**: The kernel `ip=` parameter has an empty client IP address.
This happens when `boot_mode: 'Static'` is set on the subnet but the
host record has no IP assigned. Without network connectivity, Anaconda
cannot fetch the kickstart file or installer image.

**Fix**: Either assign a static IP to the host (`hammer host update
--name "<host-fqdn>" --ip "x.x.x.x"`), or change the subnet to
`boot_mode: 'DHCP'` so Anaconda uses DHCP during installation.

### DNS queries denied on the Capsule (query (cache) ... denied)

```
named: client 172.16.80.x: query (cache) 'example.com/A/IN' denied
```

**Cause**: BIND on the Capsule denies recursive queries from the deploy
subnet. The `satellite-installer` defaults to
`allow_recursion: ['none']`, so BIND only answers authoritatively for
`deploy.crazy.lab` and rejects lookups for external domains.

**Fix**: Add a custom Hiera override (see Step 1, "DNS Recursion for
the Deploy Subnet"). Apply with:

```bash
ansible-playbook 04_capsule_installer.yml --limit <capsule-fqdn>
```

### Insights registration fails with InsightsConnectError during Anaconda

```
InsightsConnectError: Failed to connect to Red Hat Insights.
Upload archive failed with status code 401
```

**Cause**: Anaconda's built-in `ConnectToInsightsTask` tries to reach
`cert-api.access.redhat.com` directly using Satellite-issued identity
certificates. Red Hat CDN rejects these with 401 because the host is
registered through Satellite, not directly.

**Fix**: Set the global parameter `host_registration_insights=false` to
disable the broken Anaconda task. The `snt-post_insights_registration`
snippet handles Insights registration in kickstart `%post` where
`insights-client --register` with `auto_config=True` correctly routes
through Satellite. Run:

```bash
ansible-playbook 19_satellite_global_parameters.yml --limit <satellite-fqdn>
ansible-playbook 23_satellite_template_deploy.yml --limit <satellite-fqdn>
```

### Boot works on Satellite network but not on Capsule network

**Cause**: The subnet's `httpboot_proxy` and `template_proxy` still
point to the Satellite instead of the Capsule.

**Fix**: Update the subnet definition (Step 4) to point all proxies to
the Capsule FQDN.

## PXE Loader Reference

Satellite supports several PXE loaders. Choose based on your boot
method:

| PXE Loader | Boot Method | Protocol | Use Case |
|------------|-------------|----------|----------|
| `PXELinux BIOS` | Legacy BIOS PXE | TFTP | Legacy hardware |
| `PXELinux UEFI` | UEFI via PXELinux | TFTP | UEFI with PXELinux chain |
| `Grub2 UEFI` | UEFI via GRUB2 | TFTP | Standard UEFI PXE |
| `Grub2 UEFI HTTP` | UEFI HTTP Boot | HTTP | Modern UEFI with HTTP Boot |
| `Grub2 UEFI HTTPS` | UEFI HTTPS Boot | HTTPS | Secure HTTP Boot |
| `iPXE Chain BIOS` | iPXE chainloading | HTTP | iPXE environments |
