"""Минимальный клиент Telegram Bot API на requests.

Токен стоит в пути URL, поэтому ни URL, ни исходные исключения requests
не попадают в логи и тексты ошибок.
"""

import logging

import requests

logging.getLogger("urllib3").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

API = "https://api.telegram.org"


class TelegramError(Exception):
    pass


class Telegram:
    def __init__(self, token: str, session=None):
        if not token:
            raise TelegramError("TELEGRAM_BOT_TOKEN не задан в .env")
        self._token = token
        self._s = session or requests.Session()

    def __repr__(self) -> str:
        return "Telegram(token=***)"

    def call(self, method: str, timeout: float = 60, **params) -> dict | list:
        try:
            r = self._s.post(f"{API}/bot{self._token}/{method}", json=params, timeout=timeout)
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            raise TelegramError(f"{method}: {type(e).__name__}") from None
        if not data.get("ok"):
            desc = str(data.get("description", "")).replace(self._token, "***")
            raise TelegramError(f"{method}: {data.get('error_code')} {desc}")
        return data["result"]

    def send(self, chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
        params = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        return self.call("sendMessage", **params)

    def updates(self, offset: int | None, timeout: int = 50) -> list:
        params = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        return self.call("getUpdates", timeout=timeout + 10, **params)

    def answer_callback(self, callback_id: str) -> None:
        self.call("answerCallbackQuery", callback_query_id=callback_id)
