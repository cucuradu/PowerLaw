"""Web app entry point: `streamlit run dashboard.py`. One menu; each page reads data, never ingests or trains."""
import streamlit as st

st.set_page_config(page_title="German construction prices", page_icon="🏗️", layout="wide")
st.navigation([
    st.Page("overview.py", title="Overview", icon="🏗️", default=True),
    st.Page("pages/1_📜_Law_Registry.py", title="Laws", icon="📜", url_path="laws"),
    st.Page("pages/3_🕸️_Graph_Explorer.py", title="Graph", icon="🕸️", url_path="graph"),
    st.Page("pages/4_⏱️_Temporal_Table.py", title="Data", icon="⏱️", url_path="data"),
    st.Page("pages/6_⚙️_Ingestion_Console.py", title="Ingestion", icon="⚙️", url_path="ingestion"),
    # Not in the menu: pages/2_📰_News_Feed.py (news source disabled) and pages/5_🤖_GNN_Modeling_Lab.py (GNN paused).
]).run()
