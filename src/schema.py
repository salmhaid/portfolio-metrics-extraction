"""Pydantic models for structured extraction. Every metric carries provenance."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ExtractedMetric(BaseModel):
    company: str | None = Field(
        default=None,
        description="Company this metric belongs to, as named in the document. "
        "Required when the document covers multiple companies; null otherwise.",
    )
    verbatim_label: str = Field(description="Metric label exactly as printed in the document")
    value: str = Field(description="Value exactly as printed, e.g. '$8.4M', '78%', '142'")
    canonical_metric: str | None = Field(
        default=None,
        description="Matching canonical metric from the provided dictionary, or null if none fits",
    )
    unit: str | None = Field(default=None, description="e.g. 'M', '%', 'bps', 'count'")
    currency: str | None = Field(default=None, description="ISO code if determinable, else null")
    period: str = Field(description="Reporting period the value belongs to, e.g. 'Q2 2025'")
    period_basis: Literal["monthly", "quarterly", "ltm", "point_in_time", "unknown"] = "unknown"
    page: int = Field(description="1-indexed page number the value appears on")
    location: Literal["table", "commentary", "footnote"] = "table"
    note: str | None = Field(default=None, description="Restatements, equivalences, caveats")


class DocumentExtraction(BaseModel):
    company_name: str = Field(description="Company name as stated in the document")
    report_period: str = Field(description="Primary period of the report, e.g. 'Q2 2025'")
    metrics: list[ExtractedMetric]
