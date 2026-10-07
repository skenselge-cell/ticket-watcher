#!/usr/bin/env python3
"""
Watch one or more Piletimaailm performance pages and send a push notification
(ntfy.sh) when a date goes from sold out to having free seats.

Setup:
    pip install requests beautifulsoup4
    Set the NTFY_TOPIC environment variable (GitHub: repository secret).

To watch another play, add its page URL to PLAYS below.
"""
import json
import os
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# One entry per play. "dates" is optional: leave it out (or []) to watch all dates.
PLAYS = [
    {
        "url": "https://www.piletimaailm.com/performances/97714-lehman-brothers",
        # "dates": ["16.10.2026", "06.11.2026"],
    },
     {
         "url": "https://www.piletimaailm.com/performances/130852-b-koondis",
    #   "dates": [],
    },
]

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "change-me-to-something-unique-lehman-tickets")
STATE_FILE = Path(__file__).with_name("piletimaailm_state.json")

DATE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})")
SEATS_RE = re.compile(r"Vabu kohti:\s*(\d+)")


def fetch_play(url):
    """Return (play_name, {'07.10.2026 19:00': free_seats, ...})"""
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (ticket-watcher)"}, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    title = soup.title.get_text(strip=True) if soup.title else url
    name = title.split("|")[0].strip() or url

    text = soup.get_text("\n")
    start = text.find("Toimumisajad")
    end = text.find("Ürituste otsing")
    if start != -1:
        text = text[start : end if end > start else None]

    result = {}
    matches = list(DATE_RE.finditer(text))
    for i, m in enumerate(matches):
        block_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        seats = SEATS_RE.search(text[m.end() : block_end])
        if seats:
            result[f"{m.group(1)} {m.group(2)}"] = int(seats.group(1))
    return name, result


def notify(title, message, click_url):
    requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={"Title": title.encode("utf-8"), "Click": click_url, "Priority": "high"},
        timeout=30,
    )


def main():
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    new_state = {}
    failed = False

    for play in PLAYS:
        url = play["url"]
        try:
            name, current = fetch_play(url)
        except Exception as e:  # network error, blocked, etc.
            print(f"{url}: error {e}")
            failed = True
            new_state[url] = state.get(url, {})  # keep old state
            continue

        if not current:
            notify("Ticket watcher problem", f"Could not read availability for {url}. The page layout may have changed.", url)
            failed = True
            new_state[url] = state.get(url, {})
            continue

        wanted = play.get("dates") or []
        if wanted:
            current = {k: v for k, v in current.items() if k.split()[0] in wanted}

        previous = state.get(url, {})
        if not isinstance(previous, dict):
            previous = {}

        # Notify only on a change from 0 free seats to >0 (not on every run)
        newly_available = [
            f"{date}: {seats} free"
            for date, seats in current.items()
            if seats > 0 and previous.get(date, 0) == 0
        ]
        if newly_available:
            notify(f"Tickets available: {name}", "\n".join(newly_available), url)

        new_state[url] = current
        print(name, current)

    STATE_FILE.write_text(json.dumps(new_state, indent=2, ensure_ascii=False))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
