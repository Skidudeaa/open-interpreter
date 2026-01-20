# Project Research Summary

**Project:** Personal AI Memory System for Open Interpreter
**Domain:** AI assistant memory / personalization layer
**Researched:** 2026-01-20
**Confidence:** MEDIUM-HIGH

## Executive Summary

Personal AI memory systems have matured significantly in 2024-2026, with memory now considered table stakes for serious AI assistants. The industry has converged on a clear pattern: semantic memory via vector embeddings + structured memory via event sourcing + behavioral learning via implicit signals. For Open Interpreter specifically, the existing infrastructure (DuckDB, EventBus, SemanticEditGraph) provides an excellent foundation that should be extended rather than replaced.

The recommended approach leverages your existing stack: **DuckDB + VSS extension** for vector similarity search, **FastEmbed** for CPU-only embeddings (no PyTorch dependency), and **eventsourcing** for state reconstruction. The integration point is the `respond()` loop's system message construction phase. Memory is injected via pre-prompting as the initial influence mechanism, with more sophisticated patterns (suggestions, constraints, auto-decisions) deferred to future iterations.

The critical risks are: (1) retrieval returning plausible-but-wrong context, (2) memory poisoning via prompt injection, and (3) unbounded memory growth. These are mitigated by hybrid retrieval with metadata filtering, input/output sanitization, and decay mechanisms built into the data model from day one. The project's decisions around "explicit > implicit" preferences and "ask when causal inference uncertain" directly address several major pitfalls.

---

## Key Findings

### Recommended Stack

The stack recommendation minimizes new dependencies by extending existing infrastructure. DuckDB (already in pyproject.toml) gains vector search via its VSS extension, eliminating the need for a separate vector database like ChromaDB or Pinecone.

**Core technologies:**
- **DuckDB + VSS Extension**: Vector similarity search and primary storage -- already have DuckDB, just add HNSW indexing
- **FastEmbed**: Lightweight CPU-only embeddings (~80MB, ONNX Runtime) -- avoids PyTorch's 2GB footprint
- **eventsourcing**: Event sourcing for state reconstruction -- mature Python library, fits event-driven architecture
- **Jinja2**: Template-based prompt construction -- standard, no new patterns to learn
- **Existing EventBus**: Signal capture via existing event types -- zero new infrastructure

**Key insight:** Mem0 and LangMem represent production-grade implementations but add significant dependencies. Building targeted memory with DuckDB + eventsourcing integrates better with the existing codebase.

### Expected Features

Memory is now table stakes for AI assistants. All major platforms (ChatGPT, Claude, Gemini, Copilot) deployed memory in 2024-2025.

**Must have (table stakes):**
- Basic persistence across sessions -- users expect memory to survive restart
- User visibility ("what do you remember") -- GDPR compliance and trust building
- User control (edit/delete) -- required for privacy regulations
- Basic preference storage -- core value proposition
- Memory influence via pre-prompting -- the mechanism that makes memory useful
- Recency-aware retrieval -- without this, stale context pollutes responses

**Should have (competitive):**
- Outcome tracking ("last time Z happened") -- differentiator, builds on existing SemanticEditGraph patterns
- Conflict resolution / preference evolution -- essential for long-term use
- Active forgetting / memory hygiene -- prevents bloat, keeps memory relevant

**Defer (v2+):**
- Hierarchical task context (project > task > subtask) -- high complexity, active research area
- Context pattern recognition ("debug mode after midnight") -- high complexity, requires temporal reasoning
- Procedural memory (how-to knowledge) -- depends on outcome tracking foundation
- Suggestion injection, constraint application, auto-decisions -- per project scope, defer to future influence mechanisms

### Architecture Approach

The architecture follows a four-layer pattern: signal capture, memory store, retrieval engine, and context injection. The industry has converged on the "memory loop" pattern: inject relevant memories, LLM reasons with augmented context, distill memory candidates from interaction, consolidate into storage.

**Major components:**
1. **Signal Capture Layer** -- EventBus subscribers intercept CODE_START, MESSAGE_CHUNK, FILE_CHANGE, CONFIRMATION_RESPONSE events
2. **Memory Store Layer** -- Unified API over preferences (K-V), outcomes (vector), and tasks (structured)
3. **Retrieval Engine** -- Proactive recall: pre-processor runs similarity search before LLM invocation
4. **Context Injection** -- Formatter injects MemoryBundle into system message via `_build_system_message()` in respond.py

**Integration point:** The primary injection point is `_build_system_message()` in respond.py (lines 107-174), where memory context joins base system message + custom_instructions.

### Critical Pitfalls

1. **Retrieval returning plausible-but-wrong context** -- Semantic similarity returns lexically similar but contextually irrelevant memories. Mitigate with hybrid retrieval (embedding + metadata filtering) and re-ranking step.

2. **Memory poisoning via indirect prompt injection** -- Malicious content stored in memory persistently influences future behavior. Mitigate with input/output sanitization, provenance tracking, and user-visible memory dashboard.

3. **Unbounded memory growth without decay** -- Old, outdated information never expires, degrading retrieval quality. Mitigate with temporal decay, recency-weighted scoring, and contradiction detection.

4. **Causal misattribution of outcomes** -- System incorrectly attributes success/failure to wrong cause. Mitigate with confidence thresholds and "ask when uncertain" approach.

5. **Context window bloat from over-retrieval** -- Too many memories stuffed into prompt, diluting relevance. Mitigate with strict relevance thresholds, start conservative, quality over quantity.

---

## Implications for Roadmap

Based on research, suggested phase structure follows the architecture's natural dependency order:

### Phase 1: Memory Store Foundation
**Rationale:** Foundation for everything else; can test in isolation; must design decay and provenance tracking into data model from start
**Delivers:** MemoryStore base class, preference storage (key-value), basic CRUD operations, schema with decay and versioning built in
**Addresses:** Basic preference storage, persistence (table stakes)
**Avoids:** #11 Right to forget violations, #7 Embedding staleness -- design provenance and versioning from start

### Phase 2: Signal Capture
**Rationale:** Needs storage (Phase 1) but not retrieval; captures the raw material for learning
**Delivers:** SignalListener subscribing to EventBus, signal filtering logic, basic memory extraction (heuristic, no LLM)
**Uses:** EventBus (existing), MemoryStore (Phase 1)
**Avoids:** #4 Causal misattribution -- capture raw signals before inference

### Phase 3: Context Injection (Pre-prompting)
**Rationale:** This is where value is delivered; even with minimal memories, injection provides benefit
**Delivers:** ContextBuilder formatting memories for prompts, integration with `_build_system_message()`, cache invalidation
**Addresses:** Memory influence via pre-prompting (table stakes)
**Avoids:** #5 Context bloat -- implement strict relevance thresholds, conservative retrieval limits

### Phase 4: User Visibility and Control
**Rationale:** Trust and compliance features; required for real-world use
**Delivers:** Memory inspection commands, edit/delete capabilities, disable/pause memory
**Addresses:** User visibility, user control, disable/pause (table stakes)
**Avoids:** #14 Memory visibility deficit, #6 Creepy AI problem -- transparency builds trust

### Phase 5: Retrieval Engine
**Rationale:** Proactive recall improves quality but basic injection (Phase 3) works without sophisticated retrieval
**Delivers:** Similarity search for outcomes, relevance scoring, recency weighting
**Uses:** DuckDB VSS extension, FastEmbed
**Addresses:** Recency-aware retrieval (table stakes)
**Avoids:** #1 Plausible-but-wrong context -- hybrid retrieval with metadata filtering

### Phase 6: Outcome Memory
**Rationale:** Differentiator that builds on solid foundation; requires working memory loop first
**Delivers:** Outcome tracking with success/failure, pattern extraction from code results
**Addresses:** Outcome tracking (differentiator)
**Avoids:** #4 Causal misattribution -- confidence thresholds, ask when uncertain

### Phase 7: Preference Evolution
**Rationale:** Essential for long-term use; requires preference storage and outcome data
**Delivers:** Contradiction detection, preference decay, explicit > implicit priority, preference versioning
**Addresses:** Conflict resolution, active forgetting (should have)
**Avoids:** #3 Unbounded growth, #8 Contradiction without resolution

### Phase Ordering Rationale

- **Dependencies are linear:** Storage before capture before injection before retrieval before learning
- **Value delivery is early:** Phase 3 (injection) provides user value even with minimal functionality
- **Trust features grouped:** Phase 4 bundles all user-facing control features together
- **Complexity deferred:** Advanced features (hierarchical context, pattern recognition, procedural memory) are post-v1
- **Pitfall avoidance baked in:** Each phase explicitly addresses relevant pitfalls from research

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 5 (Retrieval Engine):** Embedding model selection, relevance threshold tuning, benchmark with projected data sizes
- **Phase 6 (Outcome Memory):** Causal inference design, confidence threshold calibration
- **Phase 7 (Preference Evolution):** Contradiction detection algorithms, decay function tuning

Phases with standard patterns (skip research-phase):
- **Phase 1 (Memory Store):** Well-documented DuckDB schemas, eventsourcing patterns
- **Phase 3 (Context Injection):** Standard pre-prompting, Jinja2 templates
- **Phase 4 (User Visibility):** CRUD operations, CLI patterns

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Official docs (DuckDB VSS, FastEmbed), existing codebase verified |
| Features | MEDIUM-HIGH | Industry consensus from major platforms, comprehensive survey |
| Architecture | HIGH | Multiple authoritative sources agree (OpenAI, Mem0, MemGPT) |
| Pitfalls | MEDIUM-HIGH | Recent security research (2024-2025), well-documented RAG failures |

**Overall confidence:** MEDIUM-HIGH

### Gaps to Address

- **Embedding model benchmarking:** FastEmbed recommended but should validate for coding domain specifically during Phase 5
- **Memory poisoning mitigation specifics:** Research identifies risk but sanitization implementation details need Phase 1 design work
- **Relevance threshold tuning:** "Start conservative" is guidance but actual thresholds need empirical testing in Phase 3/5
- **Privacy/security attack surface:** Unit42 research documents attacks but defensive patterns are still emerging
- **Performance at scale:** Benchmarks show 100K+ memories feasible but should validate with projected usage during Phase 1

---

## Sources

### Primary (HIGH confidence)
- [DuckDB VSS Extension](https://duckdb.org/docs/stable/core_extensions/vss) -- vector search capabilities, HNSW indexing
- [FastEmbed PyPI](https://pypi.org/project/fastembed/) -- Dec 2025 release, ONNX runtime details
- [eventsourcing docs](https://eventsourcing.readthedocs.io/) -- Python event sourcing patterns
- [IBM AI Agent Memory](https://www.ibm.com/think/topics/ai-agent-memory) -- memory type taxonomy
- [Mem0 Research](https://mem0.ai/research) -- memory layer architecture, LOCOMO benchmark

### Secondary (MEDIUM confidence)
- [MemGPT/Letta](https://docs.letta.com/concepts/memgpt/) -- hierarchical memory tiers, OS paradigm
- [OpenAI Agents SDK Cookbook](https://cookbook.openai.com/examples/agents_sdk/context_personalization) -- memory loop pattern
- [Design Patterns for Long-Term Memory](https://serokell.io/blog/design-patterns-for-long-term-memory-in-llm-powered-architectures) -- four leading architectures
- [Palo Alto Unit42](https://unit42.paloaltonetworks.com/indirect-prompt-injection-poisons-ai-longterm-memory/) -- memory poisoning attacks

### Tertiary (LOW confidence, needs validation)
- Embedding model benchmarks vary by task -- need coding-domain validation
- DuckDB VSS persistence still "experimental" per official docs
- Event sourcing patterns for AI memory are emerging (limited production case studies)

---
*Research completed: 2026-01-20*
*Ready for roadmap: yes*
