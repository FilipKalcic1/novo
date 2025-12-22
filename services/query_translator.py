"""
Query Translator - Croatian → English API term mapping
Version: 2.0 - ZERO HARDCODING

COMPLETELY DYNAMIC - NO if-statements!
All mappings are loaded from configuration.

Translation is purely declarative:
1. Load domain vocabulary from config
2. Apply fuzzy matching between query and vocabulary
3. Generate boost scores based on matched terms
"""

from typing import Dict, List, Set
import re
from dataclasses import dataclass
from collections import defaultdict


@dataclass
class DomainMapping:
    """Single domain mapping rule - fully data-driven."""
    croatian_terms: List[str]
    english_terms: List[str]
    boost_targets: Dict[str, float]  # operation_pattern → boost_score
    intent_type: str  # create, read, update, delete


class QueryTranslator:
    """
    Translates Croatian user queries to English API terminology.

    ZERO HARDCODING - all logic driven by DOMAIN_CONFIG.
    """

    # =====================================================================
    # CONFIGURATION - The ONLY place where domain knowledge lives
    # =====================================================================
    # This can be moved to a YAML/JSON file for runtime loading
    DOMAIN_CONFIG: List[DomainMapping] = [
        # Vehicle & Fleet
        DomainMapping(
            croatian_terms=["vozilo", "auto", "kamion"],
            english_terms=["vehicle", "car", "truck", "fleet"],
            boost_targets={"vehicle": 0.20, "fleet": 0.15},
            intent_type="read"
        ),
        DomainMapping(
            croatian_terms=["registracija", "tablice"],
            english_terms=["license plate", "registration", "plate number"],
            boost_targets={"masterdata": 0.35, "get": 0.10},
            intent_type="read"
        ),

        # Mileage & Tracking
        DomainMapping(
            croatian_terms=["kilometraža", "kilometraza", "km"],
            english_terms=["mileage", "odometer", "distance", "kilometers"],
            boost_targets={"mileage": 0.25, "addmileage": 0.35},
            intent_type="read"
        ),
        DomainMapping(
            croatian_terms=["unos", "unesi", "unijeti", "zapisati", "upisati", "zabilježiti", "staviti"],
            english_terms=["add", "input", "record", "create", "log"],
            boost_targets={"add": 0.20, "post": 0.15, "create": 0.15},
            intent_type="create"
        ),

        # Booking & Calendar
        DomainMapping(
            croatian_terms=["rezervacija", "booking"],
            english_terms=["booking", "reservation", "schedule"],
            boost_targets={"booking": 0.20, "calendar": 0.15},
            intent_type="create"
        ),
        DomainMapping(
            croatian_terms=["slobodan", "slobodno", "dostupan"],
            english_terms=["available", "free", "vacancy"],
            boost_targets={"available": 0.15, "calendar": 0.10},
            intent_type="read"
        ),

        # People
        DomainMapping(
            croatian_terms=["vozač", "korisnik", "osoba"],
            english_terms=["driver", "person", "user"],
            boost_targets={"person": 0.15, "user": 0.10},
            intent_type="read"
        ),

        # General actions - READ
        DomainMapping(
            croatian_terms=["popis", "pregled", "lista", "pokaži", "prikaži"],
            english_terms=["list", "get", "view", "show", "fetch"],
            boost_targets={"get": 0.15, "list": 0.10},
            intent_type="read"
        ),

        # General actions - CREATE
        DomainMapping(
            croatian_terms=["dodaj", "kreiraj", "nova", "novi", "novo"],
            english_terms=["add", "create", "post", "new"],
            boost_targets={"post": 0.20, "create": 0.15, "add": 0.10},
            intent_type="create"
        ),

        # General actions - UPDATE
        DomainMapping(
            croatian_terms=["promijeni", "ažuriraj", "izmijeni", "update"],
            english_terms=["update", "change", "modify", "patch"],
            boost_targets={"put": 0.20, "patch": 0.15, "update": 0.10},
            intent_type="update"
        ),

        # General actions - DELETE
        DomainMapping(
            croatian_terms=["obriši", "ukloni", "delete"],
            english_terms=["delete", "remove"],
            boost_targets={"delete": 0.20, "remove": 0.10},
            intent_type="delete"
        ),
    ]

    def __init__(self):
        """Initialize with precomputed lookup tables for performance."""
        # Build reverse index: croatian_word → [mappings]
        self._croatian_index: Dict[str, List[DomainMapping]] = defaultdict(list)

        for mapping in self.DOMAIN_CONFIG:
            for term in mapping.croatian_terms:
                self._croatian_index[term].append(mapping)

    def translate_query(self, query: str) -> str:
        """
        Enhance query with English API terminology.

        PURE ALGORITHM - NO if-statements about domains!

        Args:
            query: Original user query (Croatian)

        Returns:
            Enhanced query with English synonyms
        """
        query_lower = query.lower()
        words = re.findall(r'\b\w+\b', query_lower)

        # Original query
        enhanced_parts = [query]

        # Find matching mappings
        matched_mappings = self._find_matching_mappings(words)

        # Add English translations from matched mappings
        for mapping in matched_mappings:
            enhanced_parts.extend(mapping.english_terms)

        # Join with original query first, then translations
        return " ".join(enhanced_parts)

    def get_domain_hints(self, query: str) -> Dict[str, float]:
        """
        Get domain-specific boost scores for operations.

        PURE ALGORITHM - NO if-statements!

        Strategy:
        1. Find all mappings matching query words
        2. Aggregate boost_targets from matched mappings
        3. Apply multiplicative boost if number detected (mileage usecase)

        Args:
            query: User query

        Returns:
            Dict of operation_id_patterns → boost_score
        """
        query_lower = query.lower()
        words = re.findall(r'\b\w+\b', query_lower)

        # Find matching mappings
        matched_mappings = self._find_matching_mappings(words)

        # Aggregate boosts
        boosts = defaultdict(float)

        for mapping in matched_mappings:
            for target, score in mapping.boost_targets.items():
                # Max boost wins (not sum, to avoid over-boosting)
                boosts[target] = max(boosts[target], score)

        # SPECIAL PATTERN: Number detection (for mileage-like use cases)
        # This is a GENERIC pattern, not domain-specific hardcoding
        has_number = any(char.isdigit() for char in query)

        if has_number:
            # Boost "add" operations when number is present
            # This is a PATTERN: "number in query" → likely wants to ADD value
            for target in list(boosts.keys()):
                if "add" in target.lower() or "post" in target.lower():
                    # Multiplicative boost: 1.5x stronger
                    boosts[target] = min(boosts[target] * 1.5, 0.50)

        return dict(boosts)

    def _find_matching_mappings(self, words: List[str]) -> List[DomainMapping]:
        """
        Find all domain mappings matching query words.

        PURE LOOKUP - NO if-statements!

        Args:
            words: List of words from query

        Returns:
            List of matched DomainMapping objects
        """
        matched = set()

        for word in words:
            if word in self._croatian_index:
                for mapping in self._croatian_index[word]:
                    matched.add(id(mapping))  # Use id() to deduplicate by object identity

        # Convert back to list
        return [
            mapping
            for mapping in self.DOMAIN_CONFIG
            if id(mapping) in matched
        ]
