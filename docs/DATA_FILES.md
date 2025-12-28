# Podatkovni fajlovi

## Pregled

Sustav koristi 2 glavna JSON fajla za "ucenje" koji tool koristiti.

```
data/
  training_queries.json    <- Primjeri upita -> tool

config/
  tool_categories.json     <- Kategorizacija alata
```

## 1. training_queries.json

**Lokacija:** `data/training_queries.json`

**Svrha:** Primjeri upita i koji tool ih rjesava

**Struktura:**
```json
{
  "examples": [
    {
      "query": "Udario sam u stup",
      "primary_tool": "post_AddCase",
      "alternative_tools": ["get_Cases"],
      "category": "case_management",
      "intent": "report_damage"
    },
    {
      "query": "Kolika je moja kilometraza",
      "primary_tool": "get_MasterData",
      "category": "vehicle_info",
      "intent": "read_mileage"
    }
  ]
}
```

**Kako se koristi:**

1. **Few-shot primjeri za LLM:**
   - Unified router trazi primjere s istim keyword-om
   - Daje ih LLM-u kao kontekst
   - LLM vidi: "slicni upiti koristili su ovaj tool"

2. **Training/evaluacija:**
   - Testiramo tocnost routinga na ovim primjerima

**Statistika:**
- 2,241 primjera
- 500 razlicitih alata pokriveno
- 55% ukupne pokrivenosti (ali 92% kriticnih alata)

**Kako dodati novi primjer:**
```json
{
  "query": "Prikazi mi sve troskove",
  "primary_tool": "get_Expenses",
  "category": "expenses",
  "intent": "list_expenses"
}
```

---

## 2. tool_categories.json

**Lokacija:** `config/tool_categories.json`

**Svrha:** Grupiranje alata u kategorije s opisima i keywords

**Struktura:**
```json
{
  "categories": {
    "case_management": {
      "name": "case_management",
      "description_hr": "Upravljanje slucajevima/stetama",
      "description_en": "Case management",
      "keywords_hr": ["steta", "kvar", "prijava"],
      "keywords_en": ["damage", "case", "report"],
      "typical_intents": ["Add a new case", "Delete cases"],
      "tools": [
        "post_AddCase",
        "get_Cases",
        "delete_Cases_id"
      ]
    },
    "mileage_tracking": {
      "name": "mileage_tracking",
      "description_hr": "Pracenje kilometraze",
      "keywords_hr": ["kilometri", "km", "unos"],
      "tools": [
        "post_AddMileage",
        "get_MileageReports",
        "get_LatestMileageReports"
      ]
    }
  }
}
```

**Kako se koristi:**

1. **Intelligent router:**
   - Keyword matching po kategorijama
   - Ako query sadrzi "steta" -> case_management kategorija
   - Dohvati tools iz te kategorije

2. **Embedding matching:**
   - Svaka kategorija ima embedding vektor
   - Query embedding usporeduje se s category embeddings
   - Najslicnija kategorija -> njeni tools

**Statistika:**
- 909 alata ukupno
- Organizirano u kategorije po funkcionalnosti

---

## Odnos izmedju fajlova

```
Korisnik: "Imam stetu na autu"
              |
              v
+---------------------------+
| training_queries.json     |
| Trazi primjere sa "steta" |
| -> "Prijavi stetu" -> post_AddCase
+---------------------------+
              |
              v
+---------------------------+
| tool_categories.json      |
| Kategorija: case_management
| Tools: [post_AddCase, ...]
+---------------------------+
              |
              v
        LLM odlucuje: post_AddCase
```

## Kada sto azurirati?

| Situacija | Azuriraj |
|-----------|----------|
| Novi tip upita | training_queries.json |
| Novi API endpoint | tool_categories.json |
| Bot ne prepoznaje upit | Dodaj primjer u training |
| Krivi tool za upit | Provjeri/dodaj primjer |

## Vazno

- Training primjeri NISU jedini nacin pronalazenja alata
- LLM vidi opise alata iz PRIMARY_TOOLS (hardkodirano)
- Embeddings mogu naci slicnu kategoriju cak bez primjera
- Primjeri POMAZU ali sustav radi i bez njih za nove upite
