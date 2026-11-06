"""Provision tools on Actions runners only (never read local credentials)."""
import argparse
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import tempfile
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import execute_main
from common import REPO, context, export_env, require, run, sha256, unpack, work_root

CMAKE_VERSION = '3.31.6'
ZIG_VERSION = '0.17.1'
ZIG_SHA256 = '4789895bdb1e312a2ea73b2a7c0a0cc8d2ab62ba6976e834a7c54cf443377c15'


def fresh(root, category):
    parent = root / category
    parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(dir=str(parent)))


def download(url, destination):
    require(url.startswith('https://'), 'Tool downloads must use HTTPS')
    request = urllib.request.Request(url, headers={'User-Agent': 'leveldb-release-actions'})
    with urllib.request.urlopen(request, timeout=120) as source, destination.open('wb') as target:
        shutil.copyfileobj(source, target)


def cmake_linux(root):
    machine = platform.machine().lower()
    require(machine in ('x86_64', 'aarch64', 'arm64'), 'Unsupported Linux host')
    host = 'x86_64' if machine == 'x86_64' else 'aarch64'
    filename = 'cmake-{}-linux-{}.tar.gz'.format(CMAKE_VERSION, host)
    base = 'https://github.com/Kitware/CMake/releases/download/v' + CMAKE_VERSION + '/'
    downloads = fresh(root, 'downloads')
    archive = downloads / filename
    manifest = downloads / ('cmake-' + CMAKE_VERSION + '-SHA-256.txt')
    download(base + filename, archive)
    download(base + manifest.name, manifest)
    matches = []
    for line in manifest.read_text(encoding='utf-8').splitlines():
        match = re.fullmatch(r'([0-9a-fA-F]{64})\s+\*?(.+)', line.strip())
        if match and match.group(2) == filename:
            matches.append(match.group(1).lower())
    require(len(matches) == 1 and sha256(archive) == matches[0],
            'Kitware official checksum mismatch')
    destination = fresh(root, 'tools')
    unpack(archive, destination)
    binary = destination / filename[:-len('.tar.gz')] / 'bin' / 'cmake'
    require(binary.is_file(), 'CMake executable missing from official archive')
    export_env('RELEASE_CMAKE', binary)
    export_env('RELEASE_CTEST', binary.with_name('ctest'))


def find_ninja():
    found = shutil.which('ninja')
    if found:
        return Path(found).resolve()
    candidates = list(Path('C:/Program Files/Microsoft Visual Studio').glob(
        '*/**/CommonExtensions/Microsoft/CMake/Ninja/ninja.exe'))
    require(candidates, 'Hosted runner must supply native Ninja')
    return candidates[0].resolve()


def bootstrap_mingw(root):
    pacman = Path('C:/msys64/usr/bin/pacman.exe')
    require(pacman.is_file(), 'Hosted runner MSYS2 installation not found')
    env = os.environ.copy()
    env['MSYSTEM'] = 'MSYS'
    env['MSYS2_PATH_TYPE'] = 'inherit'
    run([pacman, '-Sy', '--needed', '--noconfirm',
         'mingw-w64-ucrt-x86_64-gcc', 'mingw-w64-i686-gcc',
         'mingw-w64-ucrt-x86_64-ninja'], root / 'logs' / 'pacman.log', env=env)
    for architecture, directory in (('x64', 'ucrt64'), ('x86', 'mingw32')):
        bin_dir = Path('C:/msys64') / directory / 'bin'
        require((bin_dir / 'gcc.exe').is_file() and (bin_dir / 'g++.exe').is_file(),
                'Missing MinGW compiler: ' + architecture)
        export_env('RELEASE_MINGW_' + architecture.upper(), bin_dir)
    export_env('RELEASE_NINJA', Path('C:/msys64/ucrt64/bin/ninja.exe'))


def bootstrap_zig(root):
    require(platform.machine().lower() in ('arm64', 'aarch64'),
            'Zig release tests require a native Windows ARM64 runner')
    filename = 'zig-aarch64-windows-' + ZIG_VERSION + '.zip'
    archive = fresh(root, 'downloads') / filename
    download('https://ziglang.org/download/' + ZIG_VERSION + '/' + filename, archive)
    require(sha256(archive) == ZIG_SHA256, 'Zig official archive checksum mismatch')
    destination = fresh(root, 'tools')
    unpack(archive, destination)
    binaries = list(destination.glob('*/zig.exe'))
    require(len(binaries) == 1, 'Expected one Zig executable')
    zig = binaries[0].resolve()
    # Wrappers use the environment to keep executable paths out of cmd source.
    require(not any(c in str(zig) for c in '%!&|<>^\r\n"'), 'Unsafe Zig tool path')
    export_env('LEVELDB_ZIG_EXE', zig)
    export_env('RELEASE_NINJA', find_ninja())
    actual = run([zig, 'version'], root / 'logs' / 'zig-version.log').strip()
    require(actual == ZIG_VERSION, 'Unexpected Zig version')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--family', required=True, choices=('linux', 'msvc', 'mingw', 'zig'))
    args = parser.parse_args()
    require(os.environ.get('GITHUB_ACTIONS') == 'true', 'Provisioning is restricted to Actions')
    context()
    root = work_root()
    (root / 'logs').mkdir(parents=True, exist_ok=True)
    if args.family == 'linux':
        arch = os.environ.get('RELEASE_ARCH')
        require(arch in ('x64', 'x86', 'arm64'), 'Invalid target architecture')
        if arch == 'x86':
            run(['apt-get', '-y', 'install', '--no-install-recommends',
                 'gcc-multilib', 'g++-multilib'], root / 'logs' / 'multilib.log')
        cmake_linux(root)
    else:
        require(os.name == 'nt', 'Windows release requires a Windows runner')
        cmake = shutil.which('cmake')
        ctest = shutil.which('ctest')
        require(cmake and ctest, 'Hosted CMake/CTest are required')
        export_env('RELEASE_CMAKE', Path(cmake).resolve())
        export_env('RELEASE_CTEST', Path(ctest).resolve())
        if args.family == 'mingw':
            bootstrap_mingw(root)
        if args.family == 'zig':
            bootstrap_zig(root)


if __name__ == '__main__':
    execute_main(main)
