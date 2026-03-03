# HTTP Boot Provisioning -- Manual Setup Guide

This guide covers the same HTTP Boot setup as [httpboot.md](httpboot.md) but
uses `hammer` CLI, Satellite API (`curl`), web UI, and shell commands
instead of Ansible playbooks. Use it as a reference for understanding
each step or for environments without Ansible automation.

> All `hammer` commands assume the Satellite admin credentials are
> configured in `~/.hammer/cli.modules.d/foreman.yml` or passed via
> `--username` / `--password`.

---

## Step 1: Capsule Installer -- Enable HTTP Boot

On the **Capsule** host, run (or re-run) `satellite-installer` with the
HTTP Boot options:

```bash
satellite-installer --scenario capsule \
  --foreman-proxy-tftp true \
  --foreman-proxy-tftp-servername $(hostname -f) \
  --foreman-proxy-tftp-listen-on both \
  --foreman-proxy-httpboot true \
  --foreman-proxy-http true \
  --foreman-proxy-templates true
```

| Option | Purpose |
|--------|---------|
| `--foreman-proxy-tftp true` | Enables TFTP (still needed for fallback and file serving) |
| `--foreman-proxy-tftp-servername` | FQDN the TFTP/HTTP Boot service binds to |
| `--foreman-proxy-tftp-listen-on both` | Serve TFTP on both HTTP and TFTP |
| `--foreman-proxy-httpboot true` | Enables the HTTP Boot endpoint on port 8000 |
| `--foreman-proxy-http true` | Serves content via HTTP (not just HTTPS) |
| `--foreman-proxy-templates true` | Enables the template proxy (serves `grub.cfg`) |

**UI equivalent**: There is no UI for the installer; it must be run from
the Capsule CLI.

### DNS Recursion for the Deploy Subnet

By default, `satellite-installer` configures BIND with
`allow_recursion: ['none']`. Clients on the deploy subnet that use the
Capsule as their nameserver will fail to resolve external names (e.g.
`cert-api.access.redhat.com`) with `query (cache) ... denied`.

Create (or extend) the custom Hiera file on the **Capsule**:

```bash
cat >> /etc/foreman-installer/custom-hiera.yaml <<'EOF'
dns::allow_recursion:
  - 'localhost'
  - '172.16.80.0/24'
EOF
```

Then re-run the installer:

```bash
satellite-installer --scenario capsule
```

Custom Hiera is the Red Hat supported mechanism for tuning Puppet class
parameters that have no dedicated installer CLI flag. The file persists
across future `satellite-installer` runs.

Verify:

```bash
dig @172.16.80.1 cert-api.access.redhat.com
# Should return an A record
```

---

## Step 2: Firewall -- Open Port 8000

On the **Capsule** host:

```bash
firewall-cmd --permanent --add-port=8000/tcp
firewall-cmd --permanent --add-port=8443/tcp
firewall-cmd --permanent --add-port=9090/tcp
firewall-cmd --permanent --add-port=67/udp
firewall-cmd --permanent --add-port=69/udp
firewall-cmd --permanent --add-port=53/tcp
firewall-cmd --permanent --add-port=53/udp
firewall-cmd --permanent --add-service=http
firewall-cmd --permanent --add-service=https
firewall-cmd --reload
```

---

## Step 3: Custom PXEGrub2 Template

The stock `Kickstart default PXEGrub2` template hardcodes
`set default=0`, which always selects the TFTP-based boot entry. HTTP
Boot clients need the `efi_http` entry selected instead. We clone the
stock template and patch it.

### 3a. Export the stock template

```bash
hammer template dump --name "Kickstart default PXEGrub2" > /tmp/pxegrub2-stock.erb
```

### 3b. Patch the default selection logic

Open `/tmp/pxegrub2-stock.erb` and replace the line:

```erb
set default=0
```

with:

```erb
<% if @host.pxe_loader.to_s =~ /HTTPS/ -%>
set default="efi_https"
<% elsif @host.pxe_loader.to_s =~ /HTTP/ -%>
set default="efi_http"
<% else -%>
set default=0
<% end -%>
```

Also change the template header `name:` to `pvt-kickstart_default_pxegrub2`.

### 3c. Import the custom template

```bash
hammer template create \
  --name "pvt-kickstart_default_pxegrub2" \
  --type PXEGrub2 \
  --file /tmp/pxegrub2-stock.erb \
  --organizations "CRAZY.LAB" \
  --locations "loc-local"
```

Or via the **API**:

```bash
curl -s -H "Content-Type: application/json" -X POST \
  -u admin:password \
  https://satellite.example.com/api/v2/provisioning_templates \
  -d @- <<'JSON'
{
  "provisioning_template": {
    "name": "pvt-kickstart_default_pxegrub2",
    "template_kind_id": <PXEGrub2-kind-id>,
    "template": "<patched ERB content>",
    "organizations": [{"name": "CRAZY.LAB"}],
    "locations": [{"name": "loc-local"}]
  }
}
JSON
```

> **Tip**: To find the `template_kind_id` for PXEGrub2:
> ```bash
> hammer template-kind list | grep PXEGrub2
> ```

### 3d. Associate the custom template with the OS

```bash
hammer os set-default-template \
  --id $(hammer os list | grep "RedHat 9.6" | awk '{print $1}') \
  --provisioning-template "pvt-kickstart_default_pxegrub2" \
  --provisioning-template-type PXEGrub2
```

Or via **UI**: *Hosts > Provisioning Setup > Operating Systems > RedHat
9.6 > Templates tab > PXEGrub2 > select
`pvt-kickstart_default_pxegrub2` > Submit*.

Repeat for every OS version you intend to provision via HTTP Boot (e.g.,
RedHat 10.0).

---

## Step 4: Custom kickstart_rhsm Snippet (SSL CA Trust)

The `rhsm` Anaconda addon (RHEL 9+) registers the host during
installation, before `%post`. It cannot verify the Satellite/Capsule SSL
certificate because the CA is not in Anaconda's trust store.

We create a custom snippet that adds a `%pre` script to download the CA
certificate(s) before the `rhsm` command runs.

### 4a. Export the stock snippet

```bash
hammer template dump --name "kickstart_rhsm" > /tmp/kickstart_rhsm-stock.erb
```

### 4b. Prepend the %pre CA download

Add the following block **before** the `<% if subman_registration -%>`
line in the snippet:

```erb
<% if plugin_present?('katello') && @host.content_source && subman_registration -%>
%pre --log=/tmp/foreman_rhsm_ca.log
set -x
mkdir -p /etc/rhsm/ca
mkdir -p /etc/pki/ca-trust/source/anchors
curl -so /etc/rhsm/ca/katello-server-ca.pem http://<%= @host.content_source.hostname %>/pub/katello-server-ca.crt
curl -so /etc/rhsm/ca/custom-ca-chain.pem http://<%= @host.content_source.hostname %>/pub/custom-ca-chain.pem
cat /etc/rhsm/ca/katello-server-ca.pem /etc/rhsm/ca/custom-ca-chain.pem > /etc/rhsm/ca/redhat-uep.pem
cp /etc/rhsm/ca/custom-ca-chain.pem /etc/pki/ca-trust/source/anchors/
cp /etc/rhsm/ca/katello-server-ca.pem /etc/pki/ca-trust/source/anchors/
update-ca-trust || true
sed -i "s|^repo_ca_cert\s*=.*|repo_ca_cert = /etc/rhsm/ca/katello-server-ca.pem|" /etc/rhsm/rhsm.conf || true
command -v openssl > /dev/null && openssl rehash /etc/rhsm/ca/ || true
%end
<% end -%>
```

Also update the template header: change `name: kickstart_rhsm` to
`name: snt-kickstart_rhsm`.

### Why each line matters

| Command | Purpose |
|---------|---------|
| `curl ... katello-server-ca.crt` | Downloads Satellite's internal katello CA |
| `curl ... custom-ca-chain.pem` | Downloads the custom CA that signed the Capsule's SSL cert (e.g., `crazy.lab Root CA`) |
| `cat ... > redhat-uep.pem` | Bundles both CAs into the file `subscription-manager` reads for server SSL verification |
| `update-ca-trust` | Updates the system CA bundle (may not work in all Anaconda environments) |
| `sed ... repo_ca_cert` | Tells `subscription-manager` where to find the CA for repo access |
| `openssl rehash` | Creates hash symlinks in `ca_cert_dir` for certificate lookup |

> **Custom CA note**: If your Capsule uses the default self-signed katello
> certificates (not a custom CA), you only need the `katello-server-ca.crt`
> download and can skip the `custom-ca-chain.pem` lines. If your Capsule
> uses certificates signed by an external CA (e.g., corporate PKI), you
> **must** publish that CA at `/var/www/html/pub/custom-ca-chain.pem` on
> the Capsule (see Step 6b).

### 4c. Create the custom snippet

```bash
hammer template create \
  --name "snt-kickstart_rhsm" \
  --type snippet \
  --file /tmp/kickstart_rhsm-stock.erb \
  --organizations "CRAZY.LAB" \
  --locations "loc-local"
```

---

## Step 5: Custom Kickstart Default Provision Template

The stock `Kickstart default` calls `snippet('kickstart_rhsm')`. We
clone it and replace that call with our custom snippet.

### 5a. Export the stock template

```bash
hammer template dump --name "Kickstart default" > /tmp/kickstart-default-stock.erb
```

### 5b. Patch the snippet call

In the file, find:

```erb
<%= snippet('kickstart_rhsm') if use_rhsm -%>
```

Replace with:

```erb
<%= snippet('snt-kickstart_rhsm') if use_rhsm -%>
```

Also change the template header `name:` to `pvt-kickstart_default`.

### 5c. Import the custom provision template

```bash
hammer template create \
  --name "pvt-kickstart_default" \
  --type provision \
  --file /tmp/kickstart-default-stock.erb \
  --organizations "CRAZY.LAB" \
  --locations "loc-local"
```

### 5d. Associate with the OS

```bash
hammer os set-default-template \
  --id $(hammer os list | grep "RedHat 9.6" | awk '{print $1}') \
  --provisioning-template "pvt-kickstart_default" \
  --provisioning-template-type provision
```

Or via **UI**: *Hosts > Provisioning Setup > Operating Systems > RedHat
9.6 > Templates tab > provision > select `pvt-kickstart_default` >
Submit*.

### 5e. Associate templates with the OS (compatibility)

The custom templates must also be marked as **compatible** with the OS:

```bash
hammer template add-operatingsystem \
  --name "pvt-kickstart_default_pxegrub2" \
  --operatingsystem "RedHat 9.6"

hammer template add-operatingsystem \
  --name "pvt-kickstart_default" \
  --operatingsystem "RedHat 9.6"
```

Without this, Satellite rejects build requests with *"No PXEGrub2
templates were found for this host"*.

---

## Step 5b: Insights Registration Through Satellite

Anaconda's built-in `ConnectToInsightsTask` tries to reach
`cert-api.access.redhat.com` directly, which fails with 401 because
the host holds Satellite-issued identity certificates. The fix is to
disable the built-in task and register Insights in kickstart `%post`
where `insights-client` can auto-configure through RHSM/Satellite.

### 5b-a. Disable Anaconda's built-in Insights task

```bash
hammer global-parameter set \
  --name "host_registration_insights" \
  --parameter-type boolean \
  --value false
```

This prevents Anaconda's `ConnectToInsightsTask` from running.

### 5b-b. Create the Insights registration snippet

Create a file `/tmp/snt-post_insights_registration.erb`:

```erb
<%#
kind: snippet
name: snt-post_insights_registration
model: ProvisioningTemplate
snippet: true
-%>
<% if plugin_present?('katello') && @host.content_source -%>
if command -v insights-client > /dev/null 2>&1; then
  echo "Registering with Red Hat Insights through Satellite..."
  insights-client --register 2>&1 || echo "WARNING: Insights registration deferred to post-boot"
fi
<% end -%>
```

Import the snippet:

```bash
hammer template create \
  --name "snt-post_insights_registration" \
  --type snippet \
  --file /tmp/snt-post_insights_registration.erb \
  --organizations "CRAZY.LAB" \
  --locations "loc-local"
```

### 5b-c. Patch the provision template

In the `pvt-kickstart_default` template (Step 5b), find the line:

```erb
touch /tmp/foreman_built
```

Insert the snippet call immediately **before** it:

```erb
<%= snippet('snt-post_insights_registration') %>
touch /tmp/foreman_built
```

Then update:

```bash
hammer template update \
  --name "pvt-kickstart_default" \
  --file /tmp/kickstart-default-stock.erb
```

> **How it works**: In `%post`, RHSM is fully configured and points to
> the Capsule. `insights-client --register` with `auto_config=True`
> (the default) reads `/etc/rhsm/rhsm.conf`, detects the Satellite
> environment, and routes Insights data through Satellite instead of
> directly to Red Hat CDN.

---

## Step 6: Subnet Configuration

### 6a. Update the subnet

```bash
hammer subnet update \
  --name "sn-172_16_80_0" \
  --boot-mode "DHCP" \
  --ipam "DHCP" \
  --tftp-id $(hammer proxy list | grep capsule-fqdn | awk '{print $1}') \
  --httpboot-id $(hammer proxy list | grep capsule-fqdn | awk '{print $1}') \
  --template-id $(hammer proxy list | grep capsule-fqdn | awk '{print $1}')
```

Or via **UI**: *Infrastructure > Subnets > sn-172_16_80_0 > Capsules
tab*.

| Field | Value |
|-------|-------|
| Boot Mode | DHCP |
| IPAM | DHCP |
| TFTP Proxy | `<capsule-fqdn>` |
| HTTPBoot Proxy | `<capsule-fqdn>` |
| Template Proxy | `<capsule-fqdn>` |

### 6b. Publish the custom CA on the Capsule

If the Capsule uses certificates signed by an external CA (not the
default katello self-signed CA), copy the CA chain to the Capsule's
public HTTP directory:

```bash
# On the Capsule
cp /etc/pki/satellite/lab-capsule-6.deploy.crazy.lab-chain.pem \
   /var/www/html/pub/custom-ca-chain.pem
chmod 644 /var/www/html/pub/custom-ca-chain.pem
```

Verify it is accessible:

```bash
curl -so /dev/null -w '%{http_code}' http://localhost/pub/custom-ca-chain.pem
# Should return 200
```

---

## Step 7: Host Groups

### 7a. Create the OS-level host group

```bash
hammer hostgroup create \
  --name "hg-httpboot-rhel-9" \
  --parent "hg-base" \
  --operatingsystem "RedHat 9.6" \
  --pxe-loader "Grub2 UEFI HTTP" \
  --partition-table "Kickstart default" \
  --organizations "CRAZY.LAB" \
  --locations "loc-local"
```

Or via **UI**: *Configure > Host Groups > Create Host Group*.

### 7b. Create the lifecycle host group

```bash
hammer hostgroup create \
  --name "hg-httpboot-rhel-9-dev" \
  --parent "hg-base/hg-httpboot-rhel-9" \
  --lifecycle-environment "lce-default-dev" \
  --content-view "ccv-default-rhel-9" \
  --domain "deploy.crazy.lab" \
  --subnet "sn-172_16_80_0" \
  --content-source "lab-capsule-6.deploy.crazy.lab" \
  --organizations "CRAZY.LAB" \
  --locations "loc-local"
```

Set the activation key:

```bash
hammer hostgroup set-parameter \
  --hostgroup "hg-base/hg-httpboot-rhel-9/hg-httpboot-rhel-9-dev" \
  --name "kt_activation_keys" \
  --value "ak-default-rhel-9-dev"
```

Set the kickstart repository:

```bash
hammer hostgroup update \
  --name "hg-base/hg-httpboot-rhel-9/hg-httpboot-rhel-9-dev" \
  --kickstart-repository "Red Hat Enterprise Linux 9 for x86_64 - BaseOS Kickstart 9.6"
```

---

## Step 8: Build PXE Defaults

### 8a. Trigger via hammer

```bash
hammer template build-pxe-default
```

### 8b. Trigger via API

```bash
curl -s -X POST \
  -u admin:password \
  https://satellite.example.com/api/v2/provisioning_templates/build_pxe_default
```

### 8c. Trigger via UI

Navigate to *Hosts > Templates > Provisioning Templates* and click
**Build PXE Default** at the top.

> **This must be repeated** whenever you change PXE-related provisioning
> templates or add new OS template associations.

---

## Step 9: Fix the grub.cfg Path (on Capsule)

Satellite's DHCP `httpclients` class tells HTTP Boot clients to load
`shim.efi` from `/EFI/grub2/`, but "Build PXE Defaults" places
`grub.cfg` at `/var/lib/tftpboot/grub2/grub.cfg`. GRUB2 searches
relative to its boot path and cannot find the config. Create symlinks
to bridge this gap.

On the **Capsule**:

```bash
mkdir -p /var/lib/tftpboot/EFI/grub2
mkdir -p /var/lib/tftpboot/EFI/redhat
chown -R foreman-proxy:root /var/lib/tftpboot/EFI

ln -sf /var/lib/tftpboot/grub2/grub.cfg /var/lib/tftpboot/EFI/grub2/grub.cfg
ln -sf /var/lib/tftpboot/grub2/grub.cfg /var/lib/tftpboot/EFI/redhat/grub.cfg
```

Verify:

```bash
curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/EFI/grub2/grub.cfg
# Should return 200
```

---

## Step 10: Create and Provision a Host

### 10a. Create the host

```bash
hammer host create \
  --name "lab-rhel-9-006" \
  --hostgroup "hg-base/hg-httpboot-rhel-9/hg-httpboot-rhel-9-dev" \
  --mac "bc:24:11:18:24:1c" \
  --build true \
  --organization "CRAZY.LAB" \
  --location "loc-local" \
  --managed true
```

### 10b. Rebuild an existing host

```bash
hammer host update \
  --name "lab-rhel-9-006.deploy.crazy.lab" \
  --build true
```

### 10c. Verify the rendered templates

Check the generated kickstart:

```
https://<satellite>/unattended/provision?hostname=<host-fqdn>
```

Check the generated GRUB2 config (on the Capsule):

```bash
cat /var/lib/tftpboot/grub2/grub.cfg-01-<mac-with-dashes>
```

The `set default` line should show the ERB logic selecting `efi_http`
for HTTP Boot hosts.

---

## Verification Checklist

Run these checks after completing all steps:

```bash
# 1. Capsule HTTP Boot is serving files
curl -s -o /dev/null -w '%{http_code}' http://<capsule>:8000/EFI/grub2/grub.cfg

# 2. Custom CA is published (only if using custom certs)
curl -s -o /dev/null -w '%{http_code}' http://<capsule>/pub/custom-ca-chain.pem

# 3. Host group has correct PXE loader
hammer hostgroup info --name 'hg-base/hg-httpboot-rhel-9/hg-httpboot-rhel-9-dev' \
  | grep "PXE Loader"
# Expected: Grub2 UEFI HTTP

# 4. Subnet points to the Capsule
hammer subnet info --name 'sn-172_16_80_0' | grep -E "TFTP|HTTPBoot|Template"

# 5. OS templates are set
hammer os info --id <os-id> | grep -A20 "Default templates"
# Should show pvt-kickstart_default_pxegrub2 for PXEGrub2
# Should show pvt-kickstart_default for provision

# 6. Custom templates are compatible with the OS
hammer template info --name "pvt-kickstart_default_pxegrub2" | grep "Operating"
hammer template info --name "pvt-kickstart_default" | grep "Operating"
```

---

## Troubleshooting

### "No PXEGrub2 templates were found for this host"

The custom template exists but is not associated with the OS.

```bash
hammer template add-operatingsystem \
  --name "pvt-kickstart_default_pxegrub2" \
  --operatingsystem "RedHat 9.6"
```

### RHSM registration fails with SSL CERTIFICATE_VERIFY_FAILED

`subscription-manager` reads `/etc/rhsm/ca/redhat-uep.pem` for server
SSL verification. In the Anaconda environment this file contains the
default Red Hat CA, not the Satellite/Capsule CA.

**Quick test** from the Anaconda shell (Alt+F2 or SSH via
`/usr/sbin/sshd -f /etc/ssh/sshd_config.anaconda`):

```bash
# Confirm with insecure mode first
subscription-manager config --server.insecure=1
subscription-manager register --org=CRAZY_LAB \
  --activationkey=ak-default-rhel-9-dev \
  --serverurl=https://<capsule>/rhsm \
  --baseurl=https://<capsule>/pulp/content
```

If that succeeds, the issue is purely CA trust. Fix:

```bash
# Download both CAs and bundle them
curl -so /etc/rhsm/ca/katello-server-ca.pem http://<capsule>/pub/katello-server-ca.crt
curl -so /etc/rhsm/ca/custom-ca.pem http://<capsule>/pub/custom-ca-chain.pem
cat /etc/rhsm/ca/katello-server-ca.pem /etc/rhsm/ca/custom-ca.pem \
  > /etc/rhsm/ca/redhat-uep.pem
openssl rehash /etc/rhsm/ca/

# Now register with SSL verification
subscription-manager config --server.insecure=0
subscription-manager register --org=CRAZY_LAB \
  --activationkey=ak-default-rhel-9-dev \
  --serverurl=https://<capsule>/rhsm \
  --baseurl=https://<capsule>/pulp/content
```

### DNS queries denied on the Capsule

```
named: client 172.16.80.x: query (cache) 'example.com/A/IN' denied
```

BIND denies recursive queries from the deploy subnet. See Step 1,
"DNS Recursion for the Deploy Subnet".

### Insights registration fails with 401 during Anaconda

Anaconda's `ConnectToInsightsTask` reaches `cert-api.access.redhat.com`
directly and gets rejected. Set `host_registration_insights=false`
(Step 5b-a) and use the `snt-post_insights_registration` snippet
(Step 5b-b) instead.

### GRUB2 menu defaults to PXE entry (not HTTP Boot)

The stock template hardcodes `set default=0`. Check the host-specific
`grub.cfg`:

```bash
cat /var/lib/tftpboot/grub2/grub.cfg-01-<mac-with-dashes>
```

If it shows `set default=0`, the custom template is not active. Verify:

```bash
hammer os info --id <os-id> | grep PXEGrub2
```

Should show `pvt-kickstart_default_pxegrub2`. If it shows the stock
template, reassign it (Step 3d) and rebuild PXE defaults (Step 8).

### Anaconda fails with "missing inst.stage2 or inst.repo"

The kernel `ip=` parameter has an empty client IP. Check the subnet
boot mode:

```bash
hammer subnet info --name 'sn-172_16_80_0' | grep "Boot Mode"
```

If it shows `Static` but no IP is assigned to the host, change to DHCP:

```bash
hammer subnet update --name "sn-172_16_80_0" --boot-mode DHCP --ipam DHCP
```

Then rebuild the host.

### Accessing the Anaconda shell during installation

- **VNC console**: Use the virtual console provided by your hypervisor.
- **SSH**: From the Anaconda shell, start SSH with:
  ```bash
  /usr/sbin/sshd -f /etc/ssh/sshd_config.anaconda
  ```
  Then SSH in as `root` (no password required).
- **Keyboard layout**: If the Anaconda shell has the wrong layout:
  ```bash
  loadkeys us
  ```

---

## Summary of Custom Templates

| Template Name | Type | What it changes |
|--------------|------|-----------------|
| `pvt-kickstart_default_pxegrub2` | PXEGrub2 | Auto-selects HTTP/HTTPS/PXE boot entry based on `@host.pxe_loader` |
| `snt-kickstart_rhsm` | Snippet | Adds `%pre` script to download CA certs and configure `rhsm.conf` for SSL trust |
| `snt-post_insights_registration` | Snippet | Registers host with Red Hat Insights via Satellite in `%post` (replaces Anaconda's broken `ConnectToInsightsTask`) |
| `pvt-kickstart_default` | Provision | Calls `snt-kickstart_rhsm` instead of stock `kickstart_rhsm`; includes `snt-post_insights_registration` before `touch /tmp/foreman_built` |

These follow the `pvt-` (provisioning template) / `snt-` (snippet)
naming convention. Stock templates are never modified -- custom
templates are cloned, patched, and associated with the OS.

---

## Mapping: Ansible Playbook to Manual Steps

| Ansible Playbook | Manual Equivalent |
|-----------------|-------------------|
| `04_capsule_installer.yml` | `satellite-installer --scenario capsule ...` (Step 1) + custom Hiera for DNS recursion |
| `23_satellite_template_deploy.yml` (Satellite) | Steps 3-5 + 5b + Step 8 (template creation + Insights snippet + Build PXE Defaults) |
| `23_satellite_template_deploy.yml` (Capsule) | Steps 6b + 9 (publish CA + create symlinks) |
| `16_satellite_operating_systems.yml` | Step 3d + 5d + 5e (OS template associations) |
| `12_satellite_subnets.yml` | Step 6a (`hammer subnet update`) |
| `18_satellite_host_groups.yml` | Step 7 (`hammer hostgroup create`) |
| `19_satellite_global_parameters.yml` | `hammer global-parameter set ...` (incl. `host_registration_insights`) |
| `04b_capsule_content.yml` | *Content > Smart Proxies > Capsule > Synchronize* |
