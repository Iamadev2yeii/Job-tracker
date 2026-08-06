"""
Manages the persistent Excel tracker file. Simplified single-sheet
version of Giorgio's original tracker — this one only ever covers
Greater Munich, so there's just one "Jobs" sheet, no Singapore/Swiss
tier, no Dream Cities.

Behaviour:
  - Each run, only genuinely NEW postings (by URL) are added.
  - Postings already in the sheet are left alone (deduped by URL).
  - Every run, rows scoring below min_score are pruned — this applies
    to rows already sitting in the sheet too, not just new ones, so
    changing the threshold in cv_profile.yaml actually cleans up the
    sheet, not just gates future additions. Default is 0 (off).
  - The sheet is re-sorted by Relevance Score (highest first) every
    run, so the best matches are always at the top.
  - archive_and_reset() (called from reset_tracker.py) archives the
    sheet and starts fresh.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

COLUMNS = [
    "Job Posted",
    "Company",
    "Job Title",
    "Relevance Score (1-10)",
    "Location",
    "URL",
]
COLUMN_WIDTHS = [14, 26, 44, 12, 30, 60]

# Old header names that should map onto a current column, so a rename
# doesn't strand existing data.
HEADER_ALIASES: dict[str, str] = {}

HEADER_FILL = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
HEADER_FONT = Font(color="FFFFFF", bold=True)
NEW_ROW_FILL = PatternFill(start_color="D1FAE5", end_color="D1FAE5", fill_type="solid")  # green

SHEET_NAME = "Jobs"


def _style_header(ws: Worksheet) -> None:
    for col_idx in range(1, len(COLUMNS) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
    ws.freeze_panes = "A2"
    for i, w in enumerate(COLUMN_WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _new_workbook() -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME
    ws.append(COLUMNS)
    _style_header(ws)
    return wb


def _migrate_sheet(wb: Workbook) -> None:
    ws = wb[SHEET_NAME]
    existing_headers = [c.value for c in ws[1]]
    if existing_headers == COLUMNS:
        return  # already current

    mapped_headers = [HEADER_ALIASES.get(h, h) for h in existing_headers]
    rows = []
    for row_idx in range(2, ws.max_row + 1):
        row_dict = {}
        for col_idx, header in enumerate(mapped_headers, start=1):
            if header in COLUMNS:
                row_dict[header] = ws.cell(row=row_idx, column=col_idx).value
        if row_dict:
            rows.append(row_dict)

    wb.remove(ws)
    new_ws = wb.create_sheet(SHEET_NAME, 0)
    new_ws.append(COLUMNS)
    for row_dict in rows:
        new_ws.append([row_dict.get(col, "") for col in COLUMNS])
    _style_header(new_ws)


def load_or_create(path: Path) -> Workbook:
    if not path.exists():
        return _new_workbook()
    wb = load_workbook(path)
    if SHEET_NAME not in wb.sheetnames:
        ws = wb.create_sheet(SHEET_NAME, 0)
        ws.append(COLUMNS)
        _style_header(ws)
    else:
        _migrate_sheet(wb)
    return wb


def _existing_urls(ws: Worksheet) -> set[str]:
    url_col = COLUMNS.index("URL") + 1
    return {
        ws.cell(row=row, column=url_col).value
        for row in range(2, ws.max_row + 1)
        if ws.cell(row=row, column=url_col).value
    }


def _prune_below_score(ws: Worksheet, min_score: int) -> int:
    """Deletes any row scoring below min_score. Runs every update, so
    it retroactively cleans rows already in the sheet too. Returns how
    many rows were removed."""
    if not min_score:
        return 0
    score_col = COLUMNS.index("Relevance Score (1-10)") + 1
    rows_to_delete = [
        row
        for row in range(2, ws.max_row + 1)
        if not (
            isinstance(ws.cell(row=row, column=score_col).value, (int, float))
            and ws.cell(row=row, column=score_col).value >= min_score
        )
    ]
    for row in reversed(rows_to_delete):
        ws.delete_rows(row)
    return len(rows_to_delete)


def _sort_by_relevance(ws: Worksheet, newly_added_urls: set[str]) -> None:
    """Re-sorts all data rows by Relevance Score (highest first), then
    re-applies the "new" highlight to whichever URLs were added this
    run (their row position may have moved during the sort)."""
    score_col = COLUMNS.index("Relevance Score (1-10)")
    url_col = COLUMNS.index("URL")

    rows = []
    for row in range(2, ws.max_row + 1):
        values = [ws.cell(row=row, column=c).value for c in range(1, len(COLUMNS) + 1)]
        rows.append(values)

    rows.sort(key=lambda v: (v[score_col] if isinstance(v[score_col], (int, float)) else 0), reverse=True)

    for row in range(2, ws.max_row + 1):
        for col in range(1, len(COLUMNS) + 1):
            ws.cell(row=row, column=col).value = None
            ws.cell(row=row, column=col).fill = PatternFill(fill_type=None)

    for i, values in enumerate(rows):
        row_idx = i + 2
        for col_idx, value in enumerate(values, start=1):
            ws.cell(row=row_idx, column=col_idx).value = value
        if values[url_col] in newly_added_urls:
            for col_idx in range(1, len(COLUMNS) + 1):
                ws.cell(row=row_idx, column=col_idx).fill = NEW_ROW_FILL


def _build_row(job: dict[str, Any], url: str) -> list:
    return [
        job.get("posted_date", ""),
        job.get("company", ""),
        job.get("title", ""),
        job.get("relevance_score", 1),
        job.get("location", ""),
        url,
    ]


def update_tracker(path: Path, new_jobs: list[dict[str, Any]], min_score: int = 0) -> dict[str, int]:
    """Updates the "Jobs" sheet. Saves the file."""
    wb = load_or_create(path)
    ws = wb[SHEET_NAME]
    existing_urls = _existing_urls(ws)

    added = 0
    already_tracked = 0
    newly_added_urls = set()
    for job in new_jobs:
        url = job.get("url", "")
        if not url:
            continue
        if url in existing_urls:
            already_tracked += 1
            continue
        ws.append(_build_row(job, url))
        newly_added_urls.add(url)
        existing_urls.add(url)
        added += 1

    pruned = _prune_below_score(ws, min_score)
    _sort_by_relevance(ws, newly_added_urls)

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return {"added": added, "already_tracked": already_tracked, "pruned": pruned, "total_rows": ws.max_row - 1}


def archive_and_reset(path: Path, archive_dir: Path) -> Path | None:
    """
    Moves the current tracker to archive/job_tracker_YYYY-MM.xlsx and
    creates a fresh empty tracker at `path`. Returns the archive path,
    or None if there was nothing to archive.
    """
    if not path.exists():
        _new_workbook().save(path)
        return None

    archive_dir.mkdir(parents=True, exist_ok=True)
    month_tag = dt.date.today().strftime("%Y-%m")
    archive_path = archive_dir / f"job_tracker_{month_tag}.xlsx"

    counter = 2
    final_path = archive_path
    while final_path.exists():
        final_path = archive_dir / f"job_tracker_{month_tag}_v{counter}.xlsx"
        counter += 1

    path.rename(final_path)
    _new_workbook().save(path)
    return final_path
