#!/usr/bin/env python3

from ansible.plugins.action import ActionBase
from ansible.utils.vars import merge_hash
import string
import random


def disk_resource(context, disk_name, size, is_ssd, image=None, snapshot=None):
    vm_name = context['name']

    disk = {
        'name': vm_name + '-' + disk_name,
        'type': 'compute.v1.disk',
        'properties': {
            'sizeGb': size,
            'type': 'zones/' + zone(context) + '/diskTypes/' + ('pd-ssd' if is_ssd else 'pd-standard'),
            'zone': zone(context),
        },
    }

    if image:
        disk['properties']['source_image'] = image

    if snapshot:
        disk['properties']['source_snapshot'] = snapshot

    return disk


def attached_disk(context, disk_name, is_boot=False, auto_delete=True):
    vm_name = context['name']

    return {
        'autoDelete': auto_delete,
        'boot': is_boot,
        'deviceName': disk_name,
        'source': '$(ref.' + vm_name + '-' + disk_name + '.selfLink)',
    }


def disk_resources(context):
    disk_arr = [
        disk_resource(
            context=context,
            disk_name='boot',
            is_ssd=True,
            size=int(context['boot_disk_size_gb']),
            image=context['boot_disk_image'],
        )
    ]

    swap_disk_size = int(context['swap_disk_size_gb'])
    if swap_disk_size > 0:
        disk_arr.append(disk_resource(
            context=context,
            disk_name='swap',
            is_ssd=True,
            size=swap_disk_size,
        ))

    disks = context['disks']
    for disk in disks:
        disk_arr.append(disk_resource(
            context=context,
            disk_name=disk['name'],
            image=disk.get('image'),
            is_ssd=disk['is_ssd'],
            size=disk['size_gb'],
            snapshot=disk.get('snapshot')
        ))

    return disk_arr


def attached_disks(context):
    disk_arr = [
        attached_disk(
            context=context,
            disk_name='boot',
            is_boot=True
        )
    ]

    swap_disk_size = int(context['swap_disk_size_gb'])
    if swap_disk_size > 0:
        disk_arr.append(attached_disk(
            context=context,
            disk_name='swap',
        ))

    disks = context['disks']
    for disk in disks:
        disk_arr.append(attached_disk(
            context=context,
            disk_name=disk['name'],
        ))

    return disk_arr


def access_configs(context):
    has_external_ip = context['has_external_ip']
    access_config_arr = []
    if has_external_ip:
        access_config_arr.append({
            'name': 'External NAT',
            'networkTier': 'PREMIUM',
            'type': 'ONE_TO_ONE_NAT',
            'natIP': '$(ref.' + context['name'] + '-eip.address)'
        })
    return access_config_arr


def dns_record(context):
    name = context['name']
    dns_subdomain = context['dns_subdomain']
    dns_zone_name = context['dns_zone_name']

    recordset = {
        'name': name + '.' + dns_subdomain,
        'rrdatas': ['$(ref.' + name + '-eip.address)'],
        'ttl': 300,
        'type': 'A'
    }

    deployment_name = name + '-dns-' + generate_unique_string(8)

    recordset_create = {
        'name': deployment_name + '-create',
        'action': 'gcp-types/dns-v1:dns.changes.create',
        'metadata': {
            'runtimePolicy': [
                'CREATE',
            ],
        },
        'properties':
            {
                'managedZone': dns_zone_name,
                'additions': [recordset]
            },
    }

    recordset_delete = {
        'name': deployment_name + '-delete',
        'action': 'gcp-types/dns-v1:dns.changes.create',
        'metadata': {
            'runtimePolicy': [
                'DELETE',
            ],
        },
        'properties':
            {
                'managedZone': dns_zone_name,
                'deletions': [recordset]
            },
    }

    return [
        recordset_create,
        recordset_delete,
    ]


def external_ip_resource(context):
    name = context['name']

    return {
        'name': name + '-eip',
        'type': 'compute.v1.address',
        'properties': {
            'region': context['region'],
        }
    }


def generate_unique_string(num_chars):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for x in range(num_chars))


def labels(context):
    return_value = {}

    for key, val in context['labels'].items():
        if isinstance(val, list):
            return_value[key] = '_'.join(val)
        else:
            return_value[key] = val

    return return_value


def machine_type(context):
    return 'zones/' + zone(context) + '/machineTypes/' + context['machine_type']


def min_cpu_platform(context):
    min_cpu = context['min_cpu_platform']
    return min_cpu if len(min_cpu) > 0 else None


def service_account(context):
    return context['service_account']


def subnetwork(context):
    return 'regions/' + context['region'] + '/subnetworks/' + context['subnetwork']


def tags(context):
    if 'tags' in context and len(context['tags']) > 0:
        return {
            'items': context['tags']
        }


def zone(context):
    return context['zone']


def get_resources(context):
    resources = []

    name = context['name']
    has_external_ip = context['has_external_ip']

    # If there's an external IP, create a resource for it
    if has_external_ip:
        resources.append(external_ip_resource(context))

    # Create resources for all disks
    resources.extend(disk_resources(context))

    resources.append({
        'name': name,
        'type': 'compute.v1.instance',
        'properties': {
            'canIpForward': context['can_ip_forward'],
            'disks': attached_disks(context),
            'labels': labels(context),
            'machineType': machine_type(context),
            'minCpuPlatform': min_cpu_platform(context),
            'networkInterfaces': [{
                'accessConfigs': access_configs(context),
                'subnetwork': subnetwork(context)
            }],
            'serviceAccounts': [{
                'email': service_account(context),
                'scopes': ['https://www.googleapis.com/auth/cloud-platform']
            }],
            'tags': tags(context),
            'zone': zone(context),
        },
    })

    # If there's an external IP, create a DNS record
    if has_external_ip:
        resources.extend(dns_record(context))

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

