"""Channel verifier: checks every channel in channels.yml WITHOUT touching the
Telegram API.

Telegram publishes a read-only web preview at t.me/s/<username>. It needs no
account, no api_id, no session - and therefore cannot trigger FloodWait. That
matters here: resolving 98 handles through Telethon is what got the parsing
account limited in the first place, and most of those handles turned out to be
dead or renamed.

For each channel this reports:
  alive      - does the preview page exist and show posts
  posts      - how many recent posts were readable
  orders     - posts that look like a client ordering work
  vacancies  - posts that look like an employer hiring staff
  verdict    - keep / drop / review

The orders-vs-vacancies split is the important one. A channel full of
"ищем senior разработчика в штат" is noise for this project: matcher.py's
anti-hiring filter discards those, so monitoring it costs requests and returns
nothing.

Usage (from the repo root):
    python -m workers.telegram_monitor.verify_channels
    python -m workers.telegram_monitor.verify_channels --out verified.yml
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx
import yaml
from bs4 import BeautifulSoup

MODULE_DIR = Path(__file__).resolve().parent
PREVIEW_URL = "https://t.me/s/{username}"

# A client describing work they want done.
ORDER_MARKERS = (
    "ищем подрядчика",
    "нужен исполнитель",
    "требуется разработать",
    "нужно разработать",
    "нужен сайт",
    "нужен бот",
    "нужен дизайн",
    "заказ",
    "бюджет",
    "тз",
    "срок разработки",
    "оплата за проект",
    "за проект",
    "разово",
    "под ключ",
    "смета",
    "стоимость работ",
)

# An employer hiring a person. matcher.py filters these out, so a channel
# dominated by them is not worth monitoring.
VACANCY_MARKERS = (
    "в штат",
    "вакансия",
    "полная занятость",
    "part-time",
    "full-time",
    "оклад",
    "зарплата",
    "з/п",
    "испытательный срок",
    "дмс",
    "ищем в команду",
    "ищем сотрудника",
    "резюме",
    "собеседование",
    "график работы",
    "офис",
    "оформление по тк",
    "тк рф",
)


async def check_one(client: httpx.AsyncClient, username: str) -> dict:
    result = {
        "username": username,
        "alive": False,
        "posts": 0,
        "orders": 0,
        "vacancies": 0,
        "title": None,
        "note": None,
    }
    try:
        response = await client.get(PREVIEW_URL.format(username=username), timeout=20.0, follow_redirects=True)
    except httpx.TransportError as exc:
        result["note"] = f"transport: {exc}"
        return result

    if response.status_code != 200:
        result["note"] = f"HTTP {response.status_code}"
        return result

    soup = BeautifulSoup(response.text, "html.parser")

    # A private, deleted or renamed channel serves a page with no message
    # bodies - that is the signal, not the status code.
    messages = soup.select(".tgme_widget_message_text")
    if not messages:
        result["note"] = "preview has no posts (private, empty or gone)"
        return result

    title = soup.select_one(".tgme_channel_info_header_title")
    result["title"] = title.get_text(strip=True) if title else None
    result["alive"] = True
    result["posts"] = len(messages)

    for message in messages:
        text = message.get_text(" ", strip=True).lower()
        if any(marker in text for marker in ORDER_MARKERS):
            result["orders"] += 1
        if any(marker in text for marker in VACANCY_MARKERS):
            result["vacancies"] += 1

    return result


def verdict(row: dict) -> str:
    """keep / review / drop.

    Deliberately conservative about 'keep': a channel earns it by showing
    order-shaped posts that outnumber vacancy-shaped ones. Everything
    ambiguous goes to review rather than being silently kept - a wrong keep
    costs requests on every run forever.
    """
    if not row["alive"]:
        return "drop"
    if row["orders"] == 0:
        return "drop"
    if row["orders"] > row["vacancies"]:
        return "keep"
    return "review"


async def main_async(args) -> int:
    channels_path = MODULE_DIR / "channels.yml"
    data = yaml.safe_load(channels_path.read_text(encoding="utf-8"))
    entries = data.get("channels") or []
    usernames = [entry["username"] for entry in entries if entry.get("username")]

    print(f"Проверяю {len(usernames)} каналов через t.me/s/ (без Telegram API, FloodWait невозможен)\n", flush=True)

    semaphore = asyncio.Semaphore(args.concurrency)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; qorsa-channel-check/1.0)"}

    async with httpx.AsyncClient(headers=headers) as client:

        async def guarded(username: str) -> dict:
            async with semaphore:
                row = await check_one(client, username)
                await asyncio.sleep(args.delay)
                return row

        rows = await asyncio.gather(*(guarded(u) for u in usernames))

    for row in rows:
        row["verdict"] = verdict(row)

    keep = [r for r in rows if r["verdict"] == "keep"]
    review = [r for r in rows if r["verdict"] == "review"]
    drop = [r for r in rows if r["verdict"] == "drop"]
    dead = [r for r in rows if not r["alive"]]

    print(f"{'канал':<24} {'жив':<5} {'постов':<7} {'заказы':<7} {'вакансии':<9} вердикт")
    print("-" * 72)
    for row in sorted(rows, key=lambda r: (r["verdict"], -r["orders"])):
        print(
            f"{row['username']:<24} {'да' if row['alive'] else 'нет':<5} "
            f"{row['posts']:<7} {row['orders']:<7} {row['vacancies']:<9} {row['verdict']}"
        )

    print(f"\nИТОГО: keep {len(keep)}, review {len(review)}, drop {len(drop)} (из них мёртвых {len(dead)})")

    if args.out:
        out_path = Path(args.out)
        payload = {
            "channels": [
                {"username": r["username"], "title": r["title"], "priority": 1, "category": "verified_orders"}
                for r in keep
            ]
        }
        out_path.write_text(
            "# Автогенерировано verify_channels.py - только каналы со вердиктом keep.\n"
            "# Проверено через t.me/s/, Telegram API не задействован.\n\n"
            + yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        print(f"Список keep записан в {out_path}")

    if args.json:
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--delay", type=float, default=0.5, help="пауза после каждого запроса, секунды")
    parser.add_argument("--out", help="записать keep-каналы в YAML")
    parser.add_argument("--json", help="записать полный отчёт в JSON")
    args = parser.parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
