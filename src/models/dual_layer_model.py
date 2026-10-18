"""
Dual-Layer Data Model for PowerLaw
Defines:
1. Topological Graph Layer (Nodes: Law, Action, Reaction, Sector, Commodity; Edges: Mandates, Triggers, Impacts, Regulates)
2. Tabular Temporal Layer (Behind-the-graph timestamped state records for each node)
"""

from enum import Enum
from typing import List, Dict, Any, Optional, Union
from datetime import datetime, timezone
import uuid
from pydantic import BaseModel, Field, field_validator


class NodeType(str, Enum):
    LAW = "LAW"
    ACTION = "ACTION"
    REACTION = "REACTION"
    SECTOR = "SECTOR"
    COMMODITY = "COMMODITY"
    ECONOMIC_INDEX = "ECONOMIC_INDEX"


class EdgeRelation(str, Enum):
    MANDATES = "MANDATES"       # Law -> Action
    TRIGGERS = "TRIGGERS"       # Action -> Reaction (or Reaction -> Action)
    REGULATES = "REGULATES"     # Law/Action -> Sector or Commodity
    AFFECTS = "AFFECTS"         # Action -> Sector/Commodity
    IMPACTS = "IMPACTS"         # Reaction -> Commodity, Sector, or Metric
    CAUSES = "CAUSES"           # Reaction -> Reaction
    AMENDS = "AMENDS"           # Law -> Law


class Strictness(str, Enum):
    HARD = "HARD"
    SOFT = "SOFT"


class GraphNode(BaseModel):
    """
    Topological Graph Node.
    Represents an Action, a Reaction, a Law, an Industrial Sector, or a Commodity.
    """
    id: str = Field(..., description="Unique deterministic identifier, e.g. 'LAW_32023R0956'")
    label: str = Field(..., description="Human-readable title or label")
    node_type: Union[NodeType, str] = Field(..., description="LAW, ACTION, REACTION, SECTOR, COMMODITY")
    description: str = Field(default="", description="Detailed summary of the node semantics")
    jurisdiction: str = Field(default="EU", description="Geographic jurisdiction: EU, DE, FR, GLOBAL, etc.")
    activation_date: Optional[str] = Field(default=None, description="ISO Date string YYYY-MM-DD")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary attributes, e.g. CELEX, NACE, strictness")

    @field_validator("node_type", mode="before")
    @classmethod
    def normalize_node_type(cls, v: Any) -> Any:
        if isinstance(v, str):
            v_upper = v.strip().upper()
            for member in NodeType:
                if member.value == v_upper or member.name == v_upper:
                    return member
            return v_upper
        return v

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "node_type": self.node_type.value if hasattr(self.node_type, "value") else str(self.node_type),
            "description": self.description,
            "jurisdiction": self.jurisdiction,
            "activation_date": self.activation_date or "",
            "metadata_json": str(self.metadata)
        }


class GraphEdge(BaseModel):
    """
    Topological Graph Edge.
    Connects Laws to Actions, Actions to Reactions, and nodes to Sectors/Commodities.
    """
    source_id: str = Field(..., description="Source node ID")
    target_id: str = Field(..., description="Target node ID")
    relation_type: Union[EdgeRelation, str] = Field(..., description="MANDATES, TRIGGERS, REGULATES, IMPACTS, etc.")
    weight: float = Field(default=1.0, description="Connection strength or elasticity coefficient")
    timestamp: Optional[str] = Field(default=None, description="ISO Date when relation became active")
    properties: Dict[str, Any] = Field(default_factory=dict, description="Auxiliary properties like direction, confidence")

    @field_validator("relation_type", mode="before")
    @classmethod
    def normalize_relation_type(cls, v: Any) -> Any:
        if isinstance(v, str):
            v_upper = v.strip().upper()
            for member in EdgeRelation:
                if member.value == v_upper or member.name == v_upper:
                    return member
            return v_upper
        return v

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation_type": self.relation_type.value if hasattr(self.relation_type, "value") else str(self.relation_type),
            "weight": float(self.weight),
            "timestamp": self.timestamp or "",
            "properties_json": str(self.properties)
        }


class NodeStateRecord(BaseModel):
    """
    Behind-The-Graph Tabular Record.
    Stores the timestamped historical states and metrics for any graph node.
    Enables temporal GNN modeling and state transition inspection.
    """
    record_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    timestamp: str = Field(..., description="ISO 8601 Date string YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ")
    timestamp_utc: int = Field(default=0, description="Unix epoch timestamp in seconds")
    node_id: str = Field(..., description="Graph node identifier")
    node_type: Union[NodeType, str] = Field(..., description="LAW, ACTION, REACTION, SECTOR, COMMODITY")
    state_value: str = Field(..., description="Categorical state: e.g. ACTIVE, ENFORCED, PROPOSED, PEAK, CRITICAL")
    metric_name: str = Field(..., description="Name of quantitative metric: e.g. carbon_price, compliance_cost, volume")
    metric_value: float = Field(..., description="Current numeric metric reading")
    delta: Optional[float] = Field(default=0.0, description="Numeric change since previous period")
    unit: str = Field(default="INDEX", description="Unit of measurement: EUR, EUR/t, TONNES, PERCENT, INDEX")
    source_id: str = Field(default="", description="Reference to source document (CELEX ID, news ID, ticker)")
    source_type: str = Field(default="EUR_LEX", description="EUR_LEX, NEWS, MARKET_INDEX, EXPERT_AUDIT")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence in observation")
    notes: Optional[str] = Field(default="", description="Explanatory commentary or context")

    @field_validator("node_type", mode="before")
    @classmethod
    def normalize_node_type(cls, v: Any) -> Any:
        if isinstance(v, str):
            v_upper = v.strip().upper()
            for member in NodeType:
                if member.value == v_upper or member.name == v_upper:
                    return member
            return v_upper
        return v

    def __init__(self, **data):
        super().__init__(**data)
        if self.timestamp and not self.timestamp_utc:
            try:
                clean_ts = self.timestamp.replace("Z", "+00:00")
                if len(clean_ts) == 10:
                    dt = datetime.strptime(clean_ts, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                else:
                    dt = datetime.fromisoformat(clean_ts)
                self.timestamp_utc = int(dt.timestamp())
            except Exception:
                self.timestamp_utc = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id,
            "timestamp": self.timestamp,
            "timestamp_utc": self.timestamp_utc,
            "node_id": self.node_id,
            "node_type": self.node_type.value if isinstance(self.node_type, NodeType) else self.node_type,
            "state_value": self.state_value,
            "metric_name": self.metric_name,
            "metric_value": float(self.metric_value),
            "delta": float(self.delta or 0.0),
            "unit": self.unit,
            "source_id": self.source_id,
            "source_type": self.source_type,
            "confidence": float(self.confidence),
            "notes": self.notes or ""
        }
