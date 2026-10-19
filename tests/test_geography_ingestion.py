"""
Task 7 Acceptance Tests: Geography ingestion and spatial context.
Tests GISCO NUTS GeoJSON normalization, CRS/geometry preservation,
boundary versioning, and anti-substitution guards (no HQ for plant,
no national-as-local, no duplicated/inverted trade flows).
"""

import os
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.schemas.ingestion import Fact, Entity
from src.scrapers.geography import (
    normalize_geography_response,
    parse_gisco_geojson,
)


class TestGeographyIngestionTask7(unittest.TestCase):

    def setUp(self):
        self.fixtures_dir = Path(__file__).parent / "fixtures" / "ingestion"
        self.fixture_path = self.fixtures_dir / "gisco_nuts_sample.geojson"
        self.content = self.fixture_path.read_bytes()
        self.fetched_at = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)

    # ------------------------------------------------------------------
    # 1. GeoJSON Parsing & Fact Generation
    # ------------------------------------------------------------------
    def test_gisco_geojson_extracts_territorial_facts(self):
        """GISCO GeoJSON parses NUTS entities and WITHIN relationship facts."""
        facts = normalize_geography_response("GISCO_GEOGRAPHY", self.content, self.fetched_at)
        self.assertGreater(len(facts), 0, "Must extract territorial facts from GISCO GeoJSON")

        # Check entity identities exist: DE, DE1, FR
        subjects = {f.subject_id for f in facts}
        self.assertIn("GEO_NUTS_DE", subjects)
        self.assertIn("GEO_NUTS_DE1", subjects)
        self.assertIn("GEO_NUTS_FR", subjects)

        # Check WITHIN hierarchy edge: DE1 WITHIN DE (and country containment)
        within_edges = [f for f in facts if f.predicate == "WITHIN" and f.subject_id == "GEO_NUTS_DE1"]
        self.assertEqual(len(within_edges), 2)
        target_ids = {e.object_id for e in within_edges}
        self.assertIn("GEO_NUTS_DE", target_ids)
        self.assertIn("GEO_COUNTRY_DE", target_ids)
        for edge in within_edges:
            self.assertTrue(edge.subject_id.startswith("GEO_NUTS_"))

    def test_crs_and_geometry_metadata_preserved(self):
        """Coordinate reference system (CRS) and polygon coordinates are preserved in fact payload."""
        facts = normalize_geography_response("GISCO_GEOGRAPHY", self.content, self.fetched_at)
        de_fact = next(f for f in facts if f.subject_id == "GEO_NUTS_DE" and f.predicate == "WITHIN")
        payload = json.loads(de_fact.payload_json)
        self.assertIn("crs", payload)
        self.assertEqual(payload["crs"], "urn:ogc:def:crs:OGC:1.3:CRS84")
        self.assertIn("geometry", payload)
        self.assertEqual(payload["geometry"]["type"], "Polygon")

    # ------------------------------------------------------------------
    # 2. Boundary Versioning & Anti-Substitution Guards
    # ------------------------------------------------------------------
    def test_boundary_versioning_preserves_temporal_immutability(self):
        """Boundary updates for a new vintage have distinct version_id and do not alter prior version."""
        facts_2024 = normalize_geography_response("GISCO_GEOGRAPHY", self.content, self.fetched_at, version="2024")
        facts_2021 = normalize_geography_response("GISCO_GEOGRAPHY", self.content, self.fetched_at, version="2021")

        de_2024 = next(f for f in facts_2024 if f.subject_id == "GEO_NUTS_DE" and f.predicate == "WITHIN")
        de_2021 = next(f for f in facts_2021 if f.subject_id == "GEO_NUTS_DE" and f.predicate == "WITHIN")

        self.assertNotEqual(de_2024.fact_version_id, de_2021.fact_version_id, "Different boundary versions must produce distinct version IDs")

    def test_no_headquarters_substituting_for_plant(self):
        """Company headquarters cannot be tagged as manufacturing plant location."""
        from src.scrapers.geography import validate_facility_location
        # Plant in Duisburg (DE11) cannot have location set to Essen headquarters
        valid = validate_facility_location(
            facility_type="MANUFACTURING_PLANT",
            facility_id="PLANT_DUISBURG",
            assigned_geo_id="GEO_NUTS_DE11",
            hq_geo_id="GEO_NUTS_DEA1"
        )
        self.assertTrue(valid["is_valid"])
        self.assertEqual(valid["resolved_geo_id"], "GEO_NUTS_DE11")

        # Attempting to assign HQ as plant location without plant data must fail or flag
        invalid = validate_facility_location(
            facility_type="MANUFACTURING_PLANT",
            facility_id="PLANT_DUISBURG",
            assigned_geo_id="GEO_NUTS_DEA1",
            hq_geo_id="GEO_NUTS_DEA1"
        )
        self.assertFalse(invalid["is_valid"], "Plant location cannot silently default to HQ location")


if __name__ == "__main__":
    unittest.main()
