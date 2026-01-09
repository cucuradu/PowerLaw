import streamlit as st
import lancedb
import pandas as pd
import json
import os
import altair as alt
import networkx as nx
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="GNN Power-Law Dashboard", layout="wide", page_icon="🕸️")

# Connect to DB
DB_PATH = os.getenv("LANCEDB_PATH", "data/lancedb")
try:
    db = lancedb.connect(DB_PATH)
    if "structured_rules" in db.table_names():
        table = db.open_table("structured_rules")
        df = table.search().limit(10000).to_pandas()
    else:
        st.error(f"Table 'structured_rules' not found in {DB_PATH}")
        st.stop()
except Exception as e:
    st.error(f"Failed to connect to DB: {e}")
    st.stop()

# --- HEADER ---
st.title("🕸️ GNN Power-Law Network Analysis")
st.markdown("Analysis of **Legal-Economic Connectivity** and **Node Degree Distributions**.")

if df.empty:
    st.warning("No data ingested yet.")
    st.stop()

# --- PARSE DATA ---
rules = []
nodes = []
edges = []

for _, row in df.iterrows():
    try:
        r = json.loads(row['gnn_rule_json'])
        
        # Rule Info
        rule_id = row['chunk_id']
        law_id = row['law_id']
        
        # Extract ALL fields from JSON
        metadata = r.get('metadata', {})
        scope = r.get('scope', {})
        timeline = r.get('timeline', {})
        mechanism = r.get('mechanism', {})
        enforcement = r.get('enforcement', {})
        
        rules.append({
            # Metadata
            "Law ID": law_id,
            "Article": metadata.get('article_id', ''),
            "Type": metadata.get('law_type', ''),
            "Confidence": metadata.get('parse_confidence', 0),
            
            # Scope
            "Regions": ", ".join(scope.get('regions', [])),
            "Node Types": ", ".join(scope.get('node_types', [])),
            "Sectors (NACE)": ", ".join(scope.get('industrial_sectors', [])),
            "Commodities": ", ".join(scope.get('affected_commodities', [])),
            
            # Timeline
            "Activation": timeline.get('activation_date', ''),
            "Expiration": timeline.get('expiration_date', 'N/A'),
            "Phase-In (days)": timeline.get('phase_in_period_days', 0),
            
            # Mechanism
            "Target Variable": mechanism.get('target_variable', ''),
            "Action": mechanism.get('action_type', ''),
            "Operator": mechanism.get('operator', ''),
            "Value": mechanism.get('value', ''),
            "Unit": mechanism.get('unit', ''),
            
            # Enforcement
            "Strictness": enforcement.get('strictness', ''),
            "Penalty Factor": enforcement.get('penalty_factor', ''),
            
            # Summary
            "Summary": r.get('summary', row['summary']),
            "Context": r.get('background_context', '')
        })
        
        # Graph Construction
        sectors = scope.get('industrial_sectors', [])
        commodities = scope.get('affected_commodities', [])
        
        for s in sectors:
            nodes.append({"id": s, "type": "SECTOR"})
            edges.append({"source": law_id, "target": s, "type": "AFFECTS"})
            
        for c in commodities:
            nodes.append({"id": c, "type": "COMMODITY"})
            edges.append({"source": law_id, "target": c, "type": "REGULATES"})

    except:
        pass

# --- 1. POWER LAW DISTRIBUTION ---
st.header("1. Scale-Free Network Properties")
col1, col2 = st.columns(2)

# Degree Centrality (Sectors)
sector_counts = {}
for e in edges:
    if e['target'] in [n['id'] for n in nodes if n['type'] == 'SECTOR']:
        sector_counts[e['target']] = sector_counts.get(e['target'], 0) + 1

sect_df = pd.DataFrame(sector_counts.items(), columns=['Sector', 'Degree']).sort_values('Degree', ascending=False)

with col1:
    st.subheader("Sector Connectivity (In-Degree)")
    chart = alt.Chart(sect_df).mark_bar().encode(
        x=alt.X('Sector', sort='-y'),
        y='Degree',
        color='Degree'
    )
    st.altair_chart(chart, use_container_width=True)

# Commodity Frequency
comm_counts = {}
for e in edges:
    if e['target'] in [n['id'] for n in nodes if n['type'] == 'COMMODITY']:
        comm_counts[e['target']] = comm_counts.get(e['target'], 0) + 1
        
comm_df = pd.DataFrame(comm_counts.items(), columns=['Commodity', 'Degree']).sort_values('Degree', ascending=False)

with col2:
    st.subheader("Commodity Regulation Frequency")
    if not comm_df.empty:
        chart2 = alt.Chart(comm_df.head(20)).mark_bar().encode(
            x=alt.X('Commodity', sort='-y'),
            y='Degree',
            color=alt.value('orange')
        )
        st.altair_chart(chart2, use_container_width=True)
    else:
        st.info("No commodities extracted yet.")

# --- 2. DATA INSPECTOR ---
st.divider()
st.header("2. Full Rule Inspector")

grid_df = pd.DataFrame(rules)

if not grid_df.empty:
    # All columns in one table
    st.dataframe(grid_df, use_container_width=True, height=600)
