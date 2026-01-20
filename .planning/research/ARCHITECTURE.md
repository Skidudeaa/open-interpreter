# Architecture Research: Personal AI Memory System

**Domain:** Personal AI assistant memory for LLM-based coding tools
**Researched:** 2026-01-20
**Overall confidence:** HIGH (multiple authoritative sources, verified against existing codebase)

## Executive Summary

Personal AI memory systems for LLM assistants follow a layered architecture with four core components: signal capture, memory store, retrieval engine, and context injection. The industry has converged on several key patterns: the memory loop (inject -> reason -> distill -> consolidate), hierarchical memory tiers (working/short-term, long-term, archival), and hybrid storage (vector + graph + structured).

For Open Interpreter, the existing SemanticEditGraph provides a solid foundation but needs extension for the four memory types (preferences, outcomes, tasks, patterns). The integration point is the respond() loop, specifically the system message construction phase where memory should be pre-injected.

---

## Component Overview

### 1. Signal Capture Layer

**Purpose:** Intercept relevant events from the system and extract memory-worthy information.

**Components:**
- **Event Listeners**: Subscribe to EventBus events (CODE_START, MESSAGE_CHUNK, FILE_CHANGE, etc.)
- **Signal Filters**: Determine which events contain memory-worthy content
- **Memory Extractors**: Transform raw events into structured memory candidates

**Data Flow:**
```
User Input  ──┐
              │
LLM Response ─┼──> Signal Router ──> Memory Extractor ──> Candidate Queue
              │
Code Output ──┘
```

**For Open Interpreter Integration:**
The existing EventBus already emits relevant events. Add subscribers for:
- `MESSAGE_CHUNK`: User preferences, task context
- `CONSOLE_OUTPUT`: Outcome feedback (errors, success patterns)
- `FILE_CHANGE`: Edit outcomes, pattern extraction
- `CONFIRMATION_RESPONSE`: User approval patterns

### 2. Memory Store Layer

**Purpose:** Persist memories with indexing for efficient retrieval.

**Industry Patterns (from Mem0, MemGPT research):**

| Storage Type | What It Stores | Retrieval Method |
|--------------|----------------|------------------|
| Vector Store | Semantic embeddings | Similarity search |
| Graph Store | Entity relationships | Traversal queries |
| Key-Value Store | Structured facts | Direct lookup |
| FIFO Queue | Recent context | Sequential access |

**Recommended Architecture for Open Interpreter:**

```
┌─────────────────────────────────────────────────────────────┐
│                      MemoryStore                            │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  Preferences │  │   Outcomes   │  │  Tasks/Patterns  │  │
│  │   (K-V)      │  │   (Vector)   │  │   (Graph)        │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
│                           │                                 │
│                    ┌──────┴──────┐                         │
│                    │ Unified API │                         │
│                    └─────────────┘                         │
└─────────────────────────────────────────────────────────────┘
```

**Memory Types Mapping:**

| Memory Type | Storage | Schema | Example |
|-------------|---------|--------|---------|
| Preferences | Key-Value | `{key, value, confidence, updated_at}` | "prefers pytest over unittest" |
| Outcomes | Vector + Structured | `{context_embedding, success, pattern, outcome}` | "this fix approach worked for TypeError" |
| Tasks | Structured | `{task_id, description, status, history}` | "implement auth middleware" |
| Patterns | Graph | `{pattern_type, trigger, response, edges}` | "when X happens, user usually wants Y" |

### 3. Retrieval Engine Layer

**Purpose:** Find relevant memories for the current context.

**Industry Patterns:**

1. **Proactive Recall (Mem0 pattern)**
   - Pre-processor runs similarity search before LLM invocation
   - Injects top-K relevant memories into context
   - Fast path: skip retrieval for simple queries

2. **On-Demand Recall (MemGPT pattern)**
   - LLM requests memories via tool calls
   - More flexible but consumes cognitive bandwidth
   - Better for complex multi-step reasoning

3. **Hybrid Recall (OpenAI pattern)**
   - Combine proactive injection with on-demand tools
   - Structured preferences injected automatically
   - Narrative memories available via search

**Recommended for Open Interpreter: Proactive + Selective**

```python
# Pseudocode for retrieval
def retrieve_for_context(user_message: str, session_context: dict) -> MemoryBundle:
    """Retrieve relevant memories before LLM call."""

    # 1. Always inject: user preferences (low cost, high value)
    preferences = memory_store.get_preferences(user_id, limit=10)

    # 2. Similarity search for outcomes (if task looks familiar)
    if is_code_task(user_message):
        outcomes = memory_store.search_outcomes(user_message, limit=5)

    # 3. Pattern matching for tasks (if continuing work)
    if session_context.get("active_task"):
        task_memory = memory_store.get_task(session_context["active_task"])

    # 4. Selective recall: only include if relevance > threshold
    return MemoryBundle(
        preferences=preferences,
        outcomes=filter_by_relevance(outcomes, threshold=0.7),
        task=task_memory,
        patterns=memory_store.get_active_patterns()
    )
```

### 4. Context Injection Layer

**Purpose:** Transform retrieved memories into LLM-consumable format and inject into prompts.

**Industry Patterns:**

1. **System Message Injection**
   - Memories prepended to system prompt
   - Highest priority, always considered
   - Best for preferences and stable facts

2. **Working Memory Section**
   - Dedicated section in prompt with delimiter tags
   - Medium priority, structured
   - Best for session-specific context

3. **Tool-Based Injection**
   - Memory returned as tool output
   - Lowest priority but most flexible
   - Best for on-demand, verbose memories

**Recommended Structure for Open Interpreter:**

```markdown
## System Instructions
{base_system_message}

## User Context
<preferences>
- Prefers: pytest, type hints, explicit error handling
- Style: concise responses, show diffs
- Project: Python 3.11, uses poetry
</preferences>

<recent_outcomes>
- Fixed import error by using absolute imports (worked)
- Refactored auth module (tests passed)
</recent_outcomes>

<active_task>
Task: Implement rate limiting for API endpoints
Status: In progress (phase 2 of 3)
Last action: Added Redis dependency
</active_task>

## Current Conversation
{messages}
```

---

## Data Flow

### Signal -> Memory Flow

```
┌──────────────────────────────────────────────────────────────────────┐
│                         Signal Ingestion                             │
│                                                                      │
│  ┌─────────┐    ┌─────────────┐    ┌──────────────┐    ┌──────────┐ │
│  │ EventBus│───>│Signal Router│───>│Memory Extract│───>│Candidate │ │
│  │ Events  │    │  (filter)   │    │   (LLM?)     │    │  Queue   │ │
│  └─────────┘    └─────────────┘    └──────────────┘    └────┬─────┘ │
│                                                              │       │
└──────────────────────────────────────────────────────────────│───────┘
                                                               │
                                                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        Memory Update (A.U.D.N.)                      │
│                                                                      │
│  ┌────────────────┐    ┌───────────────┐    ┌────────────────────┐  │
│  │ Candidate Note │───>│ Find Similar  │───>│ LLM Decision:      │  │
│  │                │    │ (vector search)│    │ Add/Update/Delete/ │  │
│  └────────────────┘    └───────────────┘    │ No-op              │  │
│                                             └─────────┬──────────┘  │
│                                                       │              │
│                                             ┌─────────▼──────────┐  │
│                                             │   Memory Store     │  │
│                                             └────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

### Memory -> Prompt Flow

```
┌──────────────────────────────────────────────────────────────────────┐
│                         Context Injection                            │
│                                                                      │
│  ┌────────────┐    ┌─────────────────┐    ┌──────────────────────┐  │
│  │User Message│───>│Retrieval Engine │───>│  MemoryBundle        │  │
│  │            │    │ (proactive)     │    │  - preferences       │  │
│  └────────────┘    └─────────────────┘    │  - outcomes          │  │
│                                           │  - tasks             │  │
│                                           │  - patterns          │  │
│                                           └───────────┬──────────┘  │
│                                                       │              │
│  ┌──────────────┐    ┌─────────────────┐    ┌────────▼───────────┐  │
│  │System Message│<───│ Context Builder │<───│  Formatter         │  │
│  │ + Memory     │    │ (inject)        │    │  (YAML/Markdown)   │  │
│  └──────┬───────┘    └─────────────────┘    └────────────────────┘  │
│         │                                                            │
└─────────│────────────────────────────────────────────────────────────┘
          │
          ▼
┌──────────────────────────────────────────────────────────────────────┐
│                         respond() Loop                               │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ _build_system_message(interpreter) <── INJECTION POINT         │  │
│  │                                                                │  │
│  │ Current: base + lang_messages + custom_instructions            │  │
│  │ Add:     + memory_context (preferences, outcomes, tasks)       │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Integration Points

### Primary Integration: respond.py `_build_system_message()`

**Current Implementation (lines 107-174):**
```python
def _build_system_message(interpreter):
    # Build cache key from dependencies
    cache_key = (
        interpreter.system_message,
        lang_messages,
        interpreter.custom_instructions,
        # ...
    )

    # Build system message using parts
    parts: list[str] = []
    parts.append(base)
    for lang_msg in lang_messages:
        if lang_msg:
            parts.append(lang_msg)
    if interpreter.custom_instructions:
        parts.append(interpreter.custom_instructions)
    # ...

    system_message = "\n\n".join(p for p in parts if p)
    return system_message
```

**Proposed Integration:**
```python
def _build_system_message(interpreter):
    # ... existing code ...

    # NEW: Inject memory context after custom_instructions
    if interpreter.enable_memory_layer and interpreter.memory_layer:
        memory_context = interpreter.memory_layer.get_context_for_prompt(
            user_message=_get_last_user_message(interpreter),
            session_id=interpreter.conversation_linker.get_conversation_id()
        )
        if memory_context:
            parts.append(memory_context)

    # Update cache key to include memory state
    cache_key = (
        # ... existing ...
        interpreter.memory_layer.get_state_hash() if interpreter.memory_layer else None,
    )
```

### Secondary Integration: Signal Capture

**Location:** After major events in respond.py

```python
# After successful code execution (around line 1000)
if interpreter.enable_memory_layer:
    interpreter.memory_layer.ingest_signal({
        "type": "code_outcome",
        "language": language,
        "code": code,
        "success": not _execution_trace.exception_occurred if _execution_trace else True,
        "output": truncated_output,
    })

# After file changes detected (around line 1030)
if interpreter.enable_memory_layer and _changed_files:
    interpreter.memory_layer.ingest_signal({
        "type": "file_edit",
        "files": list(_changed_files.keys()),
        "user_intent": user_msgs[-1].get("content", "") if user_msgs else "",
    })
```

### Tertiary Integration: EventBus Subscribers

**New Module:** `interpreter/core/memory/signal_listener.py`

```python
class MemorySignalListener:
    """Subscribe to EventBus and route signals to memory layer."""

    def __init__(self, memory_layer: 'MemoryLayer'):
        self.memory_layer = memory_layer
        self._subscribed = False

    def subscribe(self, event_bus: EventBus):
        if self._subscribed:
            return

        event_bus.subscribe(EventType.MESSAGE_END, self._on_message_end)
        event_bus.subscribe(EventType.CONSOLE_OUTPUT, self._on_console_output)
        event_bus.subscribe(EventType.FILE_CHANGE, self._on_file_change)
        event_bus.subscribe(EventType.CONFIRMATION_RESPONSE, self._on_confirmation)
        self._subscribed = True
```

---

## Build Order

Based on component dependencies, suggested implementation order:

### Phase 1: Memory Store Foundation
**Dependencies:** None (new module)
**Components:**
1. `MemoryStore` base class with unified API
2. Preference storage (key-value, simplest)
3. Basic retrieval interface

**Why First:** Foundation for everything else. Can test in isolation.

### Phase 2: Signal Capture
**Dependencies:** MemoryStore, EventBus (exists)
**Components:**
1. `SignalListener` subscribing to EventBus
2. Signal filtering logic
3. Basic memory extraction (no LLM needed initially)

**Why Second:** Needs somewhere to store signals (Phase 1), but doesn't need retrieval working yet.

### Phase 3: Context Injection
**Dependencies:** MemoryStore, respond.py integration point
**Components:**
1. `ContextBuilder` formatting memories for prompts
2. Integration with `_build_system_message()`
3. Cache invalidation logic

**Why Third:** This is where value is delivered. Once injection works, the system provides benefit even with minimal memories.

### Phase 4: Retrieval Engine
**Dependencies:** MemoryStore, Vector embeddings
**Components:**
1. Similarity search for outcomes
2. Pattern matching for tasks
3. Relevance scoring and filtering

**Why Fourth:** Proactive recall improves quality but basic injection works without sophisticated retrieval.

### Phase 5: Memory Types Expansion
**Dependencies:** All previous phases
**Components:**
1. Outcome memory (vector storage, embeddings)
2. Task memory (structured + graph relationships)
3. Pattern memory (graph-based)

**Why Fifth:** Once the basic loop works, expand memory types for richer context.

### Phase 6: LLM-Assisted Extraction
**Dependencies:** Working memory system
**Components:**
1. A.U.D.N. decision making (add/update/delete/no-op)
2. Semantic deduplication
3. Memory consolidation

**Why Last:** Adds intelligence but increases complexity and cost. Start simple.

---

## Architectural Patterns

### Memory Loop Pattern

**Source:** OpenAI Agents SDK, Mem0, industry consensus

```
┌─────────┐     ┌─────────┐     ┌──────────┐     ┌─────────────┐
│ Inject  │────>│ Reason  │────>│ Distill  │────>│ Consolidate │
│         │     │ (LLM)   │     │ (extract)│     │ (store)     │
└─────────┘     └─────────┘     └──────────┘     └──────┬──────┘
     ▲                                                   │
     │                                                   │
     └───────────────────────────────────────────────────┘
                    (next interaction)
```

- **Inject:** Load relevant memories into context before LLM call
- **Reason:** LLM processes with memory-augmented context
- **Distill:** Extract memory candidates from interaction
- **Consolidate:** Update memory store (merge, dedupe, prune)

### Hierarchical Memory Tiers (MemGPT Pattern)

```
┌─────────────────────────────────────────────────────────┐
│              Working Memory (Context Window)            │
│  - Current conversation                                 │
│  - Active task state                                    │
│  - Injected preferences (top-K)                         │
│  Size: ~4K-8K tokens, managed carefully                 │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│              Long-Term Memory (Vector Store)            │
│  - Semantic search for relevant memories                │
│  - Outcomes, patterns, historical context               │
│  Size: Unlimited, retrieved on demand                   │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│              Archival Memory (Cold Storage)             │
│  - Full conversation history                            │
│  - Rarely accessed, compliance/audit                    │
│  Size: Unlimited, slow retrieval                        │
└─────────────────────────────────────────────────────────┘
```

### A.U.D.N. Pattern (Mem0)

When new memory candidate arrives, decide action via LLM:
- **Add:** New unique information, store it
- **Update:** Supersedes existing memory, merge/replace
- **Delete:** Contradicts or invalidates existing memory
- **No-op:** Duplicate or not memory-worthy, skip

### Context Compression Pattern

Prevent context overflow with:
1. **Selective injection:** Only inject relevant memories
2. **Summarization:** Compress old memories periodically
3. **Pruning:** Remove low-relevance or stale memories
4. **Token budgeting:** Hard limit on memory token count

---

## Anti-Patterns to Avoid

### 1. Context Stuffing
**What:** Injecting all memories into every prompt
**Why Bad:** Wastes tokens, dilutes relevance, increases cost
**Instead:** Selective retrieval based on current query

### 2. Synchronous Memory Operations
**What:** Blocking LLM call for memory operations
**Why Bad:** Adds latency to every interaction
**Instead:** Background ingestion, pre-computed retrieval

### 3. Global Memory Scope
**What:** All memories available across all contexts
**Why Bad:** Context leakage, irrelevant information, privacy
**Instead:** Scoped memories (project, session, user)

### 4. Implicit Memory Injection
**What:** Memories injected without user awareness
**Why Bad:** Security risk (prompt injection via memory), unpredictable behavior
**Instead:** Explicit memory sections with delimiters

### 5. LLM-Heavy Memory Operations
**What:** Using LLM for every memory decision
**Why Bad:** Expensive, slow, unpredictable
**Instead:** LLM only for complex decisions (A.U.D.N.), heuristics for filtering

---

## Sources

### Research Papers & Documentation
- [Mem0 Research: 26% Accuracy Boost](https://mem0.ai/research) - Memory layer architecture, LOCOMO benchmark
- [MemGPT: LLMs as Operating Systems](https://arxiv.org/abs/2310.08560) - Hierarchical memory, context paging
- [Design Patterns for Long-Term Memory](https://serokell.io/blog/design-patterns-for-long-term-memory-in-llm-powered-architectures) - Four leading architectures
- [OpenAI Agents SDK: Context Personalization](https://cookbook.openai.com/examples/agents_sdk/context_personalization) - Memory loop pattern, injection strategies
- [A-Mem: Agentic Memory (NeurIPS 2025)](https://openreview.net/forum?id=FiM0M8gcct) - Zettelkasten-inspired dynamic indexing

### GitHub Repositories
- [mem0ai/mem0](https://github.com/mem0ai/mem0) - Production memory layer, hybrid storage
- [Letta/MemGPT](https://docs.letta.com/concepts/memgpt/) - OS-paradigm memory management

### Industry Analysis
- [Memory for AI Agents: Context Engineering Paradigm](https://thenewstack.io/memory-for-ai-agents-a-new-paradigm-of-context-engineering/) - Memory as first-class system
- [Cognee vs Mem0 Comparison](https://dasroot.net/posts/2025/12/cognee-vs-mem0-memory-layer-comparison-llm-agents/) - Hybrid graph + vector architecture

---

## Confidence Assessment

| Component | Confidence | Rationale |
|-----------|------------|-----------|
| Memory Loop Pattern | HIGH | Multiple authoritative sources agree (OpenAI, Mem0, MemGPT) |
| Hierarchical Tiers | HIGH | Well-established OS analogy, proven in production |
| Integration Point (respond.py) | HIGH | Verified against existing codebase |
| A.U.D.N. Pattern | MEDIUM | Mem0 implementation, may need adaptation |
| Vector + Graph Hybrid | MEDIUM | Industry trend, complexity tradeoff unclear |
| Build Order | MEDIUM | Logical dependencies, may need adjustment |

---

## Open Questions for Phase-Specific Research

1. **Embedding Model Choice:** Which embedding model for outcome similarity search? (balance cost/quality)
2. **Memory TTL Strategy:** How long to keep memories? Decay function or explicit expiry?
3. **Privacy/Security:** How to prevent prompt injection via poisoned memories?
4. **Evaluation Metrics:** How to measure memory quality and retrieval effectiveness?
5. **Graph Backend:** Neo4j vs embedded (Kuzu) for pattern storage?
