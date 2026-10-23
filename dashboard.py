"""Web app entry point: `streamlit run dashboard.py`. One menu; each page reads data, never ingests or trains."""
import streamlit as st

st.set_page_config(page_title="German construction prices", page_icon="🏗️", layout="wide")
st.navigation([
    st.Page("overview.py", title="Overview", icon="🏗️", default=True),
    st.Page("views/graph.py", title="Graph", icon="🕸️", url_path="graph"),
    st.Page("views/laws.py", title="Laws", icon="📜", url_path="laws"),
    st.Page("views/ingestion.py", title="Ingestion", icon="⚙️", url_path="ingestion"),
]).run()
