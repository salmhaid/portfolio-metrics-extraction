"""Tests for the deterministic layers only — no API key required.

Fixtures in tests/fixtures/ mirror the shape of real model extractions.
The LLM itself is not tested here; extraction accuracy needs an eval harness
(see README next steps).
"""

import json
from pathlib import Path

import pandas as pd
import yaml

from src.checks import dq_currency, dq_duplicates, verify_provenance
from src.normalize import normalize
from src.schema import DocumentExtraction, ExtractedMetric

FIXTURES = Path(__file__).parent / "fixtures"
_raw = yaml.safe_load((Path(__file__).parent.parent / "config" / "metrics.yaml").read_text())
METRIC_DEFS = {m["name"]: m["description"] for m in _raw["metrics"]}


def load_fixture(name: str) -> DocumentExtraction:
    return DocumentExtraction.model_validate(json.loads((FIXTURES / name).read_text()))


def metric(**overrides) -> ExtractedMetric:
    base = dict(
        verbatim_label="Recognized Revenue", value="$8.4M", canonical_metric="revenue",
        unit="M", currency="USD", period="Q2 2025", period_basis="quarterly",
        page=1, location="table",
    )
    base.update(overrides)
    return ExtractedMetric(**base)


def doc(company: str, period: str, metrics: list[ExtractedMetric]) -> DocumentExtraction:
    return DocumentExtraction(company_name=company, report_period=period, metrics=metrics)


def test_unknown_canonical_metric_becomes_non_canonical():
    df = normalize([(Path("PeopleFlow_Q2_2025.pdf"), load_fixture("peopleflow_q2_2025.json"))], METRIC_DEFS)
    attach = df[df["verbatim_label"] == "Module Attach Rate"].iloc[0]
    assert attach["non_canonical"] and pd.isna(attach["canonical_metric"])
    assert not df[df["verbatim_label"] == "Quarterly Revenue"]["non_canonical"].any()


def test_entity_map_merges_fleetlink_and_apexfreight():
    fleetlink = load_fixture("fleetlink_q4_2024.json")
    apex = doc("Apex Freight Solutions Inc.", "Q2 2025",
               [metric(verbatim_label="Total Recognized Revenue", value="9.3M", currency=None)])
    df = normalize([(Path("FleetLink_Q4_2024.pdf"), fleetlink), (Path("ApexFreight_Q2_2025.pdf"), apex)], METRIC_DEFS)
    assert set(df["company"]) == {"ApexFreight"}


def test_standalone_beats_snapshot():
    standalone = doc("NovaCloud Analytics Inc.", "Q2 2025", [metric()])
    snapshot = doc("NovaCloud Analytics Inc.", "Q2 2025", [metric(verbatim_label="Recognized Revenue")])
    df = normalize([
        (Path("NovaCloud_Q2_2025.pdf"), standalone),
        (Path("Portfolio_Snapshot_Q2_2025.pdf"), snapshot),
    ], METRIC_DEFS)
    assert not df[df["source_type"] == "standalone"]["superseded"].any()
    assert df[df["source_type"] == "snapshot"]["superseded"].all()


def test_restatement_later_filing_supersedes():
    q1_doc = doc("PeopleFlow HR Systems Ltd.", "Q1 2025",
                 [metric(value="4.7M", period="Q1 2025", currency="GBP")])
    q2_doc = load_fixture("peopleflow_q2_2025.json")  # restates Q1 revenue to 4.6M
    df = normalize([(Path("PeopleFlow_Q1_2025.pdf"), q1_doc), (Path("PeopleFlow_Q2_2025.pdf"), q2_doc)], METRIC_DEFS)
    q1_revenue = df[(df["canonical_metric"] == "revenue") & (df["period"] == "Q1 2025")]
    assert len(q1_revenue) == 2  # both retained
    kept = q1_revenue[~q1_revenue["superseded"]]
    assert kept.iloc[0]["value"] == "4.6M" and kept.iloc[0]["report_period"] == "Q2 2025"


def test_provenance_verify_flags_absent_value():
    d = doc("NovaCloud Analytics Inc.", "Q2 2025",
            [metric(), metric(verbatim_label="ARR (End of Period)", value="$99.9M", canonical_metric="arr")])
    df = normalize([(Path("NovaCloud_Q2_2025.pdf"), d)], METRIC_DEFS)
    page_texts = {"NovaCloud_Q2_2025.pdf": ["Recognized Revenue $8.4M ... ARR (End of Period) $34.2M"]}
    flags = verify_provenance(df, page_texts)
    warnings = [f for f in flags if f["severity"] == "warning"]
    assert len(warnings) == 1 and "'$99.9M' not found" in warnings[0]["detail"]
    assert len(df) == 2  # flagged, never dropped


def test_currency_check_flags_missing_currency_on_monetary_metric():
    d = doc("MediSight Data Platform Inc.", "Q2 2025",
            [metric(verbatim_label="Recognized Revenue", value="6.8M", currency=None),
             metric(verbatim_label="Gross Margin", value="77%", canonical_metric="gross_margin", currency=None)])
    df = normalize([(Path("MediSight_Q2_2025.pdf"), d)], METRIC_DEFS)
    flags = dq_currency(df)
    assert len(flags) == 1  # revenue flagged; gross_margin is not monetary
    assert flags[0]["metric"] == "revenue" and flags[0]["severity"] == "warning"


def test_exact_duplicate_rows_are_dropped():
    d = doc("LendBridge Capital Corp.", "Q2 2025",
            [metric(verbatim_label="Total Headcount", value="188", canonical_metric="headcount"),
             metric(verbatim_label="Total Headcount", value="188", canonical_metric="headcount")])
    df = normalize([(Path("LendBridge_Q2_2025.pdf"), d)], METRIC_DEFS)
    assert len(df) == 1


def test_same_value_repetition_across_locations_is_resolved_not_warned():
    d = doc("PeopleFlow HR Systems Ltd.", "Q2 2025",
            [metric(verbatim_label="Subscription ARR (end of period)", value="21.4M",
                    canonical_metric="arr", currency="GBP", location="table"),
             metric(verbatim_label="Subscription ARR", value="21.4M",
                    canonical_metric="arr", currency="GBP", location="commentary")])
    df = normalize([(Path("PeopleFlow_Q2_2025.pdf"), d)], METRIC_DEFS)
    kept = df[~df["superseded"]]
    assert len(kept) == 1 and kept.iloc[0]["location"] == "table"
    flags = dq_duplicates(df)
    assert [f["severity"] for f in flags] == ["info"]


def test_duplicate_within_one_document_is_warned():
    d = doc("ClearPay Technologies Ltd.", "Q2 2025",
            [metric(verbatim_label="Net Revenue (take-rate based)", value="$14.8M"),
             metric(verbatim_label="Total Recognized Revenue", value="$17.3M")])
    df = normalize([(Path("ClearPay_Q2_2025.pdf"), d)], METRIC_DEFS)
    flags = dq_duplicates(df)
    warnings = [f for f in flags if f["severity"] == "warning"]
    assert len(warnings) == 1 and "same canonical metric in one document" in warnings[0]["detail"]
