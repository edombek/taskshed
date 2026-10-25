"""Statistics document export (CSV / XLSX)."""

from __future__ import annotations

import csv
from datetime import date


def write_csv(task_rows: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["task", "total", "done", "missed", "pending", "percent"])
        for r in task_rows:
            w.writerow([r["title"], r["total"], r["done"], r["missed"],
                        r["pending"], r["percent"]])


def write_xlsx(task_rows: list[dict], place_rows: list[dict], path: str) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["task", "total", "done", "missed", "pending", "percent"])
    for r in task_rows:
        ws.append([r["title"], r["total"], r["done"], r["missed"], r["pending"], r["percent"]])
    ws2 = wb.create_sheet("places")
    ws2.append(["place", "total", "done", "missed", "pending", "percent"])
    for r in place_rows:
        ws2.append([r["place"], r["total"], r["done"], r["missed"], r["pending"], r["percent"]])
    wb.save(path)


def month_grid(days: dict[date, dict], year: int, month: int) -> dict[date, float]:
    """done% per day of a month for the heatmap."""
    out: dict[date, float] = {}
    for d, counts in days.items():
        if d.year == year and d.month == month:
            total = counts.get("done", 0) + counts.get("missed", 0) + counts.get("pending", 0)
            out[d] = (100 * counts.get("done", 0) / total) if total else 0.0
    return out
