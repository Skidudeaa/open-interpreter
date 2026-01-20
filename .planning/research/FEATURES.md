# Features Research: Personal AI Memory System

**Domain:** Personal AI memory system for Open Interpreter
**Researched:** 2026-01-20
**Overall Confidence:** MEDIUM-HIGH

## Executive Summary

This research surveys the feature landscape for personal AI memory systems in 2025-2026. The field has matured significantly, with memory now considered "table stakes" for serious AI assistants. Leading platforms (OpenAI, Anthropic, Google, Microsoft) all deployed memory features in 2024-2025, establishing baseline expectations.

The key insight: **Memory systems are "mostly a memory design problem"** - once agents execute multi-turn tasks, explicit mechanisms for storage, retrieval, and conflict resolution become critical. The existing SemanticEditGraph in this codebase provides a foundation for episodic/procedural memory around code edits; this milestone adds the preference/outcome layer.

---

## Table Stakes

Features users expect from any memory system. Missing these = system feels broken.

### 1. Basic Persistence Across Sessions

| Aspect | Requirement |
|--------|-------------|
| **What** | Memory survives application restart |
| **Why expected** | All major platforms (ChatGPT, Claude, Gemini, Copilot) persist memory |
| **Complexity** | Low |
| **Notes** | Already partially present via SemanticEditGraph |

### 2. User Visibility ("What Do You Remember?")

| Aspect | Requirement |
|--------|-------------|
| **What** | Users can query what the system remembers about them |
| **Why expected** | OpenAI shows "Memory updated" notifications; all vendors standardized on transparency |
| **Complexity** | Low |
| **Notes** | GDPR/privacy regulations require this; builds trust |

### 3. User Control (Edit/Delete)

| Aspect | Requirement |
|--------|-------------|
| **What** | Users can view, edit, and delete stored memories |
| **Why expected** | Required for GDPR compliance; all major platforms implement |
| **Complexity** | Medium |
| **Notes** | Must handle cascading effects when memories are deleted |

### 4. Disable/Pause Memory

| Aspect | Requirement |
|--------|-------------|
| **What** | Users can turn memory off entirely or use "temporary sessions" |
| **Why expected** | Standard across ChatGPT, Claude, Gemini |
| **Complexity** | Low |
| **Notes** | "Incognito mode" pattern is well-understood |

### 5. Basic Preference Storage

| Aspect | Requirement |
|--------|-------------|
| **What** | Store simple user preferences ("prefers X over Y") |
| **Why expected** | Core value proposition of memory; expected from all assistants |
| **Complexity** | Low |
| **Notes** | Planned in project context |

### 6. Memory Influence on Responses (Pre-prompting)

| Aspect | Requirement |
|--------|-------------|
| **What** | Retrieved memories are injected into system prompt to influence behavior |
| **Why expected** | The most basic memory influence pattern; how ChatGPT/Claude work |
| **Complexity** | Low |
| **Notes** | V1 approach per project context; defers more sophisticated mechanisms |

### 7. Recency-Aware Retrieval

| Aspect | Requirement |
|--------|-------------|
| **What** | Recent memories are prioritized; stale memories decay in relevance |
| **Why expected** | Without this, outdated context pollutes responses |
| **Complexity** | Medium |
| **Notes** | Can start simple (timestamp weighting) and evolve |

---

## Differentiators

Features that make this uniquely valuable. Not expected, but highly valued when present.

### 1. Outcome Tracking ("Last time Z happened")

| Aspect | Detail |
|--------|--------|
| **What** | Track results of past decisions/actions and learn from them |
| **Why valuable** | Enables learning from experience; rare in consumer products |
| **Complexity** | Medium |
| **Notes** | Planned in project context; builds on SemanticEditGraph patterns |

**Implementation insight:** This goes beyond simple preference storage to "episodic memory" - remembering specific events and their outcomes. The existing `EditResult` structure in the codebase shows this pattern: storing success/failure, test results, errors.

### 2. Hierarchical Task Context (Project > Task > Subtask)

| Aspect | Detail |
|--------|--------|
| **What** | Memory organized by task hierarchy; context threads through related work |
| **Why valuable** | Enables long-running task continuity; missing from most consumer AI |
| **Complexity** | High |
| **Notes** | Planned in project context; research shows hierarchical memory is cutting-edge (2025 papers) |

**Implementation insight:** This is where the field is headed. Research papers like "Hierarchical Memory for High-Efficiency Long-Term Reasoning" (July 2025) and "G-Memory: Tracing Hierarchical Memory for Multi-Agent Systems" (June 2025) show this is active research.

### 3. Context Pattern Recognition ("Debug mode after midnight")

| Aspect | Detail |
|--------|--------|
| **What** | Detect patterns in user behavior across time/context dimensions |
| **Why valuable** | Enables proactive personalization; very few systems do this well |
| **Complexity** | High |
| **Notes** | Planned in project context; requires temporal reasoning |

### 4. Conflict Resolution and Preference Evolution

| Aspect | Detail |
|--------|--------|
| **What** | Handle contradictions gracefully; recognize preferences change over time |
| **Why valuable** | Without this, memory becomes stale or conflicting |
| **Complexity** | Medium |
| **Notes** | AWS AgentCore and Mem0 implement this; essential for long-term use |

**Implementation insight:** Modern systems prioritize recency while maintaining historical context. Example from AWS AgentCore: "Existing: 'Customer budget is $500' -> New: 'budget increased to $750' -> Result: New active memory, previous marked inactive."

### 5. Semantic Memory Consolidation

| Aspect | Detail |
|--------|--------|
| **What** | Merge related facts; deduplicate; build structured knowledge |
| **Why valuable** | Prevents memory bloat; improves retrieval quality |
| **Complexity** | Medium-High |
| **Notes** | Research shows naive storage degrades performance over time |

**Implementation insight:** Mem0 handles "extraction, categorization, decay metrics, confidence scoring, and conflict resolution behind a simple API." This is the direction for mature memory systems.

### 6. Multi-Model Memory Portability

| Aspect | Detail |
|--------|--------|
| **What** | Memory works across different LLM backends |
| **Why valuable** | Open Interpreter supports multiple models; memory shouldn't be model-locked |
| **Complexity** | Low-Medium |
| **Notes** | Perplexity advertises this: "context across every model" |

### 7. Procedural Memory (How-to Knowledge)

| Aspect | Detail |
|--------|--------|
| **What** | Remember successful patterns for accomplishing tasks |
| **Why valuable** | Enables getting better at tasks over time |
| **Complexity** | High |
| **Notes** | The "third memory type" (alongside semantic and episodic) that completes the picture |

**Implementation insight:** Procedural memory stores "structured, goal-directed processes" - not time-sensitive (episodic) nor abstract facts (semantic), but actionable knowledge. The existing code edit tracking could evolve into procedural memory for coding patterns.

### 8. Active Forgetting / Memory Hygiene

| Aspect | Detail |
|--------|--------|
| **What** | Intelligent decay, pruning, and cleanup of memories |
| **Why valuable** | Prevents bloat; keeps memory relevant; privacy compliance |
| **Complexity** | Medium |
| **Notes** | Research shows "forgetting is a design feature, not a problem" |

**Implementation insight:** The MaRS framework formalizes six forgetting policies: FIFO, LRU, Priority Decay, Reflection-Summary, Random-Drop, and Hybrid. Start simple (decay by recency/relevance), evolve as needed.

---

## Anti-Features

Features to deliberately NOT build. Common mistakes in this domain.

### 1. Full Conversation History Storage

| Anti-Feature | Full Conversation History |
|--------------|---------------------------|
| **What it looks like** | Store every message, send full history with each request |
| **Why it's bad** | Increases latency, costs, hits context limits, privacy nightmare |
| **What to do instead** | Store extracted facts/preferences, not raw conversations |
| **Source** | Mem0 guide: "Sending full conversation history...increases latency and token costs without solving memory" |

### 2. Opaque Memory (Hidden from User)

| Anti-Feature | Opaque Memory |
|--------------|---------------|
| **What it looks like** | System remembers things but user can't see what |
| **Why it's bad** | Trust erosion, GDPR non-compliance, debugging impossible |
| **What to do instead** | Transparency features from day one |
| **Source** | Industry consensus: "Users must be able to see what the agent remembers" |

### 3. Memory File Bloat / Context Window Waste

| Anti-Feature | Memory Rules Bloat |
|--------------|-------------------|
| **What it looks like** | Memory/rules files grow unbounded, waste context on redundant info |
| **Why it's bad** | Degrades response quality, increases costs |
| **What to do instead** | Regular pruning; every rule "fights for its right to exist" |
| **Source** | Dev.to AI coding patterns guide |

### 4. Premature Graph Complexity

| Anti-Feature | Over-Engineered Graph Structure |
|--------------|--------------------------------|
| **What it looks like** | Building complex knowledge graphs before validating simpler approaches |
| **Why it's bad** | Adds complexity without proven benefit |
| **What to do instead** | Start with vector search; add graph only when explicit relationships needed |
| **Source** | Mem0 guide: "Most implementations don't require graph complexity initially" |

### 5. Global Memory Without Scoping

| Anti-Feature | Flat Memory Structure |
|--------------|----------------------|
| **What it looks like** | All memories in one pool, no user/session/project scoping |
| **Why it's bad** | Context bleeds across unrelated work; privacy issues in multi-user |
| **What to do instead** | Hierarchical scoping: user > project > session |
| **Source** | LangGraph patterns: "User-level, Session-level, Agent-level memory" |

### 6. Autonomous Memory Updates Without Notification

| Anti-Feature | Silent Memory Changes |
|--------------|----------------------|
| **What it looks like** | System updates memory without telling user |
| **Why it's bad** | User loses trust; can't correct errors; feels surveilled |
| **What to do instead** | "Memory updated" notifications; audit trail |
| **Source** | OpenAI pattern with ChatGPT memory |

### 7. Ignoring Memory Security

| Anti-Feature | Unprotected Memory |
|--------------|-------------------|
| **What it looks like** | Memory can be poisoned via prompt injection |
| **Why it's bad** | "Memory-based persistence creates self-sustaining compromises" |
| **What to do instead** | Input validation; memory isolation; consider dual-LLM approaches |
| **Source** | Palo Alto Unit42 research; OWASP LLM01:2025 |

### 8. Building "Full Autonomy" Memory First

| Anti-Feature | Over-Scoped Initial Implementation |
|--------------|-----------------------------------|
| **What it looks like** | Memory that auto-decides, auto-acts, auto-constrains from v1 |
| **Why it's bad** | Hard to debug; users lose control; trust issues |
| **What to do instead** | Start with pre-prompting (v1), add suggestion/constraint later |
| **Source** | VentureBeat: "Ship narrowly scoped agents; scale to memory patterns as you prove reliability" |

---

## Feature Dependencies

Which features require others to be built first.

```
Legend:
A --> B means "B requires A to be built first"

[Persistence] --> [User Visibility]
                          |
                          v
                  [User Control (Edit/Delete)]
                          |
                          v
                  [Disable/Pause Memory]

[Basic Preference Storage] --> [Preference Evolution/Conflict Resolution]
                                        |
                                        v
                              [Semantic Consolidation]

[Basic Preference Storage] --> [Memory Influence (Pre-prompting)]
                                        |
                                        v
                              [Suggestion Injection] (deferred)
                                        |
                                        v
                              [Constraint Application] (deferred)
                                        |
                                        v
                              [Auto-decisions] (deferred)

[Outcome Tracking] --> [Procedural Memory]
         |
         v
[Pattern Recognition]

[Hierarchical Task Context] requires [Basic Preference Storage] AND [Outcome Tracking]

[Active Forgetting] requires [Recency-Aware Retrieval]
```

### Recommended Build Order

**Phase 1: Foundation (Table Stakes)**
1. Basic Preference Storage (schema + CRUD)
2. Persistence Across Sessions
3. Memory Influence via Pre-prompting
4. User Visibility ("what do you remember")

**Phase 2: Trust & Control**
5. User Control (edit/delete)
6. Disable/Pause Memory
7. Recency-Aware Retrieval

**Phase 3: Learning (Differentiators)**
8. Outcome Tracking
9. Conflict Resolution / Preference Evolution
10. Active Forgetting

**Phase 4: Intelligence (Advanced Differentiators)**
11. Hierarchical Task Context
12. Context Pattern Recognition
13. Procedural Memory
14. Semantic Consolidation

---

## Complexity Assessment

| Feature | Complexity | Effort | Risk | Notes |
|---------|------------|--------|------|-------|
| Basic Persistence | Low | 1-2 days | Low | Extend existing DB schema |
| User Visibility | Low | 1 day | Low | Query + format existing data |
| User Control | Medium | 2-3 days | Medium | Handle cascading deletes |
| Disable/Pause | Low | 1 day | Low | Flag check on write path |
| Basic Preference Storage | Low | 2 days | Low | New table + CRUD |
| Pre-prompting Influence | Low | 1-2 days | Low | Inject into system message |
| Recency-Aware Retrieval | Medium | 2-3 days | Low | Timestamp weighting |
| Outcome Tracking | Medium | 3-5 days | Medium | New data model; integration points |
| Conflict Resolution | Medium | 3-5 days | Medium | Versioning; resolution logic |
| Active Forgetting | Medium | 2-4 days | Medium | Decay algorithm; cleanup jobs |
| Hierarchical Context | High | 5-10 days | High | New data model; complex queries |
| Pattern Recognition | High | 5-10 days | High | Temporal analysis; heuristics |
| Procedural Memory | High | 5-10 days | High | Action sequence modeling |
| Semantic Consolidation | Medium-High | 4-7 days | Medium | Deduplication; merging logic |

---

## MVP Recommendation

For MVP, prioritize **Table Stakes + one Differentiator**:

### Must Have (MVP)
1. **Basic Preference Storage** - The core value proposition
2. **Pre-prompting Influence** - Makes preferences actually do something
3. **User Visibility** - Trust and GDPR compliance
4. **Persistence** - Memory must survive restarts

### Should Have (MVP+)
5. **User Control** - Edit/delete for trust
6. **Outcome Tracking** - The unique differentiator in project scope

### Defer to Post-MVP
- Hierarchical Task Context (complexity)
- Context Pattern Recognition (complexity)
- Advanced forgetting (can start with simple TTL)
- Suggestion injection, constraint application, auto-decisions (per project scope)

---

## Sources

### High Confidence (Official Documentation / Authoritative)
- [IBM: What Is AI Agent Memory](https://www.ibm.com/think/topics/ai-agent-memory)
- [Letta/MemGPT Documentation](https://docs.letta.com/concepts/memgpt/)
- [Mem0 GitHub](https://github.com/mem0ai/mem0)
- [LangChain Memory Concepts](https://docs.langchain.com/oss/python/concepts/memory)

### Medium Confidence (Verified with Multiple Sources)
- [Machine Learning Mastery: 3 Types of Long-term Memory](https://machinelearningmastery.com/beyond-short-term-memory-the-3-types-of-long-term-memory-ai-agents-need/)
- [MarkTechPost: Comparing Memory Systems](https://www.marktechpost.com/2025/11/10/comparing-memory-systems-for-llm-agents-vector-graph-and-event-logs/)
- [Serokell: Design Patterns for Long-Term Memory](https://serokell.io/blog/design-patterns-for-long-term-memory-in-llm-powered-architectures)
- [Tribe AI: Context-Aware Memory Systems 2025](https://www.tribe.ai/applied-ai/beyond-the-bubble-how-context-aware-memory-systems-are-changing-the-game-in-2025)

### Low Confidence (Single Source / WebSearch Only)
- [arXiv: Memory in the Age of AI Agents Survey](https://arxiv.org/abs/2512.13564)
- [AWS AgentCore Long-term Memory](https://aws.amazon.com/blogs/machine-learning/building-smarter-ai-agents-agentcore-long-term-memory-deep-dive/)
- [Palo Alto Unit42: Persistent Memory Attacks](https://unit42.paloaltonetworks.com/indirect-prompt-injection-poisons-ai-longterm-memory/)

---

## Codebase Integration Notes

The existing `SemanticEditGraph` in `/interpreter/core/memory/` provides:
- **Episodic memory** for code edits (what happened, when, outcome)
- **Procedural patterns** via edit chains and symbol tracking
- **Persistence** via DuckDB/SQLite
- **Querying** by symbol, file, intent, conversation

This milestone adds a **parallel memory layer** for:
- User preferences (new table: `preferences`)
- Outcomes beyond code edits (generalize `EditResult` pattern)
- Task hierarchy (new table: `task_context`)
- Context patterns (new table: `context_patterns`)

The pre-prompting influence mechanism will retrieve from both layers and inject into the system message.
