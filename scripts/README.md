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

Provides a fuzzy-search interface identical to `build_target.py`, but writes the selected target to a file so other processes can consume it later. You can also skip the prompt by supplying `--target`.

```
# Interactive selection written to /tmp/tb_target.txt
python3 cmake/select_target.py -B build --config-name Debug --output /tmp/tb_target.txt

# Non-interactive selection that overwrites the target file
python3 cmake/select_target.py -B build --config-name Debug --target TrenchBroom --output /tmp/tb_target.txt
```

### `build_selected_target.py`

Reads a target name from a file (typically produced by `select_target.py`) and runs `cmake --build` for that target, respecting generator configs and parallel job settings.

```
# Preview the build command
python3 cmake/build_selected_target.py --target-file /tmp/tb_target.txt -B build --config-name Debug --dry-run

# Execute the build with 8 parallel jobs
python3 cmake/build_selected_target.py --target-file /tmp/tb_target.txt -B build --config-name Debug -j 8
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
- Use `select_target.py` together with `build_selected_target.py` when you want to persist the selection in a file and kick off the build later (even from another shell or CI step).
