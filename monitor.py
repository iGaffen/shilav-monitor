"""Monitor Shilav pages for a change from 'closed' to 'open' and notify via Telegram."""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

import requests

# Pages to monitor. Add a URL here to monitor another page.
URLS = [
    "https://lp.vp4.me/jzze",
    "https://lp.vp4.me/waxf",
]
ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")
CLOSED_MARKER = "המלאי אזל"
STATE_FILE = Path(__file__).parent / "state.json"
FAILURE_ALERT_THRESHOLD = 3  # consecutive failed checks before alerting

# A gap above CADENCE_MIN_GAP_MINUTES between two runs on the same Israel
# calendar day means the external cron-job.org trigger has likely stopped.
# (The overnight pause always crosses midnight, so it never triggers this.)
CADENCE_MIN_GAP_MINUTES = 40

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

DEFAULT_PAGE_STATE = {"status": "closed", "redirect_detected": False}

DEFAULT_STATE = {
    "pages": {},
    "last_heartbeat_date": None,
    "consecutive_failures": 0,
    "failure_alert_sent": False,
    "last_run_at": None,
    "cadence_alert_sent": False,
}


def load_state() -> dict:
    if STATE_FILE.exists():
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        for key, value in DEFAULT_STATE.items():
            data.setdefault(key, value)
    else:
        data = json.loads(json.dumps(DEFAULT_STATE))
    # Migrate the old single-page format.
    if "status" in data:
        data["pages"].setdefault(URLS[0], {
            "status": data.pop("status"),
            "redirect_detected": data.pop("redirect_detected", False),
        })
    for url in URLS:
        page = data["pages"].setdefault(url, {})
        for key, value in DEFAULT_PAGE_STATE.items():
            page.setdefault(key, value)
    return data


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def fetch_page(url: str) -> tuple[str, bool]:
    """Return (status, redirected) for the monitored page.

    Adds a cache-busting query param alongside the no-cache headers so any CDN
    or proxy in front of the page can't serve a stale, previously cached copy.
    """
    cache_buster = {"_": str(int(time.time()))}
    resp = requests.get(url, headers=HEADERS, params=cache_buster, timeout=30, allow_redirects=True)
    resp.raise_for_status()

    # Compare only scheme/host/path (ignoring query strings, including our own
    # cache-buster) so a real redirect is detected without false positives.
    final = urlsplit(resp.url)
    original = urlsplit(url)
    redirected = (final.scheme, final.netloc, final.path.rstrip("/")) != (
        original.scheme,
        original.netloc,
        original.path.rstrip("/"),
    )

    status = "closed" if CLOSED_MARKER in resp.text else "open"
    return status, redirected


def check_cadence(state: dict, now: datetime) -> None:
    """Alert if checks have slowed down (the external trigger likely died)
    without touching consecutive_failures, since the checks themselves may
    still be succeeding via GitHub's hourly fallback schedule."""
    last_run_at = state["last_run_at"]
    if last_run_at is None:
        return

    last = datetime.fromisoformat(last_run_at)
    gap_minutes = (now - last).total_seconds() / 60
    same_israel_day = last.astimezone(ISRAEL_TZ).date() == now.astimezone(ISRAEL_TZ).date()

    if same_israel_day and gap_minutes > CADENCE_MIN_GAP_MINUTES:
        if not state["cadence_alert_sent"]:
            send_telegram_message(
                "⚠️ הבדיקות מאטות: עברו "
                f"{gap_minutes:.0f} דקות מאז הבדיקה הקודמת (רגיל: כל 10-15 דק'). "
                "כנראה שהטריגר החיצוני מ-cron-job.org הפסיק לעבוד, וכרגע רצה רק "
                "רשת הביטחון השעתית של GitHub. כדאי לבדוק את ה-cron job שם."
            )
            state["cadence_alert_sent"] = True
            print(f"Cadence alert sent (gap={gap_minutes:.0f} min).")
    else:
        state["cadence_alert_sent"] = False


def send_telegram_message(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(api_url, data={"chat_id": chat_id, "text": text}, timeout=30)
    resp.raise_for_status()


def main() -> None:
    state = load_state()
    now = datetime.now(timezone.utc)

    check_cadence(state, now)
    state["last_run_at"] = now.isoformat()

    statuses = {}
    failed = []
    for url in URLS:
        try:
            statuses[url] = fetch_page(url)
        except requests.RequestException as exc:
            print(f"Check failed for {url}: {exc}", file=sys.stderr)
            failed.append(url)

    if failed:
        state["consecutive_failures"] += 1
        print(f"{state['consecutive_failures']} failed run(s) in a row.", file=sys.stderr)
        if (
            state["consecutive_failures"] >= FAILURE_ALERT_THRESHOLD
            and not state["failure_alert_sent"]
        ):
            send_telegram_message(
                "⚠️ הבדיקה של עמוד שילב נכשלה "
                f"{state['consecutive_failures']} פעמים ברצף. "
                "יכול להיות שהמערכת תקועה - כדאי לבדוק ידנית.\n" + "\n".join(failed)
            )
            state["failure_alert_sent"] = True
    else:
        if state["consecutive_failures"] > 0:
            print(f"Recovered after {state['consecutive_failures']} failed run(s).")
        state["consecutive_failures"] = 0
        state["failure_alert_sent"] = False

    for url, (current_status, redirected) in statuses.items():
        page = state["pages"][url]
        previous_status = page["status"]
        print(f"{url}: previous={previous_status} current={current_status} redirected={redirected}")

        if redirected and not page["redirect_detected"]:
            send_telegram_message(
                "🚨 שינוי חשוד: הבקשה לעמוד שילב הופנתה (redirect) לכתובת אחרת! "
                f"יכול להיות שהם הפעילו את הרכישה. {url}"
            )
            print("Redirect notification sent.")
        page["redirect_detected"] = redirected

        if previous_status == "closed" and current_status == "open":
            send_telegram_message(f"🔔 העמוד של שילב נפתח להזמנה! {url}")
            print("Telegram notification sent.")
        page["status"] = current_status

    today = now.strftime("%Y-%m-%d")
    if statuses and state["last_heartbeat_date"] != today:
        lines = [
            f"{'פתוח' if st == 'open' else 'סגור'} - {url}"
            for url, (st, _) in statuses.items()
        ]
        send_telegram_message("✅ בדיקה יומית: העמודים נבדקו ותקינים.\n" + "\n".join(lines))
        state["last_heartbeat_date"] = today
        print("Daily heartbeat sent.")

    save_state(state)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
