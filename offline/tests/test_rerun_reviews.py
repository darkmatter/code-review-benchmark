"""Tests for rerun_reviews and report_tool."""

from __future__ import annotations

import pytest

from code_review_benchmark import report_tool
from code_review_benchmark import rerun_reviews

TARGETS = [
    rerun_reviews.Target(
        key="keycloak#37429",
        golden_url="https://github.com/keycloak/keycloak/pull/37429",
        original_url=None,
        repo_name="keycloak__keycloak__darkmatter__PR37429__20261126",
    ),
    rerun_reviews.Target(
        key="sentry-greptile#1",
        golden_url="https://github.com/ai-code-review-evaluation/sentry-greptile/pull/1",
        original_url="https://github.com/getsentry/sentry/pull/93824",
        repo_name="sentry__sentry-greptile__darkmatter__PR1__20261126",
    ),
]


def test_golden_key():
    assert rerun_reviews.golden_key("https://github.com/calcom/cal.com/pull/10600") == "cal.com#10600"


def test_select_targets_by_key_and_urls():
    assert rerun_reviews.select_targets(TARGETS, []) == TARGETS
    chosen = rerun_reviews.select_targets(
        TARGETS, [" Keycloak#37429 ", "https://github.com/getsentry/sentry/pull/93824/"]
    )
    assert [t.key for t in chosen] == ["keycloak#37429", "sentry-greptile#1"]


def test_select_targets_rejects_unknown():
    with pytest.raises(ValueError, match="nope#1"):
        rerun_reviews.select_targets(TARGETS, ["nope#1"])


def test_build_report_ranks_against_tools_on_same_prs():
    evaluations = {
        "https://github.com/a/b/pull/1": {
            "darkmatter": {"tp": 1, "fp": 1, "fn": 1, "pr_url": "https://github.com/org/r/pull/2"},
            "other": {"tp": 2, "fp": 0, "fn": 0},
            "partial": {"tp": 0, "fp": 0, "fn": 2},
        },
        "https://github.com/a/b/pull/2": {
            "darkmatter": {"tp": 0, "fp": 0, "fn": 2},
            "other": {"tp": 1, "fp": 1, "fn": 1},
        },
    }
    report = report_tool.build_report(evaluations, "darkmatter", "openai/gpt-5.2")
    assert "2 PR(s)" in report
    assert "Rank 2 of 2" in report  # "partial" lacks PR 2, so it isn't comparable
    assert "partial" not in report
    assert "| 1 | other |" in report
