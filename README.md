# PowerLaw: Dual-Layer Legal & Economic Platform

An AI-driven platform that converts unstructured EU legislation and economic news into an executable, **Dual-Layer Knowledge Graph & Temporal Tabular System** ready for Graph Neural Networks (GNNs).

```
                          ┌──────────────────────────────────────────────┐
                          │               DATA SOURCES                   │
                          │  • EUR-Lex (SPARQL & Direct CELEX)           │
                          │  • Economic & Trade News (Live RSS / Feeds)  │
                          └──────────────────────┬───────────────────────┘
                                                 │
                                                 ▼
                          ┌──────────────────────────────────────────────┐
                          │            LLM EXTRACTION ENGINE             │
                          │    (Gemini 2-Stage: Flash Draft → Pro QA)    │
                          └──────────────────────┬───────────────────────┘
                                                 │
               ┌─────────────────────────────────┴─────────────────────────────────┐
               ▼                                                                   ▼
┌───────────────────────────────┐                               ┌───────────────────────────────────┐
│    TOPOLOGICAL GRAPH LAYER    │                               │    TEMPORAL TABULAR STATE LAYER   │
│     (Causal Connectivity)     │                               │         (State Over Time)         │
├───────────────────────────────┤                               ├───────────────────────────────────┤
│ Nodes:                        │                               │ For Every Node & Edge:            │
│  • [LAW] Regulation/Directive │                               │  • node_id / edge_id              │
│  • [ACTION] Firm/State Action │◄─────────────────────────────►│  • effective_timestamp (UTC)      │
│  • [REACTION] Market Response │                               │  • metric_value (price, volume)   │
│  • [SECTOR] NACE Codes        │                               │  • event_summary & source_url     │
│  • [COMMODITY] Physical Goods │                               │                                   │
│                               │                               │ Point-in-Time Historical Queries  │
│ Directed Edges:               │                               │ `store.query_state_at(timestamp)` │
│  • MANDATES, TRIGGERS,        │                               │                                   │
│    REGULATES, IMPACTS, AFFECTS│                               │ Backed by LanceDB & Parquet       │
└──────────────┬────────────────┘                               └─────────────────┬─────────────────┘
               │                                                                  │
               └─────────────────────────────────┬────────────────────────────────┘
                                                 ▼
                          ┌──────────────────────────────────────────────┐
                          │           MULTI-PAGE DASHBOARD / UI          │
                          │  • Hub: Executive KPIs & Graph Architecture  │
                          │  • Page 1: Law Registry & Logic Gate Viewer  │
                          │  • Page 2: Live News & Event Stream          │
                          │  • Page 3: PyVis Interactive Graph Explorer  │
                          │  • Page 4: Temporal Tabular State Inspector  │
                          │  • Page 5: GNN Modeling Lab (Tensor Export)  │
                          │  • Page 6: Ingestion & Crawler Console       │
                          └──────────────────────┬───────────────────────┘
                                                 ▼
                          ┌──────────────────────────────────────────────┐
                          │       DOWNSTREAM GNN INTERFACE (READY)       │
                          │  PyTorch Geometric / Temporal GNN Tensors    │
                          └──────────────────────────────────────────────┘
```

---

## 🚀 Quickstart

### 1. Installation
```bash
git clone https://github.com/cucuradu/PowerLaw.git
cd PowerLaw
pip install -r requirements.txt
```

### 2. Configuration
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Add your `GOOGLE_API_KEY` (optional for local/offline mock execution).

### 3. Launch the Multi-Page Application
```bash
streamlit run dashboard.py
```

---

## 📁 Repository Structure

```
PowerLaw/
├── dashboard.py                     # Executive Hub with system KPIs & architecture diagram
├── pages/                           # Interactive 6-Page Streamlit Suite
│   ├── 1_📜_Law_Registry.py         # Search EU laws, browse extracted IF-THEN logic gates
│   ├── 2_📰_News_Feed.py            # Real-time trade & commodity news stream with extractions
│   ├── 3_🕸️_Graph_Explorer.py       # Interactive PyVis / NetworkX graph visualization
│   ├── 4_⏱️_Temporal_Table.py       # Behind-the-graph Tabular Inspector with time slider
│   ├── 5_🤖_GNN_Modeling_Lab.py     # Inspect feature tensors, dynamic adjacency, and graph splits
│   └── 6_⚙️_Ingestion_Console.py     # Manage crawlers, trigger scrapers & rebuild LanceDB
├── src/
│   ├── scrapers/
│   │   ├── eurlex.py                # Direct CELEX PDF downloader
│   │   ├── eurlex_bulk.py           # SPARQL bulk harvester for EU Publications Office
│   │   ├── eurlex_history.py        # Historical EU legislation scraper
│   │   └── news_scraper.py          # Real-time trade, energy, and commodity RSS scraper
│   ├── schemas/
│   │   ├── graph_temporal.py        # LawNode, ActionNode, ReactionNode, SectorNode schemas
│   │   ├── rule_schema.py           # GNNRule & executable logic gates (MAX_LIMIT, etc.)
│   │   ├── extraction.py            # Pydantic extraction models
│   │   └── lancedb.py               # Vector + relational LanceDB schemas
│   ├── storage/
│   │   ├── dual_layer_store.py      # Dual-layer storage manager (Graph + Tabular)
│   │   ├── graph_store.py           # Topo graph & temporal snapshot queries
│   │   └── dashboard_data.py        # Analytics & metric aggregation
│   ├── gnn/
│   │   ├── temporal_gnn.py          # Spatio-Temporal GNN (Graph Conv + Temporal GRU)
│   │   ├── trainer.py               # Multi-task training & counterfactual shock simulation
│   │   └── graph_exporter.py        # PyTorch Geometric tensors, node feature matrices X,
│   │                                # adjacency matrices A, and temporal NPZ/JSON snapshots
│   └── utils/
│       ├── api_wrapper.py           # Google Gemini API wrapper (Flash + Pro)
│       ├── seed_dual_layer.py       # Verified EU legal-economic seed data generator
│       └── minio_client.py          # MinIO raw PDF & document object storage
├── scripts/                         # Maintenance and diagnostic scripts
│   ├── ops/                         # Data ingestion, MinIO, and LanceDB operations
│   └── debug/                       # SPARQL, model inspection, and schema debug tools
└── tests/                           # 119 automated unit and integration tests (100% pass)
```

---

## 🧪 Testing

Run the comprehensive test suite (119 tests):
```bash
pytest
```

Run test suite with verbosity:
```bash
pytest -v
```

---

## ⚖️ Legal & Compliance
Official EU legal acts published on EUR-Lex are open for commercial and non-commercial re-use under **Commission Decision 2011/833/EU** and licensed under **Creative Commons Attribution 4.0 International (CC BY 4.0)**. Text and data mining (TDM) is explicitly protected under **Articles 3 & 4 of Directive (EU) 2019/790**.
