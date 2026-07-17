"""Tests for task-state memory: extraction + store with open/done transitions."""

import pytest

from interpreter.core.memory.tasks import Task, TaskStore, extract_task_events

# --- extraction ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text, kind, needle",
    [
        ("let's add the reranker to scout", "open", "reranker"),
        ("TODO: wire up preference memory", "open", "preference memory"),
        ("we need to fix the socket perms", "open", "fix the socket"),
        ("done with the outcome store", "done", "outcome store"),
        ("the pipeline seam is finished", "done", "pipeline seam"),
    ],
)
def test_extracts_task_events(text, kind, needle):
    events = extract_task_events(text)
    assert events, f"nothing extracted from: {text}"
    assert events[0].kind == kind
    assert needle in events[0].title


def test_no_false_positive_on_short_or_plain():
    assert extract_task_events("let's go") == []  # too few words
    assert extract_task_events("what is the status?") == []
    assert extract_task_events("") == []


def test_done_not_also_registered_as_open():
    events = extract_task_events("we need to finish the docs")
    # "finish the docs" — the done pattern ("finished X") shouldn't fire here;
    # this is an open declaration.
    assert all(e.kind == "open" for e in events)


# --- store --------------------------------------------------------------------


@pytest.fixture
def store():
    s = TaskStore(db_path=None)
    yield s
    s.close()


def test_open_and_get(store):
    store.open_task(Task("add reranker", "add reranker"))
    open_tasks = store.get_open()
    assert len(open_tasks) == 1
    assert open_tasks[0].title == "add reranker"


def test_open_dedups_by_subject(store):
    store.open_task(Task("add reranker", "add reranker"))
    store.open_task(Task("add reranker", "add reranker"))
    assert len(store.get_open()) == 1


def test_complete_by_subject_marks_done(store):
    store.open_task(Task("add the reranker to scout", "add the reranker to scout"))
    completed = store.complete_by_subject("add the reranker to scout")
    assert completed == 1
    assert store.get_open() == []


def test_record_from_text_open_then_done(store):
    store.record_from_text("let's build the outcome store")
    assert len(store.get_open()) == 1
    store.record_from_text("done with the outcome store")
    assert store.get_open() == []


def test_distinct_tasks_coexist(store):
    store.record_from_text("we need to add reranking")
    store.record_from_text("let's write the preference tests")
    assert len(store.get_open()) == 2
