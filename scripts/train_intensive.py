"""
INTENSIVE TRAINING - Add 50+ examples for failing categories
Focus on: availability, booking, damage
"""
import json
import os

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

with open('data/training_queries.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

print(f"Current examples: {len(data['examples'])}")

# =============================================================================
# AVAILABILITY - get_AvailableVehicles (currently 0%)
# =============================================================================
availability_examples = [
    # Basic availability queries
    "ima li slobodnih vozila",
    "koja vozila su slobodna",
    "koja vozila su dostupna",
    "slobodna vozila",
    "dostupna vozila",
    "slobodni auti",
    "dostupni auti",
    "ima li auta",
    "ima li vozila",
    # With time
    "slobodna vozila za sutra",
    "slobodna vozila za vikend",
    "slobodna vozila za ponedjeljak",
    "dostupna vozila za sljedeci tjedan",
    "koja vozila su slobodna sutra",
    "koja vozila su dostupna ovaj tjedan",
    "ima li slobodnih vozila za danas",
    "ima li vozila za sutra",
    # Questions
    "koje vozilo mogu uzeti",
    "koji auto je slobodan",
    "koji auti su na raspolaganju",
    "sto je slobodno",
    "sto mogu rezervirati",
    # Check/verify
    "provjeri slobodna vozila",
    "provjeri dostupnost",
    "provjeri dostupnost vozila",
    "pogledaj slobodna vozila",
    "pokazi slobodna vozila",
    "prikazi dostupna vozila",
    # Informal
    "ima nesto slobodno",
    "ima li ista slobodno",
    "di ima slobodnih auta",
    "ima kakvo vozilo slobodno",
]

# =============================================================================
# BOOKING / CALENDAR - get_VehicleCalendar (currently 0%)
# =============================================================================
booking_examples = [
    # My bookings
    "moje rezervacije",
    "moji bookings",
    "moje booking",
    "moja rezervacija",
    "rezervacije koje imam",
    "koje rezervacije imam",
    "imam li rezervaciju",
    "imam li booking",
    # Show bookings
    "pokazi moje rezervacije",
    "prikazi moje rezervacije",
    "pokazi rezervacije",
    "prikazi sve rezervacije",
    "daj mi moje rezervacije",
    "daj mi listu rezervacija",
    # When questions
    "kad imam rezervaciju",
    "kada imam rezervaciju",
    "kad je moja rezervacija",
    "kada je moj booking",
    "za kada imam rezervirano",
    "kad sam rezervirao",
    "kada sam rezervirao vozilo",
    # Calendar
    "kalendar vozila",
    "kalendar rezervacija",
    "raspored vozila",
    "raspored rezervacija",
    "tko ima rezervaciju",
    "tko je rezervirao",
    # Informal
    "mos mi rec kad imam rezervaciju",
    "reci mi moje rezervacije",
    "pokazi mi bookinge",
    "pokazi moje bookinge",
    "di su moje rezervacije",
    "koje bookinge imam",
]

# =============================================================================
# DAMAGE / CASE - post_AddCase (currently 40%)
# =============================================================================
damage_examples = [
    # Report damage
    "prijavi stetu",
    "prijavi kvar",
    "prijavi ostecenje",
    "prijavi problem",
    "prijava stete",
    "prijava kvara",
    # Accidents
    "udario sam",
    "udario sam u stup",
    "udario sam u zid",
    "udario sam u auto",
    "imao sam sudar",
    "dogodila se nesreca",
    "dogodio se sudar",
    "imao sam nesrecu",
    # Scratches
    "ogrebao sam auto",
    "ogrebao sam vozilo",
    "imam ogrebotinu",
    "netko je ogrebao auto",
    "ogrebotina na autu",
    "ogreban auto",
    # Damage description
    "imam stetu",
    "imam stetu na vozilu",
    "imam kvar",
    "imam kvar na vozilu",
    "imam ostecenje",
    "osteceno vozilo",
    "ostecen auto",
    # Problems
    "problem s motorom",
    "problem s autom",
    "problem s vozilom",
    "auto ne radi",
    "vozilo ne radi",
    "motor ne radi",
    "ne pali",
    "ne starta",
    "pokvario se",
    "pokvarilo se vozilo",
    # Create case
    "otvori slucaj",
    "dodaj slucaj",
    "novi slucaj",
    "kreiraj slucaj",
    "otvori slucaj za kvar",
    "otvori slucaj za stetu",
]

# =============================================================================
# Create training examples
# =============================================================================
new_examples = []

# Availability -> get_AvailableVehicles
for query in availability_examples:
    new_examples.append({
        "query": query,
        "intent": "CHECK_AVAILABILITY",
        "primary_tool": "get_AvailableVehicles",
        "alternative_tools": [],
        "extract_fields": ["FullVehicleName", "LicencePlate", "Id"],
        "response_template": None,
        "category": "vehicle_calendar"
    })

# Booking -> get_VehicleCalendar
for query in booking_examples:
    new_examples.append({
        "query": query,
        "intent": "GET_BOOKINGS",
        "primary_tool": "get_VehicleCalendar",
        "alternative_tools": [],
        "extract_fields": ["FromTime", "ToTime", "VehicleName"],
        "response_template": None,
        "category": "booking_management"
    })

# Damage -> post_AddCase
for query in damage_examples:
    new_examples.append({
        "query": query,
        "intent": "REPORT_DAMAGE",
        "primary_tool": "post_AddCase",
        "alternative_tools": [],
        "extract_fields": ["Id", "CaseNumber"],
        "response_template": None,
        "category": "case_management"
    })

# Add to training data
data['examples'].extend(new_examples)

# Save
with open('data/training_queries.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"\nAdded {len(new_examples)} intensive training examples:")
print(f"  - Availability (get_AvailableVehicles): {len(availability_examples)}")
print(f"  - Booking (get_VehicleCalendar): {len(booking_examples)}")
print(f"  - Damage (post_AddCase): {len(damage_examples)}")
print(f"\nTotal examples now: {len(data['examples'])}")
