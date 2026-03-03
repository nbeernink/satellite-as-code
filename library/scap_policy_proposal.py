#!/usr/bin/python
# -*- coding: utf-8 -*-
# (c) 2026 satellite.crazy.lab contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import absolute_import, division, print_function
__metaclass__ = type


DOCUMENTATION = '''
---
module: scap_policy
version_added: 1.0.0
short_description: Manage OpenSCAP Compliance Policies
description:
  - Create, update, and delete OpenSCAP Compliance Policies on a Red Hat Satellite.
  - Resolves SCAP content, profiles, tailoring files, organizations, locations,
    and hostgroups by name automatically through the Foreman API.
author:
  - "satellite.crazy.lab contributors"
options:
  name:
    description:
      - Name of the compliance policy.
    required: true
    type: str
  updated_name:
    description:
      - New name for the policy.
      - When this parameter is set, the module will not be idempotent.
    type: str
  description:
    description:
      - Description of the compliance policy.
    type: str
  scap_content:
    description:
      - Title of the SCAP content to use.
    required: true
    type: str
  scap_content_profile:
    description:
      - The XCCDF profile ID within the SCAP content
        (e.g. C(xccdf_org.ssgproject.content_profile_cis)).
    required: true
    type: str
  tailoring_file:
    description:
      - Name of the SCAP tailoring file to use.
      - Optional. Only needed when customising a profile with a tailoring file.
    type: str
  tailoring_file_profile:
    description:
      - The XCCDF profile ID within the tailoring file.
      - Required when I(tailoring_file) is set.
    type: str
  deploy_by:
    description:
      - Method used to deploy the policy to hosts.
    required: true
    type: str
    choices:
      - ansible
      - puppet
      - manual
  period:
    description:
      - How often the scan should run.
    required: true
    type: str
    choices:
      - weekly
      - monthly
      - custom
  weekday:
    description:
      - Day of the week for weekly scans.
      - Required when I(period=weekly).
    type: str
    choices:
      - monday
      - tuesday
      - wednesday
      - thursday
      - friday
      - saturday
      - sunday
  day_of_month:
    description:
      - Day of the month for monthly scans (1-31).
      - Required when I(period=monthly).
    type: int
  cron_line:
    description:
      - Cron expression for custom scan schedules.
      - Required when I(period=custom).
    type: str
  hostgroups:
    description:
      - List of hostgroup titles to assign to the policy.
      - Use the full hierarchical title (e.g. C(hg-base/hg-rhel-9)).
    type: list
    elements: str
extends_documentation_fragment:
  - redhat.satellite.foreman
  - redhat.satellite.foreman.entity_state
  - redhat.satellite.foreman.taxonomy
notes:
  - Requires the OpenSCAP plugin to be installed on the Satellite server.
  - Verify success in the Satellite UI under Hosts > Compliance > Policies
    or via C(hammer policy list).
'''

EXAMPLES = '''
- name: "Create a CIS compliance policy"
  scap_policy:
    server_url: "https://satellite.example.com"
    username: "admin"
    password: "changeme"
    name: "CIS-Server-L1-RHEL9-Policy"
    description: "CIS Server Level 1 for RHEL 9"
    scap_content: "Red Hat rhel9 default content"
    scap_content_profile: "xccdf_org.ssgproject.content_profile_cis_server_l1"
    deploy_by: "ansible"
    period: "custom"
    cron_line: "0 9 * * *"
    organizations:
      - "ACME"
    locations:
      - "Default Location"
    hostgroups:
      - "hg-base/hg-rhel-9"
    state: present

- name: "Create a policy with a tailoring file"
  scap_policy:
    server_url: "https://satellite.example.com"
    username: "admin"
    password: "changeme"
    name: "CIS-Tailored-RHEL9-Policy"
    scap_content: "Red Hat rhel9 default content"
    scap_content_profile: "xccdf_org.ssgproject.content_profile_cis"
    tailoring_file: "CIS RHEL9 Tailoring"
    tailoring_file_profile: "xccdf_org.ssgproject.content_profile_cis_tailored"
    deploy_by: "ansible"
    period: "weekly"
    weekday: "monday"
    organizations:
      - "ACME"
    locations:
      - "Default Location"
    state: present

- name: "Remove a compliance policy"
  scap_policy:
    server_url: "https://satellite.example.com"
    username: "admin"
    password: "changeme"
    name: "CIS-Server-L1-RHEL9-Policy"
    state: absent
'''

RETURN = '''
entity:
  description: Final state of the affected entities grouped by their type.
  returned: success
  type: dict
  contains:
    policies:
      description: List of SCAP policies.
      type: list
      elements: dict
'''

from ansible_collections.redhat.satellite.plugins.module_utils.foreman_helper import (
    ForemanTaxonomicEntityAnsibleModule,
)


class ForemanPolicyModule(ForemanTaxonomicEntityAnsibleModule):
    pass


def main():
    module = ForemanPolicyModule(
        argument_spec=dict(
            updated_name=dict(),
        ),
        foreman_spec=dict(
            name=dict(required=True),
            description=dict(),
            scap_content=dict(
                type='entity',
                flat_name='scap_content_id',
                resource_type='scap_contents',
                search_by='title',
            ),
            scap_content_profile=dict(
                type='entity',
                flat_name='scap_content_profile_id',
                resource_type='scap_content_profiles',
                search_by='profile_id',
                scope=['scap_content'],
            ),
            tailoring_file=dict(
                type='entity',
                flat_name='tailoring_file_id',
                resource_type='scap_tailoring_files',
            ),
            tailoring_file_profile=dict(
                type='entity',
                flat_name='tailoring_file_profile_id',
                resource_type='scap_tailoring_file_profiles',
                search_by='profile_id',
                scope=['tailoring_file'],
            ),
            deploy_by=dict(
                required=True,
                choices=['ansible', 'puppet', 'manual'],
            ),
            period=dict(
                required=True,
                choices=['weekly', 'monthly', 'custom'],
            ),
            weekday=dict(
                choices=['monday', 'tuesday', 'wednesday', 'thursday',
                         'friday', 'saturday', 'sunday'],
            ),
            day_of_month=dict(type='int'),
            cron_line=dict(),
            hostgroups=dict(type='entity_list'),
        ),
        required_if=[
            ('period', 'weekly', ['weekday']),
            ('period', 'monthly', ['day_of_month']),
            ('period', 'custom', ['cron_line']),
        ],
        required_by=dict(
            tailoring_file_profile=('tailoring_file',),
        ),
        required_plugins=[('openscap', ['*'])],
    )

    with module.api_connection():
        module.run()


if __name__ == '__main__':
    main()
