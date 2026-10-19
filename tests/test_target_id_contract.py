"""One target identity from registry to parser, store and dashboard outlook."""
from datetime import datetime, timezone
from pathlib import Path

from src.config import load_data_sources_registry, primary_target_series_id
from src.gnn.graph_exporter import export_as_of
from src.ingestion import _make_stats_parser
from src.schemas.ingestion import BatchManifest, EvidenceLink, Observation, SourceDocument
from src.scrapers.statistics import parse_destatis_csv
from src.storage.dashboard_data import get_construction_outlook
from src.storage.dual_layer_store import DualLayerStore

FIXTURE = Path(__file__).parent / "fixtures" / "ingestion" / "destatis_61261_0002_2025_official.csv"
GENESIS_ZIP = FIXTURE.parent / "configured_destatis_genesis.zip"
FETCHED = datetime(2026, 10, 19, 18, 0, tzinfo=timezone.utc)


def test_registry_target_id_is_the_parsed_provider_series_key():
    key = load_data_sources_registry()["primary_target"]["provider_series_key"]
    assert primary_target_series_id() == key
    parsed = {o.series_id for o in parse_destatis_csv(FIXTURE.read_bytes(), FETCHED, requested_key=key)}
    assert key in parsed


def test_registry_loads_independent_of_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert load_data_sources_registry()["primary_target"]["table_id"] == "61261-0002"


def _stage_real_genesis_response(tmp_path):
    """Run the production parser on the real GENESIS response and commit exactly what it returns.

    Adds one unpublished next quarter: Destatis marks it "..." and ingestion stores it as a null row.
    """
    key = primary_target_series_id()
    batch_id = "batch_target_contract"
    content = GENESIS_ZIP.read_bytes()
    document = SourceDocument.from_content("DESTATIS_GENESIS", "https://genesis.destatis.de/genesisWS/rest/2020/data/tablefile",
                                           "Statistisches Bundesamt", content, batch_id, data_origin="live",
                                           available_at=FETCHED)
    raw = tmp_path / "raw" / document.content_sha256
    raw.parent.mkdir(parents=True)
    raw.write_bytes(content)
    document = document.model_copy(update={"raw_uri": raw.as_uri()})
    parsed = _make_stats_parser("DESTATIS_GENESIS")(
        {"series_key": "61261-0002", "required_series_keys": [key]}, content, document)
    measured = [o for o in parsed["observations"] if o.value is not None]
    assert measured and {o.series_id for o in parsed["observations"]} == {key}
    latest = max(measured, key=lambda o: o.period_start)
    pending = latest.model_copy(update={"period_start": "2026-01-01", "period_end_exclusive": "2026-04-01",
                                        "value": None, "original_value_text": "...",
                                        "missing_reason": "NOT_YET_PUBLISHED", "revision_id": "", "observation_id": ""})
    pending = Observation.model_validate(pending.model_dump())
    pending_link = EvidenceLink(assertion_version_id=pending.revision_id, assertion_kind="OBSERVATION",
                                document_version_id=document.version_id, batch_id=batch_id)
    store = DualLayerStore(storage_dir=str(tmp_path / "store"))
    batch = BatchManifest(batch_id=batch_id, source_id="DESTATIS_GENESIS", data_origin="live")
    store.stage_batch(batch, [document], [], parsed["observations"] + [pending],
                      evidence_links=parsed["evidence_links"] + [pending_link],
                      entities=[], series=parsed["series"])
    store.commit_batch(batch_id)
    return store, latest


def test_outlook_defaults_to_ingested_target_and_skips_unpublished_quarter(tmp_path):
    store, latest = _stage_real_genesis_response(tmp_path)
    outlook = get_construction_outlook(cutoff=FETCHED, store=store)

    quarter = (int(latest.period_start[5:7]) - 1) // 3 + 1
    assert outlook["latest_observation"]["value"] == latest.value
    assert outlook["latest_observation"]["period"] == f"{latest.period_start[:4]}-Q{quarter}"
    assert not any("Missing price data" in gap for gap in outlook["source_gaps"])


def test_ingested_target_is_a_series_node_in_the_as_of_graph(tmp_path):
    store, _ = _stage_real_genesis_response(tmp_path)
    key = primary_target_series_id()
    nodes = export_as_of(FETCHED, store=store)["nodes"]
    assert key in {n["id"] for n in nodes if n["node_type"] == "SERIES"}
    # Not visible before the series definition was available.
    assert key not in {n["id"] for n in export_as_of(datetime(2026, 1, 1, tzinfo=timezone.utc), store=store)["nodes"]}
