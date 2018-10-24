#!/usr/bin/env python3

#  Copyright 2018 U.C. Berkeley RISE Lab
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

import boto3
import kubernetes as k8s
import random
from util import NAMESPACE, EBS_VOL_COUNT, replace_yaml_val, load_yaml, \
        run_process, check_or_get_env_arg


cfg = k8s.config
cfg.load_kube_config()

client = k8s.client.CoreV1Api()
create_client = k8s.client.ExtensionsV1beta1Api()
ec2_client = boto3.client('ec2')


def add_nodes(kinds, counts, mon_ips, route_ips=[]):
    assert len(kinds) == len(counts), 'Must have same number of kinds and \
            counts.'

    cluster_name = check_or_get_env_arg('NAME')

    for i in range(len(kinds)):
        print('Adding %d %s server nodes to cluster...' % (counts[i], kinds[i]))
        # get the previous number of nodes of type kind that are running
        prev_count = get_previous_count(kinds[i])

        # run kops script to add servers to the cluster
        run_process(['./add_servers', kinds[i], str(counts[i])])

    run_process(['./validate_cluster.sh'])

    kops_ip = get_kops_ip()

    route_str = ' '.join(route_ips)
    mon_str = ' '.join(mon_ips)

    for i in range(len(kinds)):
        print('Creating %d %s pods...' % (counts[i], kinds[i]))
        selector = 'role=%s' % (kinds[i])
        max_id = max(list(map(lambda node:
            int(node.metadata.labels['podid'].split('-')[-1],
            client.list_node(label_selector=selector).items))))

        for j in range(max_id + 1, max_id + counts[i] + 1):
            filename = 'yaml/pods/%s-pod.yml' % (kind)
            pod_spec = load_yaml(filename)

            seed_ip = random.choice(route_ips)

            cont = pod_spec['spec']['containers'][0]
            replace_yaml_val(cont['env'], 'ROUTING_IPS', route_str)
            replace_yaml_val(cont['env'], 'MON_IPS', mon_str)
            replace_yaml_val(cont['env'], 'MGMT_IP', kops_ip)
            replace_yaml_val(cont['env'], 'SEED_IP', seed_ip)
            cont['nodeSelector']['podid'] = ('%s-%d' % (kind, j))

            if kind == 'ebs':
                vols = cont['volumes']
                for i in range(EBS_VOL_COUNT):
                    volobj = ec2_client.create_volume(
                            AvailabilityZone='us-east-1a', Size=64,
                            VolumeType='gp2')
                    volid = volobj['VolumeId']

                    ec2_client.create_tags(Resources=[volid], Tags=[
                        {
                            'Key': 'KubernetesCluster',
                            'Value': cluster_name
                        }
                    ])

                    vols[i]['awsElasticBlockStore']['volumeID'] = volid

            create_client.create_namespaced_pod(namespace=NAMESPACE,
                    body=pod_spec)

def get_previous_count(kind):
    selector = 'role=%s' % (kind)
    items = client.list_namespaced_pod(namespace=NAMESPACE,
            label_selector=selector).items

    return len(items)

def get_kops_ip():
    return client.list_namespace_pods(namespace=NAMESPACE,
            label_selector="role=kops").items[0].status.pod_ip
