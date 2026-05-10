"""Markdown + CSV report rendering.

Output layout: a header, the top-N table, and a "rejected but interesting"
tail. The availability matrix uses these cell glyphs:

  free            ✅
  taken           ❌
  soft_claimed    ⚠️
  reserved        🚫
  available_soon  🕒
  premium         💰
  error           ❓
  skipped         ➖
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from weavenames.models import AvailabilityResult, Candidate

GLYPHS: dict[str, str] = {
    "free": "✅",
    "taken": "❌",
    "soft_claimed": "⚠️",
    "reserved": "🚫",
    "available_soon": "🕒",
    "premium": "💰",
    "error": "❓",
    "skipped": "➖",
}


def cell(result: AvailabilityResult | None) -> str:
    if result is None:
        return GLYPHS["skipped"]
    return GLYPHS.get(result.status, "?")


def _registry_columns(candidates: list[Candidate]) -> list[str]:
    """Stable column order across all observed registries."""

    seen: list[str] = []
    base_order = ["pypi", "npm", "github_user", "github_repo", "trademark"]
    for r in base_order:
        seen.append(r)
    # Domains last, sorted by TLD so the table reads consistently.
    domain_keys = sorted(
        {
            r
            for c in candidates
            for r in c.availability
            if r.startswith("domain:")
        }
    )
    return seen + domain_keys


def render_markdown(
    description: str,
    top: list[Candidate],
    rejected_interesting: list[Candidate],
    *,
    keywords: list[str] | None = None,
) -> str:
    cols = _registry_columns(top)
    header_cells = ["Rank", "Name", "Score"] + [_pretty_registry(c) for c in cols] + ["Why"]
    sep = ["---"] * len(header_cells)
    lines: list[str] = []
    lines.append(f"# Weavenames — {description}")
    if keywords:
        lines.append(f"_Keywords: {', '.join(keywords)}_")
    lines.append("")
    lines.append(
        f"_Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} · "
        f"{len(top)} top candidates._"
    )
    lines.append("")
    lines.append("## Legend")
    lines.append(
        "  ".join(f"{g} {label}" for label, g in GLYPHS.items())
    )
    lines.append("")
    lines.append("## Top candidates")
    lines.append("| " + " | ".join(header_cells) + " |")
    lines.append("| " + " | ".join(sep) + " |")
    for i, c in enumerate(top, 1):
        row = [
            str(i),
            f"`{c.name}`",
            f"{c.scores.composite:.3f}",
        ]
        for r in cols:
            row.append(cell(c.availability.get(r)))
        rationale = c.rationale or _auto_rationale(c)
        row.append(rationale)
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    # Trademark section if any hits
    tm_hits = [c for c in top if c.trademark_hits]
    if tm_hits:
        lines.append("## Trademark watch")
        for c in tm_hits:
            lines.append(f"- **{c.name}**: " + _format_tm(c))
        lines.append("")

    if rejected_interesting:
        lines.append("## Rejected but interesting (bottom of shortlist)")
        for c in rejected_interesting:
            reason = c.reject_reason or _summarize_availability(c) or "low rank"
            lines.append(f"- `{c.name}` — {reason}")
        lines.append("")
    return "\n".join(lines)


def _pretty_registry(r: str) -> str:
    if r.startswith("domain:"):
        return r.split(":", 1)[1]
    return {
        "pypi": "PyPI",
        "npm": "npm",
        "github_user": "GH user",
        "github_repo": "GH repo",
        "trademark": "TM",
    }.get(r, r)


def _summarize_availability(c: Candidate) -> str:
    bad = [
        f"{_pretty_registry(r)}={a.status}"
        for r, a in c.availability.items()
        if a.status in {"taken", "soft_claimed", "reserved"}
    ]
    return ", ".join(bad)


def _auto_rationale(c: Candidate) -> str:
    parts: list[str] = []
    if c.pairwise_matches:
        parts.append(
            f"won {c.pairwise_wins}/{c.pairwise_matches} pairings"
        )
    flags: list[str] = []
    for r, a in c.availability.items():
        if a.flags:
            flags.append(f"{_pretty_registry(r)}: {', '.join(a.flags[:2])}")
    if flags:
        parts.append("; ".join(flags[:2]))
    return " · ".join(parts) or "—"


def _format_tm(c: Candidate) -> str:
    parts = []
    for h in c.trademark_hits[:3]:
        cls = ",".join(h.classes) if h.classes else "?"
        parts.append(f"{h.mark} (cls {cls})")
    return "; ".join(parts)


def render_csv(top: list[Candidate]) -> str:
    cols = _registry_columns(top)
    buf = io.StringIO()
    w = csv.writer(buf)
    header = (
        ["rank", "name", "composite", "availability_score", "pairwise_fit", "pronounceability"]
        + cols
        + ["flags", "trademark_hits", "reject_reason"]
    )
    w.writerow(header)
    for i, c in enumerate(top, 1):
        row: list[str] = [
            str(i),
            c.name,
            f"{c.scores.composite:.4f}",
            f"{c.scores.availability:.4f}",
            f"{c.scores.pairwise_fit:.4f}",
            f"{c.scores.pronounceability:.4f}",
        ]
        for r in cols:
            res = c.availability.get(r)
            row.append(res.status if res else "skipped")
        flags = ";".join(
            f"{r}:{','.join(a.flags)}" for r, a in c.availability.items() if a.flags
        )
        row.append(flags)
        row.append("; ".join(h.mark for h in c.trademark_hits))
        row.append(c.reject_reason or "")
        w.writerow(row)
    return buf.getvalue()
