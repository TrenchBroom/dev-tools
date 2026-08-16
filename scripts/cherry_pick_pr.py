#!/usr/bin/env python3
"""
Cherry-pick merged pull requests onto a base branch and open a pull request.

Creates a branch named cp-<number>-<number>... off the base branch, cherry-picks
the merge commit of every given pull request onto it in the given order (pausing
so you can resolve conflicts), builds the CMake build tree, runs the tests, and
finally pushes the branch and opens a pull request titled "Cherry Pick #<number>
#<number>..." against the base branch, listing the cherry-picked pull requests in
its body.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

# Git config key below branch.<name> that records the base branch of a cherry-pick.
BASE_BRANCH_CONFIG_KEY = "cherryPickBase"


@dataclass(frozen=True)
class PullRequest:
    number: int
    state: str
    title: str
    branch: str
    merge_commit: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Cherry-pick merged pull requests onto a base branch, build and test "
            "the result, and open a pull request for them."
        )
    )
    parser.add_argument(
        "pr_numbers",
        type=int,
        nargs="+",
        metavar="pr-number",
        help="Numbers of the merged pull requests to cherry-pick, in the order in "
        "which they should be applied.",
    )
    parser.add_argument(
        "-b",
        "--base",
        default=None,
        help="Base branch to cherry-pick onto (default: the checked out branch).",
    )
    parser.add_argument(
        "-B",
        "--build-dir",
        default="build",
        help="Path to the configured CMake build directory (default: %(default)s).",
    )
    parser.add_argument(
        "-C",
        "--repo",
        default=".",
        help="Path inside the TrenchBroom repository (default: %(default)s).",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=None,
        help="Number of tests to run in parallel (default: as many as possible).",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip building and running the tests.",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Do not ask before pushing the branch and creating the pull request.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Keep our own output interleaved correctly with that of git, cmake and ctest.
    sys.stdout.reconfigure(line_buffering=True)

    require_tools(args.skip_build)
    repo = find_repo_root(Path(args.repo))
    require_idle_worktree(repo)

    base_branch = args.base or default_base_branch(repo)
    if not branch_exists(repo, base_branch):
        raise SystemExit(f"No such branch: {base_branch}")

    build_dir = Path(args.build_dir)
    if not build_dir.is_absolute():
        build_dir = repo / build_dir
    if not args.skip_build and not (build_dir / "CMakeCache.txt").is_file():
        raise SystemExit(
            f"'{build_dir}' is not a configured CMake build directory. "
            "Configure it first or pass --build-dir."
        )

    announce("Fetching from origin")
    git(repo, "fetch", "origin")
    warn_if_behind_origin(repo, base_branch, assume_yes=args.yes)

    if len(set(args.pr_numbers)) != len(args.pr_numbers):
        raise SystemExit("The same pull request was given more than once.")

    pull_requests = []
    for pr_number in args.pr_numbers:
        announce(f"Reading pull request #{pr_number}")
        pull_request = read_pull_request(repo, pr_number)
        if pull_request.state != "MERGED":
            raise SystemExit(
                f"Pull request #{pull_request.number} is {pull_request.state}, "
                "only merged pull requests can be cherry-picked."
            )
        if not pull_request.merge_commit:
            raise SystemExit(
                f"Pull request #{pull_request.number} has no merge commit."
            )
        ensure_commit_available(repo, pull_request)
        pull_requests.append(pull_request)

    # The cherry-pick branch can already exist because an earlier run stopped at a
    # failing build or test. If it is checked out, continue where that run left off.
    branch = cherry_pick_branch_name(repo, pull_requests)
    resuming = branch_exists(repo, branch)
    if resuming and current_branch(repo) != branch:
        raise SystemExit(f"Branch already exists: {branch}")
    if base_branch == branch:
        raise SystemExit(
            f"{branch} is the cherry-pick branch for the given pull requests, not a "
            "base branch. Pass the branch to cherry-pick onto with --base."
        )

    print()
    for pull_request in pull_requests:
        print(
            f"#{pull_request.number}: {pull_request.title} "
            f"({describe(repo, pull_request.merge_commit)})"
        )
    print(f"cherry-pick branch: {branch}")
    print(f"base branch:        {base_branch}")

    if resuming:
        announce(f"Branch {branch} already exists and is checked out")
        if not confirm(
            "Continue with the build, the tests and the pull request?",
            assume_yes=args.yes,
        ):
            raise SystemExit("Aborted.")
    else:
        announce(f"Creating branch {branch}")
        git(repo, "switch", "-c", branch, base_branch)

        for pull_request in pull_requests:
            announce(
                f"Cherry-picking {pull_request.merge_commit} of "
                f"pull request #{pull_request.number}"
            )
            cherry_pick(repo, base_branch, branch, pull_request.merge_commit)

    # Remember the base branch so that a resumed run finds it again even though the
    # cherry-pick branch is then the checked out branch.
    git(repo, "config", f"branch.{branch}.{BASE_BRANCH_CONFIG_KEY}", base_branch)

    # The cherry-pick can end up empty, for example when it was skipped or aborted.
    if int(git(repo, "rev-list", "--count", f"{base_branch}..{branch}")) == 0:
        raise SystemExit(
            f"{branch} has no commits beyond {base_branch}, there is nothing to open a "
            "pull request for."
        )

    if not args.skip_build:
        announce(f"Building {build_dir}")
        run_or_fail(
            ["cmake", "--build", str(build_dir)],
            cwd=repo,
            message=f"The build failed. {branch} is checked out, fix it and rerun with --skip-build.",
        )

        announce("Running tests")
        # ctest runs the tests serially unless it is told otherwise.
        parallel = ["--parallel"] + ([str(args.jobs)] if args.jobs else [])
        run_or_fail(
            ["ctest", "--test-dir", str(build_dir), "--output-on-failure", *parallel],
            cwd=repo,
            message=f"The tests failed. {branch} is checked out, fix them and rerun with --skip-build.",
        )

    announce(f"Pushing {branch} and creating the pull request")
    if not confirm(
        f"Push {branch} to origin and open a pull request against {base_branch}?",
        assume_yes=args.yes,
    ):
        raise SystemExit(f"Aborted. {branch} is checked out but was not pushed.")

    git(repo, "push", "--set-upstream", "origin", branch)
    run_or_fail(
        [
            "gh",
            "pr",
            "create",
            "--base",
            base_branch,
            "--head",
            branch,
            "--title",
            pull_request_title(pull_requests),
            "--body",
            pull_request_body(pull_requests),
        ],
        cwd=repo,
        message="Creating the pull request failed.",
    )


def require_tools(skip_build: bool) -> None:
    tools = ["git", "gh"] if skip_build else ["git", "gh", "cmake", "ctest"]
    for tool in tools:
        if shutil.which(tool) is None:
            raise SystemExit(f"'{tool}' is not installed or not on PATH.")


def find_repo_root(path: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"'{path}' is not inside a git repository.")
    return Path(result.stdout.strip())


def require_idle_worktree(repo: Path) -> None:
    if git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise SystemExit(
            "The working tree has uncommitted changes, commit or stash them first."
        )
    for name, description in (
        ("CHERRY_PICK_HEAD", "cherry-pick"),
        ("MERGE_HEAD", "merge"),
        ("rebase-merge", "rebase"),
        ("rebase-apply", "rebase"),
    ):
        if git_path(repo, name).exists():
            raise SystemExit(f"A {description} is in progress.")


def git_path(repo: Path, name: str) -> Path:
    """Return the path of a file in the git directory, for example CHERRY_PICK_HEAD."""
    return repo / git(repo, "rev-parse", "--git-path", name)


def cherry_pick_in_progress(repo: Path) -> bool:
    return git_path(repo, "CHERRY_PICK_HEAD").exists() or git_path(
        repo, "sequencer"
    ).exists()


def default_base_branch(repo: Path) -> str:
    branch = current_branch(repo)
    recorded = git(
        repo,
        "config",
        "--get",
        f"branch.{branch}.{BASE_BRANCH_CONFIG_KEY}",
        check=False,
    )
    return recorded or branch


def current_branch(repo: Path) -> str:
    branch = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if not branch:
        raise SystemExit("HEAD is detached, pass a base branch with --base.")
    return branch


def branch_exists(repo: Path, branch: str) -> bool:
    return (
        git_status(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}") == 0
    )


def warn_if_behind_origin(repo: Path, base_branch: str, assume_yes: bool) -> None:
    if (
        git_status(
            repo, "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{base_branch}"
        )
        != 0
    ):
        return

    behind = int(
        git(repo, "rev-list", "--count", f"{base_branch}..origin/{base_branch}")
    )
    if behind > 0:
        print(f"{base_branch} is {behind} commit(s) behind origin/{base_branch}.")
        if not confirm(
            f"Cherry-pick onto the local {base_branch} anyway?", assume_yes=assume_yes
        ):
            raise SystemExit("Aborted.")


def read_pull_request(repo: Path, pr_number: int) -> PullRequest:
    result = subprocess.run(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--json",
            "state,title,headRefName,mergeCommit",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"Cannot read pull request #{pr_number}: {result.stderr.strip()}"
        )

    data = json.loads(result.stdout)
    merge_commit = data.get("mergeCommit") or {}
    return PullRequest(
        number=pr_number,
        state=data.get("state") or "UNKNOWN",
        title=data.get("title") or "",
        branch=data.get("headRefName") or "",
        merge_commit=merge_commit.get("oid") or "",
    )


def ensure_commit_available(repo: Path, pull_request: PullRequest) -> None:
    if git_status(repo, "cat-file", "-e", f"{pull_request.merge_commit}^{{commit}}") == 0:
        return
    if git_status(repo, "fetch", "origin", pull_request.merge_commit) != 0:
        raise SystemExit(
            f"Cannot fetch merge commit {pull_request.merge_commit} of "
            f"pull request #{pull_request.number}."
        )


def cherry_pick_branch_name(repo: Path, pull_requests: Sequence[PullRequest]) -> str:
    branch = "cp-" + "-".join(str(each.number) for each in pull_requests)
    if git_status(repo, "check-ref-format", f"refs/heads/{branch}") != 0:
        raise SystemExit(f"Invalid branch name: {branch}")
    return branch


def pull_request_title(pull_requests: Sequence[PullRequest]) -> str:
    return "Cherry Pick " + " ".join(f"#{each.number}" for each in pull_requests)


def pull_request_body(pull_requests: Sequence[PullRequest]) -> str:
    # GitHub expands the reference to the pull request's title by itself.
    return "\n".join(f"- #{each.number}" for each in pull_requests)


def cherry_pick(repo: Path, base_branch: str, branch: str, merge_commit: str) -> None:
    # A squash-merged pull request has an ordinary commit, which is cherry-picked
    # without -m, while a merge commit needs -m 1 to pick the changes of the branch.
    parents = git(repo, "rev-list", "--parents", "-n", "1", merge_commit).split()
    mainline = ["-m", "1"] if len(parents) > 2 else []

    if git_status(repo, "cherry-pick", *mainline, merge_commit, quiet=False) == 0:
        return

    while True:
        print()
        print(
            f"The cherry-pick stopped. Resolve any conflicts in {repo} and stage the\n"
            "result with git add, then continue here. If the commit turned out to be\n"
            "empty, skip it instead."
        )
        print(git(repo, "status", "--short", "--untracked-files=no"))

        answer = ask("[c]ontinue, [s]kip the commit, or [a]bort? ").lower()

        # The cherry-pick may have been finished, skipped or aborted from another
        # terminal while we were waiting for input here.
        if not cherry_pick_in_progress(repo):
            print("The cherry-pick is no longer in progress.")
            return

        if answer == "c":
            if git(repo, "diff", "--name-only", "--diff-filter=U"):
                print("There are still unmerged files.")
                continue
            # git wants to open an editor for the commit message of the picked commit.
            environment = dict(os.environ, GIT_EDITOR="true")
            if (
                git_status(
                    repo,
                    "-c",
                    "advice.waitingForEditor=false",
                    "cherry-pick",
                    "--continue",
                    quiet=False,
                    env=environment,
                )
                == 0
            ):
                return
        elif answer == "s":
            if git_status(repo, "cherry-pick", "--skip", quiet=False) == 0:
                return
        elif answer == "a":
            git(repo, "cherry-pick", "--abort")
            git(repo, "switch", base_branch)
            git(repo, "branch", "-D", branch)
            raise SystemExit(
                f"Aborted. {branch} was deleted, including any pull requests that "
                "were already cherry-picked onto it."
            )


def describe(repo: Path, commit: str) -> str:
    return git(repo, "log", "-1", "--format=%h %s", commit)


def announce(message: str) -> None:
    print(f"\n==> {message}")


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        raise SystemExit("Aborted, no input available.")


def confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    return ask(f"{prompt} [y/N] ").lower() in ("y", "yes")


def git(repo: Path, *args: str, check: bool = True) -> str:
    """Run a git command and return its output."""
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True
    )
    if check and result.returncode != 0:
        raise SystemExit(
            f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def git_status(
    repo: Path, *args: str, quiet: bool = True, env: dict[str, str] | None = None
) -> int:
    """Run a git command and return its exit code, printing its output unless quiet."""
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        capture_output=quiet,
        text=True,
    )
    return result.returncode


def run_or_fail(command: Sequence[str], cwd: Path, message: str) -> None:
    if subprocess.run(list(command), cwd=cwd).returncode != 0:
        raise SystemExit(message)


if __name__ == "__main__":
    main()
