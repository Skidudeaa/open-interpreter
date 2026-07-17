"""ChunkPipeline + Middleware base.

A middleware is a generator-transformer: ``process(chunks, ctx) -> Iterator[chunk]``.
It observes the incoming chunk stream, may emit EventBus events, call services, or
inject chunks, and yields chunks onward. The default is a pass-through, so an empty
pipeline (or an all-pass-through pipeline) is exactly the identity — the oi golden
chunk sequence is unchanged.

``ctx`` is a per-turn dict carrying at least ``interpreter`` and ``backend``, plus
scratch space middleware may use (e.g. file snapshots).
"""

from __future__ import annotations

from typing import Any, Iterator


class Middleware:
    """Base middleware: pass every chunk through unchanged."""

    def process(self, chunks: Iterator[dict], ctx: dict[str, Any]) -> Iterator[dict]:
        yield from chunks


class ChunkPipeline:
    """Composes middleware over a chunk stream, in order.

    ``process`` wraps the source iterator with each middleware in sequence, so the
    first middleware in the list is closest to the producer (it observes/transforms
    each chunk first; the last is closest to the consumer). An empty pipeline returns
    the source unchanged.
    """

    def __init__(self, middlewares: list[Middleware] | None = None):
        self._middlewares: list[Middleware] = list(middlewares or [])

    def process(self, chunks: Iterator[dict], ctx: dict[str, Any]) -> Iterator[dict]:
        for mw in self._middlewares:
            chunks = mw.process(chunks, ctx)
        return chunks
