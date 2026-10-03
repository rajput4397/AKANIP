import logging
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def _load_env_file():
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env", override=False)


def get_telegram_credentials():
    _load_env_file()
    return os.getenv("BOT_TOKEN", "").strip(), os.getenv("CHAT_ID", "").strip()


def send_telegram_alert(message):
    bot_token, chat_id = get_telegram_credentials()
    if not bot_token or not chat_id:
        logger.warning("Telegram notification skipped: BOT_TOKEN and CHAT_ID are not configured.")
        return False

    text = str(message).strip()
    if not text:
        return False

    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }

    for attempt in range(3):
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                data=payload,
                timeout=30,
            )
            if response.status_code == 200:
                logger.info("Telegram alert sent successfully")
                return True

            logger.warning("Telegram alert failed (attempt %s/%s): %s", attempt + 1, 3, response.text)
            if response.status_code in (400, 401, 403, 404):
                return False
        except requests.exceptions.Timeout as exc:
            logger.warning("Telegram timeout on attempt %s/%s: %s", attempt + 1, 3, exc)
            if attempt == 2:
                logger.exception("Telegram alert failed: %s", exc)
                return False
        except Exception as exc:  # pragma: no cover - defensive logging path
            logger.exception("Telegram alert failed: %s", exc)
            return False

    return False
