"""Unit tests for the ChunkPipeline + Middleware base (Phase 0)."""

from interpreter.core.pipeline import ChunkPipeline, Middleware


def _chunks():
    return [
        {"role": "assistant", "type": "message", "content": "a"},
        {"role": "computer", "type": "console", "format": "output", "content": "b"},
    ]


def test_empty_pipeline_is_identity():
    src = _chunks()
    out = list(ChunkPipeline().process(iter(src), {}))
    assert out == src


def test_base_middleware_passes_through_in_order():
    src = _chunks()
    out = list(ChunkPipeline([Middleware()]).process(iter(src), {}))
    assert out == src


def test_middleware_can_inject_without_reordering():
    class Appender(Middleware):
        def process(self, chunks, ctx):
            for c in chunks:
                yield c
            yield {
                "role": "computer",
                "type": "console",
                "format": "output",
                "content": "z",
            }

    src = _chunks()
    out = list(ChunkPipeline([Appender()]).process(iter(src), {}))
    assert out[: len(src)] == src
    assert out[-1]["content"] == "z"


def test_first_middleware_sees_chunks_before_second():
    """Data-flow order: producer -> first -> second -> consumer. The first
    middleware in the list transforms each chunk before the second does."""

    class Tag(Middleware):
        def __init__(self, name):
            self.name = name

        def process(self, chunks, ctx):
            for c in chunks:
                yield {**c, "content": c["content"] + self.name}

    out = list(ChunkPipeline([Tag("A"), Tag("B")]).process(iter(_chunks()), {}))
    # A appends before B, so content ends with "AB".
    assert out[0]["content"] == "aAB"


def test_ctx_is_passed_through():
    seen = {}

    class Capture(Middleware):
        def process(self, chunks, ctx):
            seen.update(ctx)
            yield from chunks

    list(ChunkPipeline([Capture()]).process(iter(_chunks()), {"backend": "hermes"}))
    assert seen.get("backend") == "hermes"
