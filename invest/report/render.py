"""Markdown to a self-contained HTML page, plus inline SVG sparklines.

The report is written as Markdown (readable in a terminal, in git, in the
Streamlit page) and rendered once to HTML for mail and the archive. Sparklines
are inline SVG so the file has no external dependencies.
"""

from __future__ import annotations

import html
import math
from typing import Sequence

import markdown

CSS = """
body { font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; max-width: 1080px;
       margin: 2rem auto; padding: 0 1rem; color: #1f2328; line-height: 1.45; }
h1, h2, h3 { line-height: 1.2; }
h1 { border-bottom: 2px solid #d0d7de; padding-bottom: .3rem; }
h2 { border-bottom: 1px solid #d8dee4; padding-bottom: .2rem; margin-top: 2rem; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; margin: .8rem 0; }
th, td { border: 1px solid #d0d7de; padding: .3rem .5rem; text-align: left; vertical-align: top; }
th { background: #f6f8fa; }
tr:nth-child(even) td { background: #fafbfc; }
code { background: #f6f8fa; padding: .1rem .3rem; border-radius: 3px; font-size: .9em; }
blockquote { border-left: 4px solid #d0d7de; margin: .8rem 0; padding: .2rem 1rem; color: #57606a; }
.missing { background: #fff8c5; border: 1px solid #d4a72c; padding: .5rem 1rem; border-radius: 6px; }
svg.spark { vertical-align: middle; }
.toc { font-size: .9rem; }
"""


def sparkline_svg(values: Sequence[float], *, width: int = 120, height: int = 24, stroke: str = "#1f77b4",
                  baseline: float | None = None) -> str:
    """A polyline of the values scaled to the box; an optional dashed baseline (a threshold)."""
    points = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if len(points) < 2:
        return ""
    lo, hi = min(points), max(points)
    if baseline is not None:
        lo, hi = min(lo, baseline), max(hi, baseline)
    span = hi - lo or 1.0
    step = (width - 2) / (len(points) - 1)

    def y(v: float) -> float:
        return 1 + (height - 2) * (1 - (v - lo) / span)

    coords = " ".join(f"{1 + i * step:.1f},{y(v):.1f}" for i, v in enumerate(points))
    parts = [f'<svg class="spark" xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             f'viewBox="0 0 {width} {height}">']
    if baseline is not None:
        yb = y(baseline)
        parts.append(f'<line x1="0" y1="{yb:.1f}" x2="{width}" y2="{yb:.1f}" stroke="#d62728" stroke-dasharray="3,3" stroke-width="1"/>')
    parts.append(f'<polyline fill="none" stroke="{stroke}" stroke-width="1.5" points="{coords}"/>')
    last = points[-1]
    parts.append(f'<circle cx="{1 + (len(points) - 1) * step:.1f}" cy="{y(last):.1f}" r="2" fill="{stroke}"/>')
    parts.append("</svg>")
    return "".join(parts)


def markdown_to_html(text: str, *, title: str) -> str:
    body = markdown.markdown(text, extensions=["tables", "toc", "fenced_code", "sane_lists"], output_format="html5")
    return (
        "<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>{html.escape(title)}</title>"
        f"<style>{CSS}</style></head><body>\n{body}\n</body></html>\n"
    )


def md_table(rows: Sequence[Sequence], header: Sequence[str]) -> str:
    """A Markdown table; cells are stringified and pipes escaped."""
    if not rows:
        return "_none_\n"

    def cell(v) -> str:
        if v is None:
            return "–"
        if isinstance(v, float):
            if not math.isfinite(v):
                return "–"
            if v.is_integer() or abs(v) >= 1e6:
                return f"{v:,.0f}"
            return f"{v:,.2f}"
        return str(v).replace("|", "\\|").replace("\n", " ")

    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(cell(v) for v in row) + " |")
    return "\n".join(lines) + "\n"
