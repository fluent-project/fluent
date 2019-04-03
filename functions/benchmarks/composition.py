import sys
import time

from include.functions_pb2 import *
from include.serializer import *

def run(flconn, kvs, num_requests):
    ### DEFINE AND REGISTER FUNCTIONS ###

    def incr(x):
        return x + 1

    def square(x):
        return x * x

    cloud_incr = flconn.register(incr, 'incr')
    cloud_square = flconn.register(square, 'square')

    if cloud_incr and cloud_square:
        print('Successfully registered incr and square functions.')
    else:
        sys.exit(1)

    ### TEST REGISTERED FUNCTIONS ###
    incr_test = cloud_incr(2).get()
    if incr_test != 3:
        print('Unexpected result from incr(2): %s' % (str(incr_test)))

    square_test = cloud_square(2).get()
    if square_test != 4:
        print('Unexpected result from square(2): %s' % (str(square_test)))

    print('Successfully tested functions!')

    ### CREATE DAG ###

    dag_name = 'composition'

    functions = ['incr', 'square']
    connections = [('incr', 'square')]
    success, error = flconn.register_dag(dag_name, functions, connections)

    if not success:
        print('Failed to register DAG: %s' % (ErrorType.Name(error)))
        sys.exit(1)

    ### RUN DAG ###

    arg_map = { 'incr' : [1] }

    scheduler_time = 0.0
    kvs_time = 0.0

    retries = 0

    for _ in range(num_requests):
        start = time.time()
        rid = flconn.call_dag(dag_name, arg_map)
        end = time.time()

        scheduler_time += (end - start)

        start = time.time()
        res = kvs.get(rid)
        while not res:
            retries += 1
            res = kvs.get(rid)
        res = deserialize_val(res.reveal()[1])
        end = time.time()

        kvs_time += (end - start)

    print('Total computation time: %.4f' % (scheduler_time + kvs_time))
    print('Average latency: %.4f' % ((scheduler_time + kvs_time) / num_requests))

    print()

    print('Average scheduler latency: %.4f' % (scheduler_time / num_requests))
    print('Average KVS get latency: %.4f' % (kvs_time / num_requests))
    print('Number of KVS get retries: %d' % (retries))
