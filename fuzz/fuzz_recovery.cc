// Corruption/recovery fuzzing: nothing upstream fuzzes the recovery paths,
// and this fork patches several of them (durable RenameFile, #1334 short
// reads, #1336 incomplete-log handling, CRLF-tolerant CURRENT, the
// RepairTable parse guard). For every seed this harness copies a small
// healthy database, flips bytes at seed-chosen offsets in a seed-chosen file
// (sometimes CRLF-mangling CURRENT instead), then opens it, repairs it, and
// opens it again. Any result - including Corruption - is fine as long as the
// process survives and a successful open serves reads correctly.
//
// Standalone entry point: leveldb_fuzz_recovery <seeds> [seed]

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <memory>
#include <random>
#include <string>
#include <vector>

#include "leveldb/cache.h"
#include "leveldb/db.h"
#include "leveldb/iterator.h"
#include "leveldb/options.h"
#include "leveldb/write_batch.h"

namespace {

namespace fs = std::filesystem;

struct Rng {
  std::mt19937_64 impl;
  explicit Rng(uint64_t seed) : impl(seed) {}
  size_t Bounded(size_t max) {
    return std::uniform_int_distribution<size_t>(0, max)(impl);
  }
};

std::vector<std::string> ListFiles(const std::string& dir) {
  std::vector<std::string> files;
  for (const auto& entry : fs::directory_iterator(dir)) {
    if (entry.is_regular_file()) {
      files.push_back(entry.path().filename().string());
    }
  }
  return files;
}

bool IsDataFile(const std::string& name) {
  const std::string ldb = ".ldb";
  const std::string log = ".log";
  if (name.size() >= ldb.size() &&
      name.compare(name.size() - ldb.size(), ldb.size(), ldb) == 0) {
    return true;
  }
  return name.size() >= log.size() &&
         name.compare(name.size() - log.size(), log.size(), log) == 0;
}

bool ReadWholeFile(const std::string& path, std::string* out) {
  FILE* f = std::fopen(path.c_str(), "rb");
  if (f == nullptr) {
    return false;
  }
  char buf[4096];
  size_t n;
  while ((n = std::fread(buf, 1, sizeof(buf), f)) > 0) {
    out->append(buf, n);
  }
  std::fclose(f);
  return true;
}

bool WriteWholeFile(const std::string& path, const std::string& data) {
  FILE* f = std::fopen(path.c_str(), "wb");
  if (f == nullptr) {
    return false;
  }
  const size_t written = data.empty() ? 0 : std::fwrite(data.data(), 1, data.size(), f);
  std::fclose(f);
  return written == data.size();
}

// Builds a small healthy database: keys "key000000".."key<K>", values carry
// the key so a successful Get can be verified for self-consistency.
bool BuildBaseDb(const std::string& path, int num_keys) {
  fs::remove_all(path);
  leveldb::Options options;
  options.create_if_missing = true;
  leveldb::DB* raw = nullptr;
  if (!leveldb::DB::Open(options, path, &raw).ok()) {
    return false;
  }
  std::unique_ptr<leveldb::DB> db(raw);
  for (int i = 0; i < num_keys; i++) {
    char key[32];
    std::snprintf(key, sizeof(key), "key%06d", i);
    std::string value = std::string("value-") + key;
    if (!db->Put(leveldb::WriteOptions(), key, value).ok()) {
      return false;
    }
  }
  db.reset();
  return true;
}

// Opens (if possible), and when the open succeeded serves a few gets and one
// iterator sweep. Returns false only on a broken invariant: an open that
// reports success but then fails to serve its own data correctly.
bool CheckUsableIfOpenable(const std::string& path, const leveldb::Options& options) {
  leveldb::DB* raw = nullptr;
  leveldb::Status s = leveldb::DB::Open(options, path, &raw);
  if (!s.ok()) {
    // Corruption / IOError on a corrupted database is the expected outcome.
    return true;
  }
  std::unique_ptr<leveldb::DB> db(raw);
  for (int probe = 0; probe < 5; probe++) {
    char key[32];
    std::snprintf(key, sizeof(key), "key%06d",
                  (probe * 7919) % 500);  // spread over the key space
    std::string value;
    s = db->Get(leveldb::ReadOptions(), key, &value);
    if (s.ok() && value != std::string("value-") + key) {
      std::fprintf(stderr, "BROKEN INVARIANT: get(%s) = %s\n", key, value.c_str());
      return false;
    }
  }
  leveldb::Iterator* it = db->NewIterator(leveldb::ReadOptions());
  std::string last_key;
  for (it->SeekToFirst(); it->Valid(); it->Next()) {
    if (it->key().ToString() < last_key) {
      std::fprintf(stderr, "BROKEN INVARIANT: iteration not ordered\n");
      delete it;
      return false;
    }
    last_key = it->key().ToString();
  }
  leveldb::Status iter_status = it->status();
  delete it;
  return iter_status.ok() || iter_status.IsCorruption() || iter_status.IsIOError();
}

int FuzzRecovery(uint64_t seed, const std::string& base_dir,
                 const std::string& work_dir) {
  Rng rng(seed * 2 + 1);

  // Copy the healthy base database.
  fs::remove_all(work_dir);
  fs::copy(base_dir, work_dir, fs::copy_options::recursive);

  // Choose a victim file: prefer data files, sometimes CURRENT.
  std::vector<std::string> files = ListFiles(work_dir);
  std::vector<std::string> data_files;
  std::string current;
  for (const std::string& name : files) {
    if (IsDataFile(name)) {
      data_files.push_back(name);
    } else if (name == "CURRENT") {
      current = name;
    }
  }

  const bool mangle_current = (rng.Bounded(3) == 0) && !current.empty();
  std::string victim;
  if (mangle_current) {
    victim = current;
  } else if (!data_files.empty()) {
    victim = data_files[rng.Bounded(data_files.size() - 1)];
  } else if (!current.empty()) {
    victim = current;
  } else {
    return 0;  // nothing to corrupt in this copy
  }

  std::string path = (fs::path(work_dir) / victim).string();
  std::string contents;
  if (!ReadWholeFile(path, &contents) || contents.empty()) {
    return 0;
  }

  if (victim == "CURRENT" && rng.Bounded(2) == 0) {
    // CRLF-mangle CURRENT instead of flipping bytes: the exact defect fixed
    // by mojang/leveldb#8.
    std::string crlf;
    for (char c : contents) {
      crlf += c;
      if (c == '\n') {
        crlf.insert(crlf.size() - 1, "\r");
      }
    }
    contents = crlf;
  } else {
    const int flips = 1 + static_cast<int>(rng.Bounded(7));
    for (int i = 0; i < flips && !contents.empty(); i++) {
      const size_t offset = rng.Bounded(contents.size() - 1);
      contents[offset] = static_cast<char>(rng.Bounded(255));
    }
  }
  WriteWholeFile(path, contents);

  // 1) Open the corrupted database: any status is fine, survival is not
  //    optional, and a successful open must serve reads correctly.
  leveldb::Options options;
  options.create_if_missing = false;
  if (!CheckUsableIfOpenable(work_dir, options)) {
    std::fprintf(stderr, "seed %llu: broken open behaviour\n",
                 (unsigned long long)seed);
    return 1;
  }

  // 2) Repair, then require a fully usable database.
  if (!leveldb::RepairDB(work_dir, options).ok()) {
    // Repair may legitimately refuse a badly corrupted database.
    return 0;
  }
  leveldb::DB* raw = nullptr;
  leveldb::Status s = leveldb::DB::Open(options, work_dir, &raw);
  if (!s.ok()) {
    // RepairDB reported success but the result does not open: suspicious.
    std::fprintf(stderr, "seed %llu: repaired db does not open\n",
                 (unsigned long long)seed);
    return 1;
  }
  std::unique_ptr<leveldb::DB> db(raw);
  leveldb::Iterator* it = db->NewIterator(leveldb::ReadOptions());
  std::string last_key;
  for (it->SeekToFirst(); it->Valid(); it->Next()) {
    if (it->key().ToString() < last_key) {
      std::fprintf(stderr, "BROKEN INVARIANT: repaired iteration not ordered\n");
      delete it;
      return 1;
    }
    last_key = it->key().ToString();
  }
  delete it;
  return 0;
}

}  // namespace

int main(int argc, char** argv) {
  const size_t seeds = argc > 1 ? std::strtoul(argv[1], nullptr, 10) : 200;
  const uint64_t first_seed = argc > 2 ? std::strtoull(argv[2], nullptr, 10) : 1;

  const std::string base_dir =
      (fs::temp_directory_path() / "leveldb_fuzz_recovery_base").string();
  const std::string work_dir =
      (fs::temp_directory_path() / "leveldb_fuzz_recovery_work").string();

  std::printf("leveldb_fuzz_recovery: building base database\n");
  if (!BuildBaseDb(base_dir, 500)) {
    std::fprintf(stderr, "failed to build base database\n");
    return 2;
  }

  std::printf("leveldb_fuzz_recovery: %zu seeds from seed %llu\n", seeds,
              (unsigned long long)first_seed);
  int failures = 0;
  for (size_t i = 0; i < seeds; i++) {
    failures += FuzzRecovery(first_seed + i, base_dir, work_dir);
  }
  fs::remove_all(base_dir);
  fs::remove_all(work_dir);
  if (failures != 0) {
    std::printf("leveldb_fuzz_recovery: FAILED (%d broken invariants)\n",
                failures);
    return 1;
  }
  std::printf("leveldb_fuzz_recovery: OK (%zu seeds)\n", seeds);
  return 0;
}
