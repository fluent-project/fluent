//  Copyright 2018 U.C. Berkeley RISE Lab
//
//  Licensed under the Apache License, Version 2.0 (the "License");
//  you may not use this file except in compliance with the License.
//  You may obtain a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
//  Unless required by applicable law or agreed to in writing, software
//  distributed under the License is distributed on an "AS IS" BASIS,
//  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
//  See the License for the specific language governing permissions and
//  limitations under the License.

#include <chrono>

#include "kvs/kvs_handlers.hpp"

void user_request_handler(
    unsigned& total_accesses, unsigned& seed, std::string& serialized,
    std::shared_ptr<spdlog::logger> logger,
    std::unordered_map<unsigned, GlobalHashRing>& global_hash_ring_map,
    std::unordered_map<unsigned, LocalHashRing>& local_hash_ring_map,
    std::unordered_map<Key, std::pair<unsigned, LatticeType>>& key_stat_map,
    PendingMap<PendingRequest>& pending_request_map,
    std::unordered_map<
        Key, std::multiset<std::chrono::time_point<std::chrono::system_clock>>>&
        key_access_timestamp,
    std::unordered_map<Key, KeyInfo>& placement,
    std::unordered_set<Key>& local_changeset, ServerThread& wt,
    std::unordered_map<LatticeType, Serializer*, lattice_type_hash>&
        serializers,
    SocketCache& pushers) {
  KeyRequest request;
  request.ParseFromString(serialized);

  KeyResponse response;
  std::string response_id = "";

  if (request.has_request_id()) {
    response_id = request.request_id();
    response.set_response_id(response_id);
  }

  bool succeed;
  std::string request_type = RequestType_Name(request.type());
  std::string response_address =
      request.has_response_address() ? request.response_address() : "";

  for (const auto& tuple : request.tuples()) {
    // first check if the thread is responsible for the key
    Key key = tuple.key();
    std::string payload =
        tuple.has_payload() ? (std::move(tuple.payload())) : "";

    ServerThreadList threads = kHashRingUtil->get_responsible_threads(
        wt.get_replication_factor_connect_addr(), key, is_metadata(key),
        global_hash_ring_map, local_hash_ring_map, placement, pushers,
        kSelfTierIdVector, succeed, seed);

    if (succeed) {
      if (std::find(threads.begin(), threads.end(), wt) == threads.end()) {
        if (is_metadata(key)) {
          // this means that this node is not responsible for this metadata key
          KeyTuple* tp = response.add_tuples();

          tp->set_key(key);
          tp->set_lattice_type(tuple.lattice_type());
          tp->set_error(2);
        } else {
          // if we don't know what threads are responsible, we issue a rep
          // factor request and make the request pending
          kHashRingUtil->issue_replication_factor_request(
              wt.get_replication_factor_connect_addr(), key,
              global_hash_ring_map[1], local_hash_ring_map[1], pushers, seed);

          pending_request_map[key].push_back(
              PendingRequest(request_type, tuple.lattice_type(), payload,
                             response_address, response_id));
        }
      } else {  // if we know the responsible threads, we process the request
        KeyTuple* tp = response.add_tuples();
        tp->set_key(key);

        if (request_type == "GET") {
          if (key_stat_map.find(key) == key_stat_map.end()) {
            tp->set_error(1);
          } else {
            auto res = process_get(key, serializers[key_stat_map[key].second]);
            tp->set_lattice_type(key_stat_map[key].second);
            tp->set_payload(res.first);
            tp->set_error(res.second);
          }
        } else if (request_type == "PUT") {
          if (tuple.lattice_type() == LatticeType::NO) {
            logger->error("PUT request missing lattice type.");
          } else if (key_stat_map.find(key) != key_stat_map.end() &&
                     key_stat_map[key].second != tuple.lattice_type()) {
            logger->error(
                "Lattice type mismatch: {} from query but {} expected.",
                LatticeType_Name(tuple.lattice_type()),
                key_stat_map[key].second);
          } else {
            process_put(key, tuple.lattice_type(), payload,
                        serializers[tuple.lattice_type()], key_stat_map);

            local_changeset.insert(key);
            tp->set_error(0);
          }
        } else {
          logger->error("Unknown request type {} in user request handler.",
                        request_type);
        }

        if (tuple.has_address_cache_size() &&
            tuple.address_cache_size() != threads.size()) {
          tp->set_invalidate(true);
        }

        key_access_timestamp[key].insert(std::chrono::system_clock::now());
        total_accesses += 1;
      }
    } else {
      pending_request_map[key].push_back(
          PendingRequest(request_type, tuple.lattice_type(), payload,
                         response_address, response_id));
    }
  }

  if (response.tuples_size() > 0 && request.has_response_address()) {
    std::string serialized_response;
    response.SerializeToString(&serialized_response);
    kZmqUtil->send_string(serialized_response,
                          &pushers[request.response_address()]);
  }
}
