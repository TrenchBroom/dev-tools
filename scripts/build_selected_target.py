#!/usr/bin/env python3
"""
Build a previously selected CMake target whose name is stored in a file.

Typical usage:
    python3 build_selected_target.py --target-file /tmp/selected_target.txt -B build --config-name Debug
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read a target name from a file and invoke `cmake --build` for that target."
    )
    parser.add_argument(
        "--target-file",
        required=True,
        help="Path to the file containing the target name (first non-empty line is used).",
    )
    parser.add_argument(
        "-B",
        "--build-dir",
        default="build",
        help="Path to the CMake build directory (default: %(default)s).",
    )
    parser.add_argument(
        "--config-name",
        default=None,
        help="Configuration to build (e.g. Debug). Omit for single-config generators.",
    )
    parser.add_argument(
        "--cmake",
        default="cmake",
        help="Path to the CMake executable (default: %(default)s).",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=None,
        help="Number of parallel build jobs to pass to CMake.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the build command without executing it.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    target = read_target(Path(args.target_file))
    build_dir = Path(args.build_dir).resolve()

    cmd = compose_build_command(
        cmake_exe=args.cmake,
        build_dir=build_dir,
        target=target,
        config_name=args.config_name,
        jobs=args.jobs,
    )

    print("Building target with command:")
    print("  ", " ".join(shlex.quote(part) for part in cmd))

    if args.dry_run:
        return

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"CMake exited with status {exc.returncode} while building '{target}'."
        ) from exc


def read_target(target_file: Path) -> str:
    if not target_file.is_file():
        raise SystemExit(f"Target file not found: {target_file}")

    for line in target_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped

    raise SystemExit(f"No target name found in {target_file}.")


def compose_build_command(
    cmake_exe: str,
    build_dir: Path,
    target: str,
    config_name: Optional[str],
    jobs: Optional[int],
) -> List[str]:
    cmd = [cmake_exe, "--build", str(build_dir), "--target", target]
    if config_name:
        cmd.extend(["--config", config_name])
    if jobs:
        cmd.extend(["-j", str(jobs)])
    return cmd


if __name__ == "__main__":
    main()
