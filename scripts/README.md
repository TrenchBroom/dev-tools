# Python Utilities for TrenchBroom CMake Builds

This directory contains helper scripts that streamline working with the TrenchBroom CMake build through Python.

## Available scripts

### `list_cmake_targets.py`

Queries the CMake File API and prints the names of all executable and library targets in the configured build tree.

Typical usage:

```
python3 cmake/list_cmake_targets.py -B build --config-name Debug
```

Key options:

- `-B/--build-dir`: Path to the CMake build directory (default: `build`).
- `--config-name`: Multi-config generators can specify `Debug`, `Release`, etc.
- `--refresh`: Re-run CMake configuration to refresh File API replies before listing.

### `build_target.py`

Provides a fuzzy-search interface (powered by `InquirerPy`) to select a target and store the selection in the `TB_BUILD_TARGET` environment variable for later use. It can also run non-interactively when a target name is supplied.

Examples:

```
# Interactive fuzzy prompt that sets TB_BUILD_TARGET
python3 cmake/build_target.py -B build --config-name Debug

# Non-interactive selection that sets TB_BUILD_TARGET directly
python3 cmake/build_target.py -B build --config-name Debug --target TrenchBroom
```

### `select_target.py`

Provides the same fuzzy-search experience as `build_target.py`, writes the chosen target to a file for other stages to consume, and whenever the target is an executable it also (re)generates a `debug.json` inside `.zed/` so Zed’s CodeLLDB adapter can launch it immediately. Use `--target` to skip the prompt, `--zed-debug-path` to pick a different output file, and `--cmake`/`--jobs` to control the build command recorded in that debug profile.

```
# Interactive selection persisted to /tmp/tb_target.txt
# and (for executable targets) mirrored into .zed/debug.json
python3 cmake/select_target.py -B build --config-name Debug --output /tmp/tb_target.txt --zed-debug-path .zed/debug.json

# Non-interactive selection that overwrites the target file and Zed config
python3 cmake/select_target.py -B build --config-name Debug --target TrenchBroom --output /tmp/tb_target.txt --zed-debug-path .zed/debug.json -j 8
```

### `build_selected_target.py`

Reads a target name from a file (typically produced by `select_target.py`) and runs `cmake --build` for that target, respecting generator configs and parallel job settings.

```
# Preview the build command
python3 cmake/build_selected_target.py --target-file /tmp/tb_target.txt -B build --config-name Debug --dry-run

# Execute the build with 8 parallel jobs
python3 cmake/build_selected_target.py --target-file /tmp/tb_target.txt -B build --config-name Debug -j 8
```

### `run_tests.py`

Executes the currently selected target as a Catch2 suite, automatically discovering the executable via the CMake File API and fanning tests out across every available logical CPU using Catch2’s sharding flags. You can pass additional Catch2 options after `--`.

```
# Run all shards (defaults to the machine's core count)
python3 cmake/run_tests.py --target-file /tmp/tb_target.txt -B build --config-name Debug

# Force a smaller shard count and forward extra Catch2 filters
python3 cmake/run_tests.py --target-file /tmp/tb_target.txt -B build --shards 4 -- --success --abort
```


## Managing dependencies with uv


The Python utilities use [`uv`](https://github.com/astral-sh/uv) for dependency management. The project metadata lives in the repository root `pyproject.toml`, which declares the required packages (currently `InquirerPy>=0.3.4`).

Common workflows:

1. **Install uv** (once per machine):

   ```
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Create a virtual environment** (once per project):

   ```
   uv venv create
   ```

3. **Install dependencies** (from repo root):

   ```
   uv pip install -r pyproject.toml
   ```

   This installs `InquirerPy` into uv’s managed environment.

4. **Run scripts via uv** to ensure they see the managed interpreter and packages:

   ```
   uv run python cmake/list_cmake_targets.py -B build
   uv run python cmake/build_target.py -B build
   ```

   `uv run` automatically resolves the virtual environment according to `pyproject.toml`.

5. **Add new dependencies** by editing `pyproject.toml` and re-running `uv pip install -r pyproject.toml`.

## Tips

- The scripts expect that you have already configured the CMake build directory (e.g., via `cmake -S . -B build`).
- If you switch generators or configurations, re-run `cmake` so the File API replies stay up-to-date.
- After running `build_target.py`, read the `TB_BUILD_TARGET` environment variable to decide which target to pass to downstream build commands.
- Use `select_target.py` together with `build_selected_target.py` when you want to persist the selection in a file, kick off the build later (even from another shell or CI step), and keep Zed’s CodeLLDB debug configuration in sync for executable targets.
- Invoke `run_tests.py` after building the target to run Catch2 suites in parallel shards; pass `--` followed by Catch2 flags to narrow or expand the test selection as needed.
