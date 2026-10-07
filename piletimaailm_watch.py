#!/usr/bin/env python3
"""
Watch a Piletimaailm performance page and send a push notification (ntfy.sh)
when any date goes from sold out to having free seats.

Setup:
    pip install requests beautifulsoup4
    Install the ntfy app on your phone and subscribe to your NTFY_TOPIC.

Run every 5-10 minutes (cron, Task Scheduler, or GitHub Actions).
"""
import json
import os
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = "https://www.piletimaailm.com/performances/97714-lehman-brothers"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "change-me-to-something-unique-lehman-tickets")
WATCH_DATES = []  # e.g. ["16.10.2026", "06.11.2026"]; empty list = watch all dates
STATE_FILE = Path(__file__).with_name("piletimaailm_state.json")

DATE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})")
SEATS_RE = re.compile(r"Vabu kohti:\s*(\d+)")


def fetch_availability():
    """Return {'07.10.2026 19:00': free_seats, ...}"""
    r = requests.get(URL, headers={"User-Agent": "Mozilla/5.0 (ticket-watcher)"}, timeout=30)
    r.raise_for_status()
    text = BeautifulSoup(r.text, "html.parser").get_text("\n")

    # Only look at the "Toimumisajad" (performance dates) section
    start = text.find("Toimumisajad")
    end = text.find("Ürituste otsing")
    if start != -1:
        text = text[start : end if end > start else None]

    result = {}
    matches = list(DATE_RE.finditer(text))
    for i, m in enumerate(matches):
        block_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[m.end() : block_end]
        seats = SEATS_RE.search(block)
        if seats:
            result[f"{m.group(1)} {m.group(2)}"] = int(seats.group(1))
    return result


def notify(message):
    requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={"Title": "Tickets available!", "Click": URL, "Priority": "high"},
        timeout=30,
    )


def main():
    current = fetch_availability()
    if not current:
        # Page layout changed or request was blocked; tell yourself instead of failing silently
        notify("Ticket watcher: could not read availability, the page layout may have changed.")
        sys.exit(1)

    if WATCH_DATES:
        current = {k: v for k, v in current.items() if k.split()[0] in WATCH_DATES}

    previous = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}

    # Notify only on a change from 0 free seats to >0 (not on every run)
    newly_available = [
        f"{date}: {seats} free"
        for date, seats in current.items()
        if seats > 0 and previous.get(date, 0) == 0
    ]
    if newly_available:
        notify("Lehman Brothers tickets:\n" + "\n".join(newly_available))

    STATE_FILE.write_text(json.dumps(current, indent=2))
    print(current)


if __name__ == "__main__":
    main()
