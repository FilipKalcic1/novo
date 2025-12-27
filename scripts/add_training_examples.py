"""Add 20 new training examples to training_queries.json"""
import json
import os

# Change to project root
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load existing data
with open('data/training_queries.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

print(f"Current examples: {len(data['examples'])}")

# 20 NEW training examples from our tests
new_examples = [
    # === AVAILABILITY ===
    {
        "query": "ima li slobodnih vozila za sutra",
        "intent": "CHECK_AVAILABILITY",
        "primary_tool": "get_AvailableVehicles",
        "alternative_tools": ["get_VehicleCalendar"],
        "extract_fields": ["FullVehicleName", "LicencePlate", "Id"],
        "response_template": None,
        "category": "vehicle_calendar"
    },
    {
        "query": "koji auti su dostupni od ponedjeljka do srijede",
        "intent": "CHECK_AVAILABILITY_RANGE",
        "primary_tool": "get_AvailableVehicles",
        "alternative_tools": [],
        "extract_fields": ["FullVehicleName", "LicencePlate"],
        "response_template": None,
        "category": "vehicle_calendar"
    },

    # === DAMAGE/CASE REPORTING ===
    {
        "query": "udario sam u stup na parkiralistu",
        "intent": "REPORT_DAMAGE",
        "primary_tool": "post_AddCase",
        "alternative_tools": ["post_Cases"],
        "extract_fields": ["Id", "CaseNumber"],
        "response_template": None,
        "category": "case_management"
    },
    {
        "query": "netko mi je ogrebao auto dok je bio parkiran",
        "intent": "REPORT_DAMAGE",
        "primary_tool": "post_AddCase",
        "alternative_tools": [],
        "extract_fields": ["Id", "CaseNumber"],
        "response_template": None,
        "category": "case_management"
    },
    {
        "query": "imam problema s motorom, cuje se cudna buka",
        "intent": "REPORT_MALFUNCTION",
        "primary_tool": "post_AddCase",
        "alternative_tools": [],
        "extract_fields": ["Id", "CaseNumber"],
        "response_template": None,
        "category": "case_management"
    },

    # === SERVICE/MAINTENANCE ===
    {
        "query": "koliko jos mogu voziti do servisa",
        "intent": "GET_SERVICE_STATUS",
        "primary_tool": "get_MasterData",
        "alternative_tools": [],
        "extract_fields": ["ServiceMileage", "NextServiceMileage", "LastMileage"],
        "response_template": None,
        "category": "vehicle_info"
    },
    {
        "query": "kad je zadnji put auto bio na servisu",
        "intent": "GET_LAST_SERVICE",
        "primary_tool": "get_MasterData",
        "alternative_tools": [],
        "extract_fields": ["LastServiceDate", "LastServiceMileage"],
        "response_template": None,
        "category": "vehicle_info"
    },

    # === MILEAGE ===
    {
        "query": "trenutna kilometraza",
        "intent": "GET_MILEAGE",
        "primary_tool": "get_MasterData",
        "alternative_tools": ["get_Vehicles_id"],
        "extract_fields": ["LastMileage", "MileageDate"],
        "response_template": None,
        "category": "vehicle_info"
    },
    {
        "query": "moram upisati kilometre 45000",
        "intent": "UPDATE_MILEAGE",
        "primary_tool": "post_Mileage",
        "alternative_tools": [],
        "extract_fields": ["Id"],
        "response_template": None,
        "category": "mileage_reports"
    },

    # === BOOKING ===
    {
        "query": "trebam auto za poslovni put u Zagreb sljedeci tjedan",
        "intent": "BOOK_VEHICLE",
        "primary_tool": "get_AvailableVehicles",
        "alternative_tools": ["post_VehicleCalendar"],
        "extract_fields": ["FullVehicleName", "LicencePlate"],
        "response_template": None,
        "category": "booking_management"
    },
    {
        "query": "moje rezervacije",
        "intent": "GET_MY_BOOKINGS",
        "primary_tool": "get_VehicleCalendar",
        "alternative_tools": [],
        "extract_fields": ["FromTime", "ToTime", "VehicleName"],
        "response_template": None,
        "category": "booking_management"
    },
    {
        "query": "otkazi moju rezervaciju za petak",
        "intent": "CANCEL_BOOKING",
        "primary_tool": "delete_VehicleCalendar_id",
        "alternative_tools": ["delete_LatestVehicleCalendar_id"],
        "extract_fields": [],
        "response_template": None,
        "category": "booking_management"
    },

    # === VEHICLE INFO ===
    {
        "query": "registracija istice za koliko",
        "intent": "GET_REGISTRATION_EXPIRY",
        "primary_tool": "get_MasterData",
        "alternative_tools": [],
        "extract_fields": ["RegistrationExpirationDate"],
        "response_template": None,
        "category": "vehicle_info"
    },
    {
        "query": "koji je broj tablice od passata",
        "intent": "GET_LICENSE_PLATE",
        "primary_tool": "get_MasterData",
        "alternative_tools": ["get_Vehicles"],
        "extract_fields": ["LicencePlate", "FullVehicleName"],
        "response_template": None,
        "category": "vehicle_info"
    },

    # === MIXED LANGUAGE ===
    {
        "query": "check availability za sutra",
        "intent": "CHECK_AVAILABILITY",
        "primary_tool": "get_AvailableVehicles",
        "alternative_tools": [],
        "extract_fields": ["FullVehicleName", "LicencePlate"],
        "response_template": None,
        "category": "vehicle_calendar"
    },

    # === INFORMAL CROATIAN ===
    {
        "query": "di je moj auto",
        "intent": "GET_MY_VEHICLE",
        "primary_tool": "get_MasterData",
        "alternative_tools": [],
        "extract_fields": ["FullVehicleName", "LicencePlate", "Location"],
        "response_template": None,
        "category": "vehicle_info"
    },
    {
        "query": "mos mi rec kad imam rezervaciju",
        "intent": "GET_MY_BOOKINGS",
        "primary_tool": "get_VehicleCalendar",
        "alternative_tools": [],
        "extract_fields": ["FromTime", "ToTime", "VehicleName"],
        "response_template": None,
        "category": "booking_management"
    },
    {
        "query": "reci mi kad mi istice registracija",
        "intent": "GET_REGISTRATION_EXPIRY",
        "primary_tool": "get_MasterData",
        "alternative_tools": [],
        "extract_fields": ["RegistrationExpirationDate"],
        "response_template": None,
        "category": "vehicle_info"
    },

    # === ADDITIONAL VARIATIONS ===
    {
        "query": "slobodna vozila za vikend",
        "intent": "CHECK_AVAILABILITY",
        "primary_tool": "get_AvailableVehicles",
        "alternative_tools": [],
        "extract_fields": ["FullVehicleName", "LicencePlate"],
        "response_template": None,
        "category": "vehicle_calendar"
    },
    {
        "query": "imam stetu na vozilu",
        "intent": "REPORT_DAMAGE",
        "primary_tool": "post_AddCase",
        "alternative_tools": [],
        "extract_fields": ["Id", "CaseNumber"],
        "response_template": None,
        "category": "case_management"
    },
]

# Add new examples
data['examples'].extend(new_examples)

# Save
with open('data/training_queries.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"Added {len(new_examples)} new training examples")
print(f"Total examples now: {len(data['examples'])}")
print("\nNew examples added:")
for ex in new_examples:
    print(f"  - {ex['query'][:50]}")
