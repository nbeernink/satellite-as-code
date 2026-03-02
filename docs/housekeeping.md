# Satellite Housekeeping

Over time, a Satellite server can accumulate resources that were created
manually or through earlier automation runs but are no longer represented
in the repository. The housekeeping playbook detects this **configuration
drift** and optionally removes the orphaned resources.

## How It Works

The playbook follows a simple three-step process for each resource type:

1. **Fetch** all resources of that type from the Satellite API
2. **Compare** them against the definitions in `host_vars`
3. **Report** (and optionally **remove**) anything that exists in
   Satellite but is not defined in the repository

```
Repository (desired state)          Satellite (actual state)
┌────────────────────┐              ┌────────────────────┐
│ hg-base            │              │ hg-base            │
│ hg-rhel-9          │              │ hg-rhel-9          │
│ hg-default-rhel-9  │   compare   │ hg-default-rhel-9  │
│                    │ ──────────> │ hg-old-test        │ ← orphan
│                    │              │ hg-manual-setup    │ ← orphan
└────────────────────┘              └────────────────────┘
```

## Supported Resource Types

| Resource | Repo Variable | Comparison Key |
|----------|--------------|----------------|
| Host groups | `satellite_hostgroups` | Full title (parent path + name) |
| Subnets | `satellite_subnets` | Name |
| Domains | `satellite_domains` | Name |

Additional resource types (activation keys, content views, lifecycle
environments) can be added following the same pattern.

## Usage

### Dry Run (default)

By default, the playbook only **reports** orphaned resources without
making any changes:

```bash
ansible-playbook 24_satellite_housekeeping.yml
```

Sample output:

```
TASK [Report orphaned host groups] ********
ok: [lab-satellite-6.crazy.lab] =>
  msg: 3 orphaned host group(s): hg-base/hg-old-test/hg-old-test-dev,
       hg-base/hg-old-test/hg-old-test-prod, hg-base/hg-old-test

TASK [Subnets are in sync] ********
ok: [lab-satellite-6.crazy.lab] =>
  msg: All subnets in Satellite match the repository definition.

TASK [Domains are in sync] ********
ok: [lab-satellite-6.crazy.lab] =>
  msg: All domains in Satellite match the repository definition.

TASK [Housekeeping summary] ********
ok: [lab-satellite-6.crazy.lab] =>
  msg:
  - '--- Satellite Housekeeping Summary ---'
  - 'Host groups : 3 orphaned (of 25 total)'
  - 'Subnets     : 0 orphaned (of 2 total)'
  - 'Domains     : 0 orphaned (of 2 total)'
  - ''
  - 'DRY RUN: No changes made. Re-run with -e satellite_housekeeping_enforce=true to remove orphans.'
```

### Enforce Mode

To actually remove orphaned resources, set
`satellite_housekeeping_enforce` to `true`:

```bash
ansible-playbook 24_satellite_housekeeping.yml \
  -e satellite_housekeeping_enforce=true
```

### Deletion Order

Host groups are deleted **deepest first** (children before parents) to
respect Satellite's referential integrity constraints. The playbook
achieves this by sorting orphaned titles in reverse alphabetical order,
which naturally puts deeper paths before their ancestors.

For example, given these orphans:

```
hg-base/hg-old-test/hg-old-test-dev
hg-base/hg-old-test/hg-old-test-prod
hg-base/hg-old-test
```

The deletion order is:
1. `hg-base/hg-old-test/hg-old-test-prod`
2. `hg-base/hg-old-test/hg-old-test-dev`
3. `hg-base/hg-old-test`

## Safety

- **Default is dry run**: The playbook never deletes anything unless you
  explicitly pass `-e satellite_housekeeping_enforce=true`.
- **No false positives on repo-defined resources**: The comparison is
  based on exact name/title matching against `host_vars` variables. Only
  resources absent from the repository are flagged.
- **Dependency errors surface naturally**: If a resource cannot be
  deleted because other objects depend on it (e.g., a host group with
  assigned hosts, a subnet referenced by a host), the Satellite API will
  return an error and the task will fail with a descriptive message.
  Fix the dependency first, then re-run.

## Extending to New Resource Types

To add housekeeping for a new resource type (e.g., activation keys),
add a block to `24_satellite_housekeeping.yml` following this pattern:

```yaml
    - name: 'Fetch all <resources> from Satellite'
      redhat.satellite.resource_info:
        username: '{{ satellite_username }}'
        password: '{{ satellite_password }}'
        server_url: '{{ satellite_server_url }}'
        validate_certs: '{{ satellite_validate_certs | default(false) }}'
        resource: '<api_resource_name>'
      register: '__t_xx_satellite'

    - name: 'Identify orphaned <resources>'
      ansible.builtin.set_fact:
        __t_xx_orphaned: >-
          {{ __t_xx_satellite.resources |
             map(attribute='name') |
             reject('in', <repo_variable> | default([]) |
               map(attribute='name') | list) |
             list }}

    - name: 'Remove orphaned <resources>'
      redhat.satellite.<module>:
        # ... credentials ...
        name: '{{ item }}'
        state: 'absent'
      loop: '{{ __t_xx_orphaned }}'
      when:
        - satellite_housekeeping_enforce | bool
        - __t_xx_orphaned | length > 0
```

Then add the new counters to the summary task at the end of the
playbook.

## Integration with CI/CD

The housekeeping playbook works well as a scheduled job in AAP:

- **Weekly dry-run job**: Runs with default settings, sends a
  notification if orphans are detected.
- **On-demand enforce job**: Triggered manually after reviewing the
  dry-run report to clean up confirmed orphans.
