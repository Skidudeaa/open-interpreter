# Pitfalls Research: Personal AI Memory System

**Domain:** Personal AI memory for coding assistant
**Researched:** 2026-01-20
**Overall Confidence:** MEDIUM-HIGH (multiple sources corroborate critical pitfalls)

---

## Critical Pitfalls

These mistakes cause rewrites, architectural failures, or fundamentally broken systems.

### 1. Retrieval That Returns Plausible-But-Wrong Context

**What goes wrong:** Semantic similarity search returns memories that are lexically or embedding-similar but contextually irrelevant. The LLM then confidently acts on wrong context, leading to incorrect suggestions or code that breaks existing functionality.

**Warning signs:**
- LLM responses reference memories from unrelated projects/contexts
- User frequently corrects "I didn't mean that X, I meant the other X"
- Multi-hop reasoning fails (e.g., retrieving a preference about "Python style" when user meant "documentation style")
- Memories about different entities with similar names get conflated (e.g., two functions named `process()` in different files)

**Prevention:**
- Implement hybrid retrieval: combine embedding similarity with keyword/metadata filtering
- Add a re-ranking step using a dense model or lightweight LLM to reshuffle results based on deeper semantic understanding
- Store rich metadata (project, file context, time window, session) and filter before similarity search
- Use explicit disambiguation: when "Karen" or "process" could refer to multiple things, include the disambiguating context in the memory itself
- Test with adversarial queries that surface similar-but-wrong results

**Phase relevance:** Phase 1-2 (memory storage design and retrieval implementation). This must be right from the start because changing retrieval fundamentally affects all downstream behavior.

**Sources:** [freecodecamp - RAG failures with knowledge graphs](https://www.freecodecamp.org/news/how-to-solve-5-common-rag-failures-with-knowledge-graphs/), [NB Data - 23 RAG pitfalls](https://www.nb-data.com/p/23-rag-pitfalls-and-how-to-fix-them)

---

### 2. Memory Poisoning via Indirect Prompt Injection

**What goes wrong:** Malicious or corrupted content gets stored in long-term memory, then persistently influences future behavior across sessions. Unlike one-time prompt injections, poisoned memories persist and affect hundreds of future interactions.

**Warning signs:**
- System starts exhibiting unexpected behaviors that persist across sessions
- Memory contains instructions that look like system prompts ("Always respond in X way")
- Retrieval returns content that wasn't explicitly created by the user
- Security tools don't detect issues because the "attack" looks like legitimate stored data

**Prevention:**
- Sanitize all content before storing in memory (input filters)
- Validate retrieved memories before including in prompts (context filters)
- Store provenance metadata: where did this memory come from? (user input, system inference, external source)
- Implement memory review/dashboard for user to inspect and delete suspicious entries
- Never store raw external content (web pages, file contents) as memories without explicit user approval
- Separate "facts about user" from "facts from external sources"

**Phase relevance:** Phase 1 (memory storage design). Security architecture must be designed in, not bolted on.

**Sources:** [Palo Alto Unit42 - Indirect prompt injection poisons AI long-term memory](https://unit42.paloaltonetworks.com/indirect-prompt-injection-poisons-ai-longterm-memory/), [Lakera - Agentic AI threats](https://www.lakera.ai/blog/agentic-ai-threats-p1), [arxiv - MINJA memory injection attack](https://arxiv.org/html/2503.03704v2)

---

### 3. Unbounded Memory Growth Without Decay

**What goes wrong:** Memory accumulates indefinitely. Old, outdated, or contradicted information stays forever. System becomes slow, retrieval quality degrades, and outdated preferences override current ones.

**Warning signs:**
- Query latency increases over time
- Old preferences keep resurfacing despite user changing their mind
- Memory database grows without bound
- Retrieval returns increasingly stale context
- User says "I told you weeks ago I changed that preference"

**Prevention:**
- Implement explicit temporal decay: memories lose weight over time
- Use recency-weighted scoring (e.g., 60% relevance, 25% recency, 15% importance)
- Build contradiction detection: new explicit preferences should deactivate conflicting old ones
- Implement "contextual forgetting": selectively remove or reduce importance based on recency, relevance, or accuracy
- Set memory retention policies (e.g., implicit preferences decay faster than explicit ones)
- Monitor memory growth metrics and alert on anomalous growth

**Phase relevance:** Phase 1-2 (memory storage and preference learning). Decay mechanisms must be part of the core data model.

**Your decision "explicit > implicit, both decay when contradicted":** This directly addresses this pitfall. The key implementation detail is ensuring contradiction detection actually works and decay rates are tuned.

**Sources:** [Tribe AI - Context-aware memory systems](https://www.tribe.ai/applied-ai/beyond-the-bubble-how-context-aware-memory-systems-are-changing-the-game-in-2025), [Memoria framework - arxiv](https://www.arxiv.org/pdf/2512.12686), [mem0 - Context engineering guide](https://mem0.ai/blog/context-engineering-ai-agents-guide)

---

### 4. Causal Misattribution of Outcomes

**What goes wrong:** System incorrectly attributes success/failure to the wrong cause. E.g., user runs code that fails, system concludes "user prefers to avoid library X" when the actual cause was a typo. These wrong attributions then influence future suggestions incorrectly.

**Warning signs:**
- System starts avoiding libraries/patterns the user actually likes
- User notices "why do you never suggest X anymore?"
- Outcome-based suggestions diverge from user's actual preferences
- System becomes increasingly opinionated based on noisy signals

**Prevention:**
- Distinguish correlation from causation explicitly in the data model
- Require minimum confidence threshold before storing causal inferences
- Ask when uncertain: "I noticed this failed - was it because of X or Y?"
- Store raw outcome data separately from inferred preferences
- Allow user to correct misattributions ("No, that failed because of Z, not X")
- Weight explicit corrections much higher than inferred patterns

**Phase relevance:** Phase 2-3 (outcome memory and preference learning). This is subtle and requires careful design of the inference pipeline.

**Your decision "Ask when causal inference uncertain":** This directly addresses this pitfall. The key is calibrating what "uncertain" means - be conservative.

**Sources:** [kin.ai - Why personal AI memory is difficult](https://mykin.ai/resources/why-personal-ai-memory-difficult), [Evidentlyai - Concept drift](https://www.evidentlyai.com/ml-in-production/concept-drift)

---

### 5. Context Window Bloat from Over-Retrieval

**What goes wrong:** System retrieves too many memories and stuffs them into the prompt. LLM gets distracted by irrelevant context, response quality degrades, costs increase, and the most relevant information gets lost in noise.

**Warning signs:**
- LLM responses reference tangentially related memories unnecessarily
- Token usage spikes without corresponding value increase
- LLM starts ignoring relevant memories because they're buried in noise
- Response quality is worse with memory enabled than without
- User says "why are you bringing up X? That's not relevant"

**Prevention:**
- Set strict limits on retrieved memories (quality over quantity)
- Implement tiered retrieval: only surface highly relevant memories, not everything that matches
- Use Adaptive Focus Memory approach: assign fidelity levels (Full, Compressed, Placeholder) based on relevance
- Monitor context window utilization: which memories actually influence responses?
- A/B test memory inclusion: does adding this memory improve response quality?
- "The sweet spot is providing just enough relevant context for the LLM to deliver useful, accurate results" - start conservative, add more only if needed

**Phase relevance:** Phase 2 (pre-prompting implementation). The retrieval-to-prompt pipeline design.

**Sources:** [agenta - Managing context length in LLMs](https://agenta.ai/blog/top-6-techniques-to-manage-context-length-in-llms), [JetBrains Research - Efficient context management](https://blog.jetbrains.com/research/2025/12/efficient-context-management/), [eval.16x.engineer - LLM context management guide](https://eval.16x.engineer/blog/llm-context-management-guide)

---

### 6. The "Creepy AI" Problem: Surfacing Memories User Forgot

**What goes wrong:** System helpfully reminds user of things they'd rather forget - past relationships, health problems, failed projects, embarrassing mistakes. The "helpfulness" becomes intrusive and makes users uncomfortable.

**Warning signs:**
- User expresses surprise/discomfort: "How do you know that?"
- User starts self-censoring to avoid creating memories
- User requests to delete specific memories
- Trust in system erodes despite correct functionality

**Prevention:**
- Implement "relevance threshold" that's higher for personal/sensitive topics
- Don't surface memories unprompted about: relationships, health, failures, unless directly asked
- Provide clear memory dashboard where users can see and delete anything
- Default to opt-in for sensitive categories
- Never surface memories from long ago unless directly relevant to current query
- Consider "graceful forgetting": some things should fade even if technically relevant

**Phase relevance:** Phase 3-4 (proactive memory surfacing). This is about restraint in the influence mechanism.

**Sources:** [AIThority - The AI memory paradox](https://aithority.com/ait-featured-posts/the-ai-memory-paradox-should-machines-remember-everything/), [TechPolicy.Press - What we risk when AI systems remember](https://www.techpolicy.press/what-we-risk-when-ai-systems-remember/)

---

## Moderate Pitfalls

These cause delays, technical debt, or degraded experience but are recoverable.

### 7. Embedding Model Staleness and Drift

**What goes wrong:** Embeddings become inconsistent over time. When you update your embedding model, old memories use old embeddings and new memories use new ones. Similarity search across mixed embeddings gives unreliable results.

**Warning signs:**
- Retrieval quality degrades after embedding model update
- Old memories stop being retrieved even when relevant
- New memories cluster separately from old ones

**Prevention:**
- Version embeddings: tag each memory with embedding model version
- When upgrading models: either re-embed everything or maintain separate indices
- Consider backward-compatible training for new models
- Implement embedding drift detection (compare distributions over time)
- For personal use (smaller corpus): full re-embedding on model update is feasible

**Phase relevance:** Phase 1 (memory storage design) - plan for this even if not implementing embeddings in v1.

**Sources:** [Medium - When embeddings go stale](https://medium.com/@yashtripathi.nits/when-embeddings-go-stale-detecting-fixing-retrieval-drift-in-production-778a89481a57), [Milvus - Strategies to update embeddings](https://milvus.io/ai-quick-reference/what-strategies-can-be-used-to-update-or-improve-embeddings-over-time-as-new-data-becomes-available-and-how-would-that-affect-ongoing-rag-evaluations)

---

### 8. Preference Contradiction Without Resolution

**What goes wrong:** User states conflicting preferences at different times. System stores both without resolving. When both get retrieved, LLM gets confused or picks arbitrarily.

**Warning signs:**
- LLM behavior is inconsistent: sometimes does X, sometimes does Y
- Retrieval returns contradictory preferences for same query
- User says "I told you I prefer X" but system does Y

**Prevention:**
- Detect contradictions at write time: does this new preference conflict with existing ones?
- Implement explicit resolution: newer explicit preference supersedes older
- For implicit preferences: track confidence, newer observations update rather than duplicate
- Consider computational argumentation approaches for complex conflicts
- When contradiction detected: either auto-resolve (with rule) or ask user

**Phase relevance:** Phase 2 (preference memory implementation).

**Your decision "explicit > implicit, both decay when contradicted":** Good foundation. Key is implementing actual contradiction detection, not just decay.

**Sources:** [ResearchGate - Preference handling for AI](https://www.researchgate.net/publication/220604823_Preference_Handling_for_Artificial_Intelligence), [arxiv - Multi-user preference conflict resolution](https://arxiv.org/html/2511.03576)

---

### 9. Task State That Doesn't Match Reality

**What goes wrong:** Hierarchical task state gets out of sync with actual project state. System thinks task is in progress when it's done, or thinks project structure is X when user restructured to Y.

**Warning signs:**
- System references tasks that were completed long ago
- Task hierarchy doesn't match current project structure
- User has to repeatedly correct "no, that's not the current status"

**Prevention:**
- Task state should be derived from signals (git, files, conversations) not just stored
- Implement staleness detection: task not mentioned in N sessions = possibly stale
- Provide easy task state review/update interface
- Don't over-structure: loose task tracking is better than rigid wrong structure
- Consider "last confirmed" timestamp: flag tasks not confirmed recently

**Phase relevance:** Phase 2-3 (task state memory implementation).

**Your decision "Hierarchical task state":** Good, but implement with staleness detection and easy correction.

---

### 10. Performance Degradation at Scale

**What goes wrong:** Memory system works great with 100 memories but degrades significantly at 10,000+. Vector search precision drops, query latency increases, index doesn't fit in memory.

**Warning signs:**
- Noticeable latency increase as memory grows
- Retrieval quality degrades with more memories
- Memory consumption grows non-linearly

**Prevention:**
- Benchmark early with projected data sizes (estimate 6-12 months of usage)
- Use metadata filtering to reduce search space before similarity search
- Implement memory compaction/archival for old, low-value memories
- For personal use: likely okay up to 100K memories with proper indexing
- Monitor key metrics: query latency, precision@k, memory usage

**Phase relevance:** Phase 1 (storage design). Design for scale even if v1 is small.

**Your decision "Extend existing DuckDB/SQLite":** Good for relational queries. If adding embeddings, ensure you can add HNSW index (DuckDB has vss extension, or use separate vector store).

**Sources:** [EyeLevel - Vector databases lose accuracy at scale](https://www.eyelevel.ai/post/do-vector-databases-lose-accuracy-at-scale), [DagsHub - Vector database pitfalls](https://dagshub.com/blog/common-pitfalls-to-avoid-when-using-vector-databases/)

---

### 11. Right to Forget Violations

**What goes wrong:** User wants to delete a memory but system has already used it to derive other memories/preferences. Deleting the source doesn't delete the derived knowledge. Or, deletion is technically difficult because data is scattered.

**Warning signs:**
- User deletes memory but system still exhibits influenced behavior
- User can't find where a belief came from to delete it
- Deletion is partial or ineffective

**Prevention:**
- Track provenance: every memory/inference links to its source(s)
- Implement cascade deletion: deleting source can optionally delete derived
- Make deletion first-class: don't treat it as edge case
- Provide "forget everything about X" capability
- Don't use memories to train/fine-tune models that can't be unlearned
- Regular "memory audit" capability for user

**Phase relevance:** Phase 1 (data model) and Phase 4 (privacy features).

**Sources:** [CSA - The right to be forgotten but can AI forget](https://cloudsecurityalliance.org/blog/2025/04/11/the-right-to-be-forgotten-but-can-ai-forget), [TechPolicy.Press - The right to be forgotten is dead](https://www.techpolicy.press/the-right-to-be-forgotten-is-dead-data-lives-forever-in-ai/)

---

## Minor Pitfalls

Annoying but fixable without major rework.

### 12. Temporal Context Loss

**What goes wrong:** "Soon" means different things in different contexts. "I'm fixing my bike soon" vs "I'm having a baby soon" - the system doesn't capture temporal context, leading to inappropriate urgency modeling.

**Prevention:** Store temporal context explicitly (deadline, urgency, expected duration) rather than inferring from vague language.

**Phase relevance:** Phase 2-3 (task and context memory).

---

### 13. Over-Reliance on Recent Context

**What goes wrong:** System weights recency too heavily and forgets important long-term preferences. User's explicit style guide from 3 months ago gets overridden by casual comment yesterday.

**Prevention:** Distinguish between "recent observations" and "established preferences." Explicit preferences should decay more slowly than implicit observations.

**Phase relevance:** Phase 2 (preference learning).

---

### 14. Memory Visibility Deficit

**What goes wrong:** User can't see what the system remembers about them. This creates distrust and makes debugging impossible.

**Prevention:** Always provide memory inspection capability. Even CLI-only: `interpreter --show-memories "topic"`.

**Phase relevance:** Phase 3-4 (UI integration).

---

## Your Decisions - Risk Assessment

| Decision | Risk Level | Notes |
|----------|------------|-------|
| Explicit > implicit, both decay when contradicted | LOW | Good foundation. Ensure contradiction detection works. |
| Ask when causal inference uncertain | LOW | Good. Calibrate "uncertain" conservatively. |
| Hierarchical task state | MEDIUM | Implement staleness detection, easy correction. |
| Pre-prompting as first influence mechanism | LOW | Safe, non-invasive. Good starting point. |
| Extend existing DuckDB/SQLite | LOW | Good for relational. Plan for vector extension if needed. |

### Risks Your Decisions Mitigate

1. **Runaway implicit inference** - Your explicit > implicit rule prevents the system from becoming confident about things it shouldn't be confident about.

2. **Confident wrong attributions** - Your "ask when uncertain" approach directly prevents causal misattribution.

3. **Infrastructure complexity** - Extending existing storage rather than adding new systems reduces integration risk.

### Risks to Monitor

1. **Retrieval quality** - Not addressed by current decisions. Need explicit retrieval strategy.

2. **Memory poisoning** - Not addressed. Need input/output sanitization design.

3. **Creepy surfacing** - Pre-prompting is safe, but any proactive surfacing needs restraint rules.

4. **Scale** - DuckDB/SQLite will need vector extension planning if using embeddings.

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| Memory storage schema | #11 Right to forget violations | Design provenance tracking and cascade deletion from start |
| Memory storage schema | #7 Embedding staleness | Version embeddings, plan for re-embedding |
| Preference learning | #3 Unbounded growth | Implement decay in core data model |
| Preference learning | #8 Contradiction without resolution | Build contradiction detection, not just storage |
| Outcome memory | #4 Causal misattribution | Confidence thresholds, ask when uncertain |
| Retrieval implementation | #1 Plausible-but-wrong context | Hybrid retrieval, re-ranking, metadata filtering |
| Pre-prompting | #5 Context window bloat | Strict relevance thresholds, start conservative |
| Proactive surfacing | #6 Creepy AI | Restraint rules for sensitive topics |
| Task state | #9 State mismatch | Staleness detection, derive from signals |

---

## Confidence Assessment

| Area | Confidence | Reason |
|------|------------|--------|
| Retrieval pitfalls | HIGH | Multiple sources, well-documented RAG failures |
| Security pitfalls | HIGH | Recent research (2024-2025) with demonstrated attacks |
| Preference learning | MEDIUM | Less documented for personal AI specifically |
| Performance | MEDIUM-HIGH | Vector DB limitations well-documented |
| Privacy/UX | MEDIUM | Emerging area, less empirical data |

---

## Sources

### Retrieval and RAG
- [freecodecamp - How to solve RAG failures with knowledge graphs](https://www.freecodecamp.org/news/how-to-solve-5-common-rag-failures-with-knowledge-graphs/)
- [NB Data - 23 RAG pitfalls and how to fix them](https://www.nb-data.com/p/23-rag-pitfalls-and-how-to-fix-them)
- [Evidentlyai - RAG evaluation guide](https://www.evidentlyai.com/llm-guide/rag-evaluation)

### Memory and Context
- [Tribe AI - Context-aware memory systems 2025](https://www.tribe.ai/applied-ai/beyond-the-bubble-how-context-aware-memory-systems-are-changing-the-game-in-2025)
- [mem0 - Context engineering guide](https://mem0.ai/blog/context-engineering-ai-agents-guide)
- [kin.ai - Why personal AI memory is difficult](https://mykin.ai/resources/why-personal-ai-memory-difficult)
- [Memoria framework - arxiv](https://www.arxiv.org/pdf/2512.12686)
- [arxiv - Memory in the age of AI agents](https://arxiv.org/abs/2512.13564)

### Security
- [Palo Alto Unit42 - Memory poisoning via prompt injection](https://unit42.paloaltonetworks.com/indirect-prompt-injection-poisons-ai-longterm-memory/)
- [Lakera - Agentic AI threats](https://www.lakera.ai/blog/agentic-ai-threats-p1)
- [arxiv - MINJA memory injection attack](https://arxiv.org/html/2503.03704v2)
- [DarkReading - ChatGPT memory feature and prompt injection](https://www.darkreading.com/endpoint-security/chatgpt-memory-feature-prompt-injection)

### Concept Drift
- [Evidentlyai - Concept drift in ML](https://www.evidentlyai.com/ml-in-production/concept-drift)
- [Lumenova - AI drift types and detection](https://www.lumenova.ai/blog/model-drift-concept-drift-introduction/)
- [orq.ai - Model vs data drift in LLMs](https://orq.ai/blog/model-vs-data-drift)

### Vector Databases
- [EyeLevel - Vector databases lose accuracy at scale](https://www.eyelevel.ai/post/do-vector-databases-lose-accuracy-at-scale)
- [DagsHub - Vector database pitfalls](https://dagshub.com/blog/common-pitfalls-to-avoid-when-using-vector-databases/)
- [KX - 8 common mistakes in vector search](https://kx.com/blog/8-common-mistakes-in-vector-search/)
- [Medium - When embeddings go stale](https://medium.com/@yashtripathi.nits/when-embeddings-go-stale-detecting-fixing-retrieval-drift-in-production-778a89481a57)

### Privacy and UX
- [AIThority - The AI memory paradox](https://aithority.com/ait-featured-posts/the-ai-memory-paradox-should-machines-remember-everything/)
- [TechPolicy.Press - What we risk when AI systems remember](https://www.techpolicy.press/what-we-risk-when-ai-systems-remember/)
- [CSA - The right to be forgotten and AI](https://cloudsecurityalliance.org/blog/2025/04/11/the-right-to-be-forgotten-but-can-ai-forget)
- [TechPolicy.Press - The right to be forgotten is dead](https://www.techpolicy.press/the-right-to-be-forgotten-is-dead-data-lives-forever-in-ai/)

### Context Window Management
- [agenta - Top techniques to manage context length](https://agenta.ai/blog/top-6-techniques-to-manage-context-length-in-llms)
- [JetBrains Research - Efficient context management](https://blog.jetbrains.com/research/2025/12/efficient-context-management/)
- [eval.16x.engineer - LLM context management guide](https://eval.16x.engineer/blog/llm-context-management-guide)

### Preference Handling
- [ResearchGate - Preference handling for AI](https://www.researchgate.net/publication/220604823_Preference_Handling_for_Artificial_Intelligence)
- [Montreal AI Ethics - Inconsistent preferences](https://montrealethics.ai/the-challenge-of-understanding-what-users-want-inconsistent-preferences-and-engagement-optimization/)
- [arxiv - Beyond preferences in AI alignment](https://arxiv.org/abs/2408.16984)
