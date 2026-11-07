// Copyright (c) 2011 The LevelDB Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file. See the AUTHORS file for names of contributors.

#include "util/coding.h"

#include <limits>
#include <vector>

#include "gtest/gtest.h"

namespace leveldb {

TEST(Coding, Fixed32) {
  std::string s;
  for (uint32_t v = 0; v < 100000; v++) {
    PutFixed32(&s, v);
  }

  const char* p = s.data();
  for (uint32_t v = 0; v < 100000; v++) {
    uint32_t actual = DecodeFixed32(p);
    ASSERT_EQ(v, actual);
    p += sizeof(uint32_t);
  }
}

TEST(Coding, Fixed64) {
  std::string s;
  for (int power = 0; power <= 63; power++) {
    uint64_t v = static_cast<uint64_t>(1) << power;
    PutFixed64(&s, v - 1);
    PutFixed64(&s, v + 0);
    PutFixed64(&s, v + 1);
  }

  const char* p = s.data();
  for (int power = 0; power <= 63; power++) {
    uint64_t v = static_cast<uint64_t>(1) << power;
    uint64_t actual;
    actual = DecodeFixed64(p);
    ASSERT_EQ(v - 1, actual);
    p += sizeof(uint64_t);

    actual = DecodeFixed64(p);
    ASSERT_EQ(v + 0, actual);
    p += sizeof(uint64_t);

    actual = DecodeFixed64(p);
    ASSERT_EQ(v + 1, actual);
    p += sizeof(uint64_t);
  }
}

// Test that encoding routines generate little-endian encodings
TEST(Coding, EncodingOutput) {
  std::string dst;
  PutFixed32(&dst, 0x04030201);
  ASSERT_EQ(4, dst.size());
  ASSERT_EQ(0x01, static_cast<int>(dst[0]));
  ASSERT_EQ(0x02, static_cast<int>(dst[1]));
  ASSERT_EQ(0x03, static_cast<int>(dst[2]));
  ASSERT_EQ(0x04, static_cast<int>(dst[3]));

  dst.clear();
  PutFixed64(&dst, 0x0807060504030201ull);
  ASSERT_EQ(8, dst.size());
  ASSERT_EQ(0x01, static_cast<int>(dst[0]));
  ASSERT_EQ(0x02, static_cast<int>(dst[1]));
  ASSERT_EQ(0x03, static_cast<int>(dst[2]));
  ASSERT_EQ(0x04, static_cast<int>(dst[3]));
  ASSERT_EQ(0x05, static_cast<int>(dst[4]));
  ASSERT_EQ(0x06, static_cast<int>(dst[5]));
  ASSERT_EQ(0x07, static_cast<int>(dst[6]));
  ASSERT_EQ(0x08, static_cast<int>(dst[7]));
}

TEST(Coding, Varint32) {
  std::string s;
  for (uint32_t i = 0; i < (32 * 32); i++) {
    uint32_t v = (i / 32) << (i % 32);
    PutVarint32(&s, v);
  }

  const char* p = s.data();
  const char* limit = p + s.size();
  for (uint32_t i = 0; i < (32 * 32); i++) {
    uint32_t expected = (i / 32) << (i % 32);
    uint32_t actual;
    const char* start = p;
    p = GetVarint32Ptr(p, limit, &actual);
    ASSERT_TRUE(p != nullptr);
    ASSERT_EQ(expected, actual);
    ASSERT_EQ(VarintLength(actual), p - start);
  }
  ASSERT_EQ(p, s.data() + s.size());
}

TEST(Coding, Varint64) {
  // Construct the list of values to check
  std::vector<uint64_t> values;
  // Some special values
  values.push_back(0);
  values.push_back(100);
  values.push_back(~static_cast<uint64_t>(0));
  values.push_back(~static_cast<uint64_t>(0) - 1);
  for (uint32_t k = 0; k < 64; k++) {
    // Test values near powers of two
    const uint64_t power = 1ull << k;
    values.push_back(power);
    values.push_back(power - 1);
    values.push_back(power + 1);
  }

  std::string s;
  for (size_t i = 0; i < values.size(); i++) {
    PutVarint64(&s, values[i]);
  }

  const char* p = s.data();
  const char* limit = p + s.size();
  for (size_t i = 0; i < values.size(); i++) {
    ASSERT_TRUE(p < limit);
    uint64_t actual;
    const char* start = p;
    p = GetVarint64Ptr(p, limit, &actual);
    ASSERT_TRUE(p != nullptr);
    ASSERT_EQ(values[i], actual);
    ASSERT_EQ(VarintLength(actual), p - start);
  }
  ASSERT_EQ(p, limit);
}

TEST(Coding, Varint32Overflow) {
  uint32_t result;
  std::string input("\x81\x82\x83\x84\x85\x11");
  ASSERT_TRUE(GetVarint32Ptr(input.data(), input.data() + input.size(),
                             &result) == nullptr);
}

TEST(Coding, Varint32OverflowInLastByte) {
  uint32_t result;
  // 2^32 does not fit in 32 bits and must not be decoded as 0.
  std::string input("\x80\x80\x80\x80\x10");
  ASSERT_TRUE(GetVarint32Ptr(input.data(), input.data() + input.size(),
                             &result) == nullptr);
  Slice slice(input);
  ASSERT_FALSE(GetVarint32(&slice, &result));

  std::string max_value("\xff\xff\xff\xff\x0f");
  ASSERT_TRUE(GetVarint32Ptr(max_value.data(),
                             max_value.data() + max_value.size(),
                             &result) != nullptr);
  ASSERT_EQ(std::numeric_limits<uint32_t>::max(), result);
}

TEST(Coding, Varint32Truncation) {
  uint32_t large_value = (1u << 31) + 100;
  std::string s;
  PutVarint32(&s, large_value);
  uint32_t result;
  for (size_t len = 0; len < s.size() - 1; len++) {
    ASSERT_TRUE(GetVarint32Ptr(s.data(), s.data() + len, &result) == nullptr);
  }
  ASSERT_TRUE(GetVarint32Ptr(s.data(), s.data() + s.size(), &result) !=
              nullptr);
  ASSERT_EQ(large_value, result);
}

TEST(Coding, Varint64Overflow) {
  uint64_t result;
  std::string input("\x81\x82\x83\x84\x85\x81\x82\x83\x84\x85\x11");
  ASSERT_TRUE(GetVarint64Ptr(input.data(), input.data() + input.size(),
                             &result) == nullptr);
}

TEST(Coding, Varint64OverflowInLastByte) {
  uint64_t result;
  // 2^64 does not fit in 64 bits and must not be decoded as 0.
  std::string input("\x80\x80\x80\x80\x80\x80\x80\x80\x80\x02");
  ASSERT_TRUE(GetVarint64Ptr(input.data(), input.data() + input.size(),
                             &result) == nullptr);
  Slice slice(input);
  ASSERT_FALSE(GetVarint64(&slice, &result));

  std::string max_value("\xff\xff\xff\xff\xff\xff\xff\xff\xff\x01");
  ASSERT_TRUE(GetVarint64Ptr(max_value.data(),
                             max_value.data() + max_value.size(),
                             &result) != nullptr);
  ASSERT_EQ(std::numeric_limits<uint64_t>::max(), result);
}

TEST(Coding, Varint64Truncation) {
  uint64_t large_value = (1ull << 63) + 100ull;
  std::string s;
  PutVarint64(&s, large_value);
  uint64_t result;
  for (size_t len = 0; len < s.size() - 1; len++) {
    ASSERT_TRUE(GetVarint64Ptr(s.data(), s.data() + len, &result) == nullptr);
  }
  ASSERT_TRUE(GetVarint64Ptr(s.data(), s.data() + s.size(), &result) !=
              nullptr);
  ASSERT_EQ(large_value, result);
}

// A failed decode must leave both the input Slice and the output value
// untouched, while a successful decode advances the input exactly past the
// varint - block and MANIFEST parsing rely on both halves of the contract.
TEST(Coding, Varint32FailureLeavesInputAndOutputUntouched) {
  // Overflows in the last byte (2^32) and truncation must both fail.
  std::string overflow("\x80\x80\x80\x80\x10\x7f");
  for (const std::string& input : {overflow, std::string("\x80\x80", 2)}) {
    Slice slice(input);
    uint32_t result = 42;
    ASSERT_FALSE(GetVarint32(&slice, &result)) << input.size();
    EXPECT_EQ(42, result);
    EXPECT_EQ(input.data(), slice.data());
    EXPECT_EQ(input.size(), slice.size());
  }
}

TEST(Coding, Varint32SuccessAdvancesExactlyPastTheEncoding) {
  char buffer[6] = {'\xff', '\xff', '\xff', '\xff', '\x0f', 'X'};
  Slice slice(buffer, sizeof(buffer));
  uint32_t result = 0;
  ASSERT_TRUE(GetVarint32(&slice, &result));
  EXPECT_EQ(std::numeric_limits<uint32_t>::max(), result);
  EXPECT_EQ(1u, slice.size());  // Only the trailing 'X' is left.
  EXPECT_EQ(buffer + 5, slice.data());
}

TEST(Coding, Varint64FailureLeavesInputAndOutputUntouched) {
  std::string overflow("\x80\x80\x80\x80\x80\x80\x80\x80\x80\x02\x7f");
  for (const std::string& input :
       {overflow, std::string("\x80\x80\x80\x80\x80", 5)}) {
    Slice slice(input);
    uint64_t result = 42;
    ASSERT_FALSE(GetVarint64(&slice, &result)) << input.size();
    EXPECT_EQ(42, result);
    EXPECT_EQ(input.data(), slice.data());
    EXPECT_EQ(input.size(), slice.size());
  }
}

// Redundant continuation bytes that still encode an in-range value are
// legal: decoders must accept them and consume exactly their length.
TEST(Coding, VarintAcceptsNonCanonicalEncodings) {
  struct Case {
    const char* bytes;
    size_t length;
    uint64_t value;
  };
  const Case cases[] = {
      {"\x80\x00", 2, 0},                              // 0 in two bytes
      {"\x80\x80\x80\x80\x00", 5, 0},                  // 0 in five bytes
      {"\x80\x80\x80\x80\x80\x80\x80\x80\x80\x00", 10, 0},  // 0 in ten bytes
      {"\x81\x00", 2, 1},                              // 1 in two bytes
  };
  for (const Case& c : cases) {
    std::string buffer(c.bytes, c.length);
    buffer.push_back('E');  // A marker byte to prove the exact advance.
    Slice slice(buffer);
    uint64_t result = 99;
    ASSERT_TRUE(GetVarint64(&slice, &result)) << c.length;
    EXPECT_EQ(c.value, result) << c.length;
    EXPECT_EQ(1u, slice.size()) << c.length;  // Only the marker is left.
  }
}

TEST(Coding, Strings) {
  std::string s;
  PutLengthPrefixedSlice(&s, Slice(""));
  PutLengthPrefixedSlice(&s, Slice("foo"));
  PutLengthPrefixedSlice(&s, Slice("bar"));
  PutLengthPrefixedSlice(&s, Slice(std::string(200, 'x')));

  Slice input(s);
  Slice v;
  ASSERT_TRUE(GetLengthPrefixedSlice(&input, &v));
  ASSERT_EQ("", v.ToString());
  ASSERT_TRUE(GetLengthPrefixedSlice(&input, &v));
  ASSERT_EQ("foo", v.ToString());
  ASSERT_TRUE(GetLengthPrefixedSlice(&input, &v));
  ASSERT_EQ("bar", v.ToString());
  ASSERT_TRUE(GetLengthPrefixedSlice(&input, &v));
  ASSERT_EQ(std::string(200, 'x'), v.ToString());
  ASSERT_EQ("", input.ToString());
}

}  // namespace leveldb
