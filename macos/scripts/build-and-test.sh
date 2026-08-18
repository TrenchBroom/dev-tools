#!/usr/bin/env bash

set -u -o pipefail

BUILD_DIR="${BUILD_DIR:-build}"

cmake -S . -B "$BUILD_DIR" || exit 1
cmake --build "$BUILD_DIR" --target all || exit 1

ctest --test-dir "$BUILD_DIR" --output-on-failure -j
