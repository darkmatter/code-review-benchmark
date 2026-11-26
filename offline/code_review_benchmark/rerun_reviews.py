#!/usr/bin/env python3
"""Re-run a tool's review on benchmark repos that already exist, without re-forking.

For each selected benchmark repo, the newest PR is closed and a new PR is
opened from the same branches. A reviewer that starts on newly opened PRs, and
reviews each PR once, then reviews the new PR from scratch. The script waits
until the tool's account has reviewed every new PR, then writes the reviewed
repo names so step1 can fetch just those (``--repos``).

PRs are re-opened in batches (``--batch-size``), each batch after the previous
one is reviewed: all 50 at once drained the reviewer account's GitHub API quota.

Usage:
    # Every benchmark repo the tool has in the org
    python -m code_review_benchmark.rerun_reviews --org darkmatter --tool darkmatter

    # A subset, by golden URL or <repo>#<number>
    python -m code_review_benchmark.rerun_reviews --org darkmatter --tool darkmatter \\
        --prs "keycloak#37429, sentry-greptile#1"

    # Show the selectable PRs
    python -m code_review_benchmark.rerun_reviews --org darkmatter --tool darkmatter --list
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import sys
import time

from code_review_benchmark.step1_download_prs import _TOOL_LOGINS
from code_review_benchmark.step1_download_prs import gh
from code_review_benchmark.step1_download_prs import load_golden_comments
from code_review_benchmark.step1_download_prs import parse_repo_name

_PR_URL = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)")


@dataclass(frozen=True)
class Target:
    key: str  # <repo>#<number> of the golden PR, e.g. "keycloak#37429"
    golden_url: str
    original_url: str | None
    repo_name: str  # benchmark repo in the org


def golden_key(url: str) -> str | None:
    """``https://github.com/o/repo/pull/7`` -> ``repo#7``."""
    match = _PR_URL.search(url)
    return f"{match.group(2)}#{match.group(3)}" if match else None


def find_targets(org: str, tool: str, golden_dir: str) -> list[Target]:
    """Benchmark repos for ``tool`` in ``org``, one per golden PR (newest date wins)."""
    golden = load_golden_comments(golden_dir)
    by_key = {golden_key(url): url for url in golden}
    repos = gh(["repo", "list", org, "--limit", "5000", "--json", "name"])

    newest: dict[str, tuple[str, str]] = {}  # key -> (date, repo_name)
    for entry in repos:
        parsed = parse_repo_name(entry["name"])
        if not parsed or parsed["tool"] != tool:
            continue
        key = f"{parsed['original_repo']}#{parsed['pr_number']}"
        if key not in by_key:
            continue
        if key not in newest or parsed["date"] > newest[key][0]:
            newest[key] = (parsed["date"], entry["name"])

    return [
        Target(
            key=key,
            golden_url=by_key[key],
            original_url=golden[by_key[key]].get("original_url"),
            repo_name=repo_name,
        )
        for key, (_date, repo_name) in sorted(newest.items())
    ]


def select_targets(targets: list[Target], selectors: list[str]) -> list[Target]:
    """Targets matching any selector (golden URL, original URL or ``repo#n``); all when empty."""
    if not selectors:
        return targets
    chosen: list[Target] = []
    unknown: list[str] = []
    for raw in selectors:
        sel = raw.strip().rstrip("/").lower()
        match = next(
            (
                t
                for t in targets
                if sel in {t.key.lower(), t.golden_url.lower(), (t.original_url or "").lower()}
            ),
            None,
        )
        if match is None:
            unknown.append(raw.strip())
        elif match not in chosen:
            chosen.append(match)
    if unknown:
        raise ValueError(f"Unknown PR selector(s): {', '.join(unknown)}. Run with --list to see valid ones.")
    return chosen


def gh_send(method: str, endpoint: str, payload: dict) -> dict:
    """Call the GitHub API with a JSON body through the gh CLI."""
    result = subprocess.run(
        ["gh", "api", "-X", method, endpoint, "--input", "-"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{method} {endpoint} failed: {result.stderr.strip()}")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def reopen(org: str, repo: str) -> int:
    """Close the repo's newest PR and open a new one from the same branches. Returns its number."""
    prs = gh(["api", f"/repos/{org}/{repo}/pulls?state=all&per_page=1"])
    if not prs:
        raise RuntimeError(f"{org}/{repo} has no pull request to re-open")
    old = prs[0]
    if old["state"] == "open":
        gh_send("PATCH", f"/repos/{org}/{repo}/pulls/{old['number']}", {"state": "closed"})
    new = gh_send(
        "POST",
        f"/repos/{org}/{repo}/pulls",
        {
            "title": old["title"],
            "body": old.get("body") or "",
            "head": old["head"]["ref"],
            "base": old["base"]["ref"],
        },
    )
    return new["number"]


def has_review_by(org: str, repo: str, pr: int, login: str) -> bool:
    try:
        reviews = gh(["api", f"/repos/{org}/{repo}/pulls/{pr}/reviews"])
    except subprocess.CalledProcessError:
        return False
    return any((r.get("user") or {}).get("login") == login for r in reviews)


def wait_for_reviews(
    org: str, opened: dict[str, int], login: str, timeout_s: float, poll_s: float
) -> tuple[list[str], list[str]]:
    """Wait until ``login`` has reviewed each opened PR. Returns (reviewed, still_pending) repo names."""
    pending = dict(opened)
    reviewed: list[str] = []
    deadline = time.monotonic() + timeout_s
    while True:
        for repo, pr in list(pending.items()):
            if has_review_by(org, repo, pr, login):
                reviewed.append(repo)
                del pending[repo]
        print(f"[{time.strftime('%H:%M:%S')}] reviewed {len(reviewed)}/{len(opened)}", flush=True)
        if not pending or time.monotonic() >= deadline:
            return reviewed, sorted(pending)
        time.sleep(poll_s)


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-run a tool's review on existing benchmark repos")
    parser.add_argument("--org", required=True, help="Org holding the benchmark repos")
    parser.add_argument("--tool", required=True, help="Tool name used in the benchmark repo names")
    parser.add_argument("--login", help="Account the tool reviews as (default: step1's login for the tool)")
    parser.add_argument("--golden", default="golden_comments", help="Golden comments folder")
    parser.add_argument("--prs", default="", help="Comma-separated golden URLs or <repo>#<n>; empty = all")
    parser.add_argument("--timeout-minutes", type=float, default=300, help="How long to wait for reviews")
    parser.add_argument("--poll-seconds", type=float, default=120, help="Seconds between review checks")
    parser.add_argument("--batch-size", type=int, default=10, help="PRs re-opened at a time (0 = all at once)")
    parser.add_argument("--output", help="Write the reviewed repo names here, comma-separated (for step1 --repos)")
    parser.add_argument("--list", action="store_true", help="Print the selectable PRs and exit")
    args = parser.parse_args()

    login = args.login or _TOOL_LOGINS.get(args.tool)
    if not login and not args.list:
        print(f"Error: no known login for tool {args.tool!r}; pass --login", file=sys.stderr)
        sys.exit(2)

    targets = find_targets(args.org, args.tool, args.golden)
    if args.list:
        for t in targets:
            print(f"{t.key:<32} {t.repo_name}")
        return

    try:
        selected = select_targets(targets, [s for s in args.prs.split(",") if s.strip()])
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    if not selected:
        print(f"Error: no benchmark repos for tool {args.tool!r} in {args.org}", file=sys.stderr)
        sys.exit(1)

    size = args.batch_size if args.batch_size > 0 else len(selected)
    print(f"Re-opening {len(selected)} PR(s) in {args.org} for {args.tool}, {size} at a time (reviews by {login})")
    deadline = time.monotonic() + args.timeout_minutes * 60
    reviewed: list[str] = []
    for start in range(0, len(selected), size):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            for t in selected[start:]:
                print(f"::warning::{t.key}: not re-opened, out of time")
            break
        opened: dict[str, int] = {}
        for t in selected[start : start + size]:
            try:
                number = reopen(args.org, t.repo_name)
            except (RuntimeError, subprocess.CalledProcessError) as exc:
                print(f"::warning::{t.key}: could not re-open: {exc}")
                continue
            opened[t.repo_name] = number
            print(f"  {t.key}: https://github.com/{args.org}/{t.repo_name}/pull/{number}")

        done, pending = wait_for_reviews(args.org, opened, login, remaining, args.poll_seconds)
        reviewed += done
        for repo in pending:
            print(f"::warning::No review from {login} on {args.org}/{repo}#{opened[repo]} before the timeout")

    if args.output:
        Path(args.output).write_text(",".join(sorted(reviewed)))
    if not reviewed:
        sys.exit(1)


if __name__ == "__main__":
    main()
