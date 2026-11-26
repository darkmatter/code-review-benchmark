#!/usr/bin/env python3
"""Score one tool's reviews end to end: download, extract, dedupe, judge, report.

Runs step1 (only the given benchmark repos), step2, step2.5 and step3 for the
tool, with the judge model from MARTIAN_MODEL, then prints report_tool's
markdown and appends it to ``--summary`` (e.g. $GITHUB_STEP_SUMMARY).

Usage:
    python -m code_review_benchmark.score_tool --org darkmatter --tool darkmatter \\
        --repos-file reviewed.txt --summary "$GITHUB_STEP_SUMMARY"
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from code_review_benchmark.report_tool import build_report
from code_review_benchmark.step3_judge_comments import BENCHMARK_DATA_FILE
from code_review_benchmark.step3_judge_comments import RESULTS_DIR
from code_review_benchmark.step3_judge_comments import sanitize_model_name


def run_step(module: str, *args: str) -> None:
    print(f"\n=== {module} {' '.join(args)}", flush=True)
    subprocess.run([sys.executable, "-m", f"code_review_benchmark.{module}", *args], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Download, judge and report one tool's reviews")
    parser.add_argument("--org", required=True, help="Org holding the benchmark repos")
    parser.add_argument("--tool", required=True, help="Tool name used in the benchmark repo names")
    parser.add_argument("--repos-file", help="File of comma-separated repo names to fetch (from rerun_reviews)")
    parser.add_argument("--summary", help="Append the markdown report to this file")
    args = parser.parse_args()

    model = os.environ.get("MARTIAN_MODEL")
    if not model:
        print("Error: set MARTIAN_MODEL", file=sys.stderr)
        sys.exit(2)

    step1_args = ["--org", args.org, "--tool", args.tool, "--output", str(BENCHMARK_DATA_FILE)]
    if args.repos_file:
        repos = Path(args.repos_file).read_text().strip()
        if not repos:
            print("Error: no reviewed repos to score", file=sys.stderr)
            sys.exit(1)
        step1_args += ["--repos", repos]

    try:
        run_step("step1_download_prs", *step1_args)
        run_step("step2_extract_comments", "--tool", args.tool)
        run_step("step2_5_dedup_candidates", "--tool", args.tool)
        run_step("step3_judge_comments", "--tool", args.tool)
    except subprocess.CalledProcessError as exc:
        print(f"Error: step failed: {exc}", file=sys.stderr)
        sys.exit(1)

    evaluations = RESULTS_DIR / sanitize_model_name(model) / "evaluations.json"
    report = build_report(json.loads(evaluations.read_text()), args.tool, model)
    print("\n" + report, end="")
    if args.summary:
        with open(args.summary, "a") as f:
            f.write(report)


if __name__ == "__main__":
    main()
