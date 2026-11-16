"""
app.py  -  The web application.

How to run:
    python -m streamlit run app.py
A browser tab opens at http://localhost:8501
To stop: click the Command Prompt window and press Ctrl + C
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import streamlit as st

from tox_engine import SITES, UNIT_CHOICES, interpret_case, load_kb

st.set_page_config(page_title="PM Tox Interpreter", layout="wide")


@st.cache_data
def get_kb():
    return load_kb()


COLORS = {"Below therapeutic range": "#7f8c8d", "Therapeutic range": "#27ae60",
          "Above therapeutic range": "#e67e22", "Toxic range": "#d35400",
          "Comatose-fatal range": "#c0392b"}


def range_chart(r):
    """Horizontal bar showing where the measured value sits (log scale)."""
    vals = [v for v in [r["ther_low"], r["ther_high"], r["toxic_from"], r["fatal_from"], r["conc_mg_L"]]
            if v is not None and v > 0]
    lo, hi = min(vals) / 5, max(vals) * 5
    fig, ax = plt.subplots(figsize=(7, 1.6))
    ax.set_xscale("log")
    ax.set_xlim(lo, hi)
    bands = []
    if r["ther_low"] is not None and r["ther_high"] is not None:
        bands.append((r["ther_low"], r["ther_high"], "#27ae60", "Therapeutic"))
    if r["toxic_from"] is not None:
        end = r["fatal_from"] if r["fatal_from"] is not None and r["fatal_from"] > r["toxic_from"] else hi
        bands.append((r["toxic_from"], end, "#e67e22", "Toxic"))
    if r["fatal_from"] is not None:
        bands.append((r["fatal_from"], hi, "#c0392b", "Comatose-fatal"))
    for a, b, c, lab in bands:
        ax.axvspan(a, b, color=c, alpha=0.35, label=lab)
    ax.axvline(r["conc_mg_L"], color="black", linewidth=2.5, label="Measured")
    ax.set_yticks([])
    ax.set_xlabel("Concentration (mg/L, log scale)")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.55), ncol=4, fontsize=8, frameon=False)
    plt.tight_layout()
    return fig


st.title("Post-mortem Toxicology Interpreter")
st.caption("Decision-support tool for interpreting blood drug concentrations. "
           "Education and research use only - the final opinion belongs to a qualified expert.")

try:
    kb = get_kb()
except FileNotFoundError:
    st.error("knowledge_base.xlsx not found. Put it in the same folder as app.py.")
    st.stop()

tab_case, tab_kb, tab_help = st.tabs(["Interpret a case", "Knowledge base", "How it works"])

with tab_case:
    site = st.selectbox("Sample type", SITES)
    n = st.number_input("Number of drugs detected", min_value=1, max_value=8, value=1, step=1)
    drugs = []
    for i in range(int(n)):
        c1, c2, c3 = st.columns([3, 2, 2])
        name = c1.text_input(f"Drug {i+1} name", key=f"name{i}")
        value = c2.number_input(f"Concentration {i+1}", min_value=0.0, format="%.4f", key=f"val{i}")
        unit = c3.selectbox(f"Unit {i+1}", UNIT_CHOICES, key=f"unit{i}")
        if name.strip() and value > 0:
            drugs.append({"name": name, "value": value, "unit": unit})

    if st.button("Interpret", type="primary"):
        if not drugs:
            st.warning("Enter at least one drug name and a concentration above zero.")
        else:
            results, warnings, report = interpret_case(drugs, site, kb)
            for r in results:
                st.divider()
                if not r["found"]:
                    st.error(f"'{r['input_name']}' was not found. Closest name: '{r['suggestion']}'. "
                             "Check the spelling or add the drug to the knowledge base.")
                    continue
                head = f"### {r['drug']}  ·  {r['conc_mg_L']:g} mg/L"
                if r["match_score"] < 100:
                    head += f"  \n*(you typed '{r['input_name']}', matched with {r['match_score']:.0f}% similarity)*"
                st.markdown(head)
                color = COLORS.get(r["category"], "#555")
                st.markdown(f"<div style='background:{color};color:white;padding:8px 14px;border-radius:6px;"
                            f"display:inline-block;font-weight:600'>{r['category']}</div>",
                            unsafe_allow_html=True)
                if r["category"] != "Cannot classify (no reference values)":
                    st.pyplot(range_chart(r))
                st.info(r["pmr"])
                if r.get("extra"):
                    st.warning(r["extra"])
                if r["half_life_h"] is not None:
                    st.caption(f"Elimination half-life: about {r['half_life_h']:g} h")
            for w in warnings:
                st.error(w)
            st.divider()
            st.subheader("Draft interpretation text")
            st.text_area("You can copy this text", report, height=260)
            st.download_button("Download as .txt", report, file_name="interpretation.txt")

with tab_kb:
    q = st.text_input("Search the knowledge base")
    view = kb if not q else kb[kb["drug"].str.contains(q, case=False) |
                               kb["synonyms"].str.contains(q, case=False)]
    view = view.drop(columns=["excel_row"], errors="ignore").copy()
    for col in ["ther_low", "ther_high", "toxic_from", "fatal_from", "half_life_h", "vd_L_kg"]:
        if col in view:
            view[col] = view[col].apply(lambda v: v if isinstance(v, (int, float)) else str(v))
            view[col] = view[col].astype(str).replace("nan", "")
    st.dataframe(view, hide_index=True, width="stretch")
    st.caption(f"{len(kb)} drugs. Values in mg/L. Source: Schulz et al., Critical Care 2020 (CC BY 4.0) "
               "plus volume-of-distribution data from the sources listed in the 'source' column.")

with tab_help:
    st.markdown("""
**What the tool does**
1. Finds the drug you typed, even with small spelling mistakes or brand names (fuzzy matching).
2. Converts your unit to mg/L.
3. Compares the value with published therapeutic, toxic and comatose-fatal concentrations.
4. Warns about post-mortem redistribution (PMR) using the drug's volume of distribution and the sample site.
5. Warns when several CNS depressants are present together.
6. Writes a draft interpretation paragraph.

**What it cannot do**
It cannot know tolerance, the time of intake, disease, or autopsy findings. Reference ranges overlap,
and the authors of the source table themselves say the values are only an orientation.
""")
