# Stack Research: Personal AI Memory System

**Project:** Open Interpreter Memory System (V1)
**Researched:** 2026-01-20
**Focus:** Preference learning, outcome tracking, context-aware prompting, event sourcing

## Executive Summary

The existing Open Interpreter infrastructure (DuckDB/SQLite, EventBus, LiteLLM) provides an excellent foundation for a personal AI memory system. Rather than introducing heavyweight external dependencies, the recommended approach extends your current stack with targeted additions for embeddings and memory management.

**Key insight:** The 2025-2026 ecosystem has converged on a clear pattern: semantic memory via vector embeddings + structured memory via event sourcing + behavioral learning via implicit signals. Mem0 and LangMem represent production-grade implementations of this pattern, but both can be replaced with lighter-weight alternatives that integrate better with your existing DuckDB infrastructure.

---

## Recommended Stack

### Core Memory Layer

| Technology | Version | Purpose | Confidence |
|------------|---------|---------|------------|
| **DuckDB + VSS Extension** | DuckDB ^1.0.0, VSS 1.4.2+ | Vector similarity search, primary storage | HIGH |
| **FastEmbed** | ^0.3.0+ (Dec 2025) | Lightweight CPU-only embeddings | HIGH |
| **eventsourcing** | ^9.5.0 | Event sourcing for state reconstruction | HIGH |

**Rationale:**

1. **DuckDB + VSS Extension** - You already have DuckDB (^1.0.0) in `pyproject.toml`. The VSS extension adds HNSW indexing for vector similarity search directly in DuckDB, eliminating the need for a separate vector database. This is the path of least resistance.

   ```python
   # Example: Adding vector search to existing DuckDB
   import duckdb
   conn = duckdb.connect("memory.db")
   conn.execute("INSTALL vss; LOAD vss;")
   conn.execute("""
       CREATE TABLE memories (
           id VARCHAR PRIMARY KEY,
           content TEXT,
           embedding FLOAT[384],  -- FastEmbed dimension
           memory_type VARCHAR,
           created_at TIMESTAMP
       )
   """)
   conn.execute("CREATE INDEX idx ON memories USING HNSW (embedding)")
   ```

   Source: [DuckDB VSS Extension Docs](https://duckdb.org/docs/stable/core_extensions/vss)

2. **FastEmbed** - Lightweight (uses ONNX Runtime, not PyTorch), fast, and more accurate than OpenAI Ada-002. Works on CPU without GPU dependencies. Default model is `BAAI/bge-small-en-v1.5` (384 dimensions).

   ```python
   from fastembed import TextEmbedding
   model = TextEmbedding()  # ~80MB download, CPU-only
   embeddings = list(model.embed(["user prefers verbose output"]))
   ```

   Source: [FastEmbed PyPI](https://pypi.org/project/fastembed/)

3. **eventsourcing** - Python library for event sourcing with SQLite/DuckDB compatibility. Provides state reconstruction from event logs, essential for "memory as decision input."

   Source: [eventsourcing docs](https://eventsourcing.readthedocs.io/)

### Signal Ingestion

| Technology | Version | Purpose | Confidence |
|------------|---------|---------|------------|
| **GitPython** | ^3.1.46 (existing) | Git activity tracking | HIGH |
| **watchdog** | ^6.0.0 | File system monitoring (optional) | MEDIUM |
| **Existing EventBus** | N/A | Session context events | HIGH |

**Rationale:**

1. **GitPython** - Already in `pyproject.toml` as `git-python = "^1.0.3"`. Can track commits, diffs, and branch activity as implicit preference signals.

   ```python
   from git import Repo
   repo = Repo(".")
   for commit in repo.iter_commits(max_count=10):
       # Extract: files changed, commit message, timestamp
       signal = extract_git_signal(commit)
   ```

2. **Existing EventBus** - Your `ui_events.py` already defines `EventType.MEMORY_RECORD`, `EventType.FILE_CHANGE`, and `EventType.GIT_COMMIT`. These can be subscribed to for capturing signals without new dependencies.

3. **watchdog** (optional) - Only needed if you want real-time file system monitoring beyond git activity. Lower priority for V1.

### Preference Learning

| Technology | Version | Purpose | Confidence |
|------------|---------|---------|------------|
| **Implicit signals** | Custom | Behavioral patterns from actions | HIGH |
| **Explicit tagging** | Custom | User-stated preferences | HIGH |
| **TRL DPOTrainer** | ^0.26+ | Full RLHF (post-V1 only) | LOW |

**Rationale:**

For V1, avoid full RLHF/DPO complexity. Instead, implement preference learning through:

1. **Implicit signals** - Track patterns in user behavior:
   - Which code suggestions are accepted vs. rejected
   - Which outputs lead to follow-up questions (confusion signal)
   - Time-of-day patterns, file type preferences
   - Conversation length before task completion

2. **Explicit tagging** - Parse user statements:
   - "I prefer verbose output"
   - "Don't use semicolons"
   - "Always use type hints"

3. **Simple preference model** - Store as key-value pairs with confidence scores:
   ```python
   preferences = {
       "code_style": {"verbose_output": 0.8, "type_hints": 0.9},
       "interaction": {"brief_explanations": 0.6}
   }
   ```

**Post-V1:** TRL's DPOTrainer enables Direct Preference Optimization for fine-tuning, but this requires significant data collection first. Park this for V2.

Source: [TRL Documentation](https://huggingface.co/docs/trl/en/index)

### Pre-prompting Integration

| Technology | Version | Purpose | Confidence |
|------------|---------|---------|------------|
| **Jinja2** | ^3.0+ | Template-based prompt construction | HIGH |
| **LiteLLM** | ^1.41.26 (existing) | Unified LLM interface | HIGH |

**Rationale:**

1. **Jinja2** - Standard template engine for dynamic prompt construction. Memory context injected as template variables:

   ```jinja2
   {% if preferences %}
   USER PREFERENCES:
   {% for pref in preferences %}
   - {{ pref.key }}: {{ pref.value }} (confidence: {{ pref.confidence }})
   {% endfor %}
   {% endif %}

   {% if relevant_outcomes %}
   RELEVANT PAST OUTCOMES:
   {% for outcome in relevant_outcomes %}
   - {{ outcome.summary }} ({{ outcome.success_rate }}% success)
   {% endfor %}
   {% endif %}

   CURRENT CONTEXT:
   {{ user_message }}
   ```

2. **LiteLLM** - Already your LLM interface. Pre-prompting integrates at the `system_message` level in `respond.py`.

---

## Integration with Existing Stack

### DuckDB Schema Extension

Your existing `SemanticEditGraph` uses DuckDB with tables for `edits`, `symbols`, and `conversations`. Extend with memory tables:

```sql
-- Memory table with vector embeddings
CREATE TABLE memories (
    id VARCHAR PRIMARY KEY,
    content TEXT NOT NULL,
    embedding FLOAT[384],  -- VSS-compatible fixed array
    memory_type VARCHAR NOT NULL,  -- 'preference', 'outcome', 'context', 'task_state'
    source VARCHAR,  -- 'explicit', 'git', 'session', 'environment'
    confidence DOUBLE DEFAULT 1.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP,
    metadata JSON
);

-- Outcomes table for tracking what worked
CREATE TABLE outcomes (
    id VARCHAR PRIMARY KEY,
    memory_id VARCHAR REFERENCES memories(id),
    task_description TEXT,
    approach TEXT,
    success BOOLEAN,
    user_satisfaction DOUBLE,  -- inferred from follow-ups
    context_hash VARCHAR,  -- for grouping similar contexts
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Preference strength over time (for decay/reinforcement)
CREATE TABLE preference_signals (
    id INTEGER PRIMARY KEY,
    memory_id VARCHAR REFERENCES memories(id),
    signal_type VARCHAR,  -- 'reinforce', 'contradict', 'decay'
    signal_strength DOUBLE,
    source_event VARCHAR,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### EventBus Integration

Your EventBus already supports the needed event types. Add memory-specific handlers:

```python
from interpreter.terminal_interface.components.ui_events import (
    EventBus, EventType, UIEvent
)

def setup_memory_signals(bus: EventBus, memory_system):
    # Capture code execution outcomes
    bus.subscribe(EventType.CONSOLE_OUTPUT, lambda e:
        memory_system.record_outcome(e.data, success=True))
    bus.subscribe(EventType.CONSOLE_ERROR, lambda e:
        memory_system.record_outcome(e.data, success=False))

    # Capture git activity
    bus.subscribe(EventType.GIT_COMMIT, lambda e:
        memory_system.extract_git_signal(e.data))

    # Capture file changes for context
    bus.subscribe(EventType.FILE_CHANGE, lambda e:
        memory_system.update_context(e.data))
```

### LiteLLM Integration Point

In `respond.py`, inject memory context before LLM call:

```python
# In respond() function, before LLM call
if interpreter.enable_memory:
    memory_context = interpreter.memory_system.get_relevant_context(
        user_message=messages[-1]["content"],
        current_files=get_active_files(),
        time_of_day=datetime.now().hour
    )
    system_message = inject_memory_context(system_message, memory_context)
```

---

## Alternatives Considered

### Vector Databases

| Option | Why Not |
|--------|---------|
| **ChromaDB** | Adds SQLite3 dependency conflicts, separate process, more moving parts |
| **LanceDB** | Excellent, but you already have DuckDB. Adding Lance doubles storage complexity |
| **Pinecone/Weaviate** | External service dependency, overkill for personal AI memory |
| **Mem0** | Full memory framework, but requires OpenAI by default and adds significant dependencies |

**Decision:** DuckDB + VSS extension provides vector search without new dependencies.

### Embedding Models

| Option | Why Not |
|--------|---------|
| **sentence-transformers** | Requires PyTorch (~2GB), already optional in pyproject.toml for [os] extra |
| **OpenAI embeddings** | External API call, cost, latency, privacy concerns |
| **all-MiniLM-L6-v2** | Outdated (2019 architecture), only 512 token context, 56% accuracy on modern benchmarks |

**Decision:** FastEmbed uses ONNX Runtime (~80MB), no PyTorch, better accuracy than Ada-002. If GPU needed later, add `fastembed-gpu`.

Source: [Embedding Model Benchmarks](https://supermemory.ai/blog/best-open-source-embedding-models-benchmarked-and-ranked/)

### Memory Frameworks

| Option | Why Not |
|--------|---------|
| **Mem0** | Heavy dependency (requires gpt-4.1-nano by default), opinionated about storage |
| **LangMem** | Tied to LangGraph ecosystem, adds LangChain dependency chain |
| **MemGPT/Letta** | Full agent framework, overkill for pre-prompting use case |

**Decision:** Build targeted memory system using DuckDB + eventsourcing. Simpler, fewer dependencies, integrates with existing infrastructure.

### Preference Learning

| Option | Why Not |
|--------|---------|
| **Full RLHF with TRL** | Requires significant preference data collection, training infrastructure |
| **DPO fine-tuning** | Same issue - need data first, defer to post-V1 |

**Decision:** Start with implicit signal extraction + explicit preference tagging. Collect data for potential future DPO training.

---

## Confidence Levels

| Component | Confidence | Rationale |
|-----------|------------|-----------|
| DuckDB + VSS | HIGH | Official extension, documented, integrates with existing stack |
| FastEmbed | HIGH | Actively maintained (Dec 2025 release), PyPI verified, Qdrant backing |
| eventsourcing | HIGH | Mature library (v9.5.0), well-documented, Python standard patterns |
| GitPython | HIGH | Already in dependencies, 18M+ weekly downloads, actively maintained |
| Jinja2 | HIGH | Standard Python templating, battle-tested |
| watchdog | MEDIUM | Optional, may not be needed for V1 |
| TRL/DPO | LOW | Post-V1 concern, requires significant data collection first |

---

## Installation Commands

```bash
# Core memory dependencies
poetry add fastembed eventsourcing jinja2

# DuckDB VSS extension (installed at runtime)
# No pip install needed - loaded via: conn.execute("INSTALL vss; LOAD vss;")

# Optional: file system monitoring
poetry add watchdog
```

**Note:** DuckDB is already in `pyproject.toml` under `[memory]` extra. Ensure it's installed:
```bash
poetry install -E memory
```

---

## New poetry extras suggestion

```toml
# In pyproject.toml
[tool.poetry.extras]
memory = ["duckdb", "fastembed", "eventsourcing", "jinja2"]
```

---

## Sources

### HIGH Confidence (Official Documentation)
- [DuckDB VSS Extension](https://duckdb.org/docs/stable/core_extensions/vss) - Official docs
- [FastEmbed PyPI](https://pypi.org/project/fastembed/) - Package info, Dec 2025 release
- [eventsourcing Documentation](https://eventsourcing.readthedocs.io/) - Official docs
- [GitPython Documentation](https://gitpython.readthedocs.io/en/stable/) - v3.1.46, Jan 2026

### MEDIUM Confidence (Verified with Multiple Sources)
- [Mem0 Research Paper](https://arxiv.org/pdf/2504.19413) - Architecture patterns
- [LangMem Conceptual Guide](https://langchain-ai.github.io/langmem/concepts/conceptual_guide/) - Memory types
- [TRL Documentation](https://huggingface.co/docs/trl/en/index) - DPO trainer details

### LOW Confidence (WebSearch Only - Verify Before Use)
- Embedding model benchmarks may vary by task
- DuckDB VSS persistence is still "experimental" per official docs
- Event sourcing patterns for AI memory are emerging (limited production case studies)
