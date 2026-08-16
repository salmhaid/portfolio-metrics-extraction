"""Pipeline orchestrator + CLI: ingest -> extract -> normalize -> checks -> outputs.

Usage: python -m src.pipeline --input /path/to/pdf-folder
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import pandas as pd
import yaml
from dotenv import load_dotenv

from . import checks
from .extract import extract_document
from .ingest import ingest_pdf
from .normalize import normalize

log = logging.getLogger("pipeline")

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "metrics.yaml"
OUTPUT_DIR = ROOT / "output"

PIVOT_METRICS = ["revenue", "arr", "gross_margin", "net_revenue_retention", "headcount"]


def load_settings() -> dict:
    """Load .env (key, model, log level) and the canonical metric dictionary."""
    load_dotenv()
    settings = {
        "api_key": os.getenv("OPENAI_API_KEY", ""),
        "model": os.getenv("OPENAI_MODEL", ""),
        "log_level": os.getenv("LOG_LEVEL", "INFO").upper(),
        "metric_definitions": {
            m["name"]: m["description"] for m in yaml.safe_load(CONFIG_PATH.read_text())["metrics"]
        },
    }
    logging.basicConfig(
        level=settings["log_level"],
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    return settings


def build_pivot(df: pd.DataFrame) -> pd.DataFrame:
    """Companies x quarters for headline canonical metrics — the human-review view."""
    current = df[df["canonical_metric"].isin(PIVOT_METRICS) & ~df["superseded"]]
    if current.empty:
        return pd.DataFrame()
    pivot = current.pivot_table(
        index=["company", "canonical_metric"],
        columns="period",
        values="value",
        aggfunc="first",
    )
    ordered = sorted(pivot.columns, key=lambda p: (p.split()[-1], p.split()[0]))
    return pivot[ordered]


def write_outputs(df: pd.DataFrame, flags: list[dict], out_dir: Path = OUTPUT_DIR) -> None:
    out_dir.mkdir(exist_ok=True)
    # quarter/year fully encode the period in the fact table; keep the combined string internal.
    fact = df.drop(columns=["period"]) if "period" in df.columns else df
    fact.to_csv(out_dir / "metrics_long.csv", index=False)
    build_pivot(df).to_csv(out_dir / "pivot.csv")
    flags_df = pd.DataFrame(flags, columns=["severity", "source_file", "company", "metric", "period", "detail"])
    flags_df.sort_values(["severity", "source_file"]).to_csv(out_dir / "flags.csv", index=False)
    log.info("outputs written to %s (metrics_long, pivot, flags)", out_dir)


def run(input_dir: Path, settings: dict) -> tuple[pd.DataFrame, list[dict]]:
    pdfs = sorted(input_dir.glob("*.pdf"))
    log.info("Found %d PDFs in %s", len(pdfs), input_dir)

    extractions, page_texts, flags = [], {}, []
    for pdf in pdfs:
        try:
            pages = ingest_pdf(pdf)
            doc = extract_document(pdf, pages, settings)
            extractions.append((pdf, doc))
            page_texts[pdf.name] = pages
            log.info("%s: %d metrics extracted (%s, %s)", pdf.name, len(doc.metrics), doc.company_name, doc.report_period)
        except Exception as exc:  # a failed document becomes a flag row, never a crashed run
            log.error("%s: %s", pdf.name, exc)
            flags.append({
                "severity": "error", "source_file": pdf.name, "company": "", "metric": "",
                "period": "", "detail": f"document failed: {exc}",
            })

    df = normalize(extractions, settings["metric_definitions"])
    flags += checks.run_all(df, page_texts)
    return df, flags


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract portfolio metrics from a folder of PDFs")
    parser.add_argument("--input", required=True, type=Path, help="Folder containing PDF reports")
    args = parser.parse_args()

    settings = load_settings()
    if not settings["api_key"] or not settings["model"]:
        raise SystemExit("OPENAI_API_KEY and OPENAI_MODEL must be set in .env (see .env.example)")

    df, flags = run(args.input, settings)
    write_outputs(df, flags)

    severities = pd.Series([f["severity"] for f in flags]).value_counts().to_dict() if flags else {}
    log.info(
        "run complete: model=%s | %d rows (%d canonical, %d superseded) | flags: %s",
        settings["model"], len(df),
        int(df["canonical_metric"].notna().sum()) if not df.empty else 0,
        int(df["superseded"].sum()) if not df.empty else 0,
        severities or "none",
    )


if __name__ == "__main__":
    main()
