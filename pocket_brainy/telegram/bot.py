"""
Camada de interface Telegram.

Expõe TelegramInterface — anexa ao PocketBrainyBot, inicia polling,
responde a callbacks e envia notificações ao vivo.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from telegram import Update
from telegram.error import Conflict, NetworkError, TimedOut
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

BRT = timezone(timedelta(hours=-3))


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
        self.app.add_handler(CommandHandler("ssid", self.handlers.cmd_ssid))
        self.app.add_handler(CommandHandler("saldo", self.handlers.cmd_saldo))
        self.app.add_handler(CommandHandler("topares", self.handlers.cmd_topares))
        self.app.add_handler(CommandHandler("scalpstats", self.handlers.cmd_scalpstats))
        self.app.add_handler(CommandHandler("debug", self.handlers.cmd_debug))
        self.app.add_handler(CommandHandler("alerts", self.handlers.cmd_alerts))
        self.app.add_handler(CommandHandler("why", self.handlers.cmd_why))
        self.app.add_handler(CommandHandler("topcores", self.handlers.cmd_topcores))
        self.app.add_handler(CommandHandler("topativos", self.handlers.cmd_topativos))
        self.app.add_handler(CommandHandler("heatmap", self.handlers.cmd_heatmap))
        self.app.add_handler(CommandHandler("timestats", self.handlers.cmd_timestats))
        self.app.add_handler(CommandHandler("cores", self.handlers.cmd_cores))
        self.app.add_handler(CommandHandler("backtest", self.handlers.cmd_backtest))
        self.app.add_handler(CommandHandler("now", self.handlers.cmd_now))
        self.app.add_handler(CommandHandler("heatmap_visual", self.handlers.cmd_heatmap_visual))
        self.app.add_handler(CommandHandler("regime", self.handlers.cmd_regime))
        self.app.add_handler(CommandHandler("help", self.handlers.cmd_help))
        self.app.add_handler(CallbackQueryHandler(self.handlers.on_callback))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.on_text))
        self.app.add_error_handler(self._on_error)

        await self.app.initialize()
        await self.app.start()
        # 🔒 Claim do lock de polling: força esta instância a virar a "dona" do bot.
        # Se houver outra instância rodando (local em paralelo, deploy antigo), ela
        # vai começar a receber Conflict e o PTB dela vai parar sozinho.
        await self._claim_polling_lock()
        await self.app.updater.start_polling(drop_pending_updates=True)
        self._running = True
        logger.info("Telegram bot em polling.")
        await self._greet()

    async def _claim_polling_lock(self) -> None:
        """Toma o lock de getUpdates de qualquer outra instância rodando.

        Apaga webhook + descarta updates pendentes + faz alguns getUpdates com
        timeout curto. Cada chamada bem-sucedida invalida o getUpdates pendente
        de OUTRAS instâncias (Telegram só aceita um cliente por vez).
        """
        from telegram.error import Conflict as _Conflict
        try:
            await self.app.bot.delete_webhook(drop_pending_updates=True)
        except Exception as e:
            logger.warning(f"delete_webhook falhou (ok ignorar): {e}")

        for attempt in range(1, 6):
            try:
                await self.app.bot.get_updates(timeout=1, offset=-1)
                logger.info(f"✅ Polling lock adquirido (tentativa {attempt}).")
                return
            except _Conflict:
                logger.warning(
                    f"⏳ Outra instância segura o polling — tentando expulsar ({attempt}/5)..."
                )
                await asyncio.sleep(2 * attempt)
            except Exception as e:
                logger.warning(f"get_updates erro (tentativa {attempt}): {e}")
                await asyncio.sleep(1)
        logger.warning(
            "⚠️ Não consegui tomar o lock — outra instância está muito agressiva. "
            "Vou seguir mesmo assim; o PTB continua tentando em background."
        )

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

    async def _on_error(self, update, context) -> None:
        """Handler global de erros do PTB. Silencia ruído conhecido."""
        err = context.error
        # Conflict = outra instância do bot está com polling ativo (Railway, outro terminal).
        if isinstance(err, Conflict):
            if not getattr(self, "_warned_conflict", False):
                logger.warning(
                    "⚠️ Telegram CONFLICT: outra instância deste bot está rodando "
                    "(provavelmente no Railway ou em outro terminal). "
                    "Pare a outra instância OU mude o token. Silenciando próximos avisos."
                )
                self._warned_conflict = True
            return
        # Erros de rede transitórios — log curto, sem stack trace.
        if isinstance(err, (NetworkError, TimedOut)):
            logger.warning(f"Telegram rede: {err}")
            return
        logger.exception(f"Telegram erro não tratado: {err}")

    async def _greet(self) -> None:
        cfg = self.bot.cfg_manager.config
        conta = "🟢 Demo" if cfg.po_demo else "🔴 Real"
        ia_str = "✅ Ativa" if cfg.ai_enabled else "❌ Inativa"
        tfs = ", ".join(cfg.timeframes)
        sw = f"{cfg.stop_win}%" if cfg.stop_win_is_percent else f"$ {cfg.stop_win:.2f}"
        sl = f"{cfg.stop_loss}%" if cfg.stop_loss_is_percent else f"$ {cfg.stop_loss:.2f}"
        hora_brt = datetime.now(BRT).strftime("%H:%M:%S BRT")
        await self.send(
            f"🧠 <b>Pocket Brainy conectado!</b>\n\n"
            f"📅 Hora: <code>{hora_brt}</code>\n"
            f"💰 Conta: {conta}\n"
            f"🧠 IA DeepSeek: {ia_str}\n"
            f"⏱️ Timeframes: <b>{tfs}</b>\n"
            f"🎯 Stop Win: <b>{sw}</b> | Stop Loss: <b>{sl}</b>\n\n"
            "Envie <b>/menu</b> para abrir o painel de controle.\n"
            "<i>Dica: configure credenciais antes de iniciar.</i>"
        )

    # -------- envio --------
    def _observer_chat_ids(self) -> list[int]:
        """Lista de chat_ids observadores (somente leitura) — recebem notificações
        mas não podem controlar o bot. Ignora valores inválidos e o próprio chat principal."""
        cfg = self.bot.cfg_manager.config
        main_id = str(cfg.telegram_chat_id or "")
        out: list[int] = []
        for raw in getattr(cfg, "telegram_observer_chat_ids", []) or []:
            s = str(raw).strip()
            if not s or s == main_id:
                continue
            try:
                out.append(int(s))
            except (TypeError, ValueError):
                continue
        return out

    async def _broadcast_observers(self, text: str, **kwargs) -> None:
        """Envia o mesmo texto para todos os observadores. Falhas são silenciadas."""
        if not self.app:
            return
        for cid in self._observer_chat_ids():
            try:
                await self.app.bot.send_message(
                    chat_id=cid, text=text, parse_mode="HTML", **kwargs
                )
            except Exception as e:
                logger.warning(f"Falha ao enviar para observador {cid}: {e}")

    async def send(self, text: str, **kwargs) -> Optional[int]:
        """Envia mensagem e retorna message_id (ou None em caso de falha)."""
        if not self.app:
            return None
        chat_id = self.bot.cfg_manager.config.telegram_chat_id
        if not chat_id:
            return None
        try:
            msg = await self.app.bot.send_message(
                chat_id=int(chat_id), text=text, parse_mode="HTML", **kwargs
            )
            # Espelha para observadores (não bloqueia retorno do message_id principal)
            await self._broadcast_observers(text, **kwargs)
            return msg.message_id
        except Exception as e:
            logger.warning(f"Falha ao enviar mensagem Telegram: {e}")
            return None

    async def edit_message(self, message_id: int, text: str, **kwargs) -> bool:
        """Edita mensagem existente. Retorna True se bem-sucedido."""
        if not self.app:
            return False
        chat_id = self.bot.cfg_manager.config.telegram_chat_id
        if not chat_id:
            return False
        try:
            await self.app.bot.edit_message_text(
                chat_id=int(chat_id),
                message_id=message_id,
                text=text,
                parse_mode="HTML",
                **kwargs,
            )
            return True
        except Exception as e:
            logger.warning(f"Falha ao editar mensagem {message_id}: {e}")
            return False

    async def pin_message(self, message_id: int) -> bool:
        """Fixa uma mensagem no topo do chat. Retorna True se bem-sucedido."""
        if not self.app:
            return False
        chat_id = self.bot.cfg_manager.config.telegram_chat_id
        if not chat_id:
            return False
        try:
            await self.app.bot.pin_chat_message(
                chat_id=int(chat_id),
                message_id=message_id,
                disable_notification=True,
            )
            return True
        except Exception as e:
            logger.warning(f"Falha ao fixar mensagem {message_id}: {e}")
            return False

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
            f"🎯 Score mínimo: {c.min_score:.1f}\n"
            f"🔁 Reentrada inteligente: {'✅ ON' if c.smart_reentry else '❌ OFF'}\n"
            f"⏱️ Timeframes: {', '.join(c.timeframes)}\n"
            f"🕹️ Ativos: {c.asset_mode} ({', '.join(c.manual_assets) if c.asset_mode == 'manual' else 'top payouts'})\n"
            f"🎲 Martingale: {'ON' if mg.enabled else 'OFF'} (máx {mg.max_level}, x{mg.multiplier}, "
            f"{'reset' if mg.reset_after_win else 'sem reset'})\n"
            f"🔢 Trades abertos máx.: {c.max_open_trades}\n"
            f"🧠 IA: {'ON' if c.ai_enabled else 'OFF'}\n"
            f"🚦 Máx. trades/dia: {c.max_trades_per_day} | Streak loss máx: {c.max_loss_streak}\n"
            f"⏳ Delay entre ops: {c.delay_between_trades}s"
        )

    def render_status(self) -> str:
        s = self.bot.state
        c = self.bot.cfg_manager.config
        # Idade do último refresh do saldo (ajuda a perceber se está "travado")
        import time as _t
        age = _t.time() - getattr(self.bot, "_last_balance_refresh", 0.0)
        age_txt = f" <i>({int(age)}s)</i>" if age < 600 and age > 0 else ""
        return (
            "📊 <b>Status</b>\n"
            f"Bot: {'🟢 RODANDO' if s.running else '🔴 PARADO'}\n"
            f"Conectado: {'✅' if s.connected else '❌'}\n"
            f"Saldo inicial: $ {s.start_balance:.2f}\n"
            f"Saldo atual: $ {s.current_balance:.2f}{age_txt}\n"
            f"PnL do dia: $ {s.daily_pnl:+.2f}\n"
            f"Trades hoje: {s.trades_today} (W:{s.wins} / L:{s.losses} / D:{s.draws})\n"
            f"Winrate: {s.winrate:.1f}%\n"
            f"Streak loss: {s.current_loss_streak} | Martingale nv: {s.martingale_level}"
        )

    async def render_status_live(self) -> str:
        """Versão async: força refresh do saldo na corretora antes de renderizar."""
        try:
            await self.bot.refresh_balance()
        except Exception:
            pass
        return self.render_status()

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
