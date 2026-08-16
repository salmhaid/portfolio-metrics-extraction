"""Normalize: deterministic rules on top of raw extractions.

- Validate canonical_metric assignments against config/metrics.yaml (unknown -> non-canonical).
- Entity resolution for known renames (ENTITY_MAP) + short-name derivation for readability.
- Source precedence: standalone report > portfolio snapshot; later filing > earlier filing
  for the same (company, metric, period) — superseded rows retained and flagged, never dropped.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd

from .schema import DocumentExtraction

log = logging.getLogger("normalize")

# Known entity renames: name as printed -> canonical company name.
# FleetLink rebranded to Apex Freight Solutions effective Apr 2025 (stated in ApexFreight_Q2_2025).
ENTITY_MAP = {
    "FleetLink Logistics Network": "ApexFreight",
    "FleetLink": "ApexFreight",
    "Apex Freight Solutions Inc.": "ApexFreight",
    "Apex Freight Solutions": "ApexFreight",
}


def canonical_company(name: str) -> str:
    """Resolve known renames, then shorten 'NovaCloud Analytics Inc.' -> 'NovaCloud'.

    The first-word heuristic works for this portfolio's naming; a real system would use a
    maintained entity registry (see README next steps).
    """
    name = name.strip()
    for raw, canon in ENTITY_MAP.items():
        if raw.lower() in name.lower():
            return canon
    return name.split()[0] if name else name


def _period_sort_key(period: str) -> tuple[int, int]:
    """'Q2 2025' -> (2025, 2). Unparseable periods sort first (lowest precedence)."""
    m = re.match(r"Q([1-4])\s+(\d{4})", period.strip())
    return (int(m.group(2)), int(m.group(1))) if m else (0, 0)


def norm_value(v: str) -> str:
    """Comparison form of a printed value: '$ 8.4M' == '$8.4M' == '8.4m'."""
    return re.sub(r"[\s$£€,]", "", str(v)).lower()


# A metric often appears in a table AND is restated in commentary/footnotes with the same
# value. That is repetition, not new information: keep the most structured occurrence.
LOCATION_RANK = {"table": 2, "commentary": 1, "footnote": 0}


def normalize(
    extractions: list[tuple[Path, DocumentExtraction]], metric_definitions: dict[str, str]
) -> pd.DataFrame:
    """Flatten extractions into a long-format fact table and apply the rules above."""
    rows = []
    for path, doc in extractions:
        source_type = "snapshot" if path.stem.lower().startswith("portfolio_snapshot") else "standalone"
        for m in doc.metrics:
            known = m.canonical_metric in metric_definitions
            if m.canonical_metric and not known:
                log.debug("%s: unknown canonical_metric %r -> non-canonical", path.name, m.canonical_metric)
            company_raw = m.company or doc.company_name
            rows.append({
                "company": canonical_company(company_raw),
                "company_as_reported": company_raw,
                "canonical_metric": m.canonical_metric if known else None,
                "verbatim_label": m.verbatim_label,
                "value": m.value,
                "unit": m.unit,
                "currency": m.currency,
                "period": m.period,
                "period_basis": m.period_basis,
                "source_file": path.name,
                "source_type": source_type,
                "report_period": doc.report_period,
                "page": m.page,
                "location": m.location,
                "note": m.note,
                "non_canonical": not known,
                "superseded": False,
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # Split "Q2 2025" into sortable quarter/year columns (None when unparseable, e.g. "FY 2024").
    parsed = df["period"].map(_period_sort_key)
    df.insert(df.columns.get_loc("period") + 1, "quarter",
              pd.array([q or None for _, q in parsed], dtype="Int64"))
    df.insert(df.columns.get_loc("period") + 2, "year",
              pd.array([y or None for y, _ in parsed], dtype="Int64"))

    # Exact duplicate rows from a single document (model emitted the same metric twice)
    # are a repetition artifact, not information — safe to drop deterministically.
    before = len(df)
    df = df.drop_duplicates(
        subset=["company", "verbatim_label", "value", "period", "source_file"], keep="first"
    ).reset_index(drop=True)
    if len(df) < before:
        log.info("dropped %d exact duplicate row(s) emitted within a single document", before - len(df))

    # Within one document: same canonical metric, period, and value under different labels
    # (table + commentary restatement) -> keep the most structured location, supersede the rest.
    canonical = df[~df["non_canonical"]]
    df["_norm_value"] = df["value"].map(norm_value)
    for _, group in canonical.groupby(["company", "canonical_metric", "period", "source_file"]):
        if len(group) > 1 and df.loc[group.index, "_norm_value"].nunique() == 1:
            ranked = group.index.to_series().map(
                lambda i: LOCATION_RANK.get(df.at[i, "location"], 0)
            ).sort_values(ascending=False)
            df.loc[ranked.index[1:], "superseded"] = True
            log.debug("same-value repetition in %s: kept %s", group.iloc[0]["source_file"],
                      df.at[ranked.index[0], "verbatim_label"])

    # Precedence among canonical rows sharing (company, canonical_metric, period):
    # standalone beats snapshot; within a source type, the later filing wins (restatements).
    df["_src_rank"] = (df["source_type"] == "standalone").astype(int)
    df["_report_rank"] = df["report_period"].map(_period_sort_key)
    canonical = df[~df["non_canonical"]]
    for _, group in canonical.groupby(["company", "canonical_metric", "period"]):
        if group["source_file"].nunique() > 1:
            ranked = group.sort_values(["_src_rank", "_report_rank"], ascending=False)
            winner_file = ranked.iloc[0]["source_file"]
            # Supersede rows from losing files only; a conflict WITHIN one document is not
            # ours to adjudicate — it stays live and dq_duplicates warns on it.
            losers = group.index[group["source_file"] != winner_file]
            df.loc[losers, "superseded"] = True
            log.debug(
                "precedence: %s/%s/%s kept %s, superseded %s",
                *group.iloc[0][["company", "canonical_metric", "period"]],
                winner_file,
                sorted(set(df.loc[losers, "source_file"])),
            )
    return df.drop(columns=["_src_rank", "_report_rank", "_norm_value"])
