"""
tox_engine.py  -  The "brain" of the app (a rule-based expert system).
You do NOT run this file directly. app.py and the step scripts import it.

An expert system is a classic form of Artificial Intelligence: the knowledge of
experts is written down as data (the knowledge base) plus IF-THEN rules, and the
computer applies those rules the same way every time.

Main functions:
  load_kb()          read knowledge_base.xlsx
  find_drug()        find a drug even if it is misspelt or a brand name is used (fuzzy matching)
  to_mg_per_L()      convert any common unit to mg/L
  classify()         compare a concentration with therapeutic / toxic / fatal ranges
  pmr_note()         warning about post-mortem redistribution
  interpret_case()   run everything for one case (one or more drugs)
"""

import math

import pandas as pd
from rapidfuzz import fuzz, process

KB_FILE = "knowledge_base.xlsx"
MATCH_CUTOFF = 80          # fuzzy-match score (0-100) needed to accept a name

UNIT_TO_MG_PER_L = {
    "mg/l": 1.0, "ug/ml": 1.0, "µg/ml": 1.0, "mcg/ml": 1.0,
    "ug/l": 0.001, "µg/l": 0.001, "mcg/l": 0.001, "ng/ml": 0.001,
    "mg/dl": 10.0, "mg/100ml": 10.0, "mg%": 10.0,
    "g/l": 1000.0,
}
UNIT_CHOICES = ["mg/L", "µg/mL", "ng/mL", "µg/L", "mg/dL", "g/L"]

SITES = ["Femoral (peripheral) blood", "Heart (central) blood",
         "Antemortem blood / plasma / serum", "Unknown"]

SIMPLE = {
    "Below therapeutic range": "Non-toxic",
    "Therapeutic range": "Non-toxic",
    "Above therapeutic range": "Toxic",
    "Toxic range": "Toxic",
    "Comatose-fatal range": "Potentially fatal",
}


def _num(x):
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def load_kb(path=KB_FILE):
    kb = pd.read_excel(path, sheet_name="knowledge_base")
    kb["excel_row"] = kb.index + 2          # row number as seen in Excel (row 1 = headings)
    kb = kb[kb["drug"].notna()].copy()
    kb["drug"] = kb["drug"].astype(str).str.strip()
    kb = kb[~kb["drug"].str.upper().str.startswith("EXAMPLE")]
    kb["synonyms"] = kb["synonyms"].fillna("").astype(str)
    kb["drug_class"] = kb["drug_class"].fillna("").astype(str).str.strip()
    kb["cns_depressant"] = kb["cns_depressant"].fillna("No").astype(str).str.strip().str.lower()
    return kb.reset_index(drop=True)


def _name_index(kb):
    """List of (searchable name, row number) including synonyms and brand names."""
    idx = []
    for i, row in kb.iterrows():
        idx.append((row["drug"].lower(), i))
        for s in row["synonyms"].split(";"):
            if s.strip():
                idx.append((s.strip().lower(), i))
    return idx


def find_drug(name, kb):
    """Return (row, score, matched_text) or (None, best_score, best_text)."""
    idx = _name_index(kb)
    choices = [n for n, _ in idx]
    best = process.extractOne(name.strip().lower(), choices, scorer=fuzz.WRatio)
    if best is None:
        return None, 0, ""
    text, score, pos = best
    if score < MATCH_CUTOFF:
        return None, score, text
    return kb.loc[idx[pos][1]], score, text


def to_mg_per_L(value, unit):
    key = str(unit).strip().lower().replace(" ", "").replace("μ", "µ")
    key = key.split("(")[0]
    if key not in UNIT_TO_MG_PER_L:
        raise ValueError(f"Unknown unit '{unit}'. Use one of: {', '.join(UNIT_CHOICES)}")
    return float(value) * UNIT_TO_MG_PER_L[key]


def classify(conc, row):
    """Compare a concentration (mg/L) with the reference ranges of one drug."""
    tl, th = _num(row.get("ther_low")), _num(row.get("ther_high"))
    tox, fat = _num(row.get("toxic_from")), _num(row.get("fatal_from"))
    if fat is not None and conc >= fat:
        return "Comatose-fatal range"
    if tox is not None and conc >= tox:
        return "Toxic range"
    if th is not None and conc > th:
        return "Above therapeutic range"
    if tl is not None and conc >= tl:
        return "Therapeutic range"
    if tl is not None and conc < tl:
        return "Below therapeutic range"
    if th is not None and conc <= th:
        return "Therapeutic range"
    return "Cannot classify (no reference values)"


def pmr_note(row, site):
    vd = _num(row.get("vd_L_kg"))
    if site.startswith("Antemortem"):
        return "Antemortem sample: post-mortem redistribution does not apply."
    if vd is None:
        return "Volume of distribution not in the database: post-mortem redistribution risk unknown."
    if vd >= 3:
        if site.startswith("Heart"):
            return (f"HIGH risk of post-mortem redistribution (Vd = {vd:g} L/kg, sample = heart blood). "
                    "The measured value may be falsely high. A femoral blood result is preferred.")
        return (f"Drug has a large volume of distribution (Vd = {vd:g} L/kg), so post-mortem "
                "redistribution is possible even in peripheral blood. Interpret with caution.")
    return f"Lower post-mortem redistribution potential (Vd = {vd:g} L/kg)."


def interpret_case(drugs, site, kb):
    """
    drugs = list of dicts: {"name": ..., "value": ..., "unit": ...}
    Returns a list of result dicts plus a list of case-level warnings and a report text.
    """
    results, warnings = [], []
    for d in drugs:
        row, score, matched = find_drug(d["name"], kb)
        if row is None:
            results.append({"input_name": d["name"], "found": False, "match_score": score,
                            "suggestion": matched})
            continue
        conc = to_mg_per_L(d["value"], d["unit"])
        cat = classify(conc, row)
        r = {
            "input_name": d["name"], "found": True, "drug": row["drug"], "match_score": score,
            "drug_class": row["drug_class"], "conc_mg_L": conc, "category": cat,
            "simple_category": SIMPLE.get(cat, "Unclassified"),
            "ther_low": _num(row.get("ther_low")), "ther_high": _num(row.get("ther_high")),
            "toxic_from": _num(row.get("toxic_from")), "fatal_from": _num(row.get("fatal_from")),
            "half_life_h": _num(row.get("half_life_h")),
            "cns": row["cns_depressant"] in ("yes", "y", "1", "true"),
            "pmr": pmr_note(row, site),
        }
        if row["drug_class"].lower() in ("opioid", "opioids"):
            r["extra"] = ("Opioid tolerance: regular users may survive concentrations that are fatal "
                          "to non-users. Case history is essential.")
        results.append(r)

    found = [r for r in results if r["found"]]
    cns = [r["drug"] for r in found if r["cns"]]
    if len(cns) >= 2:
        warnings.append("Two or more CNS depressants detected (" + ", ".join(cns) + "). Their effects on "
                        "breathing and consciousness add up, so the combination can be dangerous even "
                        "when each drug alone is in or near the therapeutic range.")
    classes = {r["drug_class"].lower() for r in found}
    if any("opioid" in c for c in classes) and any("benzodiazepine" in c for c in classes):
        warnings.append("Opioid + benzodiazepine combination: a well-known high-risk combination "
                        "for fatal respiratory depression.")
    return results, warnings, make_report(results, warnings, site)


def _fmt(x):
    return "n/a" if x is None else f"{x:g}"


def make_report(results, warnings, site):
    lines = ["AUTOMATED INTERPRETATION (decision support only)", f"Sample: {site}", ""]
    for r in results:
        if not r["found"]:
            lines.append(f"- '{r['input_name']}': not found in the knowledge base "
                         f"(closest: '{r['suggestion']}'). No interpretation made.")
            continue
        lines.append(
            f"- {r['drug']}: {r['conc_mg_L']:g} mg/L -> {r['category'].upper()}. "
            f"Reference values (mg/L): therapeutic {_fmt(r['ther_low'])}-{_fmt(r['ther_high'])}, "
            f"toxic from {_fmt(r['toxic_from'])}, comatose-fatal from {_fmt(r['fatal_from'])}. "
            f"{r['pmr']}" + (f" {r['extra']}" if r.get("extra") else ""))
    if warnings:
        lines.append("")
        lines.append("Combination warnings:")
        lines.extend("- " + w for w in warnings)
    lines += ["",
              "Limitations: reference ranges are only an orientation. Tolerance, drug interactions, "
              "disease, time between intake and sampling, and post-mortem changes can change the meaning "
              "of a concentration. The final opinion must be given by a qualified forensic toxicologist "
              "or pathologist using the full case history and autopsy findings."]
    return "\n".join(lines)
