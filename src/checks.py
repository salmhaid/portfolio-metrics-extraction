"""Provenance verification + data-quality checks.

All issues become rows in flags.csv: severity, source_file, company, metric, period, detail.
Severity: error (pipeline failure), warning (needs review), info (resolved automatically).
"""

from __future__ import annotations

import logging
import re

import pandas as pd

from .normalize import norm_value as _norm_value

log = logging.getLogger("checks")

# Canonical metrics whose values are monetary and therefore need a currency.
MONETARY_METRICS = {"revenue", "arr", "cash", "net_burn"}


def _flag(severity: str, row=None, detail: str = "", **overrides) -> dict:
    base = {
        "severity": severity,
        "source_file": row["source_file"] if row is not None else "",
        "company": row["company"] if row is not None else "",
        "metric": (row["canonical_metric"] or row["verbatim_label"]) if row is not None else "",
        "period": row["period"] if row is not None else "",
        "detail": detail,
    }
    base.update(overrides)
    return base


def _squash(text: str) -> str:
    """Collapse whitespace and case so line-wrapped PDF text still matches.

    The guard exists to catch fabricated values/labels, not casing style; spacing and
    character-level differences (e.g. '+ $0.2M' vs '+$0.2M') still fail the match.
    """
    return re.sub(r"\s+", " ", text).strip().lower()


def verify_provenance(df: pd.DataFrame, page_texts: dict[str, list[str]]) -> list[dict]:
    """Hallucination guard: verbatim_label and value must appear in the source document text.

    Checked on the recorded page first; a hit elsewhere in the document is an info flag
    (wrong page); no hit anywhere is a warning. Flagged, never dropped.
    """
    flags = []
    for _, row in df.iterrows():
        pages = page_texts.get(row["source_file"])
        if pages is None:
            continue
        squashed_pages = [_squash(p) for p in pages]
        doc_text = " ".join(squashed_pages)
        page_idx = int(row["page"]) - 1
        on_page = 0 <= page_idx < len(pages)
        for field in ("verbatim_label", "value"):
            needle = _squash(str(row[field]))
            if on_page and needle in squashed_pages[page_idx]:
                continue
            if needle in doc_text:
                flags.append(_flag("info", row, f"{field} {row[field]!r} found, but not on recorded page {row['page']}"))
            else:
                flags.append(_flag("warning", row, f"{field} {row[field]!r} not found in source text (unverified)"))
    return flags


def dq_duplicates(df: pd.DataFrame) -> list[dict]:
    """Duplicate (company, canonical_metric, period) rows.

    Cross-document duplicates resolved by precedence -> info. Duplicates within a single
    document (the model mapped two labels to one canonical metric) -> warning.
    """
    flags = []
    canonical = df[df["canonical_metric"].notna()]
    for (company, metric, period), group in canonical.groupby(["company", "canonical_metric", "period"]):
        if len(group) < 2:
            continue
        row = group.iloc[0]
        srcs = sorted(group["source_file"].unique())
        if len(srcs) == 1:
            live = group[~group["superseded"]]
            if live["value"].map(_norm_value).nunique() > 1:
                detail = dict(zip(live["verbatim_label"], live["value"]))
                flags.append(_flag("warning", row, f"conflicting labels mapped to same canonical metric in one document: {detail}"))
            else:
                labels = list(group["verbatim_label"])
                flags.append(_flag("info", row, f"same value restated under multiple labels/locations; kept one: {labels}"))
        else:
            kept = group[~group["superseded"]]
            kept_src = kept.iloc[0]["source_file"] if len(kept) else "?"
            flags.append(_flag("info", row, f"reported in {len(srcs)} sources {srcs}; precedence kept {kept_src}"))
    return flags


def dq_cross_source(df: pd.DataFrame) -> list[dict]:
    """Where snapshot and standalone report the same (company, metric, period), compare values.

    Agreement -> info (free ground truth); disagreement -> warning.
    """
    flags = []
    canonical = df[df["canonical_metric"].notna()]
    for (company, metric, period), group in canonical.groupby(["company", "canonical_metric", "period"]):
        if set(group["source_type"]) != {"snapshot", "standalone"}:
            continue
        row = group.iloc[0]
        values = {st: _norm_value(g["value"].iloc[0]) for st, g in group.groupby("source_type")}
        if values["snapshot"] == values["standalone"]:
            flags.append(_flag("info", row, "snapshot and standalone report agree"))
        else:
            detail = {st: g["value"].iloc[0] for st, g in group.groupby("source_type")}
            flags.append(_flag("warning", row, f"snapshot vs standalone disagree: {detail}"))
    return flags


def dq_currency(df: pd.DataFrame) -> list[dict]:
    """Monetary canonical rows where currency could not be determined (e.g. MediSight '6.8M')."""
    flags = []
    monetary = df[df["canonical_metric"].isin(MONETARY_METRICS) & df["currency"].isna() & ~df["superseded"]]
    for _, row in monetary.iterrows():
        flags.append(_flag("warning", row, f"currency undetermined for monetary value {row['value']!r}"))
    return flags


def run_all(df: pd.DataFrame, page_texts: dict[str, list[str]]) -> list[dict]:
    flags: list[dict] = []
    if df.empty:
        return flags
    flags += verify_provenance(df, page_texts)
    flags += dq_duplicates(df)
    flags += dq_cross_source(df)
    flags += dq_currency(df)
    counts = pd.Series([f["severity"] for f in flags]).value_counts().to_dict() if flags else {}
    log.info("checks complete: %s", counts or "no flags")
    return flags
