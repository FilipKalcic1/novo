"""
Centralized Pattern Registry - Single Source of Truth

This module consolidates all regex patterns used across the codebase.
Previously duplicated in:
- ai_orchestrator.py (lines 445-446)
- dependency_resolver.py (lines 145-174)
- tool_registry.py (various locations)

Version: 1.0
"""

import re
from typing import Pattern, Dict, List, Any
from dataclasses import dataclass


@dataclass
class ValuePattern:
    """Pattern for recognizing human-readable values."""
    pattern: Pattern
    param_type: str
    filter_field: str
    description: str


class PatternRegistry:
    """
    Centralized registry for all regex patterns.

    Usage:
        from services.patterns import PatternRegistry

        # Get compiled pattern
        if PatternRegistry.CROATIAN_PLATE.match(text):
            ...

        # Find all UUIDs
        uuids = PatternRegistry.find_uuids(text)

        # Find all license plates
        plates = PatternRegistry.find_plates(text)
    """

    # ═══════════════════════════════════════════════════════════════
    # UUID PATTERNS
    # ═══════════════════════════════════════════════════════════════

    # Standard UUID format: 8-4-4-4-12 hex characters
    UUID_PATTERN_STR = r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}'
    UUID_PATTERN = re.compile(UUID_PATTERN_STR, re.IGNORECASE)

    # UUID with capture group (for extraction)
    UUID_CAPTURE = re.compile(f'({UUID_PATTERN_STR})', re.IGNORECASE)

    # ═══════════════════════════════════════════════════════════════
    # LICENSE PLATE PATTERNS (Croatian)
    # ═══════════════════════════════════════════════════════════════

    # Croatian plates: ZG-1234-AB, ZG 1234 AB, ZG1234AB
    # Supports Croatian diacritics: Č, Ć, Ž, Š, Đ
    CROATIAN_PLATE_STR = r'[A-ZČĆŽŠĐ]{2}[\s\-]?\d{3,4}[\s\-]?[A-ZČĆŽŠĐ]{1,2}'
    CROATIAN_PLATE = re.compile(f'^{CROATIAN_PLATE_STR}$', re.IGNORECASE)

    # For finding plates in text (with capture group)
    CROATIAN_PLATE_CAPTURE = re.compile(f'({CROATIAN_PLATE_STR})', re.IGNORECASE)

    # ═══════════════════════════════════════════════════════════════
    # VIN PATTERNS
    # ═══════════════════════════════════════════════════════════════

    # Vehicle Identification Number: 17 alphanumeric (no I, O, Q)
    VIN_PATTERN_STR = r'[A-HJ-NPR-Z0-9]{17}'
    VIN_PATTERN = re.compile(f'^{VIN_PATTERN_STR}$')

    # ═══════════════════════════════════════════════════════════════
    # CONTACT PATTERNS
    # ═══════════════════════════════════════════════════════════════

    # Email address
    EMAIL_PATTERN_STR = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    EMAIL_PATTERN = re.compile(f'^{EMAIL_PATTERN_STR}$')

    # Croatian phone: +385, 00385, or 0 prefix
    CROATIAN_PHONE_STR = r'(\+385|00385|0)?[1-9]\d{7,8}'
    CROATIAN_PHONE = re.compile(f'^{CROATIAN_PHONE_STR}$')

    # ═══════════════════════════════════════════════════════════════
    # VALUE PATTERNS (for parameter resolution)
    # ═══════════════════════════════════════════════════════════════

    @classmethod
    def get_value_patterns(cls) -> List[ValuePattern]:
        """
        Get all value patterns for dependency resolver.

        Returns:
            List of ValuePattern objects for recognizing human-readable values
        """
        # NOTE: param_type values MUST match keys in PARAM_PROVIDERS (lowercase, no underscore)
        return [
            ValuePattern(
                pattern=cls.CROATIAN_PLATE,
                param_type='vehicleid',
                filter_field='LicencePlate',
                description='Croatian license plate'
            ),
            ValuePattern(
                pattern=cls.VIN_PATTERN,
                param_type='vehicleid',
                filter_field='VIN',
                description='Vehicle VIN'
            ),
            ValuePattern(
                pattern=cls.EMAIL_PATTERN,
                param_type='personid',
                filter_field='Email',
                description='Email address'
            ),
            ValuePattern(
                pattern=cls.CROATIAN_PHONE,
                param_type='personid',
                filter_field='Phone',
                description='Phone number'
            ),
        ]

    # ═══════════════════════════════════════════════════════════════
    # HELPER METHODS
    # ═══════════════════════════════════════════════════════════════

    @classmethod
    def find_uuids(cls, text: str) -> List[str]:
        """
        Find all UUIDs in text.

        Args:
            text: Text to search

        Returns:
            List of UUID strings (lowercase)
        """
        if not text:
            return []
        return [m.lower() for m in cls.UUID_CAPTURE.findall(text.lower())]

    @classmethod
    def find_plates(cls, text: str) -> List[str]:
        """
        Find all Croatian license plates in text.

        Args:
            text: Text to search

        Returns:
            List of plate strings (uppercase, normalized)
        """
        if not text:
            return []
        plates = cls.CROATIAN_PLATE_CAPTURE.findall(text.upper())
        # Normalize: remove spaces and dashes, then reformat
        return [p.replace(' ', '-').replace('--', '-') for p in plates]

    @classmethod
    def is_uuid(cls, text: str) -> bool:
        """Check if text is a valid UUID."""
        if not text:
            return False
        return bool(cls.UUID_PATTERN.fullmatch(text.strip()))

    @classmethod
    def is_croatian_plate(cls, text: str) -> bool:
        """Check if text is a valid Croatian license plate."""
        if not text:
            return False
        return bool(cls.CROATIAN_PLATE.match(text.strip().upper()))

    @classmethod
    def is_vin(cls, text: str) -> bool:
        """Check if text is a valid VIN."""
        if not text:
            return False
        return bool(cls.VIN_PATTERN.match(text.strip().upper()))

    @classmethod
    def is_email(cls, text: str) -> bool:
        """Check if text is a valid email address."""
        if not text:
            return False
        return bool(cls.EMAIL_PATTERN.match(text.strip()))


# ═══════════════════════════════════════════════════════════════
# NAMING CONVENTIONS
# ═══════════════════════════════════════════════════════════════
#
# There are THREE naming layers, each serving a different purpose:
#
# 1. API PARAMETERS (PascalCase): PersonId, VehicleId
#    - From Swagger/API contract
#    - Used in HTTP requests/responses
#
# 2. CONTEXT KEYS (snake_case): person_id, vehicle_id
#    - For semantic classification (what type of ID is this?)
#    - Used in tool_registry.py, message_engine.py, tool_executor.py
#    - Defined in CONTEXT_KEY_CANONICAL below
#
# 3. LOOKUP KEYS (lowercase): personid, vehicleid
#    - For PARAM_PROVIDERS lookup in dependency_resolver.py
#    - For VALUE_PATTERNS param_type matching
#    - No underscores for faster string matching
#
# IMPORTANT: These are intentionally different!
# - Use context_key for semantic checks: if param.context_key == "person_id"
# - Use lowercase for lookups: PARAM_PROVIDERS['personid']
#
# The canonical form is snake_case (person_id, vehicle_id)
# These mappings normalize all variations to the canonical form.

CONTEXT_KEY_CANONICAL = {
    # Person ID variations -> person_id
    'personid': 'person_id',
    'person_id': 'person_id',
    'userid': 'person_id',
    'user_id': 'person_id',
    'driverid': 'person_id',
    'driver_id': 'person_id',
    'assignedtoid': 'person_id',
    'assigned_to_id': 'person_id',
    'ownerid': 'person_id',
    'owner_id': 'person_id',
    'employeeid': 'person_id',
    'employee_id': 'person_id',
    'createdby': 'person_id',
    'created_by': 'person_id',

    # Vehicle ID variations -> vehicle_id
    'vehicleid': 'vehicle_id',
    'vehicle_id': 'vehicle_id',
    'carid': 'vehicle_id',
    'car_id': 'vehicle_id',
    'assetid': 'vehicle_id',
    'asset_id': 'vehicle_id',

    # Tenant ID variations -> tenant_id
    'tenantid': 'tenant_id',
    'tenant_id': 'tenant_id',
    'organizationid': 'tenant_id',
    'organization_id': 'tenant_id',
    'companyid': 'tenant_id',
    'company_id': 'tenant_id',
    'x-tenant-id': 'tenant_id',  # HTTP header format
}


def normalize_context_key(param_name: str) -> str:
    """
    Normalize parameter name to canonical context key.

    Args:
        param_name: Parameter name in any format (PersonId, personId, person_id)

    Returns:
        Canonical context key (person_id, vehicle_id, tenant_id) or None
    """
    if not param_name:
        return None
    return CONTEXT_KEY_CANONICAL.get(param_name.lower())


# ═══════════════════════════════════════════════════════════════
# INTENT DETECTION PATTERNS (Croatian + English)
# ═══════════════════════════════════════════════════════════════
#
# Used by tool_registry.py for intent disambiguation.
# Centralized here to avoid hardcoding in multiple places.

# Patterns indicating READ intent (questions, show commands)
READ_INTENT_PATTERNS = [
    # Croatian questions (with flexible word order)
    r'^koj[aei]?\b',           # koja, koje, koji at start
    r'\bkoj[aei]?\s+\w+\s+su', # koja vozila su, koje stvari su
    r'\bkoj[aei]?\s+\w+\s+je', # koja tablica je, koji broj je
    r'što\s+je', r'sto\s+je',  # what is
    r'^koliko\b',              # koliko at start (how much/many)
    r'koliko?\s+je', r'kolika?\s+je',  # how much is
    r'kakv[aoi]?\s+je',        # what kind is
    r'koliko\s+ima',           # how many are there
    r'kako\s+se\s+zove',       # what is it called
    r'gdje\s+je', r'di\s+je',  # where is
    r'kada\s+je', r'kad\s+je', # when is
    # Croatian commands for reading
    r'\bprikaži\b', r'\bpokaži\b', r'\bdaj\s+mi\b', r'\breci\s+mi\b',
    r'\bpronađi\b', r'\bnađi\b', r'\btraži\b', r'\bpretraži\b',
    r'\bprikazi\b', r'\bpokazi\b',  # Without diacritics
    r'\bprovjeri\b', r'\bprovjeriti\b',  # check
    r'\bdohvati\b', r'\bučitaj\b',  # fetch, load
    # Availability questions
    r'\bslobodn[aio]\b',       # slobodna, slobodno, slobodni
    r'\bdostupn[aio]\b',       # dostupna, dostupno, dostupni
    r'\bima\s+li\b',           # is there
    # User's own data (possessive + noun = READ)
    r'\bmoje?\s+rezervacij',   # moje rezervacije
    r'\bmoje?\s+vozil',        # moje vozilo
    r'\bmoja\s+',              # moja (anything)
    # Status/expiry questions
    r'\bisti[cč]e\b',          # istice/ističe (expires)
    r'\btrenutn[aio]\b',       # trenutna/trenutno/trenutni (current)
    r'\bstanje\b',             # stanje (status/state)
    r'\bstatus\b',             # status
    # Mileage/distance questions
    r'\bkilometra[zž]\b',      # kilometraža
    r'\bkm\b',                 # km
    # English questions
    r'what\s+is', r'what\s+are', r'how\s+much', r'how\s+many',
    r'show\s+me', r'tell\s+me', r'get\s+me', r'find',
    r'list', r'display', r'view', r'check',
    # Common question patterns
    r'\?$',  # Ends with question mark
    r'^ima\s+li', r'^postoji\s+li',  # Croatian questions
    r'^is\s+there', r'^are\s+there',  # English questions
]

# Patterns indicating MUTATION intent (delete, create, update)
MUTATION_INTENT_PATTERNS = [
    # Delete/cancel patterns
    r'obri[sš]i', r'izbri[sš]i', r'ukloni', r'makni',  # Croatian delete
    r'otka[zž]i', r'poni[sš]ti',   # cancel, annul
    r'delete', r'remove', r'erase', r'destroy', r'cancel',  # English
    # Create patterns
    r'dodaj', r'kreiraj', r'napravi', r'stvori',  # Croatian create
    r'add', r'create', r'make', r'new',  # English create
    r'unesi', r'upi[sš]i', r'unos',  # Croatian input (with/without diacritics)
    r'moram\s+upisati', r'moram\s+unijeti',  # must enter
    # Update patterns
    r'promijeni', r'a[zž]uriraj', r'izmijeni', r'uredi',  # Croatian update
    r'update', r'change', r'modify', r'edit',  # English update
    # Report/submit patterns
    r'\bprijavi\b', r'\bprijava\b',  # report
    r'\bpo[sš]alji\b', r'\bsend\b', # send
    r'\bzapi[sš]i\b', r'\bbook\b',  # record, book
    r'\brezerviraj\b',              # reserve
    # Booking intent
    r'\btrebam\s+auto\b',           # need a car
    r'\btrebam\s+vozilo\b',         # need a vehicle
    r'\btrebam\s+rezerv',           # need to reserve (trebam rezervirati)
    r'rezervirati',                 # to reserve
    # Explicit intent
    r'[zž]elim\s+obrisati', r'ho[cć]u\s+obrisati',
    r'[zž]elim\s+prijaviti', r'ho[cć]u\s+prijaviti',
    r'trebam\s+prijaviti',
    # Damage/accident descriptions (implicit report intent)
    r'imam\s+kvar', r'imam\s+[sš]tet',
    r'dogodila.*nesre[cć]', r'imao.*sudar',
    r'\budario\b', r'\budarila\b',  # hit (masculine/feminine)
    r'\bogrebao\b', r'\bogrebala\b', r'\bogrebotina\b',  # scratched
    r'\bo[sš]tetio\b', r'\bo[sš]tetila\b',  # damaged
    r'\bslomio\b', r'\bslomila\b',  # broke
    r'\bproblem[aei]?\b.*\bmotor',   # problem/problema with engine
    r'\bne\s+radi\b',               # doesn't work
    r'\bpokvario\b', r'\bpokvarilo\b',  # broke down
]

# Patterns indicating USER-SPECIFIC intent ("my vehicle", "moje vozilo")
USER_SPECIFIC_PATTERNS = [
    # Croatian possessive pronouns (moj/moja/moje)
    r'\bmoje?\b', r'\bmoja\b', r'\bmoji\b',  # my, mine
    r'\bmeni\b', r'\bmi\b',  # to me, for me
    # Croatian reflexive possessive (svoj/svoja/svoje = "one's own")
    # "svog automobila" = "of my own car"
    r'\bsvoj[aeiou]?\b',    # svoj, svoja, svoje, svoju, svoji
    r'\bsvog(?:a)?\b',      # svog, svoga (genitive)
    r'\bsvom(?:e|u)?\b',    # svom, svome, svomu (dative/locative)
    # Croatian phrases
    r'dodijeljeno\s+mi', r'zadan[ao]?\s+mi',  # assigned to me
    r'za\s+mene', r'pripada\s+mi',  # for me, belongs to me
    r'imam\s+li', r'imali?\b',  # do I have
    r'moj[aei]?\s+vozil', r'moj[aei]?\s+auto',  # my vehicle, my car
    r'koji\s+auto.*meni', r'koje\s+vozilo.*meni',  # which car is mine
    # English equivalents
    r'\bmy\b', r'\bmine\b', r'\bme\b',
    r'assigned\s+to\s+me', r'for\s+me', r'belongs\s+to\s+me',
    r'do\s+i\s+have', r'i\s+have',
]

# ═══════════════════════════════════════════════════════════════
# PRE-COMPILED PATTERNS (Performance optimization)
# ═══════════════════════════════════════════════════════════════
# Compiled once at module load, reused on every call

READ_INTENT_COMPILED = [re.compile(p, re.IGNORECASE) for p in READ_INTENT_PATTERNS]
MUTATION_INTENT_COMPILED = [re.compile(p, re.IGNORECASE) for p in MUTATION_INTENT_PATTERNS]
USER_SPECIFIC_COMPILED = [re.compile(p, re.IGNORECASE) for p in USER_SPECIFIC_PATTERNS]


def is_read_intent(query: str) -> bool:
    """Check if query indicates READ intent using pre-compiled patterns."""
    query_lower = query.lower().strip()
    return any(pattern.search(query_lower) for pattern in READ_INTENT_COMPILED)


def is_mutation_intent(query: str) -> bool:
    """Check if query indicates MUTATION intent using pre-compiled patterns."""
    query_lower = query.lower().strip()
    return any(pattern.search(query_lower) for pattern in MUTATION_INTENT_COMPILED)


def is_user_specific(query: str) -> bool:
    """Check if query indicates user-specific data using pre-compiled patterns."""
    query_lower = query.lower().strip()
    return any(pattern.search(query_lower) for pattern in USER_SPECIFIC_COMPILED)


# Parameter names that indicate user-specific filtering capability
USER_FILTER_PARAMS = {
    'personid', 'person_id',
    'assignedtoid', 'assigned_to_id',
    'driverid', 'driver_id',
    'userid', 'user_id',
    'ownerid', 'owner_id',
    'employeeid', 'employee_id',
    'createdby', 'created_by',
}

# ═══════════════════════════════════════════════════════════════
# PERSON ID INJECTION SKIP PATTERNS
# ═══════════════════════════════════════════════════════════════
#
# Tools matching these patterns should NOT have PersonId auto-injected
# because they don't operate on user-specific data.
# Examples: get_AvailableVehicles, get_LocationSites, get_ConfigSettings

PERSON_ID_SKIP_PATTERNS = frozenset([
    "available",
    "location",
    "site",
    "config",
    "setting",
    "settings",
])


def should_skip_person_id_injection(tool_id: str) -> bool:
    """
    Check if tool should NOT receive automatic PersonId injection.

    Args:
        tool_id: Tool operation ID (e.g., "get_AvailableVehicles")

    Returns:
        True if PersonId should be skipped, False if it should be injected
    """
    if not tool_id:
        return False
    tool_lower = tool_id.lower()
    return any(pattern in tool_lower for pattern in PERSON_ID_SKIP_PATTERNS)


# ═══════════════════════════════════════════════════════════════
# INJECTABLE CONTEXT KEYS
# ═══════════════════════════════════════════════════════════════
#
# Canonical context keys that should be injected into nested objects.
# Used by parameter_manager.py for automatic context injection.

INJECTABLE_CONTEXT_KEYS = frozenset(['person_id', 'tenant_id'])


def get_injectable_context(user_context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract injectable context from user_context using canonical keys.

    Normalizes all keys in user_context and extracts those that should
    be injected into API calls (person_id, tenant_id).

    Args:
        user_context: User context dict with various key formats

    Returns:
        Dict with canonical keys and their values (only non-None values)

    Example:
        >>> get_injectable_context({"assigned_to_id": "123", "x-tenant-id": "456"})
        {"person_id": "123", "tenant_id": "456"}
    """
    result = {}

    for key, value in user_context.items():
        if value is None:
            continue

        canonical = normalize_context_key(key)
        if canonical and canonical in INJECTABLE_CONTEXT_KEYS:
            # Use canonical key name
            result[canonical] = value

    return result
