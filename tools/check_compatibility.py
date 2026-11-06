#!/usr/bin/env python3
"""Run compatibility regressions and verify that their old behaviours fail."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


def execute(command, log, expected_tests=None, expect_failure=False):
    result = subprocess.run(command, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, encoding="utf-8",
                            errors="replace", timeout=600)
    log.write_text(result.stdout, encoding="utf-8")
    print(result.stdout, flush=True)
    if expected_tests is None:
        if result.returncode:
            raise RuntimeError("Command failed: {}".format(command))
        return
    report = ET.parse(str(log.with_suffix(".xml"))).getroot()
    tests = int(report.get("tests", "0"))
    failures = int(report.get("failures", "0"))
    if tests != expected_tests or report.findall(".//skipped"):
        raise RuntimeError("Expected {} actual cases, got {} or skipped cases".
                           format(expected_tests, tests))
    if expect_failure:
        if result.returncode == 0 or failures == 0:
            raise RuntimeError("Mutation was not caught by an assertion")
    elif result.returncode != 0 or failures != 0:
        raise RuntimeError("Compatibility regressions failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--config", default="Release",
                        choices=("Debug", "Release"))
    parser.add_argument("--mode", choices=("acp", "utf8", "log-buffer"),
                        required=True)
    args = parser.parse_args()
    source_root = Path(__file__).resolve().parents[1]
    build = args.build.resolve()
    windows = os.name == "nt"
    config_dir = build / args.config if windows else build
    target = "leveldb_tests" if args.mode == "log-buffer" else "windows_path_test"
    binary = config_dir / (target + (".exe" if windows else ""))
    if not binary.is_file():
        raise RuntimeError("Missing regression binary {}".format(binary))
    count = 2 if args.mode == "log-buffer" else 3
    selection = ("LogTest.ReadFromReusedBuffer:LogTest.ShortReadsFromReusedBuffer"
                 if args.mode == "log-buffer" else "WindowsPathTest.*")

    def check(label, expect_failure=False):
        log = build / (label + ".log")
        report = log.with_suffix(".xml")
        if report.exists():
            report.unlink()
        execute([str(binary), "--gtest_filter=" + selection,
                 "--gtest_color=no", "--gtest_output=xml:" + str(report)],
                log, count, expect_failure)

    check("compatibility-green")
    if args.mode == "log-buffer":
        source = source_root / "db/log_reader.cc"
        old = ("          if (chunk.data() != backing_store_ + nread) {\n"
               "            std::memmove(backing_store_ + nread, chunk.data(), chunk.size());\n"
               "          }\n")
        new = ""
    else:
        source = source_root / "util/env_windows.cc"
        if args.mode == "acp":
            import ctypes
            codepage = ctypes.windll.kernel32.GetACP()
            print("ACP regression calibration uses code page {}".format(codepage))
            if codepage == 65001:
                raise RuntimeError("ACP/UTF-8 mutation requires a non-UTF-8 system ACP")
            old = "  return ::AreFileApisANSI() ? CP_ACP : CP_OEMCP;"
            new = "  return CP_UTF8;"
        else:
            old = '    std::FILE* fp = _wfopen(wfilename.c_str(), L"aN");'
            new = '    std::FILE* fp = std::fopen(filename.c_str(), "aN");'
    original = source.read_text(encoding="utf-8")
    if original.count(old) != 1:
        raise RuntimeError("Mutation anchor is missing or ambiguous")
    rebuild = ["cmake", "--build", str(build), "--config", args.config,
               "--target", target, "--parallel", "2"]
    try:
        source.write_text(original.replace(old, new), encoding="utf-8")
        execute(rebuild, build / "mutation-build.log")
        check("compatibility-mutated", expect_failure=True)
    finally:
        source.write_text(original, encoding="utf-8")
        os.utime(str(source), None)
        execute(rebuild, build / "restore-build.log")
    check("compatibility-restored")
    print("Compatibility regression: green -> assertion failure -> green")


if __name__ == "__main__":
    main()
