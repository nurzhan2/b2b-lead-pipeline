"""Call-list export: three sheets answering three questions.

  Без сайта        - companies with no website at all. The easiest pitch
                     there is, and the core already scores it +40.
  Сайт на доработку- companies whose site was measured and found wanting.
                     Carries the specific failures, so the call opens with
                     "у вас на сайте нет мобильной вёрстки", not "здравствуйте".
  Есть руководитель- companies where a name is known (from a госзакупка
                     notice, ЕГРЮЛ, or the site itself). These are the ones
                     where a 495 switchboard can actually be got past.

The sheets overlap on purpose - a company can be in all three, and that's
the best kind of lead there is.

Usage (from the repo root):
    python scripts/export_calllist.py
    python scripts/export_calllist.py --out C:\\path\\calllist.xlsx
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

BASE_SELECT = """
select
    l.score,
    l.status,
    c.name,
    coalesce(c.raw ->> 'contact_person', '')          as contact_person,
    coalesce(c.raw ->> 'ceo_name', '')                as ceo_name,
    coalesce(c.raw ->> 'ceo_post', '')                as ceo_post,
    c.phone,
    coalesce(c.raw ->> 'primary_phone_type', '')      as phone_type,
    c.email,
    c.messenger,
    c.city,
    c.domain,
    c.source,
    c.raw ->> 'pagespeed'                             as pagespeed,
    c.raw ->> 'auditFails'                            as audit_fails,
    c.raw ->> 'audit_failed_checks'                   as failed_checks,
    l.hot_reason,
    c.source_url
from leads l
join companies c on c.id = l.company_id
"""

# Worst sites and hottest leads first - the file is read top-down while calling.
ORDER = " order by l.score desc, c.name asc"

SHEETS = {
    "Без сайта": BASE_SELECT + " where c.has_site = false" + ORDER,
    # "Measured and bad" - never "unmeasured". A site nobody checked is not
    # a site that needs work, and putting it here would waste a call.
    "Сайт на доработку": BASE_SELECT + """
        where (c.raw ->> 'auditFails') ~ '^[0-9]+$'
          and (c.raw ->> 'auditFails')::int >= 3
           or ((c.raw ->> 'pagespeed') ~ '^[0-9]+$' and (c.raw ->> 'pagespeed')::int < 50)
    """ + ORDER,
    "Есть руководитель": BASE_SELECT + """
        where coalesce(c.raw ->> 'contact_person', c.raw ->> 'ceo_name', '') <> ''
    """ + ORDER,
}

HEADERS = [
    ("Скор", 7), ("Статус", 11), ("Компания", 34), ("Кого спрашивать", 30),
    ("Телефон", 17), ("Тип", 12), ("Email", 26), ("Мессенджер", 18),
    ("Город", 15), ("Сайт", 22), ("Источник", 11),
    ("PageSpeed", 10), ("Провалов", 9), ("Что не так с сайтом", 42),
    ("Почему лид", 42), ("Ссылка", 28),
]

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HOT_FILL = PatternFill("solid", fgColor="FCE4E4")
MOBILE_FILL = PatternFill("solid", fgColor="E2EFDA")
FONT = "Arial"


def who_to_ask(contact_person: str, ceo_name: str, ceo_post: str) -> str:
    """One column, three producers. The procurement notice's responsible
    official wins: they run THIS purchase, while a CEO merely signs."""
    if contact_person:
        return contact_person
    if ceo_name:
        return f"{ceo_post} {ceo_name}".strip() if ceo_post else ceo_name
    return ""


def pretty_checks(value) -> str:
    if not value:
        return ""
    try:
        return ", ".join(json.loads(value))
    except (ValueError, TypeError):
        return str(value)


def write_sheet(workbook, title: str, rows: list[tuple]) -> int:
    sheet = workbook.create_sheet(title)

    for index, (header, width) in enumerate(HEADERS, start=1):
        cell = sheet.cell(row=1, column=index, value=header)
        cell.font = Font(name=FONT, bold=True, color="FFFFFF")
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.row_dimensions[1].height = 28

    for row_index, row in enumerate(rows, start=2):
        (score, status, name, contact_person, ceo_name, ceo_post, phone, phone_type,
         email, messenger, city, domain, source, pagespeed, audit_fails, checks,
         reason, source_url) = row

        values = [
            score, status, name,
            who_to_ask(contact_person, ceo_name, ceo_post),
            phone, phone_type, email, messenger, city, domain, source,
            int(pagespeed) if pagespeed else None,
            int(audit_fails) if audit_fails else None,
            pretty_checks(checks), reason, source_url,
        ]

        for col_index, value in enumerate(values, start=1):
            cell = sheet.cell(row=row_index, column=col_index, value=value)
            cell.font = Font(name=FONT)
            cell.alignment = Alignment(vertical="top", wrap_text=col_index in (14, 15))
            if score is not None and score >= 70:
                cell.fill = HOT_FILL
            elif col_index == 6 and phone_type == "MOBILE":
                # A mobile number usually reaches a person directly - worth
                # spotting at a glance in a sea of 495 switchboards.
                cell.fill = MOBILE_FILL

    sheet.freeze_panes = "D2"
    if rows:
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{len(rows) + 1}"
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None)
    parser.add_argument("--dsn", default=os.environ.get(
        "LEADGEN_DSN", "postgresql://qorsa:qorsa@localhost:5432/qorsa_leadgen"))
    args = parser.parse_args()

    workbook = Workbook()
    workbook.remove(workbook.active)
    counts = {}

    with psycopg.connect(args.dsn) as conn, conn.cursor() as cur:
        for title, query in SHEETS.items():
            cur.execute(query)
            counts[title] = write_sheet(workbook, title, cur.fetchall())

    out = Path(args.out) if args.out else Path(f"calllist_{datetime.now():%Y-%m-%d_%H%M}.xlsx")
    workbook.save(out)

    print(f"Готово: {out.resolve()}")
    for title, count in counts.items():
        print(f"  {title:<20} {count}")


if __name__ == "__main__":
    main()
