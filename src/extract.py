"""Extract: one OpenAI structured-output call per document.

The prompt includes the canonical metric dictionary (config/metrics.yaml) so the model
extracts every metric verbatim AND assigns canonical_metric where a definition clearly fits.
"""

from __future__ import annotations

import logging
from pathlib import Path

from openai import OpenAI

from .schema import DocumentExtraction

log = logging.getLogger("extract")

INSTRUCTIONS = """\
You are extracting metrics from a portfolio company's quarterly reporting package for an
investment firm. Extract EVERY metric you can find — from tables, from commentary prose,
and from footnotes. Rules:

- verbatim_label and value must be copied EXACTLY as printed in the document.
- verbatim_label is the label ONLY — never append the value to it ("Monthly Net Burn",
  not "Monthly Net Burn ($0.55M)").
- Copy values with signs and parentheses intact: a value printed as ($0.75M) is "($0.75M)",
  never "$0.75M".
- verbatim_label is the metric's short label (e.g. "Recognized Revenue (USD)"), NEVER a full
  sentence. For a metric that appears only in commentary prose, use the shortest exact phrase
  from the text that names the metric (e.g. "Gross Margin"), still copied verbatim.
- If the document covers MULTIPLE companies (e.g. a portfolio summary), set the company
  field on every metric to the specific company it belongs to. company_name at the document
  level is then the issuing entity (e.g. the portfolio snapshot itself). A company's section
  may continue across a page break: attribute each metric to the company whose section it
  belongs to, not the nearest heading on the same page. Never attribute a metric to the
  portfolio/issuing entity itself — every metric belongs to a specific portfolio company.
- A table with multiple period columns (e.g. Q2 2025 and Q1 2025) yields one entry per
  period, each with its own value.
- Assign canonical_metric ONLY when one of the canonical definitions below clearly fits;
  otherwise leave it null. Never assign two different labels from the same period to the
  same canonical metric unless the document states they are the same thing.
- currency: ISO code (USD, GBP, ...) only when determinable from a symbol or a stated
  reporting currency; otherwise null.
- period format: "Q<n> <year>", e.g. "Q2 2025".
- period_basis: monthly / quarterly / ltm / point_in_time as disclosed (e.g. monthly vs
  quarterly net burn, LTM retention). Use "unknown" if not determinable.
- page: the page number from the "--- PAGE n ---" markers below.
- note: record restatements, stated label equivalences (e.g. footnotes saying a metric was
  renamed), and caveats. Also record former company names if a rebrand is mentioned.

Canonical metric definitions:
{definitions}
"""


def build_prompt(pages: list[str], metric_definitions: dict[str, str]) -> tuple[str, str]:
    """Return (system_instructions, document_text) for the extraction call."""
    definitions = "\n".join(f"- {name}: {desc.strip()}" for name, desc in metric_definitions.items())
    doc_text = "\n\n".join(f"--- PAGE {i + 1} ---\n{text}" for i, text in enumerate(pages))
    return INSTRUCTIONS.format(definitions=definitions), doc_text


def extract_document(path: Path, pages: list[str], settings: dict) -> DocumentExtraction:
    """Structured-output call parsing into DocumentExtraction."""
    system, doc_text = build_prompt(pages, settings["metric_definitions"])
    client = OpenAI(api_key=settings["api_key"])
    completion = client.chat.completions.parse(
        model=settings["model"],
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": doc_text},
        ],
        response_format=DocumentExtraction,
    )
    extraction = completion.choices[0].message.parsed
    if extraction is None:
        raise ValueError(f"Model returned no parseable extraction for {path.name}")
    return extraction
