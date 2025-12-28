# MobilityOne Bot - Arhitektura sustava

## Pregled

Bot koji omogucuje korisnicima upravljanje fleet management sustavom putem WhatsApp-a.
Koristi LLM (GPT-4o-mini) za razumijevanje upita i odabir pravog API alata.

## Glavni komponenti

```
WhatsApp (Infobip)
       |
       v
+------------------+
|  FastAPI Server  |  (main.py, routers/)
+------------------+
       |
       v
+------------------+
|     Engine       |  (services/engine/__init__.py)
|  - Orchestrator  |
|  - Flow Handler  |
+------------------+
       |
       v
+------------------+
|    Routeri       |
|  - unified       |  Glavna LLM odluka
|  - query         |  Pattern matching
|  - intelligent   |  Embeddings + LLM
+------------------+
       |
       v
+------------------+
|  Tool Executor   |  (services/tool_executor.py)
+------------------+
       |
       v
+------------------+
|   API Gateway    |  (services/api_gateway.py)
|  + TokenManager  |
+------------------+
       |
       v
+------------------+
|  MobilityOne API |
+------------------+
```

## Tok obrade poruke

1. **Webhook** prima WhatsApp poruku
2. **Engine** dohvaca stanje razgovora (je li user u flow-u?)
3. **Unified Router** odlucuje: greeting? flow? simple_api?
4. **Tool Executor** priprema parametre i poziva API
5. **API Gateway** autentificira i salje request
6. **Response Formatter** formatira odgovor za korisnika

## Kljucni fajlovi

| Fajl | Svrha |
|------|-------|
| `services/engine/__init__.py` | Glavni orchestrator - sve prolazi kroz njega |
| `services/unified_router.py` | LLM routing odluke |
| `services/tool_executor.py` | Izvrsavanje API poziva |
| `services/api_gateway.py` | HTTP klijent + auth |
| `services/token_manager.py` | OAuth2 token management |
| `services/conversation_manager.py` | Stanje razgovora (Redis) |

## Flow-ovi (visestepene operacije)

Bot podrzava 3 tipa flow-ova:

1. **Booking** - rezervacija vozila
   - Prikupi: datum od, datum do, vozilo
   - Potvrdi i kreiraj rezervaciju

2. **Mileage** - unos kilometraze
   - Prikupi: vrijednost km
   - Potvrdi i unesi

3. **Case** - prijava stete/kvara
   - Prikupi: opis problema
   - Kreiraj case

## Baza podataka

- **PostgreSQL** - trajni podaci (user mappings, tenant info)
- **Redis** - session state, token cache, conversation state

## Autentifikacija

Bot koristi OAuth2 Client Credentials flow:
```
POST /sso/connect/token
  client_id=m1AI
  client_secret=***
  grant_type=client_credentials
```

Token automatski dobiva scope-ove koje m1AI client ima definirane.
Vidi `docs/API_INTEGRATION.md` za detalje.
