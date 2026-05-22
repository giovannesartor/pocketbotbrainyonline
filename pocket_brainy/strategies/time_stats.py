"""⏰ TIME STATS — Estatísticas WR por hora, por (asset×hora×TF) e por dia.

Persiste em data/time_stats.json.

API principal:
  • register(asset, tf, hour, result, date_str) → grava win/loss
  • hour_wr(hour, min_n=10) → (wr, n) | None
  • combo_wr(asset, hour, tf, min_n=5) → (wr, n) | None
  • hour_score_adjust(hour, base) → float (delta a aplicar no min_score)
  • is_hour_blocked(hour) → bool (WR muito ruim ou histórico ruim acumulado)
  • is_recent_bad(hour, days=2) → bool (mesma hora perdeu nos últimos N dias)
"""
from __future__ import annotations

import json
import threading
from collections import deque
from pathlib import Path
from typing import Dict, Optional, Tuple

_PATH = Path(__file__).resolve().parents[1] / "data" / "time_stats.json"
_LOCK = threading.Lock()
_ROLLING = 50  # últimas N para WR rolling


class _TimeStats:
    """Estrutura interna:
    {
      "hour": {"0": [...50 results...], ..., "23": [...]},
      "combo": {"EURUSD-OTC|10|M1": [...]},
      "daily": {"2026-04-26|10": {"w": 3, "l": 1}, ...}  # PnL por dia/hora
    }
    """
    def __init__(self) -> None:
        self._data: Dict = {"hour": {}, "combo": {}, "daily": {}}
        self._load()

    def _load(self) -> None:
        if not _PATH.exists():
            return
        try:
            with _PATH.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                self._data["hour"] = raw.get("hour", {})
                self._data["combo"] = raw.get("combo", {})
                self._data["daily"] = raw.get("daily", {})
        except Exception:
            pass

    def _save(self) -> None:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            with _PATH.open("w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except Exception:
            pass

    # ---------------- registro ----------------
    def register(self, asset: str, tf: str, hour: int, result: str, date_str: str) -> None:
        if result not in ("WIN", "LOSS"):
            return
        with _LOCK:
            # Por hora (rolling)
            hk = str(hour)
            arr = self._data["hour"].setdefault(hk, [])
            arr.append(result)
            if len(arr) > _ROLLING:
                del arr[: len(arr) - _ROLLING]
            # Combo asset×hour×tf
            ck = f"{asset}|{hour}|{tf}"
            carr = self._data["combo"].setdefault(ck, [])
            carr.append(result)
            if len(carr) > _ROLLING:
                del carr[: len(carr) - _ROLLING]
            # Daily (date|hour)
            dk = f"{date_str}|{hour}"
            d = self._data["daily"].setdefault(dk, {"w": 0, "l": 0})
            if result == "WIN":
                d["w"] += 1
            else:
                d["l"] += 1
            self._save()

    # ---------------- consultas ----------------
    def hour_wr(self, hour: int, min_n: int = 10) -> Optional[Tuple[float, int]]:
        with _LOCK:
            arr = self._data["hour"].get(str(hour), [])
            n = len(arr)
            if n < min_n:
                return None
            wins = sum(1 for r in arr if r == "WIN")
            return (wins / n, n)

    def combo_wr(self, asset: str, hour: int, tf: str, min_n: int = 5) -> Optional[Tuple[float, int]]:
        with _LOCK:
            arr = self._data["combo"].get(f"{asset}|{hour}|{tf}", [])
            n = len(arr)
            if n < min_n:
                return None
            wins = sum(1 for r in arr if r == "WIN")
            return (wins / n, n)

    def hour_score_adjust(self, hour: int) -> float:
        """Retorna delta a somar no min_score baseado em WR rolling da hora.
          WR ≥ 60% → -0.5  (afrouxa)
          WR 45-60% → 0     (neutro)
          WR 35-45% → +1.0  (aperta)
          WR < 35%  → +99   (efetivamente bloqueia)
        Só aplica se houver >= 10 trades na hora.
        """
        res = self.hour_wr(hour, min_n=10)
        if res is None:
            return 0.0
        wr, _ = res
        if wr >= 0.60:
            return -0.5
        if wr >= 0.45:
            return 0.0
        if wr >= 0.35:
            return 1.0
        return 99.0  # bloqueia

    def combo_score_adjust(self, asset: str, hour: int, tf: str) -> float:
        """Mesmo princípio mas granular por combo. Mais agressivo."""
        res = self.combo_wr(asset, hour, tf, min_n=5)
        if res is None:
            return 0.0
        wr, _ = res
        if wr >= 0.65:
            return -0.5
        if wr >= 0.45:
            return 0.0
        if wr >= 0.30:
            return 1.0
        return 99.0  # bloqueia esse combo

    def is_hour_blocked(self, hour: int) -> bool:
        return self.hour_score_adjust(hour) >= 99.0

    def is_combo_blocked(self, asset: str, hour: int, tf: str) -> bool:
        return self.combo_score_adjust(asset, hour, tf) >= 99.0

    def is_recent_bad(self, hour: int, dates: list) -> bool:
        """True se essa hora teve PnL negativo (mais L que W) em TODAS as `dates`."""
        if not dates:
            return False
        with _LOCK:
            for d in dates:
                rec = self._data["daily"].get(f"{d}|{hour}")
                if not rec:
                    return False  # sem dado → não pune
                if rec.get("l", 0) <= rec.get("w", 0):
                    return False
        return True

    def hour_summary(self) -> Dict[int, Dict[str, float]]:
        out: Dict[int, Dict[str, float]] = {}
        with _LOCK:
            for k, arr in self._data["hour"].items():
                n = len(arr)
                if n == 0:
                    continue
                wins = sum(1 for r in arr if r == "WIN")
                out[int(k)] = {"wr": round(wins / n * 100, 1), "n": n}
        return out

    def top_assets_for_hour(self, hour: int, min_n: int = 3, limit: int = 10) -> list:
        """Retorna top ativos pra hora atual.
        [{"asset": ..., "tf": ..., "wr": ..., "n": ...}, ...] ordenado por WR desc.
        Agrupa por (asset, tf) — combos com pelo menos min_n trades.
        """
        rows = []
        with _LOCK:
            for key, arr in self._data["combo"].items():
                parts = key.split("|")
                if len(parts) != 3:
                    continue
                asset, hr, tf = parts
                try:
                    if int(hr) != hour:
                        continue
                except ValueError:
                    continue
                n = len(arr)
                if n < min_n:
                    continue
                wins = sum(1 for r in arr if r == "WIN")
                rows.append({
                    "asset": asset, "tf": tf,
                    "wr": round(wins / n * 100, 1), "n": n,
                })
        rows.sort(key=lambda r: (-r["wr"], -r["n"]))
        return rows[:limit]

    def heatmap_matrix(self, days: int = 7) -> Dict[str, Dict[int, Dict[str, int]]]:
        """Retorna matrix daily[date][hour] = {w, l} pros últimos N dias."""
        from datetime import datetime, timezone, timedelta
        _BRT = timezone(timedelta(hours=-3))
        today = datetime.now(_BRT)
        valid_dates = [
            (today - timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(days)
        ]
        out: Dict[str, Dict[int, Dict[str, int]]] = {d: {} for d in valid_dates}
        with _LOCK:
            for key, rec in self._data["daily"].items():
                if "|" not in key:
                    continue
                date_str, hr = key.rsplit("|", 1)
                if date_str not in out:
                    continue
                try:
                    h = int(hr)
                except ValueError:
                    continue
                out[date_str][h] = {"w": int(rec.get("w", 0)), "l": int(rec.get("l", 0))}
        return out


TIME_STATS = _TimeStats()
