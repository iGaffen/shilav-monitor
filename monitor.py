"""Monitor lp.vp4.me/jzze for a change from 'closed' to 'open' and notify via Telegram."""
import json
import os
import sys
from pathlib import Path

import requests

URL = "https://lp.vp4.me/jzze"
CLOSED_MARKER = "המלאי אזל"
STATE_FILE = Path(__file__).parent / "state.json"

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
}


def load_state() -> str:
    if STATE_FILE.exists():
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data.get("status", "closed")
    return "closed"


def save_state(status: str) -> None:
    STATE_FILE.write_text(
        json.dumps({"status": status}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def fetch_current_status() -> str:
    resp = requests.get(URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return "closed" if CLOSED_MARKER in resp.text else "open"


def send_telegram_message(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(api_url, data={"chat_id": chat_id, "text": text}, timeout=30)
    resp.raise_for_status()


def main() -> None:
    previous_status = load_state()
    current_status = fetch_current_status()

    print(f"previous={previous_status} current={current_status}")

    if previous_status == "closed" and current_status == "open":
        send_telegram_message("🔔 העמוד של שילב נפתח להזמנה! https://lp.vp4.me/jzze")
        print("Telegram notification sent.")

    if current_status != previous_status:
        save_state(current_status)
        print(f"State updated: {previous_status} -> {current_status}")
    else:
        print("No state change.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
