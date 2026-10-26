#!/usr/bin/env python3
"""
Watch ticket pages on Piletimaailm and Piletikeskus and send a push
notification (ntfy.sh) when a date goes from sold out to available.

Setup:
    pip install requests beautifulsoup4
    Set the NTFY_TOPIC environment variable (GitHub: repository secret).

To watch another play, add its page URL to PLAYS below. The site is detected
from the URL. For piletikeskus.ee you can use either the show page
(https://piletikeskus.ee/et/s/<name>) or any single event page
(https://piletikeskus.ee/et/e/<id>), which is resolved to its show page.
"""
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString

# One entry per play. "dates" is optional: leave it out (or []) to watch all dates,
# e.g. "dates": ["16.10.2026"].

PLAYS = [
     {"url": "https://piletikeskus.ee/et/s/mees-nimega-ove"},
    {
      "url": "https://piletikeskus.ee/et/s/nimi"  
    },
    {
        "url": "https://www.piletimaailm.com/performances/121675-voorad",
        # "dates": ["16.10.2026", "06.11.2026"],
    }, {
         "url": "https://www.piletimaailm.com/performances/130852-b-koondis",
    #   "dates": [],
    }, {
         "url": "https://www.piletimaailm.com/performances/145868-caligula",
    #   "dates": [],
    }
]

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "change-me-to-something-unique-lehman-tickets")
STATE_FILE = Path(__file__).with_name("piletimaailm_state.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def get_soup(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def page_name(soup, fallback):
    title = soup.title.get_text(strip=True) if soup.title else ""
    return re.split(r"\s[|–-]\s", title)[0].strip() or fallback


# ---------------------------------------------------------------- piletimaailm.com
PM_DATE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})")
PM_SEATS_RE = re.compile(r"Vabu kohti:\s*(\d+)")


def fetch_piletimaailm(url):
    """Return (name, {'07.10.2026 19:00': free_seats})"""
    soup = get_soup(url)
    name = page_name(soup, url)
    text = soup.get_text("\n")
    start = text.find("Toimumisajad")
    end = text.find("Ürituste otsing")
    if start != -1:
        text = text[start : end if end > start else None]

    result = {}
    matches = list(PM_DATE_RE.finditer(text))
    for i, m in enumerate(matches):
        block_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        seats = PM_SEATS_RE.search(text[m.end() : block_end])
        if seats:
            result[f"{m.group(1)} {m.group(2)}"] = int(seats.group(1))
    return name, result


# ---------------------------------------------------------------- piletikeskus.ee
PK_DATE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s+kell\s+(\d{2}:\d{2})")
PK_LAST_RE = re.compile(r"Viimased\s+(\d+)\s+pilet", re.I)


def resolve_piletikeskus_show(url):
    """Event pages (/et/e/<id>) are resolved to their show page (/et/s/<slug>)."""
    path = urlparse(url).path
    if "/s/" in path:
        return url, None
    soup = get_soup(url)
    for a in soup.find_all("a", href=True):
        if re.search(r"/et/s/[^/?#]+", a["href"]):
            return urljoin(url, a["href"]), soup
    return url, soup  # no show link found: parse the page we have


def fetch_piletikeskus(url):
    """Return (name, {'26.10.2026 19:00': 0 or N})

    A date counts as available only when its entry has a real buy link
    (a link to /select-ticket/...) or a visible "Osta pilet" button.
    The value is the "Viimased N piletit" number when shown, otherwise 1.
    Script/style data is ignored so hidden strings can't cause false positives.
    """
    show_url, soup = resolve_piletikeskus_show(url)
    if soup is None or show_url != url:
        soup = get_soup(show_url)
    name = page_name(soup, show_url)

    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    for a in soup.find_all("a", href=re.compile(r"select-ticket")):
        a.replace_with(NavigableString(" [[BUY]] "))
    text = soup.get_text("\n")

    matches = list(PK_DATE_RE.finditer(text))
    if matches:
        # Ignore the footer (gift cards, app promo, ...) after the last entry
        footer = text.find("Piletiostjale", matches[-1].end())
        if footer != -1:
            text = text[:footer]

    result = {}
    for i, m in enumerate(matches):
        block_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[m.end() : block_end]
        key = f"{m.group(1)} {m.group(2)}"
        if "[[BUY]]" in block or re.search(r"Osta\s+pilet", block, re.I):
            last = PK_LAST_RE.search(block)
            result[key] = int(last.group(1)) if last else 1
        else:
            result[key] = 0
        print(f"  {key}: {'AVAILABLE' if result[key] else 'sold out'} | {' '.join(block.split())[:110]}")
    return name, result


def fetch_play(url):
    host = urlparse(url).netloc.lower()
    if "piletikeskus" in host:
        return fetch_piletikeskus(url)
    if "piletimaailm" in host:
        return fetch_piletimaailm(url)
    raise ValueError(f"Unsupported site: {host}")


# ---------------------------------------------------------------- notify + main
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
        except Exception as e:  # network error, blocked, unsupported site, etc.
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
            f"{date}: {seats} free" if seats > 1 else f"{date}: available"
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
