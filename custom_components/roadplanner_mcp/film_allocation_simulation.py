"""What the new allocation WOULD do, on the trip that actually exists.

A threshold picked in a repository is a guess. `semantic_score` runs 0 to
20, and where the mass of a real trip sits on that range decides whether
a bar of 11 keeps everything, nothing, or the right half. Nobody here can
know that: the analyses live on the user's own Home Assistant, and no
fixture can stand in for them without inventing the very distribution the
number is supposed to be read off.

So this module answers the question where the data is. It reads the
stored curations - the scores, the series, the motifs, all of them paid
for when the days were curated - and reports, for several candidate
thresholds at once, how many pictures each day would earn and what the
film would carry in total.

It is free and read-only in the strict sense: no provider is called, no
picture is fetched, no record is written, and no photograph leaves the
system. What comes out are numbers.

The output is deliberately plain text. It exists to be pasted back into a
conversation, and a table a person can read is worth more than a JSON
blob nobody opens.
"""

from __future__ import annotations

from typing import Any

from .film_photo_allocation import (
    GOOD_IMAGE_THRESHOLD,
    MAX_FILM_PHOTOS,
    MAX_PER_SERIES,
    PHOTO_CAPS_BY_IMPORTANCE,
    allocate_trip,
    score_distribution,
    score_of,
)

# The bars to try. Spread across the upper half of the 0-20 scale,
# because a picture the analysis called worthless on every axis is not a
# candidate for anybody's film and a bar below the midpoint would gate
# nothing. The list is what the report compares; the choice is made by a
# person reading it, not by this module.
CANDIDATE_THRESHOLDS = (8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0)

# Which source the day offers the allocation.
#
# `curated` is what the day-curation chose - at most 14 by its own limit,
# which means a major highlight can never reach its ceiling of 18 from
# it. `pool` is every analysed candidate of the day. Reporting both is
# the point: if the two totals differ a lot, the curation's own limit is
# the binding constraint and moving the allocation alone would change
# nothing.
SOURCE_CURATED = "curated"
SOURCE_POOL = "pool"


def _rows_for(days: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    """The same days, offering either their selection or their pool."""
    rows: list[dict[str, Any]] = []
    for day in days:
        candidates = list(
            day.get("curated") if source == SOURCE_CURATED else day.get("pool") or []
        )
        row = dict(day)
        row["curated"] = candidates
        rows.append(row)
    return rows


def simulate(
    days: list[dict[str, Any]],
    *,
    thresholds: tuple[float, ...] = CANDIDATE_THRESHOLDS,
    max_per_series: int = MAX_PER_SERIES,
    global_cap: int = MAX_FILM_PHOTOS,
    reference: float = GOOD_IMAGE_THRESHOLD,
) -> dict[str, Any]:
    """Every candidate bar, over the trip as it stands.

    `days` are the records `allocate_trip` takes, plus two keys this
    module needs and the allocation does not: `pool` (every analysed
    candidate) and `old_selected` (how many pictures the day has in the
    film TODAY), because a proposal is only readable next to what it
    replaces.
    """
    days = [day for day in days or [] if isinstance(day, dict)]
    scores: list[float] = []
    for day in days:
        analyses = day.get("analyses") or {}
        for media_id in day.get("pool") or day.get("curated") or []:
            if isinstance(analyses.get(media_id), dict):
                scores.append(score_of(analyses, media_id))

    candidates: list[dict[str, Any]] = []
    per_threshold: dict[float, dict[str, Any]] = {}
    for threshold in thresholds:
        entry: dict[str, Any] = {"threshold": threshold}
        for source in (SOURCE_CURATED, SOURCE_POOL):
            result = allocate_trip(
                _rows_for(days, source),
                threshold=threshold,
                max_per_series=max_per_series,
                global_cap=global_cap,
            )
            entry[source] = result
        candidates.append(
            {
                "threshold": threshold,
                "total_from_curated": entry[SOURCE_CURATED]["total"],
                "total_from_pool": entry[SOURCE_POOL]["total"],
                "unused_budget": entry[SOURCE_POOL]["unused_budget"],
                "globally_removed": len(entry[SOURCE_POOL]["globally_removed"]),
                "days_at_cap": sum(
                    1
                    for day_id, found in entry[SOURCE_POOL]["days"].items()
                    if len(found["media_ids"]) >= found["cap"]
                ),
                "days_empty": sum(
                    1
                    for found in entry[SOURCE_POOL]["days"].values()
                    if not found["media_ids"]
                ),
                "coverage_exceptions": sum(
                    len(found["coverage_exceptions"])
                    for found in entry[SOURCE_POOL]["days"].values()
                ),
            }
        )
        per_threshold[threshold] = entry

    # The per-day table is read at ONE bar, otherwise it is unreadable at
    # 23 days. The reference is the current placeholder unless it was not
    # among the candidates, in which case the middle one stands in.
    chosen = reference if reference in per_threshold else thresholds[len(thresholds) // 2]
    detail = per_threshold[chosen][SOURCE_POOL]["days"]
    detail_curated = per_threshold[chosen][SOURCE_CURATED]["days"]

    table: list[dict[str, Any]] = []
    for index, day in enumerate(days):
        day_id = str(day.get("chapter_id") or day.get("day_id") or "")
        found = detail.get(day_id) or {}
        table.append(
            {
                "day_number": day.get("day_number", index + 1),
                "day_id": day_id,
                "title": str(day.get("title") or ""),
                "importance": found.get("importance", "normal"),
                "cap": found.get("cap", 0),
                "analysed": sum(
                    1
                    for media_id in day.get("pool") or []
                    if isinstance((day.get("analyses") or {}).get(media_id), dict)
                ),
                "curated": len(day.get("curated") or []),
                "pool": len(day.get("pool") or []),
                "above_threshold": found.get("above_threshold", 0),
                "after_series_cap": found.get("after_series_cap", 0),
                "coverage_exceptions": len(found.get("coverage_exceptions") or []),
                "old_selected": day.get("old_selected"),
                "new_from_curated": len(
                    (detail_curated.get(day_id) or {}).get("media_ids") or []
                ),
                "new_from_pool": len(found.get("media_ids") or []),
                "earned_by_threshold": {
                    threshold: len(
                        (per_threshold[threshold][SOURCE_POOL]["days"].get(day_id) or {}).get(
                            "media_ids"
                        )
                        or []
                    )
                    for threshold in thresholds
                },
            }
        )

    old_total = sum(
        int(row["old_selected"] or 0)
        for row in table
        if row["old_selected"] is not None
    )
    return {
        "day_count": len(days),
        "reference_threshold": chosen,
        "global_cap": global_cap,
        "max_per_series": max_per_series,
        "caps": dict(PHOTO_CAPS_BY_IMPORTANCE),
        "distribution": score_distribution(scores),
        "candidates": candidates,
        "days": table,
        "old_total": old_total,
    }


def _bar(count: int, scale: int) -> str:
    return "#" * max(0, min(40, round(count * 40 / scale))) if scale else ""


def format_report(result: dict[str, Any], *, check_days: list[int] | None = None) -> str:
    """The whole simulation as text somebody can read and send on."""
    lines: list[str] = []
    distribution = result.get("distribution") or {}
    lines.append("=== Bildzuteilung: Simulation (kostenlos, nur lesend) ===")
    lines.append(
        f"{result.get('day_count')} Tage · Filmobergrenze {result.get('global_cap')} "
        f"· Serienlimit {result.get('max_per_series')}"
    )
    caps = result.get("caps") or {}
    lines.append(
        "Obergrenzen je Wichtigkeit: "
        + " · ".join(f"{name} {value}" for name, value in caps.items())
    )
    lines.append("")

    lines.append("-- Verteilung der Bildbewertungen (semantic_score, Skala 0-20) --")
    if not distribution.get("count"):
        lines.append("Keine gespeicherten Analysen gefunden.")
    else:
        lines.append(
            f"n={distribution['count']} · min {distribution['min']} · p25 {distribution['p25']}"
            f" · Median {distribution['median']} · p75 {distribution['p75']}"
            f" · p90 {distribution['p90']} · max {distribution['max']}"
            f" · Mittel {distribution['mean']}"
        )
        buckets = distribution.get("buckets") or {}
        scale = max(buckets.values()) if buckets else 0
        for name, count in buckets.items():
            lines.append(f"  {name:>7} | {count:4d} {_bar(count, scale)}")
    lines.append("")

    lines.append("-- Kandidatenschwellen --")
    lines.append(
        "Schwelle | aus Kuratierung | aus Pool | ungenutzt | global gekürzt | Tage am Limit | leere Tage | Coverage"
    )
    for entry in result.get("candidates") or []:
        lines.append(
            f"{entry['threshold']:>8} | {entry['total_from_curated']:>15} | "
            f"{entry['total_from_pool']:>8} | {entry['unused_budget']:>9} | "
            f"{entry['globally_removed']:>14} | {entry['days_at_cap']:>13} | "
            f"{entry['days_empty']:>10} | {entry['coverage_exceptions']:>8}"
        )
    lines.append("")

    reference = result.get("reference_threshold")
    lines.append(f"-- Tag für Tag bei Schwelle {reference} --")
    lines.append(
        "Tag | Wichtigkeit | Cap | Pool | analysiert | kuratiert | über Schwelle | "
        "nach Serienlimit | Coverage | alt im Film | neu (Kuratierung) | neu (Pool)"
    )
    for row in result.get("days") or []:
        old = "?" if row["old_selected"] is None else row["old_selected"]
        lines.append(
            f"{row['day_number']:>3} | {row['importance']:<14} | {row['cap']:>3} | "
            f"{row['pool']:>4} | {row['analysed']:>10} | {row['curated']:>9} | "
            f"{row['above_threshold']:>13} | {row['after_series_cap']:>16} | "
            f"{row['coverage_exceptions']:>8} | {str(old):>11} | "
            f"{row['new_from_curated']:>17} | {row['new_from_pool']:>10}  {row['title'][:40]}"
        )
    lines.append("")
    lines.append(
        f"Summe alt im Film: {result.get('old_total')} · "
        + " · ".join(
            f"neu bei {entry['threshold']}: {entry['total_from_pool']}"
            for entry in result.get("candidates") or []
        )
    )

    wanted = list(check_days or [])
    if wanted:
        lines.append("")
        lines.append("-- Prüffälle --")
        by_number = {row["day_number"]: row for row in result.get("days") or []}
        for number in wanted:
            row = by_number.get(number)
            if not row:
                lines.append(f"Tag {number}: nicht in dieser Reise")
                continue
            lines.append(
                f"Tag {number} · {row['title']} · {row['importance']} (Cap {row['cap']}): "
                + " · ".join(
                    f"≥{threshold}: {count}"
                    for threshold, count in (row["earned_by_threshold"] or {}).items()
                )
                + f" · alt {row['old_selected'] if row['old_selected'] is not None else '?'}"
            )
    return "\n".join(lines)


__all__ = [
    "CANDIDATE_THRESHOLDS",
    "SOURCE_CURATED",
    "SOURCE_POOL",
    "format_report",
    "simulate",
]
