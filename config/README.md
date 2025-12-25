# Config Folder - Application Configuration

Ovaj folder sadrži **human-editable** konfiguraciju za sistem. Za razliku od `.cache/` foldera (koji sadrži auto-generated fajlove), ovi fajlovi mogu biti ručno menjani od strane admin-a.

---

## `context_param_schemas.json`

**Svrha:** Definiše pravila za klasifikaciju parametara kao "context parameters" (PersonId, VehicleId, TenantId).

**Kada ga koristiti:**
- Novi klijent ima drugačija imena parametara (npr. `employee_number` umesto `person_id`)
- API nema dobre Swagger descriptions pa schema-based classification ne radi
- Želiš da dodaš novi context type (npr. `booking_id`)

**Kako ga menjati:**

### 1. Dodati novi fallback name

Ako API koristi parametar `driver_number` koji je zapravo `person_id`:

```json
{
  "context_types": {
    "person_id": {
      ...
      "fallback_names": [
        "personid", "person_id", "userid",
        "driver_number"  ← DODAJ OVDE
      ]
    }
  }
}
```

**Promene odmah aktivne** nakon restarta aplikacije (učitava se u `ToolRegistry.__init__`).

---

### 2. Dodati novi context type

Primer: Dodavanje `booking_id` kao novi context type:

```json
{
  "context_types": {
    ...  // existing types

    "booking_id": {
      "description": "Identifies a booking/reservation in the system",
      "schema_hints": {
        "formats": ["uuid", "guid"],
        "types": ["string"]
      },
      "classification_rules": {
        "description_keywords": ["booking", "reservation", "appointment"],
        "name_patterns": [".*booking.*id.*", ".*reservation.*id.*"],
        "path_hints": ["/bookings/", "/reservations/"]
      },
      "scoring": {
        "schema_format_match": 2,
        "description_keyword_match": 3,
        "name_pattern_match": 2,
        "path_hint_match": 1,
        "threshold": 3
      },
      "fallback_names": ["bookingid", "booking_id", "reservationid"]
    }
  }
}
```

**Promene zahtevaju:**
1. Edit `context_param_schemas.json`
2. Restart aplikacije
3. (Optional) Update `tool_contracts.py` ako trebaš novi enum value

---

### 3. Tuning scoring weights

Ako klasifikacija previše često bira pogrešan context type:

```json
"scoring": {
  "schema_format_match": 2,       // UUID format hint
  "description_keyword_match": 5,  // ← POVEĆAJ OVO (najviši prioritet)
  "name_pattern_match": 1,         // ← SMANJI OVO (najmanje pouzdan)
  "path_hint_match": 1,
  "threshold": 4                   // ← POVEĆAJ threshold za strožiju proveru
}
```

---

### 4. API-specific overrides

Ponekad **isti parametar** znači **različite stvari** u različitim API-ima:

```json
{
  "api_specific_overrides": {
    "overrides": [
      {
        "api_path": "/vehicles/*",
        "parameter": "owner_id",
        "force_context_type": "person_id",
        "reason": "Vehicle owner is a person, not vehicle"
      },
      {
        "api_path": "/bookings/create",
        "parameter": "assigned_to",
        "force_context_type": "person_id",
        "reason": "Assigned driver for this booking"
      }
    ]
  }
}
```

---

## Troubleshooting

### Problem: Parameter se ne klasifikuje korektno

**Proveri:**
1. Pokreni aplikaciju sa `LOG_LEVEL=DEBUG` i pretraži logove:
   ```
   Loaded 3 context types, 15 fallback names
   ```

2. Proveri da li je config fajl učitan:
   ```python
   # U Python REPL-u:
   from services.tool_registry import ToolRegistry
   registry = ToolRegistry()
   print(registry.CONTEXT_PARAM_FALLBACK)
   ```

3. Ako config nije učitan, proveri putanju:
   - Config fajl MORA biti u `{current_working_directory}/config/context_param_schemas.json`
   - Proveriti sa `Path.cwd() / "config" / "context_param_schemas.json"`

---

### Problem: Promene u config-u se ne primenjuju

**Rešenje:** Config se učitava samo pri inicijalizaciji `ToolRegistry` objekta.

**Opcije:**
1. Restart aplikacije (Docker container)
2. Hot reload (ako implementirano): `POST /admin/reload-config`
3. Redis cache clear: `await redis.flushdb()` (briše cacheovane tool definitions)

---

## Best Practices

### ✅ DO:
- Dodaj comment uz svaku promenu (npr. `// Added for Client XYZ - their API uses employee_number`)
- Testiraj u development okruženju pre production deploya
- Commit config promene u Git sa opisnom porukom
- Drži backup pre većih promena (`cp context_param_schemas.json context_param_schemas.json.backup`)

### ❌ DON'T:
- Ne commit-uj sensitive data (API keys, passwords) - to ide u `.env` fajl
- Ne briši existing fallback names bez testiranja (može pokvariti production)
- Ne stavljaj business logiku u config (to pripada u Python kod)

---

## Version Control

Ovaj fajl JE verzioniran u Git-u (za razliku od `.cache/` foldera).

**Workflow:**
1. Edit `context_param_schemas.json` lokalno
2. Testiraj: `docker-compose up app`
3. Commit: `git add config/context_param_schemas.json && git commit -m "Add driver_number fallback"`
4. Push: `git push origin main`
5. Deploy: CI/CD će automatski postaviti novu verziju

---

## JSON Schema Validation

Config fajl ima `$schema` atribut koji omogućava validaciju:

```bash
# Install JSON schema validator
pip install jsonschema

# Validate config
python -m jsonschema -i config/context_param_schemas.json
```

Ako config nije validan, aplikacija će fallback-ovati na hardcoded defaults i logirati error.
