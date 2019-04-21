#!/usr/bin/env python3.6
import cloudpickle as cp
import logging
import sys
import zmq

from . import composition
from . import locality
from . import lambda_locality
from . import causal
from . import utils

BENCHMARK_START_PORT = 3000

def benchmark(flconn, tid):
    logging.info('start benchmark thread')
    ctx = zmq.Context(1)

    benchmark_start_socket = ctx.socket(zmq.PULL)
    benchmark_start_socket.bind('tcp://*:' + str(BENCHMARK_START_PORT + tid))
    kvs = flconn.kvs_client

    dags = {}
    dag_names = []

    while True:
        msg = benchmark_start_socket.recv_string()
        logging.info('receive benchmark request')
        splits = msg.split(':')

        resp_addr = splits[0]
        bname = splits[1]
        mode = splits[2]
        segment = None
        if len(splits) > 3:
            segment = int(splits[3])

        sckt = ctx.socket(zmq.PUSH)
        sckt.connect('tcp://' + resp_addr + ':3000')
        run_bench(bname, mode, segment, flconn, kvs, sckt, dags, dag_names)

def run_bench(bname, mode, segment, flconn, kvs, sckt, dags, dag_names):
    logging.info('Running benchmark %s.' % (bname))

    latency = None

    if bname == 'causal':
        latency = causal.run(mode, segment, flconn, kvs, dags, dag_names)
    else:
        logging.info('Unknown benchmark type: %s!' % (bname))
        sckt.send(b'END')
        return

    # some benchmark modes return no results
    sckt.send(cp.dumps(latency))
    logging.info('*** Benchmark %s finished. ***' % (bname))

    if mode == 'warmup':
        logging.info('Warmup latency is %.6f' % (latency['warmup']))
    #if mode == 'run':
    #    utils.print_latency_stats(latency, 'Causal', True)
