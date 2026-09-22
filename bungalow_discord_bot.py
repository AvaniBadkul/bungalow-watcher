#!/usr/bin/env python3
"""
Bungalow NYC table watcher -> Discord alerts (Resy)

Watches Bungalow on Resy for a dinner table for 2 between 6:00pm and 8:00pm
on any night in the next 21 days. When one opens it:
  1. posts an alert (with @everyone ping) to your Discord channel, with a
     direct link to that date on Resy, and
  2. opens that Resy page in your browser on this laptop.
You click the time, and Resy fills your name/phone/email from your account;
you enter or confirm the card for the $25/person deposit and hit Reserve.

No Resy login or card is used by this script.

Setup (one time):
    pip install requests
    In Discord: Server Settings -> Integrations -> Webhooks -> New Webhook
    -> pick the channel -> Copy Webhook URL.
    Be signed in to resy.com in your browser so your details autofill.

Run (Mac/Linux):
    export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
    python3 bungalow_discord_bot.py
    python3 bungalow_discord_bot.py --test   # sends a test message to Discord

Run (Windows PowerShell):
    $env:DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
    python bungalow_discord_bot.py

Keep the laptop awake and the terminal open. Ctrl+C stops it.
Note: this uses Resy's unofficial web API, which Resy may change or block.
"""

import argparse
import datetime as dt
import os
import random
import sys
import time
import webbrowser

import requests

# ---------- settings you can change ----------
VENUE_SLUG = "bungalow-ny"
VENUE_NAME = "Bungalow"
PARTY_SIZE = 2
EARLIEST = dt.time(18, 0)      # 6:00pm
LATEST = dt.time(20, 0)        # 8:00pm (inclusive)
DAYS_AHEAD = 21                # today through 3 weeks out
CLOSED_WEEKDAYS = {0}          # Monday = 0 (Bungalow is closed Mondays)
POLL_SECONDS = 60              # normal check interval
FAST_POLL_SECONDS = 5          # interval around the 11am release
OPEN_BROWSER = os.environ.get("OPEN_BROWSER", "1") == "1"   # open Resy page on this laptop
PING = "@everyone"             # "" for no ping, or "<@YOUR_DISCORD_USER_ID>"
RUN_MINUTES = int(os.environ.get("RUN_MINUTES", "0"))      # 0 = run forever
QUIET_START = os.environ.get("QUIET_START", "0") == "1"      # skip "Watching..." post
# ---------------------------------------------

API = "https://api.resy.com"
API_KEY = "VbWk7s3L4KiK5fzlO7JD3Q5EYolJI7n5"   # public key used by resy.com
RESY_PAGE = f"https://resy.com/cities/new-york-ny/venues/{VENUE_SLUG}"

session = requests.Session()
session.headers.update({
    "Authorization": f'ResyAPI api_key="{API_KEY}"',
    "Origin": "https://resy.com",
    "Referer": "https://resy.com/",
    "Accept": "application/json, text/plain, */*",
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0 Safari/537.36"),
})


def log(msg):
    print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def fmt_time(t):
    return t.strftime("%I:%M%p").lstrip("0").lower()


def discord(webhook, content, embed=None):
    payload = {"content": content, "allowed_mentions": {"parse": ["everyone", "users"]}}
    if embed:
        payload["embeds"] = [embed]
    try:
        r = requests.post(webhook, json=payload, timeout=15)
        if r.status_code >= 300:
            log(f"Discord error {r.status_code}: {r.text[:200]}")
    except requests.RequestException as e:
        log(f"Couldn't reach Discord: {e}")


def venue_id():
    r = session.get(f"{API}/3/venue",
                    params={"url_slug": VENUE_SLUG, "location": "ny"}, timeout=20)
    r.raise_for_status()
    return r.json()["id"]["resy"]


def dates_to_check():
    today = dt.date.today()
    for i in range(DAYS_AHEAD + 1):
        d = today + dt.timedelta(days=i)
        if d.weekday() not in CLOSED_WEEKDAYS:
            yield d


def find_slots(vid, day):
    r = session.get(f"{API}/4/find", params={
        "lat": 0, "long": 0, "day": day.isoformat(),
        "party_size": PARTY_SIZE, "venue_id": vid,
    }, timeout=20)
    if r.status_code == 429:
        raise RuntimeError("rate limited by Resy")
    r.raise_for_status()
    out = []
    now = dt.datetime.now()
    for v in r.json().get("results", {}).get("venues", []):
        for s in v.get("slots", []):
            start = dt.datetime.strptime(s["date"]["start"], "%Y-%m-%d %H:%M:%S")
            if EARLIEST <= start.time() <= LATEST and start > now + dt.timedelta(hours=1):
                out.append((start, s["config"].get("type", "") or "Table"))
    return sorted(out)


def near_release():
    now = dt.datetime.now().time()
    return dt.time(10, 58) <= now <= dt.time(11, 10)


def alert(webhook, day, slots):
    link = f"{RESY_PAGE}?date={day.isoformat()}&seats={PARTY_SIZE}"
    times = ", ".join(f"{fmt_time(s.time())} ({kind})" for s, kind in slots)
    headline = f"{day:%a %b} {day.day}"
    log(f"OPEN: {headline} - {times}")
    discord(webhook, f"{PING} Table open at {VENUE_NAME}! Book now:".strip(), {
        "title": f"{VENUE_NAME} - {headline}, party of {PARTY_SIZE}",
        "url": link,
        "description": f"**Times:** {times}\n\n[Open on Resy]({link}) -> pick the time "
                       f"-> confirm card -> Reserve.\nDeposit: $25/person, non-refundable.",
        "color": 0xE8A0B4,
    })
    if OPEN_BROWSER:
        webbrowser.open(link)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="send a test Discord message and exit")
    args = ap.parse_args()

    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        sys.exit("Set DISCORD_WEBHOOK_URL first (see top of file).")

    if args.test:
        discord(webhook, f"Test: {VENUE_NAME} watcher is connected to this channel.")
        log("Test done. Check Discord.")
        return

    vid = venue_id()
    log(f"Watching {VENUE_NAME} (id {vid}) for {PARTY_SIZE}, "
        f"{fmt_time(EARLIEST)}-{fmt_time(LATEST)}, next {DAYS_AHEAD} days.")
    if not QUIET_START:
        discord(webhook, f"Watching {VENUE_NAME} for a table for {PARTY_SIZE}, "
                         f"{fmt_time(EARLIEST)}-{fmt_time(LATEST)}, next {DAYS_AHEAD} days.")
    stop_at = time.time() + RUN_MINUTES * 60 if RUN_MINUTES else None

    already_alerted = set()   # (date, time) pairs so you aren't spammed
    backoff = 0
    while True:
        if stop_at and time.time() > stop_at:
            log("Run window finished.")
            return
        try:
            for day in dates_to_check():
                slots = find_slots(vid, day)
                new = [(s, k) for s, k in slots if (day, s.time()) not in already_alerted]
                if new:
                    alert(webhook, day, new)
                    already_alerted.update((day, s.time()) for s, _ in new)
                time.sleep(random.uniform(0.8, 1.6))
            backoff = 0
        except RuntimeError as e:
            backoff = min(backoff * 2 or 120, 900)
            log(f"{e}; pausing {backoff}s")
            time.sleep(backoff)
            continue
        except requests.RequestException as e:
            log(f"Network hiccup: {e}")
        time.sleep(FAST_POLL_SECONDS if near_release()
                   else POLL_SECONDS + random.uniform(0, 20))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Stopped.")
