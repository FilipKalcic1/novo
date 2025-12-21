# 🚀 DEPLOY NOW - Quick Commands

**Status**: ✅ Ready for Deployment
**Critical Fixes**:
1. ✅ Webhook-Worker chain restored
2. ✅ Deep Content Indexing (output_keys in embeddings)
3. ✅ Log noise reduced (SQLAlchemy/httpx to WARNING)

**Expected Impact**: 20s → 1.5s response time + vastly improved tool discovery

---

## 1. Clear Cache (MANDATORY - Embeddings Changed!)

```bash
cd C:\Users\igork\.claude-worktrees\mobility_bot\funny-elion
rm -rf .cache

# OR from Docker:
docker-compose exec worker rm -rf /app/.cache
docker-compose exec api rm -rf /app/.cache
```

**Why?** Embedding format changed to include output_keys. Old cache is invalid.

---

## 2. Rebuild Containers

```bash
docker-compose build
```

---

## 3. Restart Services

```bash
docker-compose down
docker-compose up -d
```

---

## 4. Verify Startup

```bash
docker-compose logs -f worker
```

**Expected Output**:
```
🔄 Cache invalid - fetching Swagger specs...
Fetching: https://dev-k1.mobilityone.io/automation/swagger/...
✅ automation: 450 operations
Fetching: https://dev-k1.mobilityone.io/tenantmgt/swagger/...
✅ tenantmgt: 250 operations
Fetching: https://dev-k1.mobilityone.io/vehiclemgt/swagger/...
✅ vehiclemgt: 209 operations
📦 Loaded 909 tools from Swagger
🔄 Generating embeddings for 909 tools...
✅ Built embeddings for 909 tools
🔍 Verifying cache files...
✅ manifest: swagger_manifest.json (1,234 bytes)
✅ metadata: tool_metadata.json (456,789 bytes)
✅ embeddings: tool_embeddings.json (8,901,234 bytes)
💾 Saved manifest: 3 sources
💾 Saved metadata: 909 tools
💾 Saved embeddings: 909 vectors
Worker started, listening to whatsapp_stream_inbound...
```

**First Startup**: 15-20 seconds (generates fresh cache)
**Subsequent Restarts**: <2 seconds (loads from cache)

---

## 5. Test Message Flow & Discovery

**Test 1 - Mileage Query**:

Send WhatsApp: "Kolika je kilometraža?"

Watch logs:
```bash
docker-compose logs -f worker | grep -E "(Processing:|Top matches:|Built URL:)"
```

**Expected**:
```
📨 Processing: {sender} - Kolika je kilometraža?
🎯 Top matches (expanded): [('0.920', 'get_MasterData'), ...]
Built URL: /automation/MasterData (swagger_name=automation)
✅ Processed in 1.2s
```

**CRITICAL**: `get_MasterData` should have similarity **>0.85** (was 0.706 before fix!)

---

**Test 2 - Registration Query**:

Send WhatsApp: "Prikaži registraciju vozila"

**Expected**:
```
🎯 Top matches: [('0.950', 'get_MasterData'), ...]
Built URL: /automation/MasterData (swagger_name=automation)
```

**Why This Works Now**: Embedding includes "Returns: Registration Number, Mileage, ..." so semantic match is much stronger!

---

## 6. Monitor for Errors

```bash
# Check for HTML leakage blocks
docker-compose logs -f worker | grep "HTML LEAKAGE BLOCKED"

# Check for token refresh
docker-compose logs -f worker | grep "Token acquired"

# Check for webhook reception
docker-compose logs -f api | grep "Message pushed to stream"
```

---

## 🔍 Quick Health Checks

### Check Redis Stream

```bash
docker-compose exec redis redis-cli XINFO STREAM whatsapp_stream_inbound
```

**Expected**: Stream exists with messages

### Check Database Connection

```bash
docker-compose exec worker python -c "from config import get_settings; print(get_settings().DATABASE_URL)"
```

**Expected**: PostgreSQL connection string

### Check Tool Cache

```bash
ls -lh .cache/
```

**Expected**:
```
tool_embeddings.json
tool_metadata.json
swagger_manifest.json
```

---

## ⚠️ Troubleshooting

### Problem: Worker not processing messages

**Check 1**: Webhook pushing to correct stream?
```bash
docker-compose logs api | grep "xadd"
```

**Check 2**: Worker listening to correct stream?
```bash
docker-compose logs worker | grep "whatsapp_stream_inbound"
```

### Problem: Bot still "chatting" instead of executing

**Check**: Similarity scores
```bash
docker-compose logs worker | grep "best similarity:"
```

**Expected**: Similarity >= 0.85 triggers immediate tool call

### Problem: 404 errors on API calls

**Check**: URL construction
```bash
docker-compose logs worker | grep "Built URL:"
```

**Expected**: URLs like `/automation/MasterData` (with swagger_name)

---

## 📊 Success Metrics (First 24 Hours)

Monitor these:

1. **Message Processing Rate**:
   ```bash
   docker-compose logs worker | grep "Processing:" | wc -l
   ```

2. **Action-Over-Talk Triggers**:
   ```bash
   docker-compose logs worker | grep "ACTION-OVER-TALK" | wc -l
   ```

3. **HTML Blocks** (should be low):
   ```bash
   docker-compose logs worker | grep "HTML LEAKAGE BLOCKED" | wc -l
   ```

4. **URL Construction Success**:
   ```bash
   docker-compose logs worker | grep "Built URL:" | grep "swagger_name=" | wc -l
   ```

---

## ✅ Deployment Checklist

- [ ] Cache cleared
- [ ] Containers rebuilt
- [ ] Services restarted
- [ ] Startup logs verified
- [ ] Test message sent
- [ ] Response received
- [ ] Logs monitored (1 hour)
- [ ] Success metrics checked (24 hours)

---

**Ready to deploy! 🚀**

See `SYSTEMATIC_REVIEW_V5.0.md` for complete audit report.
