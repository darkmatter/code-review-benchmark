#!/usr/bin/env python3
"""Markdown report for one tool: its score, how other tools did on the same PRs, and each PR.

Reads ``results/<model>/evaluations.json`` (step3's output, model from MARTIAN_MODEL)
and prints markdown, e.g. for a GitHub Actions job summary.

Usage:
    MARTIAN_MODEL=openai/gpt-5.2 python -m code_review_benchmark.report_tool --tool darkmatter
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from code_review_benchmark.step3_judge_comments import RESULTS_DIR
from code_review_benchmark.step3_judge_comments import sanitize_model_name


def score(results: list[dict]) -> dict:
    """Micro-averaged precision, recall and F1, as step3's leaderboard computes them."""
    tp = sum(r.get("tp", 0) for r in results)
    fp = sum(r.get("fp", 0) for r in results)
    fn = sum(r.get("fn", 0) for r in results)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1, "prs": len(results)}


def build_report(evaluations: dict, tool: str, model: str) -> str:
    """Markdown for ``tool`` and every other tool scored on the same PRs."""
    prs = {
        url: tools[tool]
        for url, tools in evaluations.items()
        if tool in tools and not tools[tool].get("skipped")
    }
    if not prs:
        return f"No evaluations for `{tool}` with judge `{model}`.\n"

    others: dict[str, list[dict]] = {}
    for url in prs:
        for name, result in evaluations[url].items():
            if name != tool and not result.get("skipped"):
                others.setdefault(name, []).append(result)

    # Only tools scored on every one of these PRs are comparable.
    board = {name: score(results) for name, results in others.items() if len(results) == len(prs)}
    board[tool] = score(list(prs.values()))
    ranked = sorted(board.items(), key=lambda kv: kv[1]["f1"], reverse=True)
    rank = next(i for i, (name, _) in enumerate(ranked, 1) if name == tool)
    own = board[tool]

    lines = [
        f"## Code review benchmark: `{tool}`",
        "",
        f"Judge `{model}`, {own['prs']} PR(s). "
        f"Precision **{own['precision']:.1%}**, recall **{own['recall']:.1%}**, F1 **{own['f1']:.1%}** "
        f"({own['tp']} found, {own['fp']} extra, {own['fn']} missed). "
        f"Rank {rank} of {len(ranked)} tools on these PRs.",
        "",
        "| # | Tool | Precision | Recall | F1 |",
        "|---|---|---|---|---|",
    ]
    for i, (name, s) in enumerate(ranked, 1):
        label = f"**{name}**" if name == tool else name
        lines.append(f"| {i} | {label} | {s['precision']:.1%} | {s['recall']:.1%} | {s['f1']:.1%} |")

    lines += ["", "### Per PR", "", "| PR | Review | Found | Extra | Missed |", "|---|---|---|---|---|"]
    for url, r in sorted(prs.items()):
        review = f"[review]({r['pr_url']})" if r.get("pr_url") else ""
        lines.append(f"| {url} | {review} | {r.get('tp', 0)} | {r.get('fp', 0)} | {r.get('fn', 0)} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Markdown report for one tool's benchmark results")
    parser.add_argument("--tool", required=True)
    parser.add_argument("--model", default=os.environ.get("MARTIAN_MODEL"), help="Judge model (default: MARTIAN_MODEL)")
    args = parser.parse_args()
    if not args.model:
        print("Error: set MARTIAN_MODEL or pass --model", file=sys.stderr)
        sys.exit(2)

    path = RESULTS_DIR / sanitize_model_name(args.model) / "evaluations.json"
    if not path.exists():
        print(f"Error: {path} not found", file=sys.stderr)
        sys.exit(1)
    print(build_report(json.loads(path.read_text()), args.tool, args.model), end="")


if __name__ == "__main__":
    main()
