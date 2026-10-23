"""Overview page: where German construction prices are, where they are heading, and what would move them.

Reads only the report files written by the weekly update (src.storage.home_data); never opens the store.
"""
import altair as alt
import pandas as pd
import streamlit as st

from src.storage.dashboard_data import get_forecast_surprises, get_price_events
from src.storage.home_data import (accuracy_rows, exposure_rows, headline, load_reports, price_path,
                                   projection_rows, report_versions, scenario_rows)

BLUE, ORANGE, GREY = "#2563EB", "#F97316", "#9CA3AF"


@st.cache_data(show_spinner=False)
def _reports(versions: tuple) -> dict:  # reloads only when a report file changes
    return load_reports()


@st.cache_data(show_spinner=False)
def _misses(versions: tuple) -> list:
    return get_forecast_surprises(load_reports()["forecast"])


reports = _reports(report_versions())
top = headline(reports)
pct = lambda v: "n/a" if v is None else f"{v:+.1f}%"
span = lambda r: "" if not r or None in r else f"{r[0]:+.1f} to {r[1]:+.1f}%"

# --- Header ---------------------------------------------------------------------------------------------------
st.title("🏗️ German construction prices")
st.caption("Price index for building new homes (Destatis 61261-0002, incl. VAT, 2021 = 100). "
           + (f"Forecast made {reports['forecast']['generated_at'][:10]}." if reports["forecast"] else ""))
if reports["failed_steps"]:
    st.warning("The last automatic update failed at: " + ", ".join(reports["failed_steps"])
               + ". Figures may be stale; see the Ingestion Console.")
if not reports["forecast"]:
    st.info("No forecast yet. Run `python -m scripts.ops.update_forecast` (or the steps it lists) to create the reports.")
    st.stop()

# --- Headline numbers -----------------------------------------------------------------------------------------
cols = st.columns(5)
latest = top["latest"]
if latest:
    cols[0].metric(f"Latest · {latest['quarter']}", f"{latest['value']:.1f}", f"{latest['change_pct']:+.1f}% on the quarter",
                   delta_color="off", delta_arrow="off", border=True)
if top["next"]:
    cols[1].metric(f"Next quarter · {top['next']['quarter']}", pct(top["next"]["change_pct"]), span(top["next"]["range"]),
                   delta_color="off", delta_arrow="off", border=True)
for col, h in zip(cols[2:], (4, 8, 12)):
    f = top["years"].get(h)
    if f:
        col.metric(f"In {h // 4} year{'s' if h > 4 else ''} · {f['quarter']}", pct(f["change_pct"]), span(f["range"]),
                   delta_color="off", delta_arrow="off", border=True)

# --- Main chart: history and the path ahead -------------------------------------------------------------------
since = st.segmented_control("Show since", ["1960", "2000", "2015"], default="2000", label_visibility="collapsed")
path = price_path(reports, since=f"{since or '2000'}-01-01")
history, ahead, band = (pd.DataFrame(path[k]) for k in ("history", "forecast", "band"))
x = alt.X("date:T", title=None)
layers = [alt.Chart(history).mark_line(color=BLUE, strokeWidth=2.5).encode(
    x, alt.Y("level:Q", title="Index (2021 = 100)", scale=alt.Scale(zero=False)),
    tooltip=[alt.Tooltip("quarter:N", title="Quarter"), alt.Tooltip("level:Q", title="Index", format=".1f")])]
if not band.empty:
    layers.append(alt.Chart(band).mark_area(color=ORANGE, opacity=0.18).encode(x, y="low:Q", y2="high:Q"))
if not ahead.empty:
    layers += [alt.Chart(ahead).mark_line(color=ORANGE, strokeDash=[6, 4], strokeWidth=2.5).encode(x, y="level:Q"),
               alt.Chart(ahead.iloc[1:]).mark_point(color=ORANGE, filled=True, size=80).encode(
                   x, y="level:Q", tooltip=[alt.Tooltip("label:N", title="Horizon"), alt.Tooltip("quarter:N", title="Quarter"),
                                            alt.Tooltip("change_pct:Q", title="Change %", format="+.1f"),
                                            alt.Tooltip("low:Q", title="Low", format=".1f"),
                                            alt.Tooltip("high:Q", title="High", format=".1f")])]
if st.toggle("Show major events", value=False):
    events = pd.DataFrame(get_price_events())
    events = events[events["date"] >= f"{since or '2000'}-01-01"]
    layers.append(alt.Chart(events).mark_rule(color=GREY, strokeDash=[3, 3]).encode(
        x="date:T", tooltip=[alt.Tooltip("date:N", title="Date"), alt.Tooltip("title:N", title="Event")]))
st.altair_chart(alt.layer(*layers).properties(height=380), width="stretch")
st.caption("Blue: published index. Orange: our forecast from the latest release; the shaded band is the range of "
           "changes seen over the same span since 1968 (held in about 8 of 10 cases from 2013 on).")

# --- What would move it / how accurate ------------------------------------------------------------------------
left, right = st.columns(2)
with left, st.container(border=True):
    st.subheader("What would move it")
    scenarios = pd.DataFrame(scenario_rows(reports))
    if not scenarios.empty:
        order = ["Current signals", "Materials weak", "Materials typical", "Materials strong", "Like 2021-23"]
        st.altair_chart(alt.Chart(scenarios).mark_bar().encode(
            x=alt.X("horizon:N", title=None, sort=["1 year", "2 years", "3 years"], axis=alt.Axis(labelAngle=0)),
            xOffset=alt.XOffset("scenario:N", sort=order),
            y=alt.Y("change_pct:Q", title="Price change (%)"),
            color=alt.Color("scenario:N", sort=order, title=None, legend=alt.Legend(orient="bottom", columns=3),
                            scale=alt.Scale(domain=order, range=[BLUE, "#86EFAC", "#FCD34D", ORANGE, "#DC2626"])),
            tooltip=[alt.Tooltip("scenario:N", title="If"), alt.Tooltip("horizon:N", title="Over"),
                     alt.Tooltip("change_pct:Q", title="Change %", format="+.1f")]).properties(height=300), width="stretch")
        st.caption("Prices follow building costs (60% materials, 40% labour). Labour keeps its trend; each bar is one "
                   "path for material costs. When the cost path is right this rule misses by about half as much as "
                   "the trend, so the hard part is materials.")
with right, st.container(border=True):
    st.subheader("How accurate it has been")
    accuracy = pd.DataFrame(accuracy_rows(reports))
    if not accuracy.empty:
        methods = ["Our forecast", "10-year trend", "Last year's average"]
        base = accuracy[accuracy["method"] == "No change"].set_index("horizon")["miss"]
        accuracy = accuracy[accuracy["method"] != "No change"].assign(relative=lambda d: 100 * d["miss"] / d["horizon"].map(base))
        st.altair_chart((alt.Chart(accuracy).mark_bar().encode(
            x=alt.X("horizon:N", title=None, sort=["Next quarter", "1 year", "2 years", "3 years"], axis=alt.Axis(labelAngle=0)),
            xOffset=alt.XOffset("method:N", sort=methods),
            y=alt.Y("relative:Q", title="Miss, % of the 'no change' miss", scale=alt.Scale(domain=[0, 110])),
            color=alt.Color("method:N", sort=methods, title=None, legend=alt.Legend(orient="bottom"),
                            scale=alt.Scale(domain=methods, range=[BLUE, ORANGE, "#A78BFA"])),
            tooltip=[alt.Tooltip("method:N", title="Method"), alt.Tooltip("horizon:N", title="Horizon"),
                     alt.Tooltip("miss:Q", title="Typical miss (points)", format=".2f"),
                     alt.Tooltip("relative:Q", title="% of no-change miss", format=".0f")])
            + alt.Chart(pd.DataFrame({"y": [100]})).mark_rule(color=GREY, strokeDash=[4, 4]).encode(y="y:Q")
            ).properties(height=300), width="stretch")
        st.caption("Size of the typical miss on past releases relative to assuming no change (dashed line = 100%), "
                   "using only data public at the time (next quarter: 2006 on; 1-3 years: 2013 on, including the "
                   "2021-23 surge). Lower is better; hover for the misses in points.")

# --- Trades and materials -------------------------------------------------------------------------------------
st.subheader("Next quarter for trades, materials and energy")


def bar_chart(rows: list) -> alt.Chart:
    data = pd.DataFrame(rows)
    data["Tested"] = data["reliable"].map({True: "beat 'no change' in tests", False: "no better than 'no change'"})
    y = alt.Y("name:N", sort=data["name"].tolist(), title=None, axis=alt.Axis(labelLimit=320, labelOverlap=False))
    bars = alt.Chart(data).mark_bar().encode(
        x=alt.X("change_pct:Q", title="Change next quarter (%)"), y=y,
        color=alt.Color("Tested:N", title=None, legend=alt.Legend(orient="bottom"),
                        scale=alt.Scale(domain=["beat 'no change' in tests", "no better than 'no change'"], range=[BLUE, GREY])),
        tooltip=[alt.Tooltip("name:N", title="Series"), alt.Tooltip("quarter:N", title="Quarter"),
                 alt.Tooltip("change_pct:Q", title="Change %", format="+.2f"), alt.Tooltip("low:Q", title="Low %", format="+.1f"),
                 alt.Tooltip("high:Q", title="High %", format="+.1f"), alt.Tooltip("miss:Q", title="Typical miss", format=".2f")])
    ranges = alt.Chart(data.dropna(subset=["low", "high"])).mark_rule(color=GREY, opacity=0.6).encode(x="low:Q", x2="high:Q", y=y)
    return (ranges + bars).properties(height=max(200, 24 * len(data)))


trades_tab, materials_tab, exposure_tab = st.tabs(["Building trades", "Materials and energy", "Exposure to costs"])
with trades_tab:
    rows = projection_rows(reports, "output_price")
    if rows:
        st.altair_chart(bar_chart(rows), width="stretch")
with materials_tab:
    rows = projection_rows(reports, "producer_price")
    if rows:
        st.altair_chart(bar_chart(rows), width="stretch")
with exposure_tab:
    exposure = pd.DataFrame(exposure_rows(reports))
    if not exposure.empty:
        order = exposure.sort_values("order")["trade"].drop_duplicates().tolist()
        st.altair_chart(alt.Chart(exposure).mark_bar().encode(
            x=alt.X("change_pct:Q", title="Price change over a year (%)"),
            y=alt.Y("trade:N", sort=order, title=None), yOffset="cost:N",
            color=alt.Color("cost:N", title=None, legend=alt.Legend(orient="bottom"), scale=alt.Scale(range=[ORANGE, BLUE])),
            tooltip=["trade:N", "cost:N", alt.Tooltip("change_pct:Q", title="Change %", format="+.1f")]
        ).properties(height=max(300, 26 * len(order))), width="stretch")
        st.caption("How much each trade's price moved when material or labour costs rose 10% over a year (2001-2026, "
                   "mostly the 2021-23 surge). A ranking of exposure, not exact pass-through.")

# --- Background -----------------------------------------------------------------------------------------------
with st.expander("Biggest past misses of the next-quarter forecast"):
    misses = _misses(report_versions())
    if misses:
        st.dataframe(pd.DataFrame([{"Quarter": m["quarter"], "Actual %": round(m["actual_pct"], 2),
                                    "Forecast %": round(m["forecast_pct"], 2), "Miss": round(m["miss_pp"], 2),
                                    "Events in the 6 months before": "; ".join(m["events"]) or "none listed"}
                                   for m in misses]), hide_index=True, width="stretch")
with st.expander("How this works"):
    scores = reports.get("scores") or {}
    st.markdown(
        "- **Next quarter:** a model on VAT, the seasonal pattern, orders, material and energy prices and builders' "
        "price expectations, each used only from the day it was published.\n"
        "- **1-3 years:** chosen on 1998-2012 and scored once on 2013 onward; the range shows what happened over the "
        "same span since 1968.\n"
        "- **What would move it:** prices follow building costs; materials are the uncertain part.\n"
        f"- **Track record:** every forecast is saved before its outcome is known ({scores.get('entries', 0)} so far); "
        "the first are scored with the 2026-Q4 release in January 2027.\n"
        "- Details: `docs/agent-memory/decisions.md`. Other pages: laws, graph, data and the ingestion console.")
