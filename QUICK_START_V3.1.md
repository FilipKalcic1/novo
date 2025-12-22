# Quick Start Guide - v3.1

**Verzija**: 3.1 - Master Prompt Implementation
**Status**: ✅ Ready for Deployment

---

## 🚀 Deployment (3 koraka)

### 1. Clear Cache ❗ OBAVEZNO
```bash
cd C:\Users\igork\.claude-worktrees\mobility_bot\funny-elion
rm -rf .cache
```

### 2. Restart Worker
```bash
docker-compose restart worker
```

### 3. Provjeri Startup Logs
```bash
docker-compose logs -f worker
```

**Očekivani output**:
```
🔄 Building tool registry cache...
✅ Loaded 909 tools from swagger
✅ Built embeddings for 909 tools
ChadAgent initialized (v3.0 with ReasoningEngine)
ReasoningEngine initialized
```

---

## ✅ Što je Novo u v3.1

### 1. **Stroga URL Konstrukcija** (`swagger_name`)
- ❌ PRIJE: `/LatestMileageReports` (404 error!)
- ✅ POSLIJE: `/automation/LatestMileageReports` (works!)

**Formula**: `/{swagger_name}/{path}`

---

### 2. **ACTION-OR-SILENCE** (Bez Brbljanja)
- ❌ PRIJE: "Naravno! Dozvolite mi da provjerim..." (chatty)
- ✅ POSLIJE: Immediate tool_call when similarity >0.85

**Trigger**: `similarity >= 0.85` → FORCE execution

---

### 3. **JSON Enforcement** (Clean Errors)
- ❌ PRIJE: User vidi `<!DOCTYPE html>...`
- ✅ POSLIJE: "Trenutno ne mogu dohvatiti te podatke..."

**Rule**: Samo JSON odgovori, HTML blokiran

---

## 🧪 Testiranje (5 query-ja)

### Test 1: Registracija (ACTION-OR-SILENCE)
**Poruka**: "Mogu li vidjeti registraciju?"

**Očekivano**:
- ✅ Similarity ~0.87
- ✅ Log: `⚡ ACTION-OVER-TALK: Similarity 0.87 >= 0.85`
- ✅ Immediate tool_call (NO chatting)
- ✅ Data returned

**Check**:
```bash
grep "ACTION-OVER-TALK" logs/*.log
```

---

### Test 2: URL Construction
**Poruka**: "Kolika je kilometraža?"

**Očekivano**:
- ✅ URL: `/automation/LatestMileageReports`
- ✅ Log: `Built URL: /automation/LatestMileageReports (swagger_name=automation)`
- ✅ NO 404 errors

**Check**:
```bash
grep "Built URL:" logs/*.log | grep "swagger_name="
```

---

### Test 3: HTML Firewall
**Scenario**: Auth token expired → API returns HTML

**Očekivano**:
- ✅ Log: `🚨 HTML LEAKAGE BLOCKED`
- ✅ User sees: "Trenutno ne mogu dohvatiti te podatke..."
- ❌ User NEVER sees HTML tags

**Check**:
```bash
grep "HTML LEAKAGE BLOCKED" logs/*.log
```

---

### Test 4: Hallucination Detection
**Poruka**: "Kreiraj booking za example@example.com"

**Očekivano**:
- ✅ Log: `🚨 REASONING ENGINE BLOCKED: Hallucinated values detected`
- ✅ Error: "Greška u planiranju: Hallucinated values detected"
- ❌ NO API call made

**Check**:
```bash
grep "REASONING ENGINE BLOCKED" logs/*.log
```

---

### Test 5: Tool Discovery
**Poruka**: "Prikaži registraciju vozila"

**Očekivano**:
- ✅ Finds `get_MasterData` (boost: +0.35)
- ✅ Similarity >0.70
- ✅ Returns vehicle data (VIN, RegistrationNumber)

**Check**:
```bash
grep "Top matches" logs/*.log
```

---

## 📊 Success Metrics

Po deployment-u, prati ove metrike **prvih 24h**:

### 1. URL Construction Success
```bash
grep "Built URL:" logs/*.log | grep "swagger_name=" | wc -l
```
**Target**: >95% koristi `swagger_name`

### 2. Action-Over-Talk Rate
```bash
grep "ACTION-OVER-TALK" logs/*.log | wc -l
```
**Target**: >50% high-similarity queries

### 3. HTML Leakage Blocks
```bash
grep "HTML LEAKAGE BLOCKED" logs/*.log | wc -l
```
**Target**: <5/tjedan

### 4. Critique Pass Rate
```bash
grep "REASONING ENGINE CRITIQUE" logs/*.log | grep -c "PASS"
```
**Target**: >95%

---

## 🔧 Troubleshooting

### Problem: Cache nije rebuild-an
**Simptom**: `⚠️ Using cache from .cache/`

**Fix**:
```bash
rm -rf .cache
docker-compose restart worker
# Wait 60s for rebuild
```

---

### Problem: swagger_name prazan
**Simptom**: `Built URL: /LatestMileageReports (no swagger_name)`

**Fix**:
1. Check `.cache/*.json` za `swagger_name` field
2. Clear cache: `rm -rf .cache`
3. Restart worker
4. Provjeri extraction logic radi

---

### Problem: Bot još uvijek "brblja"
**Simptom**: Bot odgovara tekstom umjesto tool_call

**Debug**:
```bash
grep "best similarity:" logs/*.log
```

**Fix**:
- Provjeri da li similarity >= 0.85
- Snizi threshold na 0.80 (testing only)
- Check da li Agent prima ReasoningEngine suggestion

---

### Problem: HTML procjećuje korisniku
**Simptom**: User vidi `<!DOCTYPE html>...`

**Debug**:
```bash
grep "HTML LEAKAGE BLOCKED" logs/*.log
```

**Fix**:
- Provjeri da li APIGateway koristi firewall
- Check detection: `is_html` logic
- Dodaj više HTML patterns ako treba

---

## 📁 Dokumentacija

| File | Što sadrži |
|------|------------|
| `MASTER_PROMPT_V3.1_IMPLEMENTATION.md` | Complete implementation guide |
| `FINAL_STATUS_V3.md` | v3.0 status (ReasoningEngine) |
| `REASONING_ENGINE_INTEGRATION.md` | ReasoningEngine architecture |
| `DEPLOYMENT_CHECKLIST.md` | Detailed deployment steps |
| `SESSION_SUMMARY.md` | Session summary (zero hardcoding) |
| `QUICK_START_V3.1.md` | **THIS FILE** - Quick start |

---

## ✅ Deployment Checklist

- [ ] Cache cleared (`rm -rf .cache`)
- [ ] Worker restarted
- [ ] Startup logs verified
- [ ] Test 1: Registracija (ACTION-OR-SILENCE) ✅
- [ ] Test 2: URL Construction ✅
- [ ] Test 3: HTML Firewall ✅
- [ ] Test 4: Hallucination Detection ✅
- [ ] Test 5: Tool Discovery ✅
- [ ] Metrics monitored (24h)

---

## 🎯 Key Files Modified (v3.1)

| File | Change |
|------|--------|
| `services/tool_contracts.py` | Added `swagger_name` field |
| `services/tool_registry.py` | Extract `swagger_name` from service_url |
| `services/tool_executor.py` | Strict URL formula (`/{swagger_name}/{path}`) |
| `services/api_gateway.py` | Clean error messages (Master Prompt v3.1) |
| `services/reasoning_engine.py` | ACTION-OR-SILENCE threshold (already in v3.0) |
| `services/agent.py` | ACTION-OVER-TALK enforcement (already in v3.0) |

---

## 🚀 Production Ready

**Verzija**: ✅ v3.1

**Features**:
1. ✅ Stroga URL konstrukcija
2. ✅ ACTION-OR-SILENCE imperativ
3. ✅ JSON Enforcement
4. ✅ ReasoningEngine (v3.0)
5. ✅ Zero hardcoding (v3.0)

**Final Command**:
```bash
# Deploy!
rm -rf .cache && docker-compose restart worker
```

**Monitor**:
```bash
# Watch logs
docker-compose logs -f worker

# Check for:
# - "ChadAgent initialized (v3.0 with ReasoningEngine)"
# - "Built URL: /automation/... (swagger_name=automation)"
# - "ACTION-OVER-TALK: Similarity 0.87 >= 0.85"
# - "HTML LEAKAGE BLOCKED" (if auth errors)
```

---

**Good luck! 🚀**

**Questions?** Check `MASTER_PROMPT_V3.1_IMPLEMENTATION.md` for details.

---

**Last Updated**: 2025-12-21 15:15
**Version**: v3.1 Quick Start Guide
