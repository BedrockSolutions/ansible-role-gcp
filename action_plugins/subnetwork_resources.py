#!/usr/bin/env python3

from ansible.plugins.action import ActionBase
from ansible.utils.vars import merge_hash


def get_resources(context):
    resources = []

    name = context['name']

    resources.append({
        'name': name,
        'type': 'compute.v1.subnetwork',
        'properties': {
            'name': name,
            'ipCidrRange': context['ipSubnet'],
            'region': context['region'],
            'network': context['vpcNetwork'],
        },
    })

    return resources


class ActionModule(ActionBase):
    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        action_vars = self._task.args

        # combine task and action vars, and call it context
        context = merge_hash(task_vars, action_vars)

        # get the return object from the parent class
        return_value = super(ActionModule, self).run(tmp, task_vars)

        # put the resources under the 'result' key
        return_value['result'] = get_resources(context)

        return return_value
