#!/usr/bin/env python3

from ansible.errors import AnsibleError
from ansible.plugins.action import ActionBase
from ansible.utils.vars import merge_hash

try:
    from googleapiclient import discovery
    from googleapiclient.errors import HttpError

    HAS_API_CLIENT = True
except ImportError:
    HAS_API_CLIENT = False


class ActionModule(ActionBase):
    def run(self, tmp=None, task_vars=None):
        if not HAS_API_CLIENT:
            raise AnsibleError("Please install the google-api-python-client library")

        # combine task and action vars, and call it context
        if task_vars is None:
            task_vars = dict()

        action_vars = self._task.args
        context = merge_hash(task_vars, action_vars)

        # get variables
        project = context['project']
        ssh_keys = context['ssh_keys']

        # map the keys into the proper format
        def to_key_format(obj):
            return obj['username'] + ":" + obj['key_type'] + " " + obj['key_data'] + " " + obj['comment']
        key_string = "\n".join(map(to_key_format, ssh_keys))

        # get the projects api service object
        projects_api = discovery.build('compute', 'v1').projects()

        # get the metadata fingerprint
        fingerprint = projects_api.get(project=project).commonInstanceMetadata.fingerprint

        # set the ssh-key metadata value
        request_body = {
            'fingerprint': fingerprint,
            'items': [{
                'key': 'ssh-keys',
                'value': key_string,
            }]
        }
        result = projects_api.setCommonInstanceMetadata(project=project, body=request_body)

        # raise if the result contains errors
        if len(result.get('error', {}).get('errors', [])):
            raise AnsibleError('Error while setting project metadata', result['error'])

        # get the return object from the parent class
        return_value = super(ActionModule, self).run(tmp, task_vars)

        # put the result in the return object
        return_value['result'] = result

        return return_value

