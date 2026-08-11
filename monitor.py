"""Monitor lp.vp4.me/jzze for a change from 'closed' to 'open' and notify via Telegram."""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

URL = "https://lp.vp4.me/jzze"
CLOSED_MARKER = "המלאי אזל"
STATE_FILE = Path(__file__).parent / "state.json"
FAILURE_ALERT_THRESHOLD = 3  # consecutive failed checks before alerting

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

DEFAULT_STATE = {
    "status": "closed",
    "last_heartbeat_date": None,
    "consecutive_failures": 0,
    "failure_alert_sent": False,
    "redirect_detected": False,
}


def load_state() -> dict:
    if STATE_FILE.exists():
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        for key, value in DEFAULT_STATE.items():
            data.setdefault(key, value)
        return data
    return dict(DEFAULT_STATE)


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def fetch_page() -> tuple[str, bool]:
    """Return (status, redirected) for the monitored page.

    Adds a cache-busting query param alongside the no-cache headers so any CDN
    or proxy in front of the page can't serve a stale, previously cached copy.
    """
    cache_buster = {"_": str(int(time.time()))}
    resp = requests.get(URL, headers=HEADERS, params=cache_buster, timeout=30, allow_redirects=True)
    resp.raise_for_status()

    # Compare only scheme/host/path (ignoring query strings, including our own
    # cache-buster) so a real redirect is detected without false positives.
    final = urlsplit(resp.url)
    original = urlsplit(URL)
    redirected = (final.scheme, final.netloc, final.path.rstrip("/")) != (
        original.scheme,
        original.netloc,
        original.path.rstrip("/"),
    )

    status = "closed" if CLOSED_MARKER in resp.text else "open"
    return status, redirected


def send_telegram_message(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(api_url, data={"chat_id": chat_id, "text": text}, timeout=30)
    resp.raise_for_status()


def main() -> None:
    state = load_state()

    try:
        current_status, redirected = fetch_page()
    except requests.RequestException as exc:
        state["consecutive_failures"] += 1
        print(f"Check failed ({state['consecutive_failures']} in a row): {exc}", file=sys.stderr)

        if (
            state["consecutive_failures"] >= FAILURE_ALERT_THRESHOLD
            and not state["failure_alert_sent"]
        ):
            send_telegram_message(
                "⚠️ הבדיקה של עמוד שילב נכשלה "
                f"{state['consecutive_failures']} פעמים ברצף. "
                "יכול להיות שהמערכת תקועה - כדאי לבדוק ידנית."
            )
            state["failure_alert_sent"] = True

        save_state(state)
        sys.exit(1)

    previous_status = state["status"]
    was_redirected_before = state["redirect_detected"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    print(f"previous={previous_status} current={current_status} redirected={redirected}")

    # Recovered after previous failures.
    if state["consecutive_failures"] > 0:
        print(f"Recovered after {state['consecutive_failures']} failed check(s).")
    state["consecutive_failures"] = 0
    state["failure_alert_sent"] = False

    if redirected and not was_redirected_before:
        send_telegram_message(
            "🚨 שינוי חשוד: הבקשה לעמוד שילב הופנתה (redirect) לכתובת אחרת! "
            "יכול להיות שהם הפעילו את הרכישה. https://lp.vp4.me/jzze"
        )
        print("Redirect notification sent.")
    state["redirect_detected"] = redirected

    if previous_status == "closed" and current_status == "open":
        send_telegram_message("🔔 העמוד של שילב נפתח להזמנה! https://lp.vp4.me/jzze")
        print("Telegram notification sent.")
    state["status"] = current_status

    if state["last_heartbeat_date"] != today:
        status_hebrew = "פתוח" if current_status == "open" else "סגור"
        send_telegram_message(
            f"✅ בדיקה יומית: העמוד נבדק ותקין, המצב הנוכחי: {status_hebrew}."
        )
        state["last_heartbeat_date"] = today
        print("Daily heartbeat sent.")

    save_state(state)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
