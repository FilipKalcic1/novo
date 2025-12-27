"""Add more training examples to improve accuracy."""
import json
import os

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

with open('data/training_queries.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

print(f"Current examples: {len(data['examples'])}")

# MORE training examples to fix accuracy issues
new_examples = [
    # === FIX BOOKING (0% accuracy) - need get_VehicleCalendar ===
    {"query": "moje rezervacije", "intent": "GET_MY_BOOKINGS", "primary_tool": "get_VehicleCalendar", "alternative_tools": [], "extract_fields": ["FromTime", "ToTime", "VehicleName"], "response_template": None, "category": "booking_management"},
    {"query": "kad imam rezervaciju", "intent": "GET_MY_BOOKINGS", "primary_tool": "get_VehicleCalendar", "alternative_tools": [], "extract_fields": ["FromTime", "ToTime"], "response_template": None, "category": "booking_management"},
    {"query": "pokazi moje bookinge", "intent": "GET_MY_BOOKINGS", "primary_tool": "get_VehicleCalendar", "alternative_tools": [], "extract_fields": ["FromTime", "ToTime"], "response_template": None, "category": "booking_management"},
    {"query": "prikazi sve moje rezervacije", "intent": "GET_MY_BOOKINGS", "primary_tool": "get_VehicleCalendar", "alternative_tools": [], "extract_fields": ["FromTime", "ToTime"], "response_template": None, "category": "booking_management"},
    {"query": "koje rezervacije imam", "intent": "GET_MY_BOOKINGS", "primary_tool": "get_VehicleCalendar", "alternative_tools": [], "extract_fields": ["FromTime", "ToTime"], "response_template": None, "category": "booking_management"},
    {"query": "kada sam rezervirao vozilo", "intent": "GET_MY_BOOKINGS", "primary_tool": "get_VehicleCalendar", "alternative_tools": [], "extract_fields": ["FromTime", "ToTime"], "response_template": None, "category": "booking_management"},
    {"query": "kalendar vozila", "intent": "GET_VEHICLE_CALENDAR", "primary_tool": "get_VehicleCalendar", "alternative_tools": [], "extract_fields": [], "response_template": None, "category": "booking_management"},
    {"query": "rezervacije za vozilo", "intent": "GET_VEHICLE_CALENDAR", "primary_tool": "get_VehicleCalendar", "alternative_tools": [], "extract_fields": [], "response_template": None, "category": "booking_management"},

    # === FIX DAMAGE (20% accuracy) - need post_AddCase not post_Cases ===
    {"query": "prijavi stetu", "intent": "REPORT_DAMAGE", "primary_tool": "post_AddCase", "alternative_tools": [], "extract_fields": ["Id", "CaseNumber"], "response_template": None, "category": "case_management"},
    {"query": "prijavi kvar", "intent": "REPORT_DAMAGE", "primary_tool": "post_AddCase", "alternative_tools": [], "extract_fields": ["Id", "CaseNumber"], "response_template": None, "category": "case_management"},
    {"query": "prijavi ostecenje", "intent": "REPORT_DAMAGE", "primary_tool": "post_AddCase", "alternative_tools": [], "extract_fields": ["Id", "CaseNumber"], "response_template": None, "category": "case_management"},
    {"query": "udario sam u stup", "intent": "REPORT_DAMAGE", "primary_tool": "post_AddCase", "alternative_tools": [], "extract_fields": ["Id", "CaseNumber"], "response_template": None, "category": "case_management"},
    {"query": "ogrebao sam auto", "intent": "REPORT_DAMAGE", "primary_tool": "post_AddCase", "alternative_tools": [], "extract_fields": ["Id", "CaseNumber"], "response_template": None, "category": "case_management"},
    {"query": "problem s motorom", "intent": "REPORT_DAMAGE", "primary_tool": "post_AddCase", "alternative_tools": [], "extract_fields": ["Id", "CaseNumber"], "response_template": None, "category": "case_management"},
    {"query": "auto ne radi", "intent": "REPORT_DAMAGE", "primary_tool": "post_AddCase", "alternative_tools": [], "extract_fields": ["Id", "CaseNumber"], "response_template": None, "category": "case_management"},
    {"query": "imam problem s autom", "intent": "REPORT_DAMAGE", "primary_tool": "post_AddCase", "alternative_tools": [], "extract_fields": ["Id", "CaseNumber"], "response_template": None, "category": "case_management"},
    {"query": "dodaj novi slucaj stete", "intent": "REPORT_DAMAGE", "primary_tool": "post_AddCase", "alternative_tools": [], "extract_fields": ["Id", "CaseNumber"], "response_template": None, "category": "case_management"},
    {"query": "otvori slucaj za kvar", "intent": "REPORT_DAMAGE", "primary_tool": "post_AddCase", "alternative_tools": [], "extract_fields": ["Id", "CaseNumber"], "response_template": None, "category": "case_management"},

    # === FIX MILEAGE (0% accuracy) - need post_Mileage ===
    {"query": "upisi kilometre", "intent": "UPDATE_MILEAGE", "primary_tool": "post_Mileage", "alternative_tools": ["post_AddMileage"], "extract_fields": ["Id"], "response_template": None, "category": "mileage_reports"},
    {"query": "upisi kilometre 45000", "intent": "UPDATE_MILEAGE", "primary_tool": "post_Mileage", "alternative_tools": ["post_AddMileage"], "extract_fields": ["Id"], "response_template": None, "category": "mileage_reports"},
    {"query": "unesi kilometrazu", "intent": "UPDATE_MILEAGE", "primary_tool": "post_Mileage", "alternative_tools": ["post_AddMileage"], "extract_fields": ["Id"], "response_template": None, "category": "mileage_reports"},
    {"query": "dodaj kilometre", "intent": "UPDATE_MILEAGE", "primary_tool": "post_Mileage", "alternative_tools": ["post_AddMileage"], "extract_fields": ["Id"], "response_template": None, "category": "mileage_reports"},
    {"query": "azuriraj kilometrazu", "intent": "UPDATE_MILEAGE", "primary_tool": "post_Mileage", "alternative_tools": [], "extract_fields": ["Id"], "response_template": None, "category": "mileage_reports"},
    {"query": "nova kilometraza", "intent": "UPDATE_MILEAGE", "primary_tool": "post_Mileage", "alternative_tools": [], "extract_fields": ["Id"], "response_template": None, "category": "mileage_reports"},
    {"query": "zapisati kilometre", "intent": "UPDATE_MILEAGE", "primary_tool": "post_Mileage", "alternative_tools": [], "extract_fields": ["Id"], "response_template": None, "category": "mileage_reports"},

    # === FIX AVAILABILITY ===
    {"query": "provjeri dostupnost vozila", "intent": "CHECK_AVAILABILITY", "primary_tool": "get_AvailableVehicles", "alternative_tools": [], "extract_fields": ["FullVehicleName", "LicencePlate"], "response_template": None, "category": "vehicle_calendar"},
    {"query": "dostupna vozila", "intent": "CHECK_AVAILABILITY", "primary_tool": "get_AvailableVehicles", "alternative_tools": [], "extract_fields": ["FullVehicleName", "LicencePlate"], "response_template": None, "category": "vehicle_calendar"},
    {"query": "ima li auta", "intent": "CHECK_AVAILABILITY", "primary_tool": "get_AvailableVehicles", "alternative_tools": [], "extract_fields": ["FullVehicleName", "LicencePlate"], "response_template": None, "category": "vehicle_calendar"},

    # === FIX SERVICE ===
    {"query": "koliko do servisa", "intent": "GET_SERVICE_STATUS", "primary_tool": "get_MasterData", "alternative_tools": [], "extract_fields": ["ServiceMileage", "NextServiceMileage"], "response_template": None, "category": "vehicle_info"},
    {"query": "kad je servis", "intent": "GET_SERVICE_STATUS", "primary_tool": "get_MasterData", "alternative_tools": [], "extract_fields": ["ServiceMileage"], "response_template": None, "category": "vehicle_info"},
    {"query": "sljedeci servis", "intent": "GET_SERVICE_STATUS", "primary_tool": "get_MasterData", "alternative_tools": [], "extract_fields": ["ServiceMileage"], "response_template": None, "category": "vehicle_info"},
]

# Add new examples
data['examples'].extend(new_examples)

# Save
with open('data/training_queries.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"Added {len(new_examples)} new training examples")
print(f"Total examples now: {len(data['examples'])}")

# Summary
print("\nAdded examples by category:")
from collections import Counter
cats = Counter(ex['category'] for ex in new_examples)
for cat, count in cats.most_common():
    print(f"  {cat}: +{count} examples")
