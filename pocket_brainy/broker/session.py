"""Persistência de sessão Pocket Option (cookies/SSID após login via email+senha)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SESSION_PATH = DATA_DIR / "session.json"

# SSID expira em ~4h; cookies de sessão duram vários dias
_SSID_TTL = 60 * 60 * 4       # 4 horas
_COOKIES_TTL = 60 * 60 * 72   # 72 horas


class SessionStore:
    """Cache de sessão local: ssid, cookies, timestamp."""

    def __init__(self, path: Path = SESSION_PATH):
        self.path = Path(path)

    def _read(self) -> Optional[Dict[str, Any]]:
        if not self.path.exists():
            return None
        try:
            with self.path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def load(self) -> Optional[Dict[str, Any]]:
        """Retorna sessão se o SSID ainda for válido (dentro do TTL)."""
        data = self._read()
        if not data:
            return None
        if time.time() - data.get("_ts", 0) > _SSID_TTL:
            return None
        return data

    def load_cookies(self) -> Optional[List[Dict[str, Any]]]:
        """Retorna cookies salvos se ainda válidos, mesmo que o SSID tenha expirado."""
        data = self._read()
        if not data:
            return None
        if time.time() - data.get("_ts_cookies", data.get("_ts", 0)) > _COOKIES_TTL:
            return None
        cookies = data.get("cookies")
        return cookies if isinstance(cookies, list) and cookies else None

    def save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing = self._read() or {}
        merged = dict(existing)
        merged.update(data)
        merged["_ts"] = time.time()
        # renova timestamp de cookies somente quando eles vêm junto
        if "cookies" in data and data["cookies"]:
            merged["_ts_cookies"] = time.time()
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
