"""Excel export: every lead from Postgres into one workbook for cold calling.

Not a replacement for the Sheets sync - that's a live mirror the core pushes
to on a timer. This is a snapshot you take, sort and mark up by hand.

Three sheets:
  HOT       - score >= 70, the call list
  QUALIFIED - score 40-69, the backlog
  Все лиды  - everything, including cold, for filtering by hand

Usage (from the repo root):
    python scripts/export_excel.py
    python scripts/export_excel.py --out C:\\path\\leads.xlsx
"""

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

import psycopg
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

QUERY = """
select
    l.score,
    l.status,
    c.name,
    c.source,
    c.phone,
    c.email,
    c.messenger,
    c.city,
    c.domain,
    c.address,
    l.hot_reason,
    c.raw ->> 'pagespeed'            as pagespeed,
    c.raw ->> 'auditFails'           as audit_fails,
    c.raw ->> 'audit_failed_checks'  as failed_checks,
    c.raw ->> 'primary_phone_type'   as phone_type,
    c.source_url,
    c.created_at
from leads l
join companies c on c.id = l.company_id
order by l.score desc, c.name asc
"""

HEADERS = [
    ("Скор", 7), ("Статус", 11), ("Компания", 34), ("Источник", 11),
    ("Телефон", 17), ("Email", 26), ("Мессенджер", 18), ("Город", 15),
    ("Сайт", 24), ("Адрес", 34), ("Почему лид", 46),
    ("PageSpeed", 11), ("Провалов", 10), ("Что не так с сайтом", 40),
    ("Тип телефона", 13), ("Ссылка на источник", 30), ("Добавлен", 18),
]

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HOT_FILL = PatternFill("solid", fgColor="FCE4E4")
FONT = "Arial"


def fetch_rows(dsn: str) -> list[tuple]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(QUERY)
        return cur.fetchall()


def pretty_checks(value) -> str:
    """audit_failed_checks is a JSON array in raw; show it as a plain list."""
    if not value:
        return ""
    try:
        return ", ".join(json.loads(value))
    except (ValueError, TypeError):
        return str(value)


def write_sheet(workbook, title: str, rows: list[tuple], highlight_hot: bool) -> None:
    sheet = workbook.create_sheet(title)

    for index, (header, width) in enumerate(HEADERS, start=1):
        cell = sheet.cell(row=1, column=index, value=header)
        cell.font = Font(name=FONT, bold=True, color="FFFFFF")
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.row_dimensions[1].height = 28

    for row_index, row in enumerate(rows, start=2):
        (score, status, name, source, phone, email, messenger, city, domain,
         address, reason, pagespeed, audit_fails, checks, phone_type,
         source_url, created_at) = row

        values = [
            score, status, name, source, phone, email, messenger, city, domain,
            address, reason,
            int(pagespeed) if pagespeed not in (None, "") else None,
            int(audit_fails) if audit_fails not in (None, "") else None,
            pretty_checks(checks),
            phone_type, source_url,
            created_at.strftime("%d.%m.%Y %H:%M") if created_at else None,
        ]

        for col_index, value in enumerate(values, start=1):
            cell = sheet.cell(row=row_index, column=col_index, value=value)
            cell.font = Font(name=FONT)
            cell.alignment = Alignment(vertical="top", wrap_text=col_index in (10, 11, 14))
            if highlight_hot and score is not None and score >= 70:
                cell.fill = HOT_FILL

    # Freeze the header and switch on autofilter: this file is meant to be
    # sorted and filtered by hand while calling, not read top to bottom.
    sheet.freeze_panes = "A2"
    if rows:
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{len(rows) + 1}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None)
    parser.add_argument("--dsn", default=os.environ.get(
        "LEADGEN_DSN", "postgresql://qorsa:qorsa@localhost:5432/qorsa_leadgen"))
    args = parser.parse_args()

    rows = fetch_rows(args.dsn)
    hot = [r for r in rows if r[0] is not None and r[0] >= 70]
    qualified = [r for r in rows if r[0] is not None and 40 <= r[0] < 70]

    workbook = Workbook()
    workbook.remove(workbook.active)
    write_sheet(workbook, "HOT", hot, highlight_hot=False)
    write_sheet(workbook, "QUALIFIED", qualified, highlight_hot=False)
    write_sheet(workbook, "Все лиды", rows, highlight_hot=True)

    out = Path(args.out) if args.out else Path(
        f"leads_{datetime.now():%Y-%m-%d_%H%M}.xlsx")
    workbook.save(out)

    print(f"Готово: {out.resolve()}")
    print(f"  HOT        {len(hot)}")
    print(f"  QUALIFIED  {len(qualified)}")
    print(f"  всего      {len(rows)}")


if __name__ == "__main__":
    main()
