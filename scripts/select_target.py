#!/usr/bin/env python3
"""
Interactive helper that lists CMake targets via the CMake File API,
lets you pick one with an InquirerPy fuzzy prompt, writes the selection
to a user-provided file, and (for executable targets) generates a Zed
debug configuration under .zed/debug.json.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

try:
    from InquirerPy.prompts import FuzzyPrompt
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Fuzzy prompt support requires InquirerPy. Install it via 'pip install InquirerPy' and rerun."
    ) from exc

FILE_API_RELATIVE = Path(".cmake") / "api" / "v1"
CLIENT_NAME = "select-target"
ALLOWED_TARGET_TYPES = {"EXECUTABLE", "STATIC_LIBRARY", "SHARED_LIBRARY"}
EXECUTABLE_TYPES = {"EXECUTABLE"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Interactively select a CMake target with an InquirerPy fuzzy prompt, write it to a file, "
            "and emit a Zed debug configuration when the target is executable."
        )
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
        help="Configuration to inspect (e.g. Debug). Omit for single-config generators.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="File path where the selected target name will be written.",
    )
    parser.add_argument(
        "--target",
        default=None,
        help="Skip the interactive prompt and store this target directly (use 'all' to select everything).",
    )
    parser.add_argument(
        "--cmake",
        default="cmake",
        help="Path to the CMake executable recorded in the Zed debug configuration (default: %(default)s).",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=None,
        help="Optional parallel build job count added to the debug configuration.",
    )
    parser.add_argument(
        "--zed-debug-path",
        default=".zed/debug.json",
        help="Location of the generated Zed debug.json file (default: %(default)s).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    build_dir = Path(args.build_dir).resolve()
    ensure_file_api_query(build_dir)

    targets = load_target_metadata(build_dir, args.config_name)
    if not targets:
        raise SystemExit("No eligible targets were found in the build tree.")

    target_names = list(dict.fromkeys(target["name"] for target in targets))
    if "all" not in target_names:
        target_names = ["all", *target_names]

    metadata_by_name = {}
    for target in targets:
        metadata_by_name.setdefault(target["name"], target)

    if args.target:
        target_name = validate_target_choice(args.target, target_names)
    else:
        target_name = prompt_for_target(target_names)

    output_path = Path(args.output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"{target_name}\n", encoding="utf-8")
    print(f"Wrote selected target '{target_name}' to {output_path}.")

    if target_name == "all":
        return

    target_metadata = metadata_by_name.get(target_name)
    if not target_metadata:
        print(
            f"Selected target '{target_name}' is not present in the CMake codemodel metadata. "
            "Skipping Zed debug configuration."
        )
        return

    if target_metadata["type"] not in EXECUTABLE_TYPES:
        print(
            f"Target '{target_name}' is not an executable. Skipping Zed debug configuration."
        )
        return

    artifact_path = resolve_artifact_path(target_metadata["artifacts"], build_dir)
    if artifact_path is None:
        print(
            f"Target '{target_name}' did not report any artifacts. Cannot derive a debug program path."
        )
        return

    write_zed_debug_config(
        target_name=target_name,
        artifact_path=artifact_path,
        build_dir=build_dir,
        cmake_command=args.cmake,
        config_name=args.config_name,
        jobs=args.jobs,
        destination=Path(args.zed_debug_path).expanduser(),
    )


def prompt_for_target(targets: Sequence[str]) -> str:
    unique_targets = list(dict.fromkeys(targets))
    prompt = FuzzyPrompt(
        message="Select target:",
        choices=unique_targets,
        match_exact=True,
        height="80%",
    )

    print("Type to fuzzy-search targets. Press Enter to select, Ctrl+C to abort.")
    try:
        selection = prompt.execute()
    except KeyboardInterrupt:
        raise SystemExit("\nAborted by user.") from None
    except EOFError:
        raise SystemExit("\nNo target selected.") from None

    if not selection:
        raise SystemExit("No target selected.")

    return selection


def validate_target_choice(choice: str, targets: Sequence[str]) -> str:
    choice = choice.strip()
    if choice in targets:
        return choice
    matches = [target for target in targets if target.startswith(choice)]
    if len(matches) == 1:
        return matches[0]
    raise SystemExit(
        f"Target '{choice}' was not found. Available targets include: {', '.join(targets[:10])}..."
    )


def ensure_file_api_query(build_dir: Path) -> None:
    query_dir = build_dir / FILE_API_RELATIVE / f"query/client-{CLIENT_NAME}"
    query_dir.mkdir(parents=True, exist_ok=True)
    query_file = query_dir / "query.json"
    desired_query = {
        "requests": [
            {
                "kind": "codemodel",
                "version": {"major": 2},
            }
        ]
    }

    if query_file.exists():
        try:
            current = json.loads(query_file.read_text())
            if current == desired_query:
                return
        except json.JSONDecodeError:
            pass

    query_file.write_text(json.dumps(desired_query, indent=2) + "\n", encoding="utf-8")


def load_target_metadata(
    build_dir: Path, requested_config: str | None
) -> List[Dict[str, Any]]:
    reply_dir = build_dir / FILE_API_RELATIVE / "reply"
    if not reply_dir.is_dir():
        raise SystemExit(
            f"Reply directory '{reply_dir}' does not exist. Run CMake at least once to generate File API replies."
        )

    index_data = load_latest_index(reply_dir)
    codemodel_file = locate_codemodel_file(index_data)
    codemodel = load_json(reply_dir / codemodel_file)
    configuration = select_configuration(codemodel, requested_config)
    return extract_targets(reply_dir, configuration)


def load_latest_index(reply_dir: Path) -> Dict[str, Any]:
    index_files = sorted(
        reply_dir.glob("index-*.json"),
        key=lambda candidate: candidate.stat().st_mtime,
        reverse=True,
    )
    if not index_files:
        raise SystemExit(
            f"No File API index files found in '{reply_dir}'. Run CMake with the query file in place."
        )

    for candidate in index_files:
        try:
            return load_json(candidate)
        except SystemExit:
            continue

    raise SystemExit(
        f"Unable to load any valid File API index files from '{reply_dir}'."
    )


def locate_codemodel_file(index_data: Dict[str, Any]) -> str:
    reply_entries: List[Dict[str, Any]] = []

    reply_section = index_data.get("reply")
    if isinstance(reply_section, list):
        reply_entries.extend(
            entry for entry in reply_section if isinstance(entry, dict)
        )
    elif isinstance(reply_section, dict):
        for value in reply_section.values():
            if isinstance(value, dict):
                responses = value.get("responses")
                if isinstance(responses, list):
                    reply_entries.extend(
                        entry for entry in responses if isinstance(entry, dict)
                    )

    objects_section = index_data.get("objects")
    if isinstance(objects_section, list):
        reply_entries.extend(
            entry for entry in objects_section if isinstance(entry, dict)
        )

    for entry in reply_entries:
        if (
            entry.get("kind") == "codemodel"
            and entry.get("version", {}).get("major") == 2
            and entry.get("jsonFile")
        ):
            return str(entry["jsonFile"])

    raise SystemExit(
        "The File API index does not reference a codemodel reply. Re-run CMake to generate one."
    )


def select_configuration(
    codemodel: Dict[str, Any], requested_name: str | None
) -> Dict[str, Any]:
    configurations = codemodel.get("configurations") or []
    if not configurations:
        raise SystemExit("Codemodel reply does not contain any configurations.")

    if requested_name is None:
        return configurations[0]

    for configuration in configurations:
        if configuration.get("name") == requested_name:
            return configuration

    available = ", ".join(
        config.get("name") or "<unnamed>" for config in configurations
    )
    raise SystemExit(
        f"Configuration '{requested_name}' not found. Available configurations: {available}"
    )


def extract_targets(
    reply_dir: Path, configuration: Dict[str, Any]
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    target_refs = configuration.get("targets")
    if not isinstance(target_refs, list):
        return results

    for target_ref in target_refs:
        if not isinstance(target_ref, dict):
            continue
        json_file = target_ref.get("jsonFile")
        if not json_file:
            continue

        target_data = load_json(reply_dir / json_file)
        target_type = target_data.get("type") or "UNKNOWN"
        target_name = target_data.get("name") or target_ref.get("name") or "<unnamed>"

        artifacts_field = target_data.get("artifacts") or []
        artifacts: List[str] = []
        if isinstance(artifacts_field, list):
            for artifact in artifacts_field:
                if isinstance(artifact, dict) and artifact.get("path"):
                    artifacts.append(str(artifact["path"]))

        if target_type in ALLOWED_TARGET_TYPES:
            results.append(
                {
                    "name": target_name,
                    "type": target_type,
                    "artifacts": artifacts,
                }
            )

    return results


def load_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"File '{path}' contains invalid JSON: line {exc.lineno} column {exc.colno}."
        ) from exc
    except OSError as exc:
        raise SystemExit(f"Unable to read '{path}': {exc.strerror or exc}") from exc

    if not isinstance(data, dict):
        raise SystemExit(f"File '{path}' does not contain a JSON object.")

    return data


def resolve_artifact_path(artifacts: Sequence[str], build_dir: Path) -> Path | None:
    for artifact in artifacts:
        artifact_path = Path(artifact)
        if artifact_path.is_absolute():
            return artifact_path
        return (build_dir / artifact_path).resolve()
    return None


def write_zed_debug_config(
    *,
    target_name: str,
    artifact_path: Path,
    build_dir: Path,
    cmake_command: str,
    config_name: str | None,
    jobs: int | None,
    destination: Path,
) -> None:
    worktree_root = Path.cwd().resolve()
    program = format_path_for_zed(artifact_path, worktree_root)
    build_dir_arg = format_path_for_zed(build_dir, worktree_root)

    build_args = ["--build", build_dir_arg, "--target", target_name]
    if config_name:
        build_args.extend(["--config", config_name])
    if jobs:
        build_args.extend(["-j", str(jobs)])

    config = [
        {
            "label": f"Debug {target_name}",
            "build": {
                "command": cmake_command,
                "args": build_args,
                "cwd": "$ZED_WORKTREE_ROOT",
            },
            "program": program,
            "request": "launch",
            "adapter": "CodeLLDB",
        }
    ]

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote Zed debug configuration for '{target_name}' to {destination}.")


def format_path_for_zed(path: Path, worktree_root: Path) -> str:
    path = path.resolve()
    try:
        relative = path.relative_to(worktree_root)
        return f"$ZED_WORKTREE_ROOT/{relative.as_posix()}"
    except ValueError:
        return str(path)


if __name__ == "__main__":
    main()
