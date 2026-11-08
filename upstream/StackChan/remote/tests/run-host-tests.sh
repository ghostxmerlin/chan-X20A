#!/usr/bin/env bash
set -euo pipefail
REMOTE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_TEST_BUILD="$(mktemp -d "${TMPDIR:-/tmp}/remote-host-tests.XXXXXX")"
trap 'rm -rf "$REMOTE_TEST_BUILD"' EXIT
CC="${CC:-cc}"
CXX="${CXX:-c++}"
includes=(-I "$REMOTE_ROOT/tests/host_stubs" -I "$REMOTE_ROOT/code/main"
          -I "$REMOTE_ROOT/code/main/ui" -I "$REMOTE_ROOT/code/main/joystick"
          -I "$REMOTE_ROOT/code/main/esp_now")
"$CC" -std=c11 -Wall -Wextra -Werror "${includes[@]}" \
    -c "$REMOTE_ROOT/code/main/remote_settings.c" -o "$REMOTE_TEST_BUILD/settings.o"
for test in remote_settings_test remote_boot_test; do
    "$CXX" -std=c++17 -Wall -Wextra -Werror "${includes[@]}" \
        "$REMOTE_ROOT/tests/$test.cpp" "$REMOTE_ROOT/tests/fake_nvs.cpp" \
        "$REMOTE_TEST_BUILD/settings.o" -o "$REMOTE_TEST_BUILD/$test"
    "$REMOTE_TEST_BUILD/$test"
done
"$CXX" -std=c++17 -Wall -Wextra -Werror -Wno-unused-variable "${includes[@]}" \
    "$REMOTE_ROOT/tests/remote_setup_ui_test.cpp" -o "$REMOTE_TEST_BUILD/setup_ui"
"$REMOTE_TEST_BUILD/setup_ui"
