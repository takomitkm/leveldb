// Fuzzing of the public LevelDB API with pseudo-random operation streams.
//
// Derived from Google's official OSS-Fuzz harness for LevelDB
// (google/oss-fuzz, projects/leveldb/fuzz_db.cc, Apache 2.0), changed to:
//   - fix the missing `break` in the kIterate/kGetReleaseSnapshot cases,
//   - use a portable temp directory instead of /tmp,
//   - replace FuzzedDataProvider with a small built-in reader so the harness
//     also builds with MSVC and MinGW, not only clang + libFuzzer.
//
// Standalone entry point: leveldb_fuzz_db <iterations> [seed]

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <memory>
#include <random>
#include <string>

#include "leveldb/db.h"
#include "leveldb/iterator.h"
#include "leveldb/options.h"
#include "leveldb/status.h"

namespace {

std::unique_ptr<leveldb::DB> g_db;

std::string TempDbPath() {
  std::filesystem::path p = std::filesystem::temp_directory_path();
  p /= "leveldb_fuzz_db_testdb";
  std::filesystem::remove_all(p);
  return p.string();
}

bool OpenDb() {
  leveldb::Options options;
  options.create_if_missing = true;
  leveldb::DB* raw = nullptr;
  if (!leveldb::DB::Open(options, TempDbPath(), &raw).ok()) {
    return false;
  }
  g_db.reset(raw);
  return true;
}

// Tiny stand-in for LLVM's FuzzedDataProvider: draws bytes from a seeded RNG.
class DataReader {
 public:
  explicit DataReader(uint64_t seed) : rng_(seed) {}

  size_t Bounded(size_t max) {
    return std::uniform_int_distribution<size_t>(0, max)(rng_);
  }

  std::string String(size_t max_len) {
    std::string s(Bounded(max_len), '\0');
    for (char& c : s) {
      c = static_cast<char>(Bounded(255));
    }
    return s;
  }

  template <typename T>
  T Enum(T last) {
    return static_cast<T>(Bounded(static_cast<size_t>(last)));
  }

 private:
  std::mt19937_64 rng_;
};

enum FuzzOp {
  kPut,
  kDelete,
  kGet,
  kGetProperty,
  kIterate,
  kGetSnapshot,
  kReleaseSnapshot,
  kCompactRange,
  kReopen,
  kNumOps
};

int FuzzDb(uint64_t seed) {
  DataReader data(seed);
  static std::string snapshot_key;
  const leveldb::Snapshot* snapshot = nullptr;

  if (!OpenDb()) {
    std::fprintf(stderr, "seed %llu: initial open failed\n",
                 (unsigned long long)seed);
    return 0;
  }

  const size_t num_ops = 50 + data.Bounded(200);
  for (size_t i = 0; i < num_ops && g_db != nullptr; i++) {
    switch (data.Enum(kNumOps)) {
      case kPut: {
        std::string key = data.String(64);
        std::string value = data.String(256);
        g_db->Put(leveldb::WriteOptions(), key, value);
        break;
      }
      case kDelete: {
        g_db->Delete(leveldb::WriteOptions(), data.String(64));
        break;
      }
      case kGet: {
        std::string value;
        g_db->Get(leveldb::ReadOptions(), data.String(64), &value);
        break;
      }
      case kGetProperty: {
        std::string value;
        g_db->GetProperty(data.String(64), &value);
        break;
      }
      case kIterate: {
        leveldb::Iterator* it = g_db->NewIterator(leveldb::ReadOptions());
        for (it->SeekToFirst(); it->Valid(); it->Next()) {
        }
        delete it;
        break;
      }
      case kGetSnapshot: {
        if (snapshot == nullptr) {
          snapshot = g_db->GetSnapshot();
          snapshot_key = data.String(64);
          std::string value;
          leveldb::ReadOptions ro;
          ro.snapshot = snapshot;
          g_db->Get(ro, snapshot_key, &value);
        }
        break;
      }
      case kReleaseSnapshot: {
        if (snapshot != nullptr) {
          g_db->ReleaseSnapshot(snapshot);
          snapshot = nullptr;
        }
        break;
      }
      case kCompactRange: {
        std::string begin = data.String(16);
        std::string end = data.String(16);
        leveldb::Slice begin_slice(begin);
        leveldb::Slice end_slice(end);
        g_db->CompactRange(&begin_slice, &end_slice);
        break;
      }
      case kReopen: {
        // Exclusive DB lock: close first, then open again.
        g_db.reset();
        snapshot = nullptr;
        if (!OpenDb()) {
          std::fprintf(stderr, "seed %llu: reopen failed\n",
                       (unsigned long long)seed);
          return 0;
        }
        break;
      }
      default:
        break;
    }
  }

  if (snapshot != nullptr && g_db != nullptr) {
    g_db->ReleaseSnapshot(snapshot);
  }
  g_db.reset();
  return 0;
}

}  // namespace

// libFuzzer entry point (kept for use with a real libFuzzer build).
extern "C" int LLVMFuzzerTestOneInput(const uint8_t* data, size_t size) {
  uint64_t seed = 0;
  for (size_t i = 0; i < sizeof(seed) && i < size; i++) {
    seed = (seed << 8) | data[i];
  }
  return FuzzDb(seed | 1);
}

int main(int argc, char** argv) {
  const size_t iterations = argc > 1 ? std::strtoul(argv[1], nullptr, 10) : 500;
  const uint64_t first_seed = argc > 2 ? std::strtoull(argv[2], nullptr, 10) : 1;
  std::printf("leveldb_fuzz_db: %zu iterations from seed %llu\n", iterations,
              (unsigned long long)first_seed);
  for (size_t i = 0; i < iterations; i++) {
    FuzzDb(first_seed + i);
  }
  std::printf("leveldb_fuzz_db: OK (%zu iterations)\n", iterations);
  return 0;
}
