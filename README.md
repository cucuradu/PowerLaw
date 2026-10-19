# PowerLaw: Dual-Layer Legal, Economic & Supply Chain Platform
### *Specialized Intelligence for the Construction & Building Materials Sector*

An AI-driven platform that converts unstructured EU legislation, supply chain disruptions, and market indicators into an executable, **Dual-Layer Knowledge Graph & Temporal Tabular System** equipped with **Quantitative Fragility Models & GNN Forecasters**.

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
Configure Vertex AI with Application Default Credentials (ADC). No API key is
read or required by the Gemini adapter. Keep credentials outside Git.

#### Install the modern SDK

```powershell
python -m pip install --upgrade google-genai
# For this project's uv-managed environment instead:
uv pip install --python .venv\Scripts\python.exe "google-genai>=2.31.0"
```

#### Authenticate locally on Windows

Install the free [Google Cloud CLI](https://cloud.google.com/sdk/docs/install#windows)
with its PATH option, then close and reopen PowerShell. A Python virtual
environment does not install the gcloud CLI. Sign in yourself in Google's browser
window; do not share passwords, credential JSON, refresh tokens or login codes.

If `gcloud` is not recognized, launch Google's Windows installer first:

```powershell
$installer = Join-Path $env:TEMP "GoogleCloudSDKInstaller.exe"
Invoke-WebRequest "https://dl.google.com/dl/cloudsdk/channels/rapid/GoogleCloudSDKInstaller.exe" -OutFile $installer
Start-Process -FilePath $installer -Wait
```

Select "Add gcloud to PATH", finish installation, and open a new PowerShell
window before running the following commands.

```powershell
gcloud --version
gcloud auth login
gcloud config set project project-05d7ff31-502b-4fe3-8ff
gcloud auth application-default login
gcloud auth application-default set-quota-project project-05d7ff31-502b-4fe3-8ff
```

The CLI login and application-default login serve different clients. These
commands do not upgrade your billing account. If Vertex AI is not enabled, an
authorized project administrator must enable `aiplatform.googleapis.com`; an
administrator must also grant your account the required Vertex AI permissions.
Do not relax organization policies to create an API key.

In the private `.env` (or the current PowerShell environment), configure:

```dotenv
GOOGLE_CLOUD_PROJECT=project-05d7ff31-502b-4fe3-8ff
GOOGLE_CLOUD_LOCATION=global
GEMINI_MODEL=gemini-3.8-flash
```

For the current PowerShell session, the equivalent is:

```powershell
$env:GOOGLE_CLOUD_PROJECT = "project-05d7ff31-502b-4fe3-8ff"
$env:GOOGLE_CLOUD_LOCATION = "global"
$env:GEMINI_MODEL = "gemini-3.8-flash"
```

On 2026-10-18, this project's ADC request to Gemini 3.8 Flash succeeded on
`global`; `us-central1` returned HTTP 404. The global endpoint does not guarantee
a processing region. See [Google's endpoint documentation](https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/locations).

The adapter loads the private `.env` without overriding existing environment
variables. It creates a client lazily using `from google import genai`,
`google.auth.default()` and `genai.Client(vertexai=True, project=..., location=...,
credentials=...)`. Explicit ADC credentials prevent old API-key variables from
selecting a different authentication route. Missing ADC or denied access fails
without falling back to API keys or another provider.

#### Send a prompt

From the project root, after completing ADC login:

```python
from src.utils.api_wrapper import get_completion, initialize_api

client = initialize_api()  # Uses local ADC, without an API key.
answer = get_completion("Explain construction price inflation in three sentences.", max_output_tokens=256)
print(answer)
```

The default model is `gemini-3.8-flash`; `GEMINI_MODEL` selects a model explicitly.
`use_pro=True` requests higher thinking on that same model. There is no automatic
fallback to another model or provider. Authentication and permission errors are
not retried. Existing JSON, 768-dimensional embedding and PDF helpers use the
modern SDK too. PDFs are passed inline, limited to 20 MiB by this helper; larger
documents need a GCS URI integration. Regional model availability must be verified
on the selected project before live ingestion.

#### Trial credits and billing

ADC authenticates the account; it does not establish credit eligibility or a
spending cap. Check the selected project's linked billing account, promotional
credit balance and expiry in [Cloud Billing](https://console.cloud.google.com/billing).
A successful response and token counts do not prove which credits covered its
cost. Check the billing report after its reporting delay; there is no need to
exhaust an AI Studio free-tier quota as a billing test.

For new Cloud Billing accounts opened after March 2, 2026, Google's welcome
credits cannot pay for Gemini Developer API / AI Studio usage. This project uses
the separate Vertex AI service. See Google's [Gemini billing rules](https://ai.google.dev/gemini-api/docs/billing/)
and [Cloud Free Trial FAQ](https://cloud.google.com/signup-faqs). Keep a
credit-only account on its Free Trial rather than upgrading to unrestricted paid
billing. The SDK migration does not implement the ingestion plan's durable
spending ledger; leave bulk extraction disabled until its budget controls are
verified.

#### Ingestion & Operations: Tasks 1–9

The data ingestion pipeline provides unified, bounded, recoverable collection across 8 official sources (`EURLEX_CELLAR`, `OFFICIAL_NEWS`, `DESTATIS_GENESIS`, `EUROSTAT_STS`, `ECB_SDMX`, `BUNDESBANK_SDMX`, `SMARD_DE`, `GISCO_GEOGRAPHY`):

- **CLI Operations**:
  ```powershell
  # Check status & health overview
  python -m scripts.ops.ingest --status

  # Execute dry-run across all verified pilot sources
  python -m scripts.ops.ingest --pilot --dry-run

  # Bounded historical backfill
  python -m scripts.ops.ingest --source DESTATIS_GENESIS --since 2010-01-01 --until 2024-12-31 --limit 100

  # Enforce cache & temp budgets without deleting sole unverified copies
  python -m scripts.ops.ingest --enforce-cache

  # Verify and restore batch from manifest
  python -m scripts.ops.ingest --verify-restore <manifest_uri> --restore-dir ./data/restored_test
  ```

- **Automation & Scheduling**:
  Windows Task Scheduler configurations for hourly news, daily pilot collection, weekly legal reconciliation, and monthly statistical revision updates are documented in [`docs/operations/scheduled_ingestion.md`](docs/operations/scheduled_ingestion.md).

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
│   │   ├── news_scraper.py          # Real-time trade, energy, and commodity RSS scraper
│   │   └── market_data.py           # Live Yahoo Finance ingestion for European construction leaders
│   ├── quant/                       # Quantitative Risk & Financial Macro Suite
│   │   ├── fragility.py             # Kritzman (2010) PCA Absorption Ratio (Systemic Fragility)
│   │   ├── macro_fred.py            # Keyless FRED Yield Curve, HY OAS, NFCI stress tracker
│   │   ├── regime_hmm.py            # 4-State Gaussian HMM market regime classifier
│   │   └── volatility_garch.py      # GARCH(1,1) forward term-structure volatility model
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
│       └── minio_client.py          # MinIO raw PDF & document object storage
├── scripts/                         # Maintenance and diagnostic scripts
│   ├── ops/                         # Data ingestion, MinIO, and LanceDB operations
│   └── debug/                       # SPARQL, model inspection, and schema debug tools
└── tests/                           # 135 automated unit and integration tests (100% pass)
```

---

## 🧪 Testing

Run the comprehensive test suite (135 tests):
```bash
pytest
```

Run test suite with verbosity:
```bash
pytest -v
```

---

## ⚖️ Legal, Data Sources & Re-use Policy

All tracked data sources adhere to the strict **Free-Source Policy** (`access_fee=0`, `requires_paid_subscription=false`):

- **EUR-Lex / Publications Office**: Public European Union legal acts, metadata, and notices are re-usable under [Commission Decision 2011/833/EU](https://eur-lex.europa.eu/content/legal-notice/legal-notice.html?locale=en). Certain third-party content or non-standard provisions may have specific exceptions detailed in the [EUR-Lex Legal Notice](https://eur-lex.europa.eu/content/legal-notice/legal-notice.html?locale=en).
- **Statistisches Bundesamt (Destatis)**: Official German construction producer and material price indices (family 61261) under *Datenlizenz Deutschland - Namensnennung 2.0* ([dl-de/by-2-0](https://www.destatis.de/DE/Service/OpenData/nutzungsbedingungen.html)).
- **Eurostat**: European statistical datasets re-usable under the [European Commission open data policy](https://ec.europa.eu/eurostat/about-us/policies/copyright).
- **Deutsche Bundesbank & ECB**: Public SDMX interest rate, financing, and exchange rate series under their respective open statistical dissemination policies.
- **Bundesnetzagentur (SMARD)**: Electricity market time series re-usable under [Creative Commons Attribution 4.0 (CC BY 4.0)](https://www.smard.de/home/datennutzung) with attribution *Bundesnetzagentur | SMARD.de*.
- **Official News Discovery**: Public RSS and press corner releases from the European Commission and relevant authorities are tracked for metadata and permitted excerpts only. No proprietary or paywalled news feeds are tracked.
