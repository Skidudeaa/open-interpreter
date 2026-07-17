"""Tests for preference memory: rule-based extraction + store with contradiction."""

import pytest

from interpreter.core.memory.preferences import (
    Preference,
    PreferenceStore,
    extract_preferences,
)

# --- extraction ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text, polarity, needle",
    [
        ("I prefer tabs over spaces", "prefer", "tabs"),
        ("please always run the tests before committing", "prefer", "run the tests"),
        ("never force push to main", "avoid", "force push"),
        ("I don't like verbose logging", "avoid", "verbose logging"),
        ("avoid global variables", "avoid", "global variables"),
        ("I'd rather use poetry", "prefer", "poetry"),
    ],
)
def test_extracts_explicit_declarations(text, polarity, needle):
    prefs = extract_preferences(text)
    assert prefs, f"no preference extracted from: {text}"
    assert prefs[0].polarity == polarity
    assert needle in prefs[0].text


def test_use_x_instead_of_y_yields_both_polarities():
    prefs = extract_preferences("use ruff instead of flake8")
    pols = {p.polarity: p.text for p in prefs}
    assert "ruff" in pols["prefer"]
    assert "flake8" in pols["avoid"]


def test_no_false_positive_on_plain_text():
    assert extract_preferences("what is the status of the project?") == []
    assert extract_preferences("") == []


def test_multiple_declarations_in_one_message():
    prefs = extract_preferences("I prefer black. Also never use tabs.")
    subjects = {p.polarity for p in prefs}
    assert subjects == {"prefer", "avoid"}


# --- store --------------------------------------------------------------------


@pytest.fixture
def store():
    s = PreferenceStore(db_path=None)
    yield s
    s.close()


def test_record_and_get_active(store):
    store.record(Preference("prefer tabs", "prefer", "tabs"))
    active = store.get_active()
    assert len(active) == 1
    assert active[0].text == "prefer tabs"


def test_contradiction_deactivates_old(store):
    store.record(Preference("prefer tabs", "prefer", "tabs"), now="2026-01-01")
    store.record(Preference("avoid tabs", "avoid", "tabs"), now="2026-02-01")
    active = store.get_active()
    assert len(active) == 1
    assert active[0].polarity == "avoid"  # newer explicit statement wins


def test_reinforcement_supersedes_duplicate(store):
    store.record(Preference("prefer tabs", "prefer", "tabs"), now="2026-01-01")
    store.record(Preference("prefer tabs", "prefer", "tabs"), now="2026-02-01")
    active = store.get_active()
    assert len(active) == 1  # not duplicated


def test_distinct_subjects_coexist(store):
    store.record(Preference("prefer tabs", "prefer", "tabs"))
    store.record(Preference("avoid globals", "avoid", "globals"))
    assert len(store.get_active()) == 2


def test_record_from_text_end_to_end(store):
    stored = store.record_from_text("I prefer poetry and never use pip directly")
    assert len(stored) == 2
    active = store.get_active()
    assert {p.polarity for p in active} == {"prefer", "avoid"}
