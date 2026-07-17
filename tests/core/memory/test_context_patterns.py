"""Tests for context-pattern memory: activity/time classification + evidence gate."""

from datetime import datetime

import pytest

from interpreter.core.memory.context_patterns import (
    ContextPatternStore,
    classify_activity,
    time_bucket,
)


@pytest.mark.parametrize(
    "text, activity",
    [
        ("fix the failing test traceback", "debug"),
        ("run pytest with coverage", "test"),
        ("refactor and simplify the loop", "refactor"),
        ("update the readme docstring", "docs"),
        ("investigate how does the reranker work", "research"),
        ("implement the new feature", "build"),
        ("hello there", "other"),
    ],
)
def test_classify_activity(text, activity):
    assert classify_activity(text) == activity


@pytest.mark.parametrize(
    "hour, bucket",
    [
        (2, "late night"),
        (7, "early morning"),
        (10, "morning"),
        (14, "afternoon"),
        (19, "evening"),
        (23, "night"),
    ],
)
def test_time_bucket(hour, bucket):
    assert time_bucket(datetime(2026, 7, 17, hour, 0)) == bucket


@pytest.fixture
def store():
    s = ContextPatternStore(db_path=None)
    yield s
    s.close()


def test_no_pattern_below_min_count(store):
    store.record("late night", "debug")
    store.record("late night", "debug")
    assert store.dominant("late night", min_count=3) is None  # only 2 obs


def test_dominant_when_enough_evidence(store):
    for _ in range(3):
        store.record("late night", "debug")
    dom = store.dominant("late night", min_count=3)
    assert dom is not None
    assert dom["activity"] == "debug"
    assert dom["count"] == 3


def test_no_dominant_without_majority(store):
    store.record("morning", "debug")
    store.record("morning", "debug")
    store.record("morning", "debug")
    store.record("morning", "test")
    store.record("morning", "test")
    store.record("morning", "build")
    # debug=3 but share = 3/6 = 0.5; require >0.5 majority
    assert store.dominant("morning", min_count=3, min_share=0.51) is None


def test_other_activity_not_recorded(store):
    store.record("night", "other")
    assert store.dominant("night", min_count=1) is None
