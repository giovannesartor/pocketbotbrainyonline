"""
Camada de interface Telegram.

Expõe TelegramInterface — anexa ao PocketBrainyBot, inicia polling,
responde a callbacks e envia notificações ao vivo.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from ..utils.logger import get_logger
from .handlers import Handlers

logger = get_logger("telegram.bot")


class TelegramInterface:
    def __init__(self, bot: "PocketBrainyBot"):  # noqa: F821
        self.bot = bot
        self.handlers = Handlers(self)
        self.app: Application | None = None
        self.user_data: Dict[int, Dict[str, Any]] = {}
        self._running = False

    # -------- bootstrap --------
    async def start(self) -> None:
        token = self.bot.cfg_manager.config.telegram_token
        if not token:
            raise RuntimeError(
                "Telegram token ausente. Edite data/config.json (telegram_token / telegram_chat_id)."
            )
        self.app = ApplicationBuilder().token(token).build()
        self.app.add_handler(CommandHandler(["start", "menu"], self.handlers.cmd_start))
        self.app.add_handler(CommandHandler("stats", self.handlers.cmd_stats))
        self.app.add_handler(CallbackQueryHandler(self.handlers.on_callback))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.on_text))

        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        self._running = True
        logger.info("Telegram bot em polling.")
        await self._greet()

    async def stop(self) -> None:
        if not self.app:
            return
        try:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
        except Exception:
            pass
        self._running = False

    async def _greet(self) -> None:
        cfg = self.bot.cfg_manager.config
        modo = "🧪 SIMULAÇÃO" if cfg.simulation_mode else "💰 CONTA REAL"
        await self.send(
            "🧠 <b>Pocket Brainy conectado ao Telegram!</b>\n\n"
            f"{modo}\n"
            "Envie <b>/menu</b> para abrir o painel de controle.\n"
            "<i>Dica: comece em modo simulação até ganhar confiança nas estratégias.</i>"
        )

    # -------- envio --------
    async def send(self, text: str, **kwargs) -> None:
        if not self.app:
            return
        chat_id = self.bot.cfg_manager.config.telegram_chat_id
        if not chat_id:
            return
        try:
            await self.app.bot.send_message(chat_id=int(chat_id), text=text, parse_mode="HTML", **kwargs)
        except Exception as e:
            logger.warning(f"Falha ao enviar mensagem Telegram: {e}")

    # -------- rendering --------
    def render_config(self) -> str:
        c = self.bot.cfg_manager.config
        mg = c.martingale
        sw = f"{c.stop_win}%" if c.stop_win_is_percent else f"$ {c.stop_win:.2f}"
        sl = f"{c.stop_loss}%" if c.stop_loss_is_percent else f"$ {c.stop_loss:.2f}"
        return (
            "⚙️ <b>Configurações</b>\n"
            f"💰 Entrada: $ {c.entry_amount:.2f}\n"
            f"🎯 Stop Win: {sw}\n"
            f"🛑 Stop Loss: {sl}\n"
            f"📈 Payout mín.: {c.min_payout:.0f}%\n"
            f"✅ Assertividade mín.: {c.min_assertiveness:.0f}%\n"
            f"⏱️ Timeframes: {', '.join(c.timeframes)}\n"
            f"🕹️ Ativos: {c.asset_mode} ({', '.join(c.manual_assets) if c.asset_mode == 'manual' else 'top payouts'})\n"
            f"🎲 Martingale: {'ON' if mg.enabled else 'OFF'} (máx {mg.max_level}, x{mg.multiplier}, "
            f"{'reset' if mg.reset_after_win else 'sem reset'})\n"
            f"🔢 Trades abertos máx.: {c.max_open_trades}\n"
            f"🧠 IA: {'ON' if c.ai_enabled else 'OFF'}\n"
            f"🧪 Simulação: {'ON' if c.simulation_mode else 'OFF'}\n"
            f"🚦 Máx. trades/dia: {c.max_trades_per_day} | Streak loss máx: {c.max_loss_streak}\n"
            f"⏳ Delay entre ops: {c.delay_between_trades}s"
        )

    def render_status(self) -> str:
        s = self.bot.state
        c = self.bot.cfg_manager.config
        return (
            "📊 <b>Status</b>\n"
            f"Bot: {'🟢 RODANDO' if s.running else '🔴 PARADO'}\n"
            f"Conectado: {'✅' if s.connected else '❌'}\n"
            f"Saldo inicial: $ {s.start_balance:.2f}\n"
            f"Saldo atual: $ {s.current_balance:.2f}\n"
            f"PnL do dia: $ {s.daily_pnl:+.2f}\n"
            f"Trades hoje: {s.trades_today} (W:{s.wins} / L:{s.losses} / D:{s.draws})\n"
            f"Winrate: {s.winrate:.1f}%\n"
            f"Streak loss: {s.current_loss_streak} | Martingale nv: {s.martingale_level}\n"
            f"Modo: {'🧪 SIM' if c.simulation_mode else '💰 REAL'}"
        )

    def render_stats(self) -> str:
        from . import messages as msgs
        from collections import defaultdict
        history = self.bot.state.history
        if not history:
            return "📉 <b>Stats</b>\n\n<i>Sem trades ainda.</i>"

        def _winrate(items):
            wins = sum(1 for t in items if t.result == "WIN")
            return (wins / len(items) * 100) if items else 0.0

        # por estratégia
        by_strat: dict = defaultdict(list)
        for t in history:
            by_strat[t.strategy].append(t)
        strat_stats = sorted(
            [{"strategy": k, "trades": len(v), "winrate": _winrate(v)} for k, v in by_strat.items()],
            key=lambda x: x["winrate"], reverse=True,
        )

        # por ativo
        by_asset: dict = defaultdict(list)
        for t in history:
            by_asset[t.asset].append(t)
        asset_stats = sorted(
            [{"asset": k, "trades": len(v), "winrate": _winrate(v)} for k, v in by_asset.items()],
            key=lambda x: x["winrate"], reverse=True,
        )

        # por timeframe
        by_tf: dict = defaultdict(list)
        for t in history:
            by_tf[t.timeframe].append(t)
        tf_stats = sorted(
            [{"timeframe": k, "trades": len(v), "winrate": _winrate(v)} for k, v in by_tf.items()],
            key=lambda x: x["winrate"], reverse=True,
        )

        # melhor hora
        by_hour: dict = defaultdict(list)
        for t in history:
            try:
                hour = int(t.timestamp[11:13])
                by_hour[hour].append(t)
            except Exception:
                pass
        best_hour = None
        if by_hour:
            bh = max(by_hour.items(), key=lambda x: _winrate(x[1]) if len(x[1]) >= 3 else 0)
            if len(bh[1]) >= 3:
                best_hour = {"hour": bh[0], "trades": len(bh[1]), "winrate": _winrate(bh[1])}

        return msgs.stats_message(
            by_strategy=strat_stats,
            by_asset=asset_stats,
            by_timeframe=tf_stats,
            best_hour=best_hour,
            total_trades=self.bot.state.trades_today,
            daily_pnl=self.bot.state.daily_pnl,
        )

    def render_results(self) -> str:
        s = self.bot.state
        if not s.history:
            return "📈 Sem resultados ainda."
        last = s.history[-10:]
        lines = ["<b>📈 Últimos 10 resultados</b>"]
        for t in reversed(last):
            icon = {"WIN": "🟢", "LOSS": "🔴", "DRAW": "⚪", "SIM": "🧪"}.get(t.result, "•")
            lines.append(
                f"{icon} {t.asset} {t.direction} {t.timeframe} | "
                f"{t.strategy} | $ {t.profit:+.2f}"
            )
        lines.append(f"\n<b>Total PnL hoje:</b> $ {s.daily_pnl:+.2f} | Winrate: {s.winrate:.1f}%")
        return "\n".join(lines)
