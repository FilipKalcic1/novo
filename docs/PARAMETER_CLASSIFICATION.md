# Parameter Classification Strategy

## Problem: Hardcoded Name Matching vs. Schema-Based Classification

### ❌ **Old Approach (Hardcoded Names)**

```python
CONTEXT_PARAM_MAP = {
    "personid": "person_id",
    "userid": "person_id",
    "driverid": "person_id",
    # ... 50 more variations
}

if param_name.lower() in CONTEXT_PARAM_MAP:
    context_key = CONTEXT_PARAM_MAP[param_name.lower()]
```

**Problems:**
1. **Lažni negativi** - `CreatedByPersonId` se NE matchuje
2. **Maintenance hell** - svaki novi klijent = nova varijacija
3. **Ignoriše kontekst** - `id` u `/person/{id}` vs `/vehicle/{id}`
4. **Tech debt** - hardcoded lista raste do 100+ stavki

---

## ✅ **New Approach (Schema-Based Classification)**

### Strategy: Multi-Signal Scoring

```python
CONTEXT_PARAM_PATTERNS = {
    "person_id": {
        "schema_formats": ["uuid", "guid"],          # OpenAPI format hint
        "description_keywords": ["person", "user"],   # From Swagger description
        "type_hints": ["string"],                     # Type validation
    }
}
```

### Classification Algorithm:

```
FOR each parameter:
    score = 0

    IF schema.format IN ["uuid", "guid"]:
        score += 2  # Strong signal

    IF ANY keyword IN description.lower():
        score += 3  # Strongest signal (explicit documentation)

    IF schema.type IN type_hints:
        score += 1  # Weak signal (many things are strings)

    IF score >= 3:
        RETURN context_type

    ELSE fallback to name matching (for undocumented APIs)
```

---

## Examples

### Example 1: Well-Documented API

```yaml
# Swagger spec
parameters:
  - name: AssignedToId
    in: query
    schema:
      type: string
      format: uuid
    description: "ID of the person assigned to this vehicle"
```

**Classification:**
- `format: uuid` → +2
- `description` contains "person" → +3
- `type: string` → +1
- **Total: 6 ≥ 3** → `person_id` ✅

### Example 2: Poorly Documented API

```yaml
# Swagger spec
parameters:
  - name: id
    in: path
    schema:
      type: string
```

**Classification:**
- No format → 0
- No description → 0
- `type: string` → +1
- **Total: 1 < 3** → Fallback to name matching
- `"id"` NOT in fallback map → `FROM_USER` ✅

### Example 3: Ambiguous Name (OLD approach would FAIL)

```yaml
# Swagger spec
parameters:
  - name: owner_id
    in: query
    schema:
      type: string
      format: uuid
    description: "Vehicle owner person ID"
```

**OLD approach:**
- `owner_id` NOT in hardcoded map → `FROM_USER` ❌ WRONG

**NEW approach:**
- `format: uuid` → +2
- `description` contains "person" → +3
- **Total: 5 ≥ 3** → `person_id` ✅ CORRECT

---

## Benefits

### 1. **Catches Composite Parameters**
- `CreatedByPersonId` → Detected via description: "person who created..."
- `ModifiedByUserId` → Detected via description: "user who modified..."
- `OwnerPersonId` → Detected via description: "owner of the vehicle (person)"

### 2. **Leverages API Documentation**
- Good APIs have descriptions → Best results
- Poor APIs fall back to name matching → Still works

### 3. **Context-Aware**
```yaml
# /vehicles/{id} - NOT a person
parameters:
  - name: id
    description: "Vehicle identifier"  # No "person" keyword
# → FROM_USER ✅

# /person/{id} - IS a person
parameters:
  - name: id
    description: "Person identifier"   # Contains "person"
# → person_id ✅
```

### 4. **Easy to Extend**
Add new context type in 3 lines:
```python
CONTEXT_PARAM_PATTERNS["booking_id"] = {
    "schema_formats": ["uuid"],
    "description_keywords": ["booking", "reservation"],
    "type_hints": ["string"],
}
```

---

## Future Improvements

### 1. **Load from Database/Config**
```python
# Instead of hardcoded dict, load from:
CONTEXT_PARAM_PATTERNS = await db.load_context_patterns()
```

### 2. **Runtime Learning**
```python
# If API call succeeds with auto-injected PersonId:
await db.record_success(
    endpoint="/vehicles",
    param="assignedTo",
    context_type="person_id"
)

# If API call fails:
await db.record_failure(
    endpoint="/vehicles",
    param="owner",
    context_type="person_id"
)
```

### 3. **ML-based Classification** (Future)
```python
# Train classifier on successful API calls
model = train_param_classifier(historical_api_calls)
context_type = model.predict(param_name, description, schema)
```

---

## Migration Path

### Phase 1: Schema-Based (Current)
- Use Swagger metadata + descriptions
- Fallback to name matching

### Phase 2: Database-Driven
- Move patterns to database
- Allow runtime updates without code deploy

### Phase 3: ML-Enhanced
- Train classifier on historical data
- Continuous improvement from production traffic

---

## Testing

See: `test_parameter_classification.py`

```bash
python test_parameter_classification.py
```

Expected output:
```
✅ Well-documented param: person_id
✅ Poorly-documented param: FROM_USER
✅ Composite param (CreatedByPersonId): person_id
✅ Ambiguous param (owner_id): person_id (via description)
```
