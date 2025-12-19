import asyncio
import json
import re
import httpx
from typing import Dict, Any, List, Set
from collections import defaultdict
from openai import AsyncAzureOpenAI
from config import get_settings

settings = get_settings()

client = AsyncAzureOpenAI(
    azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
    api_key=settings.AZURE_OPENAI_API_KEY,
    api_version=settings.AZURE_OPENAI_API_VERSION
)

# --- 1. NAPREDNI PARSER SWAGGERA (RESOLVER) ---

def resolve_ref(schema: Dict, full_spec: Dict) -> Dict:
    """Rekurzivno rješava $ref reference (nalazi skrivene DTO objekte)."""
    if "$ref" in schema:
        ref_path = schema["$ref"].replace("#/", "").split("/")
        ref_def = full_spec
        for part in ref_path:
            ref_def = ref_def.get(part, {})
        return resolve_ref(ref_def, full_spec)
    
    if "allOf" in schema:
        combined = {}
        for sub in schema["allOf"]:
            combined.update(resolve_ref(sub, full_spec))
        return combined
        
    if "properties" in schema:
        # Vraćamo samo properties jer nas to zanima
        return schema["properties"]
        
    return schema

def extract_deep_params(op: Dict, full_spec: Dict) -> Dict[str, Any]:
    """Izvlači SVE parametre: Query, Path i Body (Deep Dive)."""
    params = {}
    
    # 1. Obični parametri (query, path)
    for p in op.get("parameters", []):
        if "$ref" in p:
            p = resolve_ref(p, full_spec)
        
        name = p.get("name")
        if name:
            params[name] = {
                "desc": p.get("description", ""),
                "type": p.get("type", "string"),
                "enum": p.get("enum", [])
            }

    # 2. Body parametri (Ovdje je zlato!)
    # Provjera za OpenAPI 2.0 (parameters -> in: body)
    for p in op.get("parameters", []):
        if p.get("in") == "body" and "schema" in p:
            schema = resolve_ref(p["schema"], full_spec)
            # Schema može biti dict properties-a
            if isinstance(schema, dict):
                for prop_name, prop_val in schema.items():
                    # Ignoriramo nested objekte dublje razine radi uštede tokena, 
                    # fokusiramo se na top-level fields DTO-a
                    if isinstance(prop_val, dict):
                        params[prop_name] = {
                            "desc": prop_val.get("description", ""),
                            "type": prop_val.get("type", "unknown"),
                            "enum": prop_val.get("enum", [])
                        }

    # Provjera za OpenAPI 3.0 (requestBody)
    if "requestBody" in op:
        content = op["requestBody"].get("content", {})
        json_content = content.get("application/json", {})
        if "schema" in json_content:
            schema = resolve_ref(json_content["schema"], full_spec)
            if isinstance(schema, dict):
                for prop_name, prop_val in schema.items():
                     if isinstance(prop_val, dict):
                        params[prop_name] = {
                            "desc": prop_val.get("description", ""),
                            "type": prop_val.get("type", "unknown"),
                            "enum": prop_val.get("enum", [])
                        }
    return params

# --- 2. LOGIKA GRUPIRANJA ---

def categorize_tool(path: str) -> str:
    """Određuje grupu na temelju URL-a."""
    path = path.lower()
    if "calendar" in path: return "calendar"
    if "expense" in path: return "expense"
    if "ticket" in path or "case" in path: return "case"
    if "vehicle" in path: return "vehicle"
    if "person" in path or "people" in path: return "people"
    if "document" in path: return "document"
    if "import" in path: return "import"
    return "other"

# --- 3. AI ANALIZA GRUPA ---

SYSTEM_PROMPT = """
You are a Lead API Architect. Your goal is to simplify configuration by finding "Static Defaults" for GROUPS of tools.

INPUT: A JSON list of API tools belonging to a specific category (e.g., "Calendar Tools"). Each tool lists its parameters (extracted from Body/Query).

YOUR TASK:
Identify parameters that appear repeatedly across these tools and usually require the SAME technical value for a Bot/Automation context.

RULES FOR "STATIC DEFAULTS":
1. MUST be technical flags (e.g., Source, AssigneeType, IsActive, Status).
2. MUST NOT be user data (e.g., Dates, Names, IDs, Descriptions).
3. Value must be constant (e.g., Source='Bot', AssigneeType=1).

OUTPUT FORMAT (JSON):
Return a single JSON object representing the defaults for this GROUP.
Example:
{
  "Source": "Bot",
  "AssigneeType": 1,
  "IsActive": true
}

If a parameter varies (sometimes true, sometimes false), DO NOT include it.
If no defaults found, return {}.
"""

async def analyze_group(group_name: str, tools_summary: List[Dict]) -> Dict:
    if not tools_summary: return {}
    
    print(f"🧠 Analyzing Group: {group_name} ({len(tools_summary)} tools)...")

    # Optimizacija: Šaljemo samo imena parametara i njihove opise/enume
    # Ne šaljemo cijeli payload da ne probijemo kontekst
    prompt_data = json.dumps(tools_summary, indent=2)

    # Ako je payload prevelik, režemo ga (uzimamo prvih 20 reprezentativnih alata)
    if len(prompt_data) > 20000: 
        prompt_data = json.dumps(tools_summary[:20], indent=2)

    try:
        response = await client.chat.completions.create(
            model=settings.AZURE_OPENAI_DEPLOYMENT_NAME,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"GROUP: {group_name}\nTOOLS DATA:\n{prompt_data}"}
            ],
            temperature=0.0,
            max_tokens=500
        )
        
        content = response.choices[0].message.content.strip()
        if "```" in content: content = content.split("```")[1].replace("json", "").strip()
        
        return json.loads(content)
    except Exception as e:
        print(f"❌ Error analyzing group {group_name}: {e}")
        return {}

# --- 4. GLAVNA IZVEDBA ---

async def main():
    print("🚀 Starting MASTER Analysis (Deep Dive & Grouping)...")
    
    urls = [
        f"{settings.MOBILITY_API_URL}/automation/swagger/v1.0.0/swagger.json",
        f"{settings.MOBILITY_API_URL}/vehiclemgt/swagger/v2.0.0-alpha/swagger.json",
        f"{settings.MOBILITY_API_URL}/tenantmgt/swagger/v2.0.0-alpha/swagger.json"
    ]

    # Spremnik za alate po grupama
    groups: Dict[str, List[Dict]] = defaultdict(list)
    
    async with httpx.AsyncClient(verify=False) as client:
        for url in urls:
            print(f"📥 Fetching & Parsing: {url}")
            try:
                resp = await client.get(url, timeout=30)
                full_spec = resp.json()
            except Exception as e:
                print(f"⚠️ Failed to load {url}: {e}")
                continue

            paths = full_spec.get('paths', {})
            for path, methods in paths.items():
                for method, op in methods.items():
                    if method.lower() not in ['post', 'put', 'patch']: continue
                    
                    # 1. Ekstrakcija dubokih parametara
                    deep_params = extract_deep_params(op, full_spec)
                    
                    # 2. Filtriranje samo "sumnjivih" parametara za AI
                    # (Šaljemo AI-u samo one koji liče na konfiguraciju, da mu olakšamo fokus)
                    suspicious_params = {}
                    keywords = ["type", "status", "source", "active", "mode", "notification", "priority", "origin", "category"]
                    
                    for pname, pdata in deep_params.items():
                        if any(k in pname.lower() for k in keywords):
                            suspicious_params[pname] = pdata

                    if not suspicious_params:
                        continue

                    # 3. Kategorizacija
                    category = categorize_tool(path)
                    
                    groups[category].append({
                        "tool": f"{method.upper()} {path}",
                        "potential_configs": suspicious_params
                    })

    # Analiza po grupama
    final_defaults = {}
    
    for group_name, tools in groups.items():
        if not tools: continue
        
        defaults = await analyze_group(group_name, tools)
        if defaults:
            final_defaults[group_name] = defaults
            print(f"✅ GROUP '{group_name}' RESOLVED: {defaults}")

    # Ispis finalnog JSON-a
    print("\n════════════ FINAL GENERATED CONFIGURATION ════════════")
    print(json.dumps(final_defaults, indent=4))
    
    # Spremanje u file
    with open("tool_registry_defaults.json", "w") as f:
        json.dump(final_defaults, f, indent=4)

if __name__ == "__main__":
    asyncio.run(main())