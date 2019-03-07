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

#ifndef INCLUDE_COMMON_HPP_
#define INCLUDE_COMMON_HPP_

#include <algorithm>

#include "kvs.pb.h"
#include "lattices/lww_pair_lattice.hpp"
#include "lattices/vector_clock_pair_lattice.hpp"
#include "kvs_types.hpp"
#include "misc.pb.h"
#include "replication.pb.h"
#include "requests.pb.h"
#include "zmq/socket_cache.hpp"
#include "zmq/zmq_util.hpp"

enum UserMetadataType { cache_ip };

// TODO: split this off for kvs vs user metadata keys?
const string kMetadataIdentifier = "ANNA_METADATA";
const string kMetadataDelimiter = "|";
const char kMetadataDelimiterChar = '|';

const unsigned kMetadataReplicationFactor = 1;
const unsigned kMetadataLocalReplicationFactor = 1;

const unsigned kVirtualThreadNum = 3000;

const unsigned kMemoryTierId = 0;
const unsigned kEbsTierId = 1;
const unsigned kRoutingTierId = 100;

const unsigned kMaxTier = 1;
const vector<unsigned> kAllTierIds = {0, 1};

const unsigned kSloWorst = 3000;

// run-time constants
extern unsigned kSelfTierId;
extern vector<unsigned> kSelfTierIdVector;

extern unsigned kMemoryNodeCapacity;
extern unsigned kEbsNodeCapacity;

// the number of threads running in this executable
extern unsigned kThreadNum;
extern unsigned kMemoryThreadCount;
extern unsigned kEbsThreadCount;
extern unsigned kRoutingThreadCount;

extern unsigned kDefaultGlobalMemoryReplication;
extern unsigned kDefaultGlobalEbsReplication;
extern unsigned kDefaultLocalReplication;
extern unsigned kMinimumReplicaNumber;

inline void split(const string& s, char delim, vector<string>& elems) {
  std::stringstream ss(s);
  string item;

  while (std::getline(ss, item, delim)) {
    elems.push_back(item);
  }
}

// form the timestamp given a time and a thread id
inline unsigned long long get_time() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(
             std::chrono::system_clock::now().time_since_epoch())
      .count();
}

inline unsigned long long generate_timestamp(const unsigned& id) {
  unsigned pow = 10;
  auto time = get_time();
  while (id >= pow) pow *= 10;
  return time * pow + id;
}

// This version of the function should only be called with
// certain types of MetadataType,
// so if it's called with something else, we return
// an empty string.
// TODO: There should probably be a less silent error check.
inline Key get_user_metadata_key(string data_key, UserMetadataType type) {
  if (type == UserMetadataType::cache_ip) {
    return kMetadataIdentifier + kMetadataDelimiter + data_key +
           kMetadataDelimiter + "cache_ip";
  }
  return "";
}

// Inverse of get_user_metadata_key, returning just the key itself.
// TODO: same problem as get_user_metadata_key with the metadata types.
inline Key get_key_from_user_metadata(Key metadata_key) {
  vector<string> tokens;
  split(metadata_key, '|', tokens);

  string metadata_type = tokens[tokens.size() - 1];
  if (metadata_type == "cache_ip") {
    return tokens[1];
  }

  return "";
}

inline void prepare_get_tuple(KeyRequest& req, Key key,
                              LatticeType lattice_type) {
  KeyTuple* tp = req.add_tuples();
  tp->set_key(std::move(key));
  tp->set_lattice_type(std::move(lattice_type));
}

inline void prepare_put_tuple(KeyRequest& req, Key key,
                              LatticeType lattice_type, string payload) {
  KeyTuple* tp = req.add_tuples();
  tp->set_key(std::move(key));
  tp->set_lattice_type(std::move(lattice_type));
  tp->set_payload(std::move(payload));
}

inline string serialize(const LWWPairLattice<string>& l) {
  LWWValue lww_value;
  lww_value.set_timestamp(l.reveal().timestamp);
  lww_value.set_value(l.reveal().value);

  string serialized;
  lww_value.SerializeToString(&serialized);
  return serialized;
}

inline string serialize(const unsigned long long& timestamp,
                        const string& value) {
  LWWValue lww_value;
  lww_value.set_timestamp(timestamp);
  lww_value.set_value(value);

  string serialized;
  lww_value.SerializeToString(&serialized);
  return serialized;
}

inline string serialize(const SetLattice<string>& l) {
  SetValue set_value;
  for (const string& val : l.reveal()) {
    set_value.add_values(val);
  }

  string serialized;
  set_value.SerializeToString(&serialized);
  return serialized;
}

inline string serialize(const set<string>& set) {
  SetValue set_value;
  for (const string& val : set) {
    set_value.add_values(val);
  }

  string serialized;
  set_value.SerializeToString(&serialized);
  return serialized;
}

inline string serialize(const CausalPairLattice<SetLattice<string>>& l) {
  CausalValue causal_value;
  auto ptr = causal_value.mutable_vector_clock();
  // serialize vector clock
  for (const auto& pair : l.reveal().vector_clock.reveal()) {
    (*ptr)[pair.first] = pair.second.reveal();
  }
  // serialize values
  for (const string& val : l.reveal().value.reveal()) {
    causal_value.add_values(val);
  }

  string serialized;
  causal_value.SerializeToString(&serialized);
  return serialized;
}

inline LWWPairLattice<string> deserialize_lww(const string& serialized) {
  LWWValue lww;
  lww.ParseFromString(serialized);

  return LWWPairLattice<string>(
      TimestampValuePair<string>(lww.timestamp(), lww.value()));
}

inline SetLattice<string> deserialize_set(const string& serialized) {
  SetValue s;
  s.ParseFromString(serialized);

  set<string> result;

  for (const string& value : s.values()) {
    result.insert(value);
  }

  return SetLattice<string>(result);
}

inline CausalValue deserialize_causal(const string& serialized) {
  CausalValue causal;
  causal.ParseFromString(serialized);

  return causal;
}

struct lattice_type_hash {
  std::size_t operator()(const LatticeType& lt) const {
    return std::hash<string>()(LatticeType_Name(lt));
  }
};

#endif  // INCLUDE_COMMON_HPP_
