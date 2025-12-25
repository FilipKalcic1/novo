"""
Booking API Contract Constants
Version: 1.0

Centralized booking-related constants to avoid magic numbers
scattered across the codebase.

These values come from the MobilityOne API specification.
"""


class AssigneeType:
    """
    Type of entity assigned to the booking.

    From API spec: VehicleCalendar.AssigneeType
    """
    PERSON = 1
    TEAM = 2


class EntryType:
    """
    Type of calendar entry.

    From API spec: VehicleCalendar.EntryType
    """
    BOOKING = 0
    EVENT = 1
    LEAVE = 2
    MAINTENANCE = 3
    UNAVAILABLE = 4


# Field mapping from canonical context keys to API field names
BOOKING_FIELD_MAPPING = {
    "assigned_to_id": "AssignedToId",
    "vehicle_id": "VehicleId",
    "from_time": "FromTime",
    "to_time": "ToTime",
    "description": "Description",
}
