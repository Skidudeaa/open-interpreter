"""Backend-agnostic chunk pipeline.

A ``ChunkPipeline`` applies an ordered list of ``Middleware`` to the LMC chunk
stream at the ``_respond_and_store`` seam — for *both* the built-in ``respond()``
loop and the out-of-process ``hermes`` backend. Middleware observes chunks, may
call the extracted services (SystemMessageBuilder / MemoryRecorder / CodeGate),
and may inject chunks, but never reorders the producer's chunks. See
``.planning/HERMES_CHUNKPIPELINE_PLAN.md``.
"""

from .base import ChunkPipeline, Middleware
from .memory_middleware import MemoryMiddleware
from .validation_middleware import ValidationMiddleware

__all__ = ["ChunkPipeline", "Middleware", "MemoryMiddleware", "ValidationMiddleware"]
