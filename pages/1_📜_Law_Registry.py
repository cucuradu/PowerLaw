"""
ConstructPulse Law Registry & Logic Gate Inspector
Browse EU Construction Regulations, Extracted IF-THEN Logic Gates, and NACE Sector Mappings
"""

import json
import streamlit as st
import pandas as pd
import altair as alt

from src.storage.dashboard_data import get_all_laws_data, get_graph_store

st.set_page_config(
    page_title="EU Law Registry & Logic Gates",
    page_icon="📜",
    layout="wide"
)

# Header
st.title("📜 EU Law Registry & Executable Logic Gates")
st.markdown("""
Browse codified EU regulations, atomic **IF-THEN logic gates** extracted via Google Gemini, 
and their cross-industry **NACE Rev. 2 sector mappings**.
""")

# Load Laws (rebuilt only when the store changes)
@st.cache_data(show_spinner="Loading laws...")
def _laws(generation: int) -> list:
    return get_all_laws_data()


laws_data = _laws(get_graph_store()._generation)

if not laws_data:
    st.warning("No structured law data found in registry.")
    st.stop()

# --- SIDEBAR FILTERS ---
st.sidebar.header("🔍 Filter Regulations")

# Search query
search_query = st.sidebar.text_input("Search (CELEX, Title, Keyword)", "")

# Law type filter
all_types = sorted(list({l.get("rule_type", "REGULATORY") for l in laws_data}))
selected_types = st.sidebar.multiselect("Law Type", all_types, default=all_types)

# NACE Sector filter
all_sectors = set()
for l in laws_data:
    rule = l.get("rule") or {}
    scope = (rule.get("scope") or {}) if isinstance(rule, dict) else {}
    for s in (scope.get("industrial_sectors") or []):
        all_sectors.add(s)
sorted_sectors = sorted(list(all_sectors))
selected_sectors = st.sidebar.multiselect("NACE Industrial Sector", sorted_sectors, default=[])

# Strictness filter
selected_strictness = st.sidebar.multiselect("Strictness", ["HARD", "SOFT"], default=["HARD", "SOFT"])

# Apply filters
filtered_laws = []
for l in laws_data:
    rule = l.get("rule") or {}
    if not isinstance(rule, dict):
        rule = {}
    scope = rule.get("scope") or {}
    enf = rule.get("enforcement") or {}
    strictness = enf.get("strictness") or "HARD"
    sectors = scope.get("industrial_sectors") or []
    
    # Law Type filter
    if l.get("rule_type") not in selected_types:
        continue
        
    # Strictness filter
    if strictness not in selected_strictness:
        continue
        
    # Sector filter
    if selected_sectors and not any(s in selected_sectors for s in sectors):
        continue
        
    # Search query filter
    if search_query:
        q = search_query.lower()
        searchable_text = f"{l.get('law_id', '')} {l.get('title', '')} {l.get('summary', '')} {' '.join(sectors)}".lower()
        if q not in searchable_text:
            continue
            
    filtered_laws.append(l)


# --- TOP KPI METRICS ---
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric("Filtered Regulations", len(filtered_laws), delta=f"{len(laws_data)} in total")

with col2:
    total_logic_gates = len(filtered_laws)
    st.metric("Active Logic Gates", total_logic_gates)

with col3:
    impacted_sectors = set()
    for l in filtered_laws:
        rule = l.get("rule") or {}
        scope = (rule.get("scope") or {}) if isinstance(rule, dict) else {}
        impacted_sectors.update(scope.get("industrial_sectors") or [])
    st.metric("Impacted NACE Sectors", len(impacted_sectors))

with col4:
    hard_count = sum(1 for l in filtered_laws if ((l.get("rule") or {}).get("enforcement") or {}).get("strictness") == "HARD")
    ratio = (hard_count / max(len(filtered_laws), 1)) * 100
    st.metric("Hard Enforcement Ratio", f"{ratio:.0f}%", delta="Infinite Penalty" if ratio > 50 else "Finite Adjustment")

st.divider()


# --- MAIN TABS ---
tab1, tab2, tab3, tab4 = st.tabs([
    "🧩 Logic Gate & Rule Inspector",
    "🏭 NACE Sector Mappings",
    "📊 Structured Rules Grid & Export",
    "🔍 Raw Schema JSON"
])


# ============================================================================
# TAB 1: LOGIC GATE INSPECTOR
# ============================================================================
with tab1:
    st.subheader("Codified Statutory Logic Gates (Executable Rules)")
    st.markdown("Each EU regulation is decomposed into machine-readable IF-THEN conditional constraints.")

    if not filtered_laws:
        st.info("No regulations match your filter criteria.")
    else:
        for idx, item in enumerate(filtered_laws):
            law_id = item.get("law_id", "")
            title = item.get("title", f"Regulation {law_id}")
            summary = item.get("summary", "")
            rule = item.get("rule") or {}
            meta = (rule.get("metadata") or {}) if isinstance(rule, dict) else {}
            scope = (rule.get("scope") or {}) if isinstance(rule, dict) else {}
            timeline = (rule.get("timeline") or {}) if isinstance(rule, dict) else {}
            mechanism = (rule.get("mechanism") or {}) if isinstance(rule, dict) else {}
            enforcement = (rule.get("enforcement") or {}) if isinstance(rule, dict) else {}
            trigger = (mechanism.get("conditional_trigger") or {}) if isinstance(mechanism, dict) else {}

            with st.expander(f"📜 {law_id} — {title}", expanded=(idx == 0)):
                # Header row
                hcol1, hcol2, hcol3 = st.columns([3, 1, 1])
                with hcol1:
                    st.markdown(f"**CELEX ID:** `{law_id}` | **Article:** `{meta.get('article_id', 'N/A')}` | **Type:** `{item.get('rule_type', 'REGULATION')}`")
                    st.markdown(f"*{summary}*")
                with hcol2:
                    strict_val = enforcement.get("strictness", "HARD")
                    color = "#EF4444" if strict_val == "HARD" else "#F59E0B"
                    st.markdown(f"<div style='background-color: {color}; color: white; padding: 4px 8px; border-radius: 4px; text-align: center; font-weight: bold;'>STRICTNESS: {strict_val}</div>", unsafe_allow_html=True)
                    st.caption(f"Penalty Factor λ: {enforcement.get('penalty_factor', 1.0)}")
                with hcol3:
                    if item.get("source_url"):
                        st.link_button("EUR-Lex Source ↗", item["source_url"], width="stretch")

                st.markdown("---")

                # Visual IF-THEN Box
                st.markdown("##### ⚡ Executable Logic Gate:")
                
                # Format Trigger (IF)
                trig_var = trigger.get("variable", "condition")
                trig_op = trigger.get("operator", "==")
                trig_thresh = trigger.get("threshold", "ACTIVE")
                
                # Format Mechanism (THEN)
                mech_act = mechanism.get("action_type", "CONSTRAINT")
                mech_target = mechanism.get("target_variable", "parameter")
                mech_op = mechanism.get("operator", "SET_VALUE")
                mech_val = mechanism.get("value", 0.0)
                mech_unit = mechanism.get("unit", "")

                gate_col1, gate_col2 = st.columns(2)
                with gate_col1:
                    st.markdown(f"""
                    <div style="background-color: #064E3B; border-left: 5px solid #10B981; padding: 12px; border-radius: 6px;">
                        <span style="color: #6EE7B7; font-weight: bold; font-size: 0.9rem;">IF (CONDITIONAL TRIGGER):</span><br/>
                        <code style="color: #FFFFFF; font-size: 1.05rem;">{trig_var} {trig_op} {trig_thresh}</code>
                    </div>
                    """, unsafe_allow_html=True)

                with gate_col2:
                    st.markdown(f"""
                    <div style="background-color: #1E3A8A; border-left: 5px solid #3B82F6; padding: 12px; border-radius: 6px;">
                        <span style="color: #93C5FD; font-weight: bold; font-size: 0.9rem;">THEN (MANDATED MECHANISM):</span><br/>
                        <code style="color: #FFFFFF; font-size: 1.05rem;">{mech_act}: {mech_target} {mech_op} {mech_val} {mech_unit}</code>
                    </div>
                    """, unsafe_allow_html=True)

                # Scope & Timeline details
                st.markdown("<br/>", unsafe_allow_html=True)
                dcol1, dcol2, dcol3 = st.columns(3)
                with dcol1:
                    st.markdown("**NACE Industrial Sectors:**")
                    for s in scope.get("industrial_sectors", []):
                        st.markdown(f"- 🏭 `{s}`")
                with dcol2:
                    st.markdown("**Regulated Commodities:**")
                    commodities = scope.get("affected_commodities", [])
                    if commodities:
                        for c in commodities:
                            st.markdown(f"- 📦 `{c}`")
                    else:
                        st.markdown("- *General industrial activity*")
                with dcol3:
                    st.markdown("**Timeline & Phase-In:**")
                    st.markdown(f"- **Activation:** `{timeline.get('activation_date', 'N/A')}`")
                    st.markdown(f"- **Expiration:** `{timeline.get('expiration_date', 'Indefinite')}`")
                    st.markdown(f"- **Phase-in Period:** `{timeline.get('phase_in_period_days', 0)} days`")

                # Recital / Background context
                recital = item.get("recitals") or rule.get("background_context", "")
                if recital:
                    st.info(f"💡 **Legislative Recital Intent:** {recital}")


# ============================================================================
# TAB 2: NACE SECTOR MAPPING
# ============================================================================
with tab2:
    st.subheader("🏭 NACE Rev. 2 Industrial Sector Exposure")
    st.markdown("Distribution of regulatory mandates across official European Statistical Classification sectors.")

    # Aggregate sector counts
    sector_counts = {}
    sector_to_laws = {}
    for l in laws_data:
        rule = l.get("rule") or {}
        scope = (rule.get("scope") or {}) if isinstance(rule, dict) else {}
        for s in (scope.get("industrial_sectors") or []):
            sector_counts[s] = sector_counts.get(s, 0) + 1
            if s not in sector_to_laws:
                sector_to_laws[s] = []
            sector_to_laws[s].append(str(l.get("law_id", "")))

    if sector_counts:
        sect_df = pd.DataFrame(list(sector_counts.items()), columns=["NACE Code", "Rule Count"]).sort_values("Rule Count", ascending=False)
        
        # Sector descriptions dictionary
        sector_names = {
            "C24": "C24: Basic Metals (Steel, Aluminum)",
            "D35": "D35: Electricity, Gas & Steam Supply",
            "C20": "C20: Chemicals & Chemical Products",
            "C23": "C23: Non-metallic Minerals (Cement, Glass)",
            "A01": "A01: Crop & Animal Production (Agriculture)",
            "H50": "H50: Water Transport (Maritime Shipping)",
            "H49": "H49: Land Transport (Rail, Road)",
            "H51": "H51: Air Transport",
            "B06": "B06: Extraction of Crude Oil & Gas",
            "B07": "B07: Mining of Metal Ores (Lithium, Nickel)",
            "C29": "C29: Manufacture of Motor Vehicles",
            "C10": "C10: Food Products",
            "E38": "E38: Waste Collection & Decontamination",
            "B05": "B05: Mining of Coal & Lignite",
            "J62": "J62: Computer Programming & IT"
        }
        sect_df["Sector Description"] = sect_df["NACE Code"].map(lambda c: sector_names.get(c, c))

        chart = alt.Chart(sect_df).mark_bar(cornerRadius=4).encode(
            x=alt.X("Rule Count:Q", title="Number of Mandating Regulations"),
            y=alt.Y("Sector Description:N", sort="-x", title="NACE Industrial Sector"),
            color=alt.Color("Rule Count:Q", scale=alt.Scale(scheme="blues"), legend=None),
            tooltip=["NACE Code", "Sector Description", "Rule Count"]
        ).properties(height=350)

        st.altair_chart(chart, width="stretch")

        st.markdown("#### 📋 Sector Regulatory Exposure Mapping")
        mapping_rows = []
        for s, cnt in sector_counts.items():
            laws_list = ", ".join(sector_to_laws.get(s, []))
            mapping_rows.append({
                "NACE Code": s,
                "Sector Description": sector_names.get(s, s),
                "Regulations Count": cnt,
                "Governing Laws (CELEX)": laws_list
            })
        st.dataframe(pd.DataFrame(mapping_rows), width="stretch", hide_index=True)
    else:
        st.info("No NACE sector mappings found.")


# ============================================================================
# TAB 3: STRUCTURED RULES GRID & EXPORT
# ============================================================================
with tab3:
    st.subheader("📊 Full Structured Rules Catalog")
    st.markdown("Exhaustive tabular export of all logic gates and parameters.")

    table_records = []
    for l in filtered_laws:
        rule = l.get("rule") or {}
        meta = (rule.get("metadata") or {}) if isinstance(rule, dict) else {}
        scope = (rule.get("scope") or {}) if isinstance(rule, dict) else {}
        timeline = (rule.get("timeline") or {}) if isinstance(rule, dict) else {}
        mechanism = (rule.get("mechanism") or {}) if isinstance(rule, dict) else {}
        enforcement = (rule.get("enforcement") or {}) if isinstance(rule, dict) else {}
        trigger = (mechanism.get("conditional_trigger") or {}) if isinstance(mechanism, dict) else {}

        table_records.append({
            "Law ID": l.get("law_id", ""),
            "Article": meta.get("article_id", ""),
            "Type": l.get("rule_type", ""),
            "Trigger Var": trigger.get("variable", ""),
            "Trigger Op": trigger.get("operator", ""),
            "Trigger Threshold": trigger.get("threshold", ""),
            "Action Type": mechanism.get("action_type", ""),
            "Target Variable": mechanism.get("target_variable", ""),
            "Operator": mechanism.get("operator", ""),
            "Value": mechanism.get("value", 0.0),
            "Unit": mechanism.get("unit", ""),
            "Strictness": enforcement.get("strictness", ""),
            "Penalty λ": enforcement.get("penalty_factor", 1.0),
            "Sectors (NACE)": ", ".join(scope.get("industrial_sectors") or []),
            "Commodities": ", ".join(scope.get("affected_commodities") or []),
            "Activation": timeline.get("activation_date", ""),
            "Expiration": timeline.get("expiration_date", ""),
            "Summary": l.get("summary", "")
        })

    grid_df = pd.DataFrame(table_records)
    st.dataframe(grid_df, width="stretch", height=500)

    # Download controls
    dcol1, dcol2 = st.columns(2)
    with dcol1:
        csv_data = grid_df.to_csv(index=False).encode("utf-8")
        st.download_button("📥 Download Catalog as CSV", csv_data, "eu_law_logic_gates.csv", "text/csv", width="stretch")
    with dcol2:
        json_data = json.dumps(filtered_laws, indent=2).encode("utf-8")
        st.download_button("📥 Download Catalog as JSON", json_data, "eu_law_logic_gates.json", "application/json", width="stretch")


# ============================================================================
# TAB 4: RAW SCHEMA JSON INSPECTOR
# ============================================================================
with tab4:
    st.subheader("🔍 Schema-Validated GNNRule JSON Payload")
    st.markdown("Inspect raw structured JSON instances compliant with `src.schemas.rule_schema.GNNRule`.")

    if not filtered_laws:
        st.info("No regulations match your filter criteria.")
    else:
        selected_celex = st.selectbox("Select Regulation to Inspect:", [l.get("law_id") for l in filtered_laws])
        selected_item = next((l for l in filtered_laws if l.get("law_id") == selected_celex), None)

        if selected_item:
            st.json(selected_item.get("rule") or {})
