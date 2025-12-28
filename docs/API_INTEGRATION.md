# API Integracija

## Pregled

Bot komunicira s MobilityOne API-jem koristeci OAuth2 autentifikaciju.

## Autentifikacija

### Token flow

```
+------------------+
|   TokenManager   |
+------------------+
         |
         | POST /sso/connect/token
         | client_id=m1AI
         | client_secret=***
         | grant_type=client_credentials
         v
+------------------+
|   MobilityOne    |
|      SSO         |
+------------------+
         |
         | access_token (JWT)
         v
+------------------+
|   Redis Cache    |
|  (7200s TTL)     |
+------------------+
```

### Konfiguracija (.env)

```bash
MOBILITY_AUTH_URL=https://dev-k1.mobilityone.io/sso/connect/token
MOBILITY_CLIENT_ID=m1AI
MOBILITY_CLIENT_SECRET=***
# MOBILITY_SCOPE - NE KORISTITI, uzrokuje invalid_scope error
MOBILITY_AUDIENCE=none
```

### Scope-ovi

m1AI client automatski dobiva ove scope-ove:
```
- add-mileage        (unos km)
- AvailableVehicles  (dostupna vozila)
- get-master-data    (podaci o vozilu)
- get-person-data    (podaci o osobi)
- Persons            (osobe)
- VehicleCalendar    (rezervacije)
- vehicles           (vozila)
```

**VAZNO:** Scope-ovi koje m1AI NEMA:
```
- add-case           (prijava stete) <- TREBA ZATRAZITI
- Expenses           (troskovi) <- TREBA ZATRAZITI
```

Ako API vrati 403, znaci da nedostaje scope.

---

## API Pozivi

### Struktura poziva

```
+------------------+
|  Tool Executor   |
|  - Nadji tool    |
|  - Pripremi params
|  - Dodaj PersonId|
+------------------+
         |
         v
+------------------+
|   API Gateway    |
|  - Dodaj token   |
|  - Dodaj tenant  |
|  - HTTP request  |
+------------------+
         |
         v
+------------------+
|  MobilityOne API |
|  /automation/*   |
+------------------+
```

### Headers

Svaki request sadrzi:
```
Authorization: Bearer <access_token>
X-Tenant-Id: <tenant_id>
Content-Type: application/json
Accept: application/json
```

### Primjer poziva

```python
# Tool: get_MasterData
# Query: "Koja je moja registracija?"

GET /automation/MasterData?personId=abc-123
Headers:
  Authorization: Bearer eyJ...
  X-Tenant-Id: f0392a18-1e88-4861-9b29-63574aeefcb3
```

---

## Swagger

**URL:** `https://dev-k1.mobilityone.io/automation/swagger/v1.0.0/swagger.json`

Bot cita swagger pri pokretanju i:
1. Parsira sve endpointe
2. Sprema u ToolRegistry
3. Koristi opise za LLM routing

---

## Error handling

### HTTP Status kodovi

| Kod | Znacenje | Akcija |
|-----|----------|--------|
| 200 | OK | Vrati podatke korisniku |
| 400 | Bad Request | Krivi parametri |
| 401 | Unauthorized | Osvjezi token i retry |
| 403 | Forbidden | Nedostaje scope - obavijesti korisnika |
| 404 | Not Found | Resurs ne postoji |
| 405 | Method Not Allowed | Krivi HTTP method |
| 500 | Server Error | Sistemska greska |

### 403 Debugging

Ako vidis 403 error:
1. Provjeri koji endpoint
2. Provjeri token scope-ove (dekodiraj JWT)
3. Zatrazi scope od MobilityOne tima

Dekodiranje JWT-a:
```python
import base64, json
token = "eyJ..."
payload = token.split('.')[1]
payload += '=' * (4 - len(payload) % 4)
data = json.loads(base64.urlsafe_b64decode(payload))
print(data['scope'])  # Lista scope-ova
```

---

## Tenant

Bot podrzava multi-tenant:
- Tenant ID se salje u svakom requestu
- Razliciti tenant-i imaju razlicite podatke
- Konfiguracija: `MOBILITY_TENANT_ID` u .env

---

## Retry logika

```python
# api_gateway.py
max_retries = 3
retry_on = [401, 429, 500, 502, 503, 504]

# 401 -> osvjezi token -> retry
# 429 -> rate limit -> wait -> retry
# 5xx -> server error -> retry with backoff
```
