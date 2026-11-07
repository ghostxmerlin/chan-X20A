#!/usr/bin/env bash
# Build the optional ESP32 remote candidate in an isolated local directory.
# Never writes firmware or changes the source project's SDK/dependency files.
set -euo pipefail

STACKCHAN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export IDF_PATH="$STACKCHAN_ROOT/.tools/esp-idf-v5.5.4"
export IDF_TOOLS_PATH="$STACKCHAN_ROOT/.tools/espressif"
if [[ ! -f "$IDF_PATH/export.sh" ]]; then
    echo "Missing fixed ESP-IDF 5.5.4; see docs/build.md." >&2
    exit 1
fi
source "$IDF_PATH/export.sh" >/dev/null

python - "$STACKCHAN_ROOT" <<'PY'
from pathlib import Path
from datetime import datetime
import difflib
import atexit
import hashlib
import json
import shutil
import subprocess
import sys
import yaml

root = Path(sys.argv[1])
source = root / 'upstream/StackChan/remote/code'
isolated = root / '.cache/remote-center-v1'
records = root / 'logs/remote-center-v1'
attempt = records / ('build-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
attempt.mkdir(parents=True)
result_path = records / 'result.json'
result_path.write_text(json.dumps({'status': 'building', 'evidence': str(attempt.relative_to(root))}, indent=2) + '\n')
build_complete = False

def record_failure():
    if not build_complete:
        result_path.write_text(json.dumps({'status': 'build_failed', 'evidence': str(attempt.relative_to(root))}, indent=2) + '\n')

atexit.register(record_failure)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n')

def command(args, log):
    with log.open('w') as output:
        completed = subprocess.run(args, cwd=root, stdout=output, stderr=subprocess.STDOUT)
    if completed.returncode:
        tail = '\n'.join(log.read_text(errors='replace').splitlines()[-35:])
        raise RuntimeError(f'Command failed ({completed.returncode}): {args}\n{tail}\nFull log: {log}')

original_files = [source / name for name in ('dependencies.lock', 'sdkconfig', 'main/idf_component.yml')]
original_hashes = {str(p.relative_to(root)): sha(p) for p in original_files}
write_json(attempt / 'original-hashes.json', original_hashes)
lock = yaml.safe_load((source / 'dependencies.lock').read_text())
expected = lock['dependencies']
if lock.get('target') != 'esp32':
    raise RuntimeError('Expected the upstream ESP32 remote lock; no target conversion is allowed')
version = subprocess.check_output(['idf.py', '--version'], text=True).strip()
if version != 'ESP-IDF v5.5.4':
    raise RuntimeError(f'Expected fixed ESP-IDF v5.5.4, received {version!r}')

# Restore a previously patched managed source before Component Manager checks
# its registry hash. The saved original is itself checked before restoration.
relative_component = Path('managed_components/m5stack__m5gfx/src/lgfx/v1/platforms/esp32/common.cpp')
component_file = isolated / relative_component
saved_original = isolated / '.validation/m5gfx-common.cpp.original'
saved_hash = isolated / '.validation/m5gfx-common.cpp.sha256'
pattern = '__has_include(<driver/i2c_master.h>)'
# Original code has a specific old-driver branch for each of these five sites.
expected_contexts = (
    '#if __has_include(<driver/i2c_master.h>)\n #include <driver/i2c_master.h>',
    '#elif __has_include(<driver/i2c_master.h>)\n      i2c_master_bus_handle_t i2c_bus_handle',
    '#if __has_include(<driver/i2c_master.h>)\n      if ((int8_t)pin_sda',
    '#elif __has_include(<driver/i2c_master.h>)\n        auto bus_handle = i2c_context[i2c_port].i2c_bus_handle;',
    '#elif __has_include(<driver/i2c_master.h>)\n      i2c_master_bus_handle_t bus_handle',
)
if component_file.exists() and pattern not in component_file.read_text():
    if not saved_original.exists() or not saved_hash.exists() or sha(saved_original) != saved_hash.read_text().strip():
        raise RuntimeError('Patched managed source lacks a verified original; use a fresh isolation directory')
    if component_file.read_text() != saved_original.read_text().replace(pattern, '0'):
        raise RuntimeError('Managed M5GFX contains unexpected edits; refusing to overwrite them')
    component_file.write_bytes(saved_original.read_bytes())

# Only copy project sources; source-tree builds and packaged factory images are
# irrelevant to this candidate. The isolated manifest pins all transitive locks.
for item in source.iterdir():
    if item.name in ('build', 'managed_components', 'main') or item.suffix == '.bin':
        continue
    target = isolated / item.name
    if item.is_dir():
        shutil.copytree(item, target, dirs_exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
if (isolated / 'main').exists():
    shutil.rmtree(isolated / 'main')
shutil.copytree(source / 'main', isolated / 'main')
pins = {'idf': '=5.5.4'}
for name, meta in expected.items():
    if name == 'idf':
        continue
    if meta.get('source', {}).get('type') != 'service' or not meta.get('component_hash'):
        raise RuntimeError(f'Unsupported unpinned dependency source: {name}')
    pins[name] = '=' + str(meta['version'])
(isolated / 'main/idf_component.yml').write_text(
    '# Validation-only exact pins derived from the original dependencies.lock.\n' +
    yaml.safe_dump({'dependencies': pins}, sort_keys=False))
source_hashes = {str(p.relative_to(source / 'main')): sha(p)
                 for p in sorted((source / 'main').rglob('*')) if p.is_file() and p.name != 'idf_component.yml'}
write_json(attempt / 'main-source-hashes.json', source_hashes)
command(['idf.py', '-C', str(isolated), 'reconfigure'], attempt / 'reconfigure.log')
resolved = yaml.safe_load((isolated / 'dependencies.lock').read_text())['dependencies']
if set(resolved) != set(expected) or resolved['idf']['version'] != '5.5.4':
    raise RuntimeError('Dependency set or IDF version changed unexpectedly')
for name, meta in expected.items():
    if name == 'idf':
        continue
    if resolved[name]['version'] != meta['version'] or resolved[name]['component_hash'] != meta['component_hash']:
        raise RuntimeError(f'Dependency upgrade/hash mismatch refused: {name}')

original_text = component_file.read_text()
if original_text.count(pattern) != 5 or not all(context in original_text for context in expected_contexts):
    raise RuntimeError('M5GFX legacy-I2C recipe no longer matches its five original contexts')
saved_original.parent.mkdir(parents=True, exist_ok=True)
saved_original.write_bytes(component_file.read_bytes())
saved_hash.write_text(sha(saved_original) + '\n')
patched_text = original_text.replace(pattern, '0')
patch = ''.join(difflib.unified_diff(original_text.splitlines(True), patched_text.splitlines(True),
    fromfile='a/' + str(relative_component), tofile='b/' + str(relative_component)))
(attempt / 'm5gfx-legacy-i2c.patch').write_text(patch)
component_file.write_text(patched_text)
command(['idf.py', '-C', str(isolated), 'build'], attempt / 'build.log')

# Do not merely accept a linked image: the IDF constructor aborts if old and
# new I2C backends are linked together. Keep that check enabled and remove the
# new backend exactly as the upstream remote README prescribes.
elf = isolated / 'build/StackChan-RemoteControl-ESPNow.elf'
symbols = subprocess.check_output(['xtensa-esp32-elf-nm', '-C', str(elf)], text=True)
selected_symbols = '\n'.join(line for line in symbols.splitlines()
    if any(name in line for name in ('check_i2c_driver_conflict', 'i2c_driver_install', 'i2c_new_master_bus', 'i2c_acquire_bus_handle')))
(attempt / 'i2c-symbols.txt').write_text(selected_symbols + '\n')
if 'i2c_new_master_bus' in symbols or 'i2c_acquire_bus_handle' in symbols:
    raise RuntimeError('Candidate still links the new I2C backend')
if 'check_i2c_driver_conflict' not in symbols or 'i2c_driver_install' not in symbols:
    raise RuntimeError('Expected legacy I2C driver and conflict check are missing')
if 'CONFIG_I2C_SKIP_LEGACY_CONFLICT_CHECK=y' in (isolated / 'sdkconfig').read_text():
    raise RuntimeError('I2C conflict check must not be bypassed')
for filename, digest in original_hashes.items():
    if sha(root / filename) != digest:
        raise RuntimeError(f'Original source configuration changed during build: {filename}')
for filename, digest in source_hashes.items():
    if sha(source / 'main' / filename) != digest or sha(isolated / 'main' / filename) != digest:
        raise RuntimeError(f'Remote source changed during build; rerun: {filename}')
app = isolated / 'build/StackChan-RemoteControl-ESPNow.bin'
result = {
    'status': 'build_only_not_hardware_validated', 'target': 'esp32', 'idf_version': '5.5.4',
    'registry_versions': {name: meta['version'] for name, meta in expected.items() if name != 'idf'},
    'original_lock_config_manifest_unchanged': True, 'main_source_matches': True,
    'evidence': str(attempt.relative_to(root)), 'build_entrypoint': 'bash scripts/build-remote.sh',
    'application': {'path': str(app.relative_to(root)), 'bytes': app.stat().st_size, 'sha256': sha(app)},
    'hardware_actions': False,
}
write_json(attempt / 'result.json', result)
write_json(result_path, result)
build_complete = True
print(json.dumps(result, indent=2))
PY
