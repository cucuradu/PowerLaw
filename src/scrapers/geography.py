"""
Geographic identity, hierarchy, and spatial boundaries ingestion adapters.
Parses GISCO NUTS GeoJSON into canonical Facts, preserves CRS coordinates,
enforces boundary versioning, and validates against improper proxy substitution.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.schemas.ingestion import Fact, stable_id

logger = logging.getLogger(__name__)


def parse_gisco_geojson(
    payload: bytes,
    fetched_at: datetime,
    version: str = "2024"
) -> List[Fact]:
    """Parse GISCO NUTS GeoJSON into canonical Fact assertions with hierarchy and geometry."""
    data = json.loads(payload.decode("utf-8"))
    crs_name = data.get("crs", {}).get("properties", {}).get("name", "urn:ogc:def:crs:OGC:1.3:CRS84")
    features = data.get("features", [])

    facts = []
    # Track features by NUTS_ID for parent resolution
    by_nuts = {}
    for feat in features:
        props = feat.get("properties", {})
        nuts_id = props.get("NUTS_ID", "").strip()
        if nuts_id:
            by_nuts[nuts_id] = props

    for feat in features:
        props = feat.get("properties", {})
        geometry = feat.get("geometry", {})
        nuts_id = props.get("NUTS_ID", "").strip()
        level_code = props.get("LEVL_CODE", 0)
        cntr_code = props.get("CNTR_CODE", "").strip()
        name_latn = props.get("NAME_LATN", "")

        if not nuts_id:
            continue

        subj_id = f"GEO_NUTS_{nuts_id}"
        geom_str = json.dumps(geometry, sort_keys=True)
        geom_hash = hashlib.sha256(geom_str.encode("utf-8")).hexdigest()[:16]
        fact_payload = {
            "nuts_id": nuts_id,
            "level": level_code,
            "country_code": cntr_code,
            "name": name_latn,
            "crs": crs_name,
            "geometry": geometry,
            "geometry_hash": geom_hash,
            "boundary_version": version,
        }

        # 1. Base identity and location fact
        loc_fact = Fact(
            source_id="GISCO_GEOGRAPHY",
            subject_id=subj_id,
            subject_type="GEOGRAPHY",
            predicate="WITHIN",
            object_id=f"GEO_COUNTRY_{cntr_code}" if level_code > 0 else subj_id,
            object_type="GEOGRAPHY",
            payload_json=json.dumps(fact_payload, sort_keys=True),
            fact_version_id=stable_id("geover", subj_id, version, str(level_code), geom_hash),
            valid_from=f"{version}-01-01T00:00:00Z",
            available_at=fetched_at,
            recorded_at=datetime.now(timezone.utc),
            batch_id="batch_geo",
            data_origin="live",
        )
        facts.append(loc_fact)

        # 2. Hierarchy edge: Level 1 -> Level 0, Level 2 -> Level 1, etc.
        if level_code > 0:
            parent_id = None
            # NUTS code parent is prefix (e.g. DE1 parent is DE, DEA1 parent is DEA)
            for i in range(len(nuts_id) - 1, 0, -1):
                cand = nuts_id[:i]
                if cand in by_nuts:
                    parent_id = cand
                    break
            if not parent_id and cntr_code in by_nuts:
                parent_id = cntr_code

            if parent_id:
                within_fact = Fact(
                    source_id="GISCO_GEOGRAPHY",
                    subject_id=subj_id,
                    subject_type="GEOGRAPHY",
                    predicate="WITHIN",
                    object_id=f"GEO_NUTS_{parent_id}",
                    object_type="GEOGRAPHY",
                    payload_json=json.dumps({"relationship": "NUTS_HIERARCHY", "boundary_version": version}),
                    fact_version_id=stable_id("geowithin", subj_id, parent_id, version),
                    valid_from=f"{version}-01-01T00:00:00Z",
                    available_at=fetched_at,
                    recorded_at=datetime.now(timezone.utc),
                    batch_id="batch_geo",
                    data_origin="live",
                )
                facts.append(within_fact)

    return facts


def normalize_geography_response(
    source_id: str,
    payload: bytes,
    fetched_at: datetime,
    version: str = "2024"
) -> List[Fact]:
    """Normalize raw geography bytes into canonical Fact objects."""
    if source_id == "GISCO_GEOGRAPHY":
        return parse_gisco_geojson(payload, fetched_at, version=version)
    raise ValueError(f"Unsupported geography source_id: {source_id!r}")


def validate_facility_location(
    facility_type: str,
    facility_id: str,
    assigned_geo_id: str,
    hq_geo_id: str
) -> Dict[str, Any]:
    """
    Validate that plant/production facilities are not improperly assigned
    corporate headquarters locations as proxies.
    """
    if facility_type in ("MANUFACTURING_PLANT", "STEEL_MILL", "CEMENT_KILN", "QUARRY"):
        if assigned_geo_id == hq_geo_id:
            return {
                "is_valid": False,
                "reason": "PLANT_LOCATION_CANNOT_DEFAULT_TO_HQ",
                "facility_id": facility_id,
                "resolved_geo_id": None,
            }
    return {
        "is_valid": True,
        "reason": "VALID_LOCATION",
        "facility_id": facility_id,
        "resolved_geo_id": assigned_geo_id,
    }
