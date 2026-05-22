"""🔬 BACKTEST RUNNER — simula o ScalperStrategy contra histórico recente.

Roda em background a cada N minutos: para cada ativo ativo, busca os últimos
~1000 candles do menor TF do scalper, varre janela deslizante simulando
analyze() e checa a vela seguinte para WIN/LOSS hipotéticos.

Persiste ranking em data/backtest_ranking.json:
  {"EURUSD-OTC|M1": {"wr": 62.3, "n": 145, "ts": "2026-04-27T14:00:00"}}

Uso:
  await backtest_loop(bot)  # asyncio task
"""
from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from ..utils.logger import get_logger

logger = get_logger("backtest")

_PATH = Path(__file__).resolve().parents[1] / "data" / "backtest_ranking.json"
_LOCK = threading.Lock()
_DEFAULT_INTERVAL_MIN = 60


def _save(data: Dict[str, Any]) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _LOCK, _PATH.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.warning(f"backtest save falhou: {e}")


def load_ranking() -> Dict[str, Any]:
    if not _PATH.exists():
        return {}
    try:
        with _PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _simulate(candles: List[Any], strategy, timeframe: str, min_window: int = 60) -> Dict[str, int]:
    """Varre candles simulando analyze() em cada ponto. Retorna wins/losses/n."""
    wins = losses = 0
    if len(candles) < min_window + 2:
        return {"wins": 0, "losses": 0, "n": 0}
    # Walk-forward: a cada candle a partir de min_window, simula uma chamada
    for i in range(min_window, len(candles) - 1):
        window = candles[: i + 1]  # inclui vela "atual" como última
        # Simula o "closed_candles": exclui a última (em formação no real)
        closed = window[:-1]
        if len(closed) < min_window:
            continue
        try:
            sig = strategy.analyze(closed, timeframe)
        except Exception:
            sig = None
        if not sig:
            continue
        # Vela "seguinte" no real = candles[i+1]
        next_c = candles[i + 1]
        # WIN binário: cor da próxima vela bate com direção
        next_up = next_c.close > next_c.open
        if (sig.direction == "BUY" and next_up) or (sig.direction == "SELL" and not next_up):
            wins += 1
        else:
            losses += 1
    return {"wins": wins, "losses": losses, "n": wins + losses}


async def _run_once(bot) -> None:
    """Roda backtest em todos os ativos×TFs ativos do scalper."""
    from .scalper import ScalperStrategy
    cfg = bot.cfg_manager.config
    if not getattr(cfg, "scalper_mode", False):
        return
    if not getattr(cfg, "backtest_enabled", True):
        return
    tfs = list(getattr(cfg, "scalper_timeframes", ["M1"]))
    assets = (
        list(getattr(bot.state, "active_assets", []) or [])
        or list(getattr(cfg, "assets", []) or [])
    )
    if not assets or not tfs:
        return
    strategy = ScalperStrategy(weight=1.0)
    tf_map = {"S5": 5, "S10": 10, "S30": 30, "M1": 60, "M5": 300, "M15": 900}
    candle_count = int(getattr(cfg, "backtest_candles", 1000))
    out: Dict[str, Any] = {}
    ts = datetime.now().isoformat(timespec="seconds")
    # Limita pra não sobrecarregar broker
    max_pairs = int(getattr(cfg, "backtest_max_pairs", 30))
    for asset in assets[:max_pairs]:
        for tf in tfs:
            tf_s = tf_map.get(tf, 60)
            try:
                candles = await asyncio.wait_for(
                    bot.broker.get_candles(asset, tf_s, count=candle_count),
                    timeout=20.0,
                )
            except Exception as e:
                logger.debug(f"backtest get_candles {asset} {tf}: {e}")
                continue
            if not candles or len(candles) < 100:
                continue
            # Run em executor pra não travar event loop
            try:
                stats = await asyncio.get_event_loop().run_in_executor(
                    None, _simulate, candles, strategy, tf, 60
                )
            except Exception as e:
                logger.debug(f"backtest simulate falhou {asset} {tf}: {e}")
                continue
            if stats["n"] < 5:
                continue
            wr = round(stats["wins"] / stats["n"] * 100, 1)
            out[f"{asset}|{tf}"] = {"wr": wr, "n": stats["n"], "ts": ts}
        await asyncio.sleep(0.1)  # respiro
    if out:
        _save(out)
        logger.info(f"📊 backtest concluído: {len(out)} combos rankeados")


async def backtest_loop(bot) -> None:
    """Loop infinito: roda a cada N minutos."""
    cfg = bot.cfg_manager.config
    interval_min = int(getattr(cfg, "backtest_interval_minutes", _DEFAULT_INTERVAL_MIN))
    # Espera 5min antes do primeiro run pra não conflitar com startup
    await asyncio.sleep(300)
    while True:
        try:
            await _run_once(bot)
        except Exception as e:
            logger.warning(f"backtest_loop erro: {e}")
        await asyncio.sleep(interval_min * 60)


def top_ranked(limit: int = 10, min_n: int = 30) -> List[Dict[str, Any]]:
    """Retorna top N combos com WR >=50% e amostra mínima."""
    data = load_ranking()
    rows = []
    for k, v in data.items():
        if v.get("n", 0) < min_n:
            continue
        rows.append({"combo": k, **v})
    rows.sort(key=lambda r: (-r["wr"], -r["n"]))
    return rows[:limit]
