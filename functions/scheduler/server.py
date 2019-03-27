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

import logging
import random
import sys
import time
import uuid
import zmq

from anna.client import AnnaClient
from include.kvs_pb2 import *
from include.functions_pb2 import *
from include.server_utils import *
from include.shared import *
from include.serializer import *
from .create import *
from .call import *
from . import utils

THRESHOLD = 15 # how often metadata updated

def _get_cache_ip_key(ip):
    return 'ANNA_METADATA|cache_ip|' + ip

def _get_ip_list(mgmt_ip, port, ctx, exec_threads=True):
    sckt = ctx.socket(zmq.REQ)
    sckt.connect('tcp://' + mgmt_ip + ':' + str(port))

    # we can send an empty request because the response is always thes same
    sckt.send(b'')

    ips = KeySet()
    ips.ParseFromString(sckt.recv())
    result = []

    if exec_threads:
        for ip in ips.keys:
            for i in range(utils.NUM_EXEC_THREADS):
                result.append((ip, i))

        return result
    else:
        return list(ips.keys)

def _update_key_maps(kc_map, key_ip_map, executors, kvs):
    exec_ips = set(map(lambda e: e[0], executors))
    for ip in set(kc_map.keys()).difference(exec_ips): del kc_map[ip]

    key_ip_map.clear()
    for ip in exec_ips:
        key = _get_cache_ip_key(ip)

        # this is of type LWWPairLattice, which has a KeySet protobuf packed
        # into it; we want the keys in that KeySet protobuf
        l = kvs.get(key)
        if l is None: # this executor is still joining
            continue

        ks = KeySet()
        ks.ParseFromString(l.reveal()[1])

        kc_map[ip] = set(ks.keys)

        for key in ks.keys:
            if key not in key_ip_map:
                key_ip_map[key] = []

            key_ip_map[key].append(ip)


def scheduler(ip, mgmt_ip, route_addr):
    logging.basicConfig(filename='log_scheduler.txt', level=logging.INFO)

    kvs = AnnaClient(route_addr, ip)

    key_cache_map = {}
    key_ip_map = {}
    ctx = zmq.Context(1)

    # Each dag consists of a set of functions and connections. Each one of
    # the functions is pinned to one or more nodes, which is tracked here.
    dags = {}
    thread_statuses = {}
    func_locations = {}

    connect_socket = ctx.socket(zmq.REP)
    connect_socket.bind(BIND_ADDR_TEMPLATE % (CONNECT_PORT))

    func_create_socket = ctx.socket(zmq.REP)
    func_create_socket.bind(BIND_ADDR_TEMPLATE % (FUNC_CREATE_PORT))

    func_call_socket = ctx.socket(zmq.REP)
    func_call_socket.bind(BIND_ADDR_TEMPLATE % (FUNC_CALL_PORT))

    dag_create_socket = ctx.socket(zmq.REP)
    dag_create_socket.bind(BIND_ADDR_TEMPLATE % (DAG_CREATE_PORT))

    dag_call_socket = ctx.socket(zmq.REP)
    dag_call_socket.bind(BIND_ADDR_TEMPLATE % (DAG_CALL_PORT))

    list_socket = ctx.socket(zmq.REP)
    list_socket.bind(BIND_ADDR_TEMPLATE % (LIST_PORT))

    exec_status_socket = ctx.socket(zmq.PULL)
    exec_status_socket.bind(BIND_ADDR_TEMPLATE % (STATUS_PORT))

    sched_update_socket = ctx.socket(zmq.PULL)
    sched_update_socket.bind(BIND_ADDR_TEMPLATE % (SCHED_UPDATE_PORT))

    poller = zmq.Poller()
    poller.register(connect_socket, zmq.POLLIN)
    poller.register(func_create_socket, zmq.POLLIN)
    poller.register(func_call_socket, zmq.POLLIN)
    poller.register(dag_create_socket, zmq.POLLIN)
    poller.register(dag_call_socket, zmq.POLLIN)
    poller.register(list_socket, zmq.POLLIN)
    poller.register(exec_status_socket, zmq.POLLIN)
    poller.register(sched_update_socket, zmq.POLLIN)

    executors = _get_ip_list(mgmt_ip, NODES_PORT, ctx, True)
    _update_key_maps(key_cache_map, key_ip_map, executors, kvs)
    schedulers = _get_ip_list(mgmt_ip, SCHEDULERS_PORT, ctx, False)

    start = time.time()

    while True:
        socks = dict(poller.poll(timeout=1000))

        if connect_socket in socks and socks[connect_socket] == zmq.POLLIN:
            msg = connect_socket.recv_string()
            connect_socket.send_string(routing_addr)

        if func_create_socket in socks and socks[func_create_socket] == zmq.POLLIN:
            create_func(func_create_socket, kvs)

        if func_call_socket in socks and socks[func_call_socket] == zmq.POLLIN:
            call_function(func_call_socket, ctx, executors, key_ip_map)

        if dag_create_socket in socks and socks[dag_create_socket] == zmq.POLLIN:
            logging.info('Received DAG create request.')
            create_dag(dag_create_socket, ctx, kvs, executors, dags, func_locations)

        if dag_call_socket in socks and socks[dag_call_socket] == zmq.POLLIN:
            call = DagCall()
            call.ParseFromString(dag_call_socket.recv())
            exec_id = generate_timestamp(0)

            accepted, error, rid = call_dag(call, ctx, dags, func_locations,
                    key_ip_map)

            while not accepted:
                executors = _get_ip_list(mgmt_ip, NODES_PORT, ctx, True)
                _update_key_maps(key_cache_map, key_ip_map, executors, kvs)

                accepted, error, rid = call_dag(call, ctx, dags, func_locations,
                        key_ip_map)

            resp = GenericResponse()
            resp.success = True
            resp.response_id = rid
            dag_call_socket.send(resp.SerializeToString())

        if list_socket in socks and socks[list_socket] == zmq.POLLIN:
            logging.info('Received query for function list.')
            msg = list_socket.recv_string()
            prefix = msg if msg else ''

            resp = FunctionList()
            resp.names.append(_get_func_list(client, prefix))

            list_socket.send(resp.SerializeToString())

        if exec_status_socket in socks and socks[exec_status_socket] == \
                zmq.POLLIN:
            status = ThreadStatus()
            status.ParseFromString(exec_status_socket.recv())

            key = (status.ip, status.tid)
            logging.info('Received status update from executor %s:%d.' %
                    (key[0], key[1]))

            if key not in thread_statuses:
                thread_statuses[key] = status

                if key not in executors:
                    executors.append(key)
            elif thread_statuses[key] != status:
                # remove all the old function locations, and all the new ones
                # -- there will probably be a large overlap, but this shouldn't
                # be much different than calculating two different set
                # differences anyway
                for func in thread_statuses[key].functions:
                    if func in func_locations:
                        func_locations[func].remove(key)

                for func in status.functions:
                    if func not in func_locations:
                        func_locations[func] = []

                    func_locations[func].append(key)

                thread_statuses[key] = status

        if sched_update_socket in socks and socks[sched_update_socket] == \
                zmq.POLLIN:
            logging.info('Received update from another scheduler.')
            ks = KeySet()
            ks.ParseFromString(sched_update_socket.recv())

            # retrieve any DAG that some other scheduler knows about that we do
            # not yet know about
            for dname in ks.keys:
                if dname not in dags:
                    dag = Dag()
                    dag.ParseFromString(kvs.get(dname).value)

                    dags[dname] = dag



        end = time.time()
        if end - start > THRESHOLD:
            # update our local key-cache mapping information
            executors = _get_ip_list(mgmt_ip, NODES_PORT, ctx, True)
            _update_key_maps(key_cache_map, key_ip_map, executors, kvs)

            schedulers = _get_ip_list(mgmt_ip, SCHEDULERS_PORT, ctx, False)

            dag_names = KeySet()
            for name in dags.keys():
                dag_names.keys.append(name)
            msg = dag_names.SerializeToString()

            for sched_ip in schedulers:
                if sched_ip != ip:
                    sckt = ctx.socket(zmq.PUSH)
                    sckt.connect('tcp://' + sched_ip + ':' +
                            str(SCHED_UPDATE_PORT))
                    sckt.send(msg)

            start = time.time()
