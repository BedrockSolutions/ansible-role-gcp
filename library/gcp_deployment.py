#!/usr/bin/python

# Copyright: (c) 2018, Devin Solutions s.r.o.
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

ANSIBLE_METADATA = {
    'metadata_version': '1.1',
    'status': ['preview'],
    'supported_by': 'community'
}

DOCUMENTATION = '''
---
module: gcp_deployment
short_description: Manage deployments on GCP
description:
    - This module manages deployments using Google Cloud Deployment Manager.
    - At the time of writing, existing GCP modules are unable to update existing configuration.
      This module can be used instead to always achieve the desired configuration state.
requirements:
    - Python >= 3.6
    - google-api-python-client >= 1.7.0
    - PyYAML
options:
    auth_kind:
        choices:
            - application
            - serviceaccount
        description:
            - The type of credentials used.
        required: true
    config:
        description:
            - Config that specifies resources to deploy. See L(Syntax Reference,
              https://cloud.google.com/deployment-manager/docs/configuration/syntax-reference).
        required: true
        type: dict
    create_policy:
        choices:
            - create-or-acquire
            - acquire
        default: create-or-acquire
        description:
            - Create policy for resources that have changed in the update.
    delete_policy:
        choices:
            - delete
            - abandon
        default: delete
        description:
            - Delete policy for resources that will change as part of an update or
              delete. C(delete) deletes the resource while C(abandon) just removes the
              resource reference from the deployment.
    name:
        description:
            - Deployment name.
        required: true
        type: str
    project:
        description:
            - The Google Cloud Platform project to use.
        required: true
        type: str
    scopes:
        default:
            - https://www.googleapis.com/auth/cloud-platform
        description:
            - Array of scopes to be used.
        elements: str
        type: list
    service_account_file:
        description:
            - The path of a Service Account JSON file.
        type: path
    state:
        choices:
            - present
            - absent
        default: present
        description:
            - Whether the given deployment should exist.
notes:
    - For authentication, you can set service_account_file using the
      C(GCP_SERVICE_ACCOUNT_FILE) env variable.
author:
    - Ľubomír Kučera (lubomir.kucera@devinsolutions.com)
'''

EXAMPLES = '''
- name: Create GKE cluster
  gcp_deployment:
    config:
      resources:
        - name: k8s-prod-01
          type: container.v1.cluster
          properties:
            zone: europe-west3-a
            cluster:
              description: Primary production cluster
              locations:
                - europe-west3-a
              nodePools:
                - name: default-pool
                  initialNodeCount: 1
                  config:
                    machineType: g1-small
    create_policy: create-or-acquire
    delete_policy: delete
    name: k8s-prod-01
    project: test_project
    scopes:
      - https://www.googleapis.com/auth/cloud-platform
    service_account_file: /tmp/auth.pem
    state: present
'''

import time  # noqa: E402

from ansible.module_utils.basic import AnsibleModule, env_fallback  # noqa: E402

try:
    import google.auth
    from google.oauth2.service_account import Credentials
    from googleapiclient import discovery
    from googleapiclient.errors import HttpError

    HAS_API_CLIENT = True
except ImportError:
    HAS_API_CLIENT = False

try:
    import yaml

    HAS_YAML = True
except ImportError as exc:
    HAS_YAML = False

MODULE_ARGS = dict(
    auth_kind=dict(
        choices=[
            'application',
            'serviceaccount',
        ],
        default='application',
        required=True,
    ),
    config=dict(
        required=True,
        type='dict',
    ),
    create_policy=dict(
        choices=[
            'create-or-acquire',
            'acquire',
        ],
        default='create-or-acquire',
    ),
    delete_policy=dict(
        choices=[
            'delete',
            'abandon',
        ],
        default='delete',
    ),
    name=dict(
        required=True,
        type='str',
    ),
    project=dict(
        default=None,
        required=True,
        type='str',
    ),
    scopes=dict(
        default=[
            # Using https://www.googleapis.com/auth/ndev.cloudman results in HTTP error 500
            'https://www.googleapis.com/auth/cloud-platform',
        ],
        elements='str',
        type='list',
    ),
    service_account_file=dict(
        fallback=(env_fallback, ['GCP_SERVICE_ACCOUNT_FILE']),
        type='path',
    ),
    state=dict(
        choices=[
            'present',
            'absent',
        ],
        default='present',
    ),
)


class OperationError(Exception):
    pass


class OperationTimeout(Exception):
    pass


def get_real_policy_name(name):
    return name.upper().replace('-', '_')


def wait_for_operation(request, check_interval=1, timeout=180):
    ticks = 0

    while True:
        operation = request.execute()

        if operation['status'] == 'DONE':
            break

        if ticks >= timeout:
            raise OperationTimeout(
                "Operation " + operation['name'] + " exceeded timeout of " + str(timeout) + " seconds."
            )

        time.sleep(check_interval)
        ticks += check_interval

    errors = [error['message'] for error in operation.get('error', {}).get('errors', [])]

    if errors:
        raise OperationError(
            "Errors occured while processing operation " + operation['name'] + ": " + str(errors)
        )


def main():
    module = AnsibleModule(argument_spec=MODULE_ARGS, supports_check_mode=True)

    if not HAS_API_CLIENT:
        module.fail_json(msg="Please install the google-api-python-client library.")

    if not HAS_YAML:
        module.fail_json(msg="Please install the PyYAML library.")

    auth_kind = module.params['auth_kind']
    config = module.params['config']
    create_policy = get_real_policy_name(module.params['create_policy'])
    delete_policy = get_real_policy_name(module.params['delete_policy'])
    name = module.params['name']
    project = module.params['project']
    scopes = module.params['scopes']
    service_account_file = module.params['service_account_file']
    state = module.params['state']

    if auth_kind == 'application':
        credentials, _ = google.auth.default(scopes=scopes)
    elif auth_kind == 'serviceaccount':
        credentials = Credentials.from_service_account_file(
            service_account_file, scopes=scopes,
        )
    else:
        module.fail_json(msg="Authentication kind '" + auth_kind + "' is not implemented")

    deployment_manager = discovery.build('deploymentmanager', 'v2', credentials=credentials)
    deployments = deployment_manager.deployments()
    operations = deployment_manager.operations()
    resources = deployment_manager.resources()

    changed = False
    deployment = None
    operation = None

    try:
        get_deployment = deployments.get(project=project, deployment=name)
        try:
            deployment = get_deployment.execute()
        except HttpError as error:
            if error.resp.status != 404:
                raise

        if state == 'present':
            body = dict(
                name=name,
                target=dict(
                    config=dict(
                        content=yaml.safe_dump(config, default_flow_style=False),
                    ),
                ),
            )

            if not deployment:
                if not module.check_mode:
                    operation = deployments.insert(
                        project=project, body=body, createPolicy=create_policy,
                    ).execute()

                changed = True
            else:
                body['fingerprint'] = deployment['fingerprint']

                operation = deployments.update(
                    project=project, deployment=name, body=body, deletePolicy=delete_policy,
                    createPolicy=create_policy, preview=True,
                ).execute()

                wait_for_operation(operations.get(
                    project=project, operation=operation['name'],
                ))

                # Get a new fingerprint now, so that the deployment won't get updated if it
                # changes in the meantime.
                deployment = get_deployment.execute()

                next_page_token = None
                while not changed:
                    resources_list = resources.list(
                        project=project, deployment=name, pageToken=next_page_token,
                    ).execute()

                    changed = any('update' in res for res in resources_list['resources'])

                    next_page_token = resources_list.get('nextPageToken')
                    if next_page_token is None:
                        break

                if changed and not module.check_mode:
                    body['fingerprint'] = deployment['fingerprint']
                    del body['target']

                    operation = deployments.update(
                        project=project, deployment=name, body=body, deletePolicy=delete_policy,
                        createPolicy=create_policy,
                    ).execute()
                else:
                    operation = deployments.cancelPreview(
                        project=project, deployment=name, body=dict(
                            fingerprint=deployment['fingerprint'],
                        ),
                    ).execute()
        elif state == 'absent' and deployment:
            if not module.check_mode:
                operation = deployments.delete(
                    project=project, deployment=name, deletePolicy=delete_policy,
                ).execute()

            changed = True

        if operation:
            wait_for_operation(operations.get(
                project=project, operation=operation['name'],
            ))
    except (HttpError, OperationError, OperationTimeout) as error:
        module.fail_json(msg=str(error))

    module.exit_json(changed=changed)


if __name__ == '__main__':
    main()