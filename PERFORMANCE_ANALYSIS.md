# Performance Analysis Report

**Date:** 2025-12-26
**Codebase:** MobilityOne WhatsApp Bot (v11.0+)
**Analysis Scope:** Full codebase performance anti-patterns

---

## Executive Summary

This analysis identifies **15 performance anti-patterns** across the codebase, categorized by severity and impact. The codebase is generally well-architected with async patterns, but several optimization opportunities exist, particularly in embedding generation, vector search, and Redis operations.

---

## Critical Issues (High Impact)

### 1. Sequential Embedding Generation
**Location:** `services/registry/embedding_engine.py:214-225`

```python
for op_id in missing:
    tool = tools[op_id]
    text = tool.embedding_text
    embedding = await self._get_embedding(text)  # One API call per tool
    if embedding:
        embeddings[op_id] = embedding
    await asyncio.sleep(0.05)  # Rate limiting
```

**Problem:** Generates embeddings one at a time with 50ms delay between each. For 100+ tools, this takes 5+ seconds minimum.

**Impact:** Slow registry initialization (startup time)

**Recommendation:** Batch embedding generation using OpenAI's batch API:
```python
# Send texts in batches of 20
response = await self.openai.embeddings.create(
    input=batch_texts[:20],
    model=settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT
)
```

---

### 2. O(n²) Dependency Graph Building
**Location:** `services/registry/embedding_engine.py:239-296`

```python
def build_dependency_graph(self, tools: Dict[str, UnifiedToolDefinition]):
    for tool_id, tool in tools.items():           # O(n)
        for req_output in required_outputs:        # O(m)
            providers = self._find_providers(req_output, tools)  # O(n) inside
```

**Problem:** For each tool, iterates over all tools again to find providers. With 200 tools, this is 40,000+ iterations.

**Impact:** Slow startup, especially as tool count grows

**Recommendation:** Build reverse index first:
```python
# Pre-build output_key -> provider_tools mapping
output_index = defaultdict(list)
for tool_id, tool in tools.items():
    for key in tool.output_keys:
        output_index[key.lower()].append(tool_id)
# Now O(1) lookups instead of O(n)
```

---

### 3. Pure Python Cosine Similarity
**Location:** `services/scoring_utils.py:12-33`

```python
def cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b)
```

**Problem:** Pure Python loop over 1536-dimension vectors. NumPy is already in requirements.txt but not used here.

**Impact:** Each similarity calculation is ~10x slower than NumPy

**Recommendation:** Use NumPy vectorized operations:
```python
import numpy as np

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))
```

---

### 4. Linear Search for Vector Similarity
**Location:** `services/registry/search_engine.py:175-182`

```python
for op_id in search_pool:
    if op_id not in embeddings:
        continue
    similarity = cosine_similarity(query_embedding, embeddings[op_id])
    if similarity >= lenient_threshold:
        scored.append((similarity, op_id))
```

**Problem:** Compares query against ALL tool embeddings on every search. With 200 tools and 1536 dimensions, this is 300K+ float operations per query.

**Impact:** Slow tool discovery, affects every user message

**Recommendation:** Use approximate nearest neighbor (ANN) library:
- FAISS (Facebook's vector search)
- Annoy (Spotify's ANN library)
- Or use Redis Vector Search (RediSearch module)

---

## Medium Issues (Moderate Impact)

### 5. No Redis Pipeline Usage
**Location:** `services/cache_service.py`, `services/context_service.py`

```python
# Current: Multiple round-trips
await self.redis.get(key1)
await self.redis.get(key2)
await self.redis.set(key3, value3)
```

**Problem:** Each Redis operation is a network round-trip. Multiple operations could be batched.

**Impact:** 1-3ms per operation adds up with multiple cache calls per request

**Recommendation:** Use Redis pipelines for batch operations:
```python
async with self.redis.pipeline() as pipe:
    pipe.get(key1)
    pipe.get(key2)
    pipe.set(key3, value3, ex=ttl)
    results = await pipe.execute()
```

---

### 6. Standard JSON Instead of orjson
**Location:** Multiple files (37 files use json.loads/dumps)

```python
import json
value = json.loads(data)  # Standard library
```

**Problem:** `orjson` is in requirements.txt but `json` module is used instead. orjson is 3-10x faster.

**Impact:** JSON parsing overhead on every message

**Recommendation:** Replace with orjson:
```python
import orjson
value = orjson.loads(data)
json_str = orjson.dumps(value).decode()
```

---

### 7. Multiple OpenAI Client Instances
**Location:** Multiple services create their own clients

- `services/ai_orchestrator.py:66` - creates AsyncAzureOpenAI
- `services/registry/embedding_engine.py:39` - creates AsyncAzureOpenAI
- `services/registry/search_engine.py:83` - creates AsyncAzureOpenAI

**Problem:** Each client maintains its own connection pool and state. Should share a single instance.

**Impact:** Memory overhead, potential connection pool exhaustion

**Recommendation:** Create singleton OpenAI client factory:
```python
_openai_client = None

def get_openai_client() -> AsyncAzureOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncAzureOpenAI(...)
    return _openai_client
```

---

### 8. Inefficient Rate Limiter Data Structure
**Location:** `security.py:115-153` and `worker.py:673-690`

```python
# Clean old entries - O(n) filter on every check
self._requests[identifier] = [
    t for t in self._requests[identifier]
    if t > window_start
]
```

**Problem:** Stores every request timestamp and filters list on each check. Memory grows with request volume.

**Impact:** Memory usage grows, cleanup is O(n)

**Recommendation:** Use sliding window counter or Redis sorted sets:
```python
# Using collections.deque with maxlen
from collections import deque
self._requests[identifier] = deque(maxlen=self.limit)
# Or use Redis ZRANGEBYSCORE for distributed rate limiting
```

---

### 9. Multiple Scoring Passes
**Location:** `services/registry/search_engine.py:185-189`

```python
scored = self._apply_method_disambiguation(query, scored, tools)
scored = self._apply_user_specific_boosting(query, scored, tools)
scored = self._apply_category_boosting(query, scored, tools)
scored = self._apply_documentation_boosting(query, scored)
scored = self._apply_evaluation_adjustment(scored)
```

**Problem:** 5 separate passes over the scored list, each iterating through all items.

**Impact:** 5x iterations over results (typically 50-100 items)

**Recommendation:** Combine into single pass:
```python
def _calculate_final_score(self, query, op_id, base_score, tools):
    score = base_score
    score += self._get_method_adjustment(...)
    score += self._get_user_boost(...)
    score += self._get_category_boost(...)
    return score
```

---

### 10. No Query Embedding Cache
**Location:** `services/registry/search_engine.py:229-239`

```python
async def _get_query_embedding(self, query: str) -> Optional[List[float]]:
    response = await self.openai.embeddings.create(
        input=[query[:8000]],
        model=settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT
    )
    return response.data[0].embedding
```

**Problem:** Generates new embedding for every query, even if similar queries were made recently.

**Impact:** Unnecessary API calls, latency on every search

**Recommendation:** Add LRU cache with fuzzy matching:
```python
from functools import lru_cache
# Or async-lru which is already in requirements.txt
from async_lru import alru_cache

@alru_cache(maxsize=100)
async def _get_query_embedding(self, query: str):
    ...
```

---

## Low Issues (Minor Impact)

### 11. Conversation State Saves on Every Change
**Location:** `services/conversation_manager.py:206, 223, 232, etc.`

Every state modification immediately calls `await self.save()`:
```python
async def add_parameters(self, params: Dict[str, Any]):
    # ... modify params ...
    await self.save()  # Immediate Redis write
```

**Impact:** Multiple Redis writes per conversation turn

**Recommendation:** Batch saves or use write-behind pattern:
```python
self._dirty = True
# Save at end of request cycle, not on every change
```

---

### 12. History Parsing in Loop
**Location:** `services/context_service.py:96-108`

```python
raw = await self.redis.lrange(key, 0, -1)
messages = []
for item in raw:
    if item:
        messages.append(json.loads(item))  # Parse each individually
```

**Impact:** JSON parsing overhead, especially for long histories

**Recommendation:** Consider storing as single JSON blob or using Redis JSON module.

---

### 13. Synchronous File I/O for Config
**Location:** `services/registry/search_engine.py:30-47`

```python
with open(path, 'r', encoding='utf-8') as f:
    return json.load(f)  # Blocking I/O
```

**Problem:** Synchronous file read in async codebase (during initialization)

**Impact:** Blocks event loop during startup

**Recommendation:** Use `aiofiles` for async file I/O or load during startup before event loop.

---

### 14. Repeated Regex Compilation
**Location:** `services/patterns.py`, `services/ai_orchestrator.py`

Pattern lists like `READ_INTENT_PATTERNS` contain raw strings that get compiled on each use.

**Impact:** Regex compilation overhead on each pattern match

**Recommendation:** Pre-compile regex patterns:
```python
READ_INTENT_PATTERNS = [
    re.compile(pattern) for pattern in [
        r"what is", r"show me", ...
    ]
]
```

---

### 15. String Concatenation in Loops
**Location:** `services/registry/swagger_parser.py:196`, `services/ai_orchestrator.py:540`

```python
full_desc = f"{summary}. {description}".strip(". ")
```

**Impact:** Minor string allocation overhead

**Recommendation:** Generally acceptable, but for hot paths consider `"".join()`.

---

## What's Done Well

The codebase already implements several good practices:

1. **Async Throughout** - Properly uses `async/await` for I/O operations
2. **Connection Pooling** - Redis client with `max_connections=20`
3. **Circuit Breaker** - Prevents cascading failures
4. **Retry with Backoff** - Exponential backoff for rate limits
5. **Singleton Services** - Worker initializes services once
6. **Structured Logging** - Good observability with structlog
7. **Database Indexes** - Proper indexes on frequently queried columns
8. **Batch Message Processing** - Worker processes up to 5 messages concurrently
9. **Message Deduplication** - Prevents double processing with Redis locks

---

## Priority Recommendations

| Priority | Issue | Estimated Effort | Impact |
|----------|-------|------------------|--------|
| P0 | Batch embedding generation | Medium | Startup 5x faster |
| P0 | NumPy for cosine similarity | Low | 10x faster search |
| P1 | Pre-built dependency index | Low | Startup 10x faster |
| P1 | Use orjson everywhere | Low | 3-5x faster JSON |
| P1 | Redis pipelines | Medium | 50% fewer round-trips |
| P2 | Query embedding cache | Low | Fewer API calls |
| P2 | Single scoring pass | Medium | 5x fewer iterations |
| P2 | Shared OpenAI client | Low | Memory reduction |
| P3 | Efficient rate limiter | Low | Memory optimization |
| P3 | Batch state saves | Medium | Fewer Redis writes |

---

## Metrics to Track

After implementing fixes, monitor:

1. **Startup Time** - Registry initialization duration
2. **Search Latency** - P50/P99 for tool discovery
3. **Redis Operations** - Commands per request
4. **Memory Usage** - Worker process RSS
5. **API Calls** - OpenAI embedding calls per hour
6. **Request Latency** - End-to-end message processing time

---

*Report generated by performance analysis on 2025-12-26*
