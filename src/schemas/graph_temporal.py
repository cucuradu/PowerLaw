"""
Data schemas for Dual-Layer Graph + Temporal Tabular Storage Engine.

Defines:
1. Node Models: LawNode, ActionNode, ReactionNode, SectorNode (with unique ID, label, metadata,
   and 768-dim dense vector embedding), plus CommodityNode and EconomicIndexNode.
2. Edge Models: Directional edges (TRIGGERS, MANDATES, REACTS_TO, AFFECTS_COST_OF, AMENDS)
   with source, target, relationship type, and weights.
3. Temporal Tabular Model: TabularNodeHistory storing for each node its effective_timestamp,
   state_value, metric_name, unit, source_url, and event log.
"""

from enum import Enum
from typing import List, Dict, Any, Optional, Union
from datetime import datetime, timezone, date
import json
import uuid

import numpy as np
import pandas as pd
from pydantic import Field, model_validator
try:
    from lancedb.pydantic import LanceModel, Vector
except ImportError:
    from pydantic import BaseModel as LanceModel
    def Vector(dim):
        return List[float]


# ============================================================================
# 1. NODE SCHEMAS
# ============================================================================

class NodeType(str, Enum):
    LAW = "LAW"
    ACTION = "ACTION"
    REACTION = "REACTION"
    SECTOR = "SECTOR"
    COMMODITY = "COMMODITY"
    ECONOMIC_INDEX = "ECONOMIC_INDEX"
    COMPANY = "COMPANY"
    GEOGRAPHY = "GEOGRAPHY"


def _is_nan(val: Any) -> bool:
    """Check if value is float nan or pandas NA."""
    if isinstance(val, (list, tuple, dict, set, np.ndarray)):
        return False
    try:
        return bool(pd.isna(val))
    except Exception:
        return False


class BaseGraphNode(LanceModel):
    """
    Base Graph Node schema backed by LanceModel.
    Stores node identity, human label, categorical type, textual metadata,
    and a 768-dimensional dense vector embedding.
    """
    id: str = Field(..., description="Unique deterministic identifier, e.g. 'LAW_32023R0956'")
    label: str = Field(..., description="Human-readable title or label")
    node_type: str = Field(default="BASE", description="LAW, ACTION, REACTION, SECTOR, etc.")
    description: str = Field(default="", description="Detailed summary of node semantics")
    jurisdiction: str = Field(default="EU", description="Geographic jurisdiction: EU, DE, FR, GLOBAL, etc.")
    metadata: str = Field(default="{}", description="JSON-serialized metadata dictionary")
    vector: Vector(768) = Field(
        default_factory=lambda: [0.0] * 768,
        description="768-dimensional semantic embedding vector"
    )

    @model_validator(mode="before")
    @classmethod
    def process_node_input(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)

        # Ensure default node_type from class is populated if not explicitly supplied
        if ("node_type" not in data or data["node_type"] is None or _is_nan(data["node_type"])) and "node_type" in cls.model_fields:
            default_nt = cls.model_fields["node_type"].default
            if default_nt is not None:
                data["node_type"] = default_nt
            else:
                data["node_type"] = "BASE"

        # Sanitize label / description / jurisdiction against None or NaN
        if "label" in data and (data["label"] is None or _is_nan(data["label"])):
            data["label"] = str(data.get("id", ""))
        if "description" in data and (data["description"] is None or _is_nan(data["description"])):
            data["description"] = ""
        if "jurisdiction" in data and (data["jurisdiction"] is None or _is_nan(data["jurisdiction"])):
            data["jurisdiction"] = "EU"

        # Sanitize vector
        if "vector" in data:
            v = data["vector"]
            if v is None or _is_nan(v):
                data["vector"] = [0.0] * 768
            elif isinstance(v, np.ndarray):
                data["vector"] = v.tolist()

        # Handle metadata provided as dict or str
        meta: Dict[str, Any] = {}
        if "metadata" in data:
            raw_meta = data["metadata"]
            if isinstance(raw_meta, dict):
                meta = raw_meta.copy()
            elif isinstance(raw_meta, str) and raw_meta.strip():
                try:
                    meta = json.loads(raw_meta)
                except Exception:
                    meta = {"raw": raw_meta}

        # Strip helper alias metadata_json if provided without polluting metadata
        if "metadata_json" in data:
            raw_mj = data.pop("metadata_json")
            if not meta and isinstance(raw_mj, str) and raw_mj.strip():
                try:
                    meta = json.loads(raw_mj)
                except Exception:
                    meta = {"raw": raw_mj}

        # Absorb any unmodeled kwargs into metadata dict to protect Arrow fixed schema
        known_fields = {"id", "label", "node_type", "description", "jurisdiction", "metadata", "vector"}
        for k, v in list(data.items()):
            if k not in known_fields:
                if not _is_nan(v):
                    meta[k] = v
                data.pop(k)

        data["metadata"] = json.dumps(meta)
        return data

    @property
    def metadata_dict(self) -> Dict[str, Any]:
        """Parsed JSON metadata dictionary."""
        try:
            return json.loads(self.metadata)
        except Exception:
            return {}

    def to_dict(self) -> Dict[str, Any]:
        """Serialize node to standard python dictionary."""
        return {
            "id": self.id,
            "label": self.label,
            "node_type": self.node_type,
            "description": self.description,
            "jurisdiction": self.jurisdiction,
            "metadata": self.metadata_dict,
            "metadata_json": self.metadata,
            "vector": list(self.vector) if hasattr(self.vector, "__iter__") else self.vector,
        }


class LawNode(BaseGraphNode):
    """
    Regulatory / Legislative Node (e.g. EU Directives, Regulations, Statutes).
    """
    node_type: str = Field(default="LAW", description="LAW node type")


class ActionNode(BaseGraphNode):
    """
    Prescribed Regulatory or Corporate Action Node (e.g. Reporting, Purchasing Allowances).
    """
    node_type: str = Field(default="ACTION", description="ACTION node type")


class ReactionNode(BaseGraphNode):
    """
    Market or Economic Reaction Node (e.g. Price Surges, Capex Reallocation).
    """
    node_type: str = Field(default="REACTION", description="REACTION node type")


class SectorNode(BaseGraphNode):
    """
    Industrial or Economic Sector Node (e.g. Steel, Cement, Energy, Maritime).
    """
    node_type: str = Field(default="SECTOR", description="SECTOR node type")


class CommodityNode(BaseGraphNode):
    """
    Commodity or Energy Price Node (e.g. EU ETS EUA allowances, TTF Gas).
    """
    node_type: str = Field(default="COMMODITY", description="COMMODITY node type")


class EconomicIndexNode(BaseGraphNode):
    """
    Macroeconomic Index Node (e.g. Inflation, CPI, Industrial Production).
    """
    node_type: str = Field(default="ECONOMIC_INDEX", description="ECONOMIC_INDEX node type")


class CompanyNode(BaseGraphNode):
    """
    Corporate or Economic Entity Node (e.g. Heidelberg Materials, Hochtief).
    """
    node_type: str = Field(default="COMPANY", description="COMPANY node type")


class GeographyNode(BaseGraphNode):
    """
    Geographic Jurisdiction or Spatial Node (e.g. DE, EU, DE-BY).
    """
    node_type: str = Field(default="GEOGRAPHY", description="GEOGRAPHY node type")


# Registry for instantiating concrete node subclasses by type string
NODE_CLASS_REGISTRY = {
    "LAW": LawNode,
    "ACTION": ActionNode,
    "REACTION": ReactionNode,
    "SECTOR": SectorNode,
    "COMMODITY": CommodityNode,
    "ECONOMIC_INDEX": EconomicIndexNode,
    "COMPANY": CompanyNode,
    "GEOGRAPHY": GeographyNode,
}


def node_from_dict(data: Dict[str, Any]) -> BaseGraphNode:
    """Factory helper to instantiate the appropriate Node subclass from a dictionary."""
    ntype = str(data.get("node_type", "BASE")).upper()
    target_cls = NODE_CLASS_REGISTRY.get(ntype, BaseGraphNode)
    return target_cls(**data)


# ============================================================================
# 2. EDGE SCHEMAS
# ============================================================================

class EdgeRelation(str, Enum):
    """Canonical edge relationship types between nodes."""
    TRIGGERS = "TRIGGERS"
    MANDATES = "MANDATES"
    REACTS_TO = "REACTS_TO"
    AFFECTS_COST_OF = "AFFECTS_COST_OF"
    AMENDS = "AMENDS"
    # Auxiliary relationships for topological extensibility
    REGULATES = "REGULATES"
    IMPACTS = "IMPACTS"
    CAUSES = "CAUSES"
    AFFECTS = "AFFECTS"
    PERFORMS = "PERFORMS"
    BELONGS_TO = "BELONGS_TO"
    ABOUT = "ABOUT"
    LOCATED_IN = "LOCATED_IN"
    WITHIN = "WITHIN"
    MEASURES = "MEASURES"
    FLOWS_TO = "FLOWS_TO"
    APPLIES_IN = "APPLIES_IN"


class GraphEdge(LanceModel):
    """
    Directional Graph Edge schema backed by LanceModel.
    Connects source -> target with an explicit relationship type and weight.
    """
    id: str = Field(default="", description="Unique edge ID, e.g. 'source->rel->target'")
    source: str = Field(..., description="Source node ID")
    target: str = Field(..., description="Target node ID")
    relationship_type: str = Field(..., description="TRIGGERS, MANDATES, REACTS_TO, AFFECTS_COST_OF, AMENDS, etc.")
    weight: float = Field(default=1.0, description="Connection strength or elasticity weight")
    metadata: str = Field(default="{}", description="JSON-serialized edge properties")
    timestamp: str = Field(default="", description="ISO Date or timestamp when relationship became effective")

    @model_validator(mode="before")
    @classmethod
    def process_edge_input(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)

        # Field alias support - always pop aliases to prevent metadata pollution
        if "source_id" in data:
            sid = data.pop("source_id")
            if "source" not in data or data["source"] is None:
                data["source"] = sid

        if "target_id" in data:
            tid = data.pop("target_id")
            if "target" not in data or data["target"] is None:
                data["target"] = tid

        if "relation_type" in data:
            rt = data.pop("relation_type")
            if "relationship_type" not in data or data["relationship_type"] is None:
                data["relationship_type"] = rt

        if "weights" in data:
            w = data.pop("weights")
            if "weight" not in data or data["weight"] is None:
                if isinstance(w, (int, float)):
                    data["weight"] = float(w)
                elif isinstance(w, (list, tuple)) and len(w) > 0:
                    data["weight"] = float(w[0])

        # Ensure default relationship_type from class is populated if not explicitly supplied
        if ("relationship_type" not in data or data["relationship_type"] is None or _is_nan(data["relationship_type"])) and "relationship_type" in cls.model_fields:
            default_rel = cls.model_fields["relationship_type"].default
            if default_rel is not None:
                data["relationship_type"] = default_rel
            else:
                data["relationship_type"] = "TRIGGERS"

        # Normalize relationship_type if passed as an Enum
        if "relationship_type" in data and isinstance(data["relationship_type"], Enum):
            data["relationship_type"] = data["relationship_type"].value

        # Sanitize weight and timestamp
        if "weight" in data and (data["weight"] is None or _is_nan(data["weight"])):
            data["weight"] = 1.0
        else:
            try:
                data["weight"] = float(data.get("weight", 1.0))
            except (ValueError, TypeError):
                data["weight"] = 1.0

        if "timestamp" in data and (data["timestamp"] is None or _is_nan(data["timestamp"])):
            data["timestamp"] = ""

        # Handle metadata provided as dict, properties, properties_json, or str
        meta: Dict[str, Any] = {}
        if "metadata" in data:
            raw_meta = data["metadata"]
            if isinstance(raw_meta, dict):
                meta = raw_meta.copy()
            elif isinstance(raw_meta, str) and raw_meta.strip():
                try:
                    meta = json.loads(raw_meta)
                except Exception:
                    meta = {"raw": raw_meta}

        # Strip properties and metadata_json aliases
        if "properties" in data:
            raw_props = data.pop("properties")
            if isinstance(raw_props, dict):
                for pk, pv in raw_props.items():
                    if pk not in meta:
                        meta[pk] = pv
        if "properties_json" in data:
            raw_props = data.pop("properties_json")
            if isinstance(raw_props, str) and raw_props.strip():
                try:
                    parsed_p = json.loads(raw_props)
                    if isinstance(parsed_p, dict):
                        for pk, pv in parsed_p.items():
                            if pk not in meta:
                                meta[pk] = pv
                except Exception:
                    pass
        if "metadata_json" in data:
            raw_mj = data.pop("metadata_json")
            if not meta and isinstance(raw_mj, str) and raw_mj.strip():
                try:
                    meta = json.loads(raw_mj)
                except Exception:
                    pass

        # Absorb extra kwargs into metadata
        known_fields = {"id", "source", "target", "relationship_type", "weight", "metadata", "timestamp"}
        for k, v in list(data.items()):
            if k not in known_fields:
                if not _is_nan(v):
                    meta[k] = v
                data.pop(k)

        data["metadata"] = json.dumps(meta)

        # Deterministic default ID
        s = str(data.get("source", ""))
        r = str(data.get("relationship_type", ""))
        t = str(data.get("target", ""))
        if "id" not in data or not data["id"] or _is_nan(data["id"]):
            data["id"] = f"{s}->{r}->{t}"

        return data

    @property
    def source_id(self) -> str:
        """Alias for backward-compatibility with GraphEdge.source_id."""
        return self.source

    @property
    def target_id(self) -> str:
        """Alias for backward-compatibility with GraphEdge.target_id."""
        return self.target

    @property
    def relation_type(self) -> str:
        """Alias for backward-compatibility with GraphEdge.relation_type."""
        return self.relationship_type

    @property
    def weights(self) -> float:
        """Alias for weight to satisfy requirement specifying edge weights."""
        return float(self.weight)

    @property
    def metadata_dict(self) -> Dict[str, Any]:
        """Parsed JSON metadata dictionary."""
        try:
            return json.loads(self.metadata)
        except Exception:
            return {}

    def to_dict(self) -> Dict[str, Any]:
        """Serialize edge to standard python dictionary."""
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "source_id": self.source,
            "target_id": self.target,
            "relationship_type": self.relationship_type,
            "relation_type": self.relationship_type,
            "weight": float(self.weight),
            "weights": float(self.weight),
            "timestamp": self.timestamp,
            "metadata": self.metadata_dict,
            "metadata_json": self.metadata,
            "properties": self.metadata_dict,
        }


class TriggersEdge(GraphEdge):
    """Directional edge representing TRIGGERS relationship."""
    relationship_type: str = Field(default=EdgeRelation.TRIGGERS.value)


class MandatesEdge(GraphEdge):
    """Directional edge representing MANDATES relationship."""
    relationship_type: str = Field(default=EdgeRelation.MANDATES.value)


class ReactsToEdge(GraphEdge):
    """Directional edge representing REACTS_TO relationship."""
    relationship_type: str = Field(default=EdgeRelation.REACTS_TO.value)


class AffectsCostOfEdge(GraphEdge):
    """Directional edge representing AFFECTS_COST_OF relationship."""
    relationship_type: str = Field(default=EdgeRelation.AFFECTS_COST_OF.value)


class AmendsEdge(GraphEdge):
    """Directional edge representing AMENDS relationship."""
    relationship_type: str = Field(default=EdgeRelation.AMENDS.value)


EDGE_CLASS_REGISTRY = {
    EdgeRelation.TRIGGERS.value: TriggersEdge,
    EdgeRelation.MANDATES.value: MandatesEdge,
    EdgeRelation.REACTS_TO.value: ReactsToEdge,
    EdgeRelation.AFFECTS_COST_OF.value: AffectsCostOfEdge,
    EdgeRelation.AMENDS.value: AmendsEdge,
}


def edge_from_dict(data: Dict[str, Any]) -> GraphEdge:
    """Factory helper to instantiate the appropriate Edge subclass from a dictionary."""
    rel = str(data.get("relationship_type", data.get("relation_type", "TRIGGERS"))).upper()
    target_cls = EDGE_CLASS_REGISTRY.get(rel, GraphEdge)
    return target_cls(**data)


# ============================================================================
# 3. TEMPORAL TABULAR MODEL
# ============================================================================

def parse_iso_or_date_to_utc(ts: Union[str, datetime, date, int, float, pd.Timestamp]) -> int:
    """
    Robustly convert string, datetime, date, or numerical timestamp into Unix epoch seconds.
    Falls back gracefully to 0 upon encountering unparseable malformed strings.
    """
    if ts is None or _is_nan(ts):
        return 0
    if isinstance(ts, (int, float)):
        return int(ts)
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return int(ts.timestamp())
    if isinstance(ts, date):
        dt = datetime(ts.year, ts.month, ts.day, tzinfo=timezone.utc)
        return int(dt.timestamp())
    if isinstance(ts, str):
        t_clean = ts.strip()
        if not t_clean:
            return 0
        # Numerical string check
        try:
            return int(float(t_clean))
        except ValueError:
            pass

        # ISO format direct fast-path
        t_iso = t_clean.replace("Z", "+00:00")
        try:
            if len(t_iso) == 10 and "-" in t_iso:
                dt = datetime.strptime(t_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                return int(dt.timestamp())
            dt = datetime.fromisoformat(t_iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except (ValueError, TypeError):
            pass

        # Comprehensive fallback via pandas to_datetime
        try:
            dt = pd.to_datetime(t_clean, utc=True)
            return int(dt.timestamp())
        except Exception:
            return 0

    return 0


class TabularNodeHistory(LanceModel):
    """
    Behind-The-Graph Temporal Tabular Model backed by LanceModel.
    Stores timestamped historical states, quantitative metrics, and audit logs
    for any graph node over time.
    """
    record_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12], description="Unique record identifier")
    node_id: str = Field(..., description="Target graph node ID")
    effective_timestamp: str = Field(..., description="ISO 8601 Date or Datetime string (e.g. YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)")
    timestamp_utc: int = Field(default=0, description="Unix epoch timestamp in seconds for fast temporal querying and sorting")
    state_value: str = Field(default="ACTIVE", description="Categorical state: e.g. ACTIVE, ENFORCED, PROPOSED, PEAK, CRITICAL")
    metric_name: str = Field(default="state", description="Quantitative metric name: e.g. compliance_cost, carbon_price, output_volume")
    metric_value: float = Field(default=0.0, description="Quantitative metric reading")
    unit: str = Field(default="INDEX", description="Unit of measurement: EUR, EUR/t, TONNES, PERCENT, INDEX")
    source_url: str = Field(default="", description="Source URL (EUR-Lex, scientific paper, financial feed, or news report)")
    event_log: str = Field(default="[]", description="JSON-serialized array or string describing the causal event log")
    metadata: str = Field(default="{}", description="JSON-serialized dictionary of auxiliary metadata")

    @model_validator(mode="before")
    @classmethod
    def process_tabular_input(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)

        # Support alias 'timestamp' -> 'effective_timestamp' and always strip it
        if "timestamp" in data:
            ts = data.pop("timestamp")
            if "effective_timestamp" not in data or data["effective_timestamp"] is None:
                data["effective_timestamp"] = str(ts)

        # Sanitize effective_timestamp
        if "effective_timestamp" not in data or data["effective_timestamp"] is None or _is_nan(data["effective_timestamp"]):
            data["effective_timestamp"] = "1970-01-01"

        # Auto-compute timestamp_utc from effective_timestamp
        if not data.get("timestamp_utc"):
            data["timestamp_utc"] = parse_iso_or_date_to_utc(data["effective_timestamp"])

        # Sanitize state_value & metric_name
        if "state_value" in data and (data["state_value"] is None or _is_nan(data["state_value"])):
            data["state_value"] = "UNKNOWN"
        if "metric_name" in data and (data["metric_name"] is None or _is_nan(data["metric_name"])):
            data["metric_name"] = "state"
        if "unit" in data and (data["unit"] is None or _is_nan(data["unit"])):
            data["unit"] = "INDEX"
        if "source_url" in data and (data["source_url"] is None or _is_nan(data["source_url"])):
            data["source_url"] = ""

        # Normalize metric_value
        if "metric_value" in data and (data["metric_value"] is None or _is_nan(data["metric_value"])):
            data["metric_value"] = 0.0
        else:
            try:
                data["metric_value"] = float(data.get("metric_value", 0.0))
            except (ValueError, TypeError):
                data["metric_value"] = 0.0

        # Strip alias event_log_json
        if "event_log_json" in data:
            elj = data.pop("event_log_json")
            if "event_log" not in data or data["event_log"] is None:
                data["event_log"] = elj

        # Normalize event_log to JSON string if passed as list or dict
        if "event_log" in data:
            el = data["event_log"]
            if el is None or _is_nan(el):
                data["event_log"] = "[]"
            elif isinstance(el, (list, dict)):
                data["event_log"] = json.dumps(el)
            elif not isinstance(el, str):
                data["event_log"] = str(el)

        # Handle metadata
        meta: Dict[str, Any] = {}
        if "metadata" in data:
            raw_meta = data["metadata"]
            if isinstance(raw_meta, dict):
                meta = raw_meta.copy()
            elif isinstance(raw_meta, str) and raw_meta.strip():
                try:
                    meta = json.loads(raw_meta)
                except Exception:
                    meta = {"raw": raw_meta}

        # Strip metadata_json alias
        if "metadata_json" in data:
            raw_mj = data.pop("metadata_json")
            if not meta and isinstance(raw_mj, str) and raw_mj.strip():
                try:
                    meta = json.loads(raw_mj)
                except Exception:
                    pass

        # Absorb extra kwargs into metadata
        known_fields = {
            "record_id", "node_id", "effective_timestamp", "timestamp_utc",
            "state_value", "metric_name", "metric_value", "unit",
            "source_url", "event_log", "metadata"
        }
        for k, v in list(data.items()):
            if k not in known_fields:
                if not _is_nan(v):
                    meta[k] = v
                data.pop(k)

        data["metadata"] = json.dumps(meta)
        return data

    @property
    def event_log_parsed(self) -> Any:
        """Parsed event log structure (list, dict, or fallback to raw string)."""
        try:
            return json.loads(self.event_log)
        except Exception:
            return self.event_log

    @property
    def metadata_dict(self) -> Dict[str, Any]:
        """Parsed JSON metadata dictionary."""
        try:
            return json.loads(self.metadata)
        except Exception:
            return {}

    def to_dict(self) -> Dict[str, Any]:
        """Serialize tabular history record to standard python dictionary."""
        return {
            "record_id": self.record_id,
            "node_id": self.node_id,
            "effective_timestamp": self.effective_timestamp,
            "timestamp_utc": self.timestamp_utc,
            "state_value": self.state_value,
            "metric_name": self.metric_name,
            "metric_value": float(self.metric_value),
            "unit": self.unit,
            "source_url": self.source_url,
            "event_log": self.event_log_parsed,
            "event_log_json": self.event_log,
            "metadata": self.metadata_dict,
        }
