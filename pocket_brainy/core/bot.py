"""
Orquestrador principal — Pocket Brainy.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from ..ai import AIDecision, DeepSeekAI
from ..broker import BrokerError, PocketOptionBroker
from ..risk import MartingaleController, RiskManager
from ..strategies import ALL_STRATEGIES, StrategyManager
from ..telegram import messages as msgs
from ..utils.logger import get_logger
from ..utils.otc_schedule import filter_open_otc_assets, otc_session_label
from .config import ConfigManager
from .state import BotState, TradeResult

logger = get_logger("core.bot")

TIMEFRAME_SECONDS = {"M1": 60, "M5": 300, "M15": 900}
_RECONNECT_DELAYS = [5, 15, 45]   # backoff em segundos (3 tentativas)


class PocketBrainyBot:
    def __init__(self):
        self.cfg_manager = ConfigManager()
        self.state = BotState()
        self.state.load_history()
        self.broker: Optional[PocketOptionBroker] = None
        self.strategies = StrategyManager(ALL_STRATEGIES)
        self.ai = DeepSeekAI()
        self.risk: Optional[RiskManager] = None
        self.martingale: Optional[MartingaleController] = None
        self.telegram = None
        self._trading_task: Optional[asyncio.Task] = None
        self._renewal_task: Optional[asyncio.Task] = None
        self._open_tasks: List[asyncio.Task] = []    # trades rodando em background
        self._current_win_streak = 0
        self._consecutive_errors = 0
        self._last_lateral_notify: float = 0.0
        self._recent_signals: dict = {}  # chave: (ativo,tf,strat,dir) → timestamp

    # ---------------- ciclo de vida ----------------
    async def connect(self) -> None:
        cfg = self.cfg_manager.config
        self.broker = PocketOptionBroker(cfg.po_email, cfg.po_password, demo=cfg.po_demo, ssids=cfg.po_ssids)
        await self.broker.connect()
        self.state.connected = True
        self.state.start_balance = await self.broker.get_balance()
        self.state.current_balance = self.state.start_balance
        self.risk = RiskManager(cfg, self.state)
        self.martingale = MartingaleController(cfg.martingale, cfg.entry_amount)
        logger.info(f"Conectado. Saldo: $ {self.state.start_balance:.2f}")

    async def reconnect(self) -> str:
        """Reconecta com backoff exponencial (até 3 tentativas)."""
        for attempt, delay in enumerate(_RECONNECT_DELAYS, start=1):
            try:
                if self.broker:
                    await self.broker.disconnect()
                await self.connect()
                self._consecutive_errors = 0
                await self._notify(msgs.bot_reconectado(self.state.start_balance))
                return "🔄 Reconectado."
            except Exception as e:
                logger.warning(f"Tentativa {attempt}/3 falhou: {e}")
                if attempt < len(_RECONNECT_DELAYS):
                    await self._notify(
                        f"⚠️ Reconexão {attempt}/3 falhou — tentando em {delay}s...\n<code>{e}</code>"
                    )
                    await asyncio.sleep(delay)
        await self._notify(msgs.bot_erro_conexao("3 tentativas de reconexão falharam. Bot pausado."))
        self.state.running = False
        self.state.connected = False
        return "❌ Reconexão falhou após 3 tentativas."

    async def start_trading(self) -> str:
        if self.state.running:
            return "⚠️ Bot já está rodando."
        try:
            if not self.broker or not self.state.connected:
                await self.connect()
        except BrokerError as e:
            await self._notify(msgs.bot_erro_conexao(str(e)))
            return f"⚠️ Não foi possível conectar: {e}"

        self.state.running = True
        self.state.reset_daily()
        self._current_win_streak = 0
        self._recent_signals = {}  # limpa deduplicação ao iniciar
        self._trading_task = asyncio.create_task(self._trading_loop(), name="trading-loop")
        self._renewal_task = asyncio.create_task(self._ssid_renewal_loop(), name="ssid-renewal")

        cfg = self.cfg_manager.config

        # F: coletar info enriquecida para a notificação
        try:
            assets_listing = await self.broker.get_assets()
            otc_assets = filter_open_otc_assets([a["asset"] for a in assets_listing])
            otc_count = len(otc_assets)
            avg_payout = (
                sum(a["payout"] for a in assets_listing if a["asset"] in otc_assets) / otc_count
                if otc_count else 0.0
            )
        except Exception:
            otc_count = 0
            avg_payout = 0.0

        await self._notify(msgs.bot_iniciado(
            simulacao=cfg.simulation_mode,
            timeframes=cfg.timeframes,
            ia=cfg.ai_enabled,
            saldo=self.state.start_balance,
            ativos_modo=cfg.asset_mode,
            conta_demo=cfg.po_demo,
            otc_count=otc_count,
            avg_payout=avg_payout,
            sessao=otc_session_label(),
        ))
        await self._notify(self.strategies.ranking.pretty())
        return "▶️ Bot iniciado."

    async def stop_trading(self) -> str:
        if not self.state.running:
            return "ℹ️ Bot já estava parado."
        self.state.running = False
        if self._trading_task:
            self._trading_task.cancel()
            try:
                await self._trading_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._renewal_task:
            self._renewal_task.cancel()
            try:
                await self._renewal_task
            except (asyncio.CancelledError, Exception):
                pass
        # cancela trades em aberto
        for task in self._open_tasks:
            task.cancel()
        if self._open_tasks:
            await asyncio.gather(*self._open_tasks, return_exceptions=True)
        self._open_tasks.clear()
        self.state.open_trades = 0
        s = self.state
        await self._notify(msgs.bot_parado(s.daily_pnl, s.wins, s.losses, s.winrate, s.trades_today))
        await self._send_daily_summary()
        return "⏹️ Bot parado."

    async def _send_daily_summary(self) -> None:
        s = self.state
        ranking_rows = self.strategies.ranking.ranking()
        melhor = ranking_rows[0]["strategy"] if ranking_rows else None
        await self._notify(msgs.resumo_diario(
            data=datetime.now().strftime("%d/%m/%Y"),
            wins=s.wins, losses=s.losses, draws=s.draws,
            winrate=s.winrate, pnl=s.daily_pnl, trades=s.trades_today,
            melhor_estrategia=melhor,
        ))

    # ---------------- renovação automática de SSID ----------------
    async def _ssid_renewal_loop(self) -> None:
        """Renova o SSID a cada 3.5h via cookies (sem abrir janela). Se cookies expirarem, avisa no Telegram."""
        RENEWAL_INTERVAL = 3.5 * 60 * 60   # 3.5 horas em segundos
        await asyncio.sleep(RENEWAL_INTERVAL)
        while self.state.running:
            try:
                from ..broker.pocket_option import _capture_ssid_with_cookies
                cookies = self.broker.session.load_cookies()
                if not cookies:
                    await self._notify(
                        "⚠️ <b>Sessão Pocket Option expirada!</b>\n\n"
                        "Execute no terminal para renovar:\n"
                        "<code>python capturar_ssid.py</code>\n\n"
                        "<i>Faça login no navegador que abrir. O bot continua rodando com o SSID atual até lá.</i>"
                    )
                    await asyncio.sleep(RENEWAL_INTERVAL)
                    continue

                cfg = self.cfg_manager.config
                auth = await _capture_ssid_with_cookies(cookies, cfg.po_demo)
                new_ssid = auth["ssid"]
                self.broker.session.save({"ssid": new_ssid})

                # reconecta com o novo SSID sem derrubar o bot
                if self.broker and self.broker._connected:
                    self.broker._connected = False
                    ok = await self.broker._try_ssid(new_ssid)
                    if ok:
                        self.broker._connected = True
                        logger.info("SSID renovado automaticamente. ✅")
                    else:
                        logger.warning("SSID renovado mas _try_ssid retornou False — mantendo conexão atual.")
                        self.broker._connected = True

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Falha na renovação automática de SSID: {e}")

            await asyncio.sleep(RENEWAL_INTERVAL)

    # ---------------- loop ----------------
    async def _trading_loop(self) -> None:
        logger.info("Iniciando loop de trading.")
        try:
            while self.state.running:
                try:
                    await self._tick()
                    self._consecutive_errors = 0
                except asyncio.CancelledError:
                    raise
                except BrokerError as e:
                    self._consecutive_errors += 1
                    logger.error(f"BrokerError no tick ({self._consecutive_errors}x): {e}")
                    if self._consecutive_errors >= 3:
                        await self._notify(
                            f"🔌 <b>Conexão instável</b> — tentando reconectar...\n<code>{e}</code>"
                        )
                        result = await self.reconnect()
                        if not self.state.connected:
                            logger.error("Reconexão falhou. Encerrando loop.")
                            return
                        self._consecutive_errors = 0
                    else:
                        await asyncio.sleep(5)
                except Exception as e:
                    logger.exception(f"Erro no tick: {e}")
                    await asyncio.sleep(2)
                await asyncio.sleep(1.0)
        finally:
            logger.info("Loop encerrado.")

    async def _tick(self) -> None:
        cfg = self.cfg_manager.config

        dec = self.risk.can_trade()
        if not dec.allow:
            if "Stop Win" in dec.reason:
                await self._notify(msgs.stop_win_atingido(self.state.daily_pnl, self.state.trades_today))
                self.state.running = False
                return
            if "Stop Loss" in dec.reason:
                await self._notify(msgs.stop_loss_atingido(self.state.daily_pnl, self.state.trades_today))
                self.state.running = False
                return
            if "Streak" in dec.reason:
                await self._notify(msgs.streak_loss_atingido(self.state.current_loss_streak))
                self.state.running = False
                return
            if "Máx" in dec.reason:
                await self._notify(msgs.max_trades_atingido(self.state.trades_today))
                self.state.running = False
                return
            # apenas delay — silencioso
            await asyncio.sleep(2)
            return

        # limpa tarefas concluídas e verifica limite de trades abertos
        self._open_tasks = [t for t in self._open_tasks if not t.done()]
        if self._open_tasks and len(self._open_tasks) >= cfg.max_open_trades:
            # limite atingido — aguarda silenciosamente até liberar uma vaga
            await asyncio.sleep(3)
            return

        assets = await self._select_assets()
        if not assets:
            await asyncio.sleep(3)
            return

        best_analysis = None
        best_asset = None
        best_tf = None
        best_payout = 0.0
        lateral_alert = None

        for tf in cfg.timeframes:
            tf_seconds = TIMEFRAME_SECONDS.get(tf, 60)
            for asset in assets:
                try:
                    candles = await self.broker.get_candles(asset, tf_seconds, 120)
                except Exception as e:
                    logger.warning(f"Falha candles {asset} {tf}: {e}")
                    continue
                if not candles:
                    continue
                analysis = self.strategies.analyze_asset(
                    asset, candles, tf,
                    min_score=5.0,
                    min_confidence=cfg.min_assertiveness,
                )
                if analysis.market_state and analysis.market_state.is_lateral:
                    lateral_alert = analysis.market_state.description
                if not analysis.best_signal:
                    continue
                payout = await self.broker.get_payout(asset)
                if payout < cfg.min_payout:
                    continue
                if best_analysis is None or analysis.best_signal.final_score > best_analysis.best_signal.final_score:
                    best_analysis = analysis
                    best_asset = asset
                    best_tf = tf
                    best_payout = payout

        if not best_analysis or not best_analysis.best_signal:
            if lateral_alert and (time.time() - self._last_lateral_notify > 300):
                await self._notify(msgs.mercado_lateral_detectado(lateral_alert))
                self._last_lateral_notify = time.time()
            await asyncio.sleep(2)
            return

        sig = best_analysis.best_signal

        # --- deduplicação de sinal ---
        _sig_key = f"{best_asset}:{best_tf}:{sig.strategy}:{sig.direction}"
        _now = time.time()
        _sig_ttl = TIMEFRAME_SECONDS.get(best_tf, 60)
        if _now - self._recent_signals.get(_sig_key, 0) < _sig_ttl:
            await asyncio.sleep(5)
            return  # mesmo sinal ainda ativo — aguarda o candle fechar
        self._recent_signals[_sig_key] = _now
        # prune antigos (> 1h)
        self._recent_signals = {k: v for k, v in self._recent_signals.items() if _now - v < 3600}

        await self._notify(msgs.sinal_detectado(
            asset=best_asset, tf=best_tf, strategy=sig.strategy,
            direction=sig.direction, score=sig.final_score,
            confidence=sig.confidence, payout=best_payout, notes=sig.notes,
        ))

        # IA
        if cfg.ai_enabled:
            _ms = best_analysis.market_state
            _brt_now = datetime.now(timezone(timedelta(hours=-3)))
            _recent_results = [
                f"{t.result}({t.profit:+.2f})"
                for t in reversed(self.state.history)
                if t.asset == best_asset
            ][:3]
            payload = {
                "asset": best_asset, "timeframe": best_tf,
                "direction": sig.direction, "strategy": sig.strategy,
                "score": sig.final_score, "confidence": sig.confidence,
                "market": _ms.description if _ms else "",
                "confluence": sig.confluence, "payout": best_payout,
                # contexto enriquecido (feature J)
                "hour_brt": _brt_now.hour,
                "session": otc_session_label(),
                "bb_width": round(_ms.bb_width, 4) if _ms else 0,
                "adx": round(_ms.adx_value, 1) if _ms else 0,
                "recent_results": _recent_results,
            }
            decision: AIDecision = await self.ai.validate_signal(payload)
            await self._notify(msgs.ia_validando(
                operar=decision.operate, confianca=decision.confidence,
                rationale=decision.rationale, cached=decision.cached,
            ))
            if not decision.operate:
                return
        else:
            decision = AIDecision(True, sig.confidence, "IA desabilitada")

        amount = self.martingale.next_amount()
        task = asyncio.create_task(
            self._execute(best_asset, best_tf, sig, amount, decision.confidence, best_payout),
            name=f"trade-{best_asset}-{best_tf}",
        )
        self._open_tasks.append(task)

    # ---------------- execução ----------------
    async def _execute(self, asset: str, tf: str, sig, amount: float, ai_conf: float, payout: float) -> None:
        cfg = self.cfg_manager.config
        expiration = TIMEFRAME_SECONDS.get(tf, 60)
        self.risk.mark_trade_time()
        self.state.open_trades += 1
        try:
            await self._execute_inner(asset, tf, sig, amount, ai_conf, payout, cfg, expiration)
        finally:
            self.state.open_trades = max(0, self.state.open_trades - 1)

    async def _execute_inner(
        self, asset: str, tf: str, sig, amount: float,
        ai_conf: float, payout: float, cfg, expiration: int
    ) -> None:

        if cfg.simulation_mode:
            import random
            prob = min(0.95, max(0.45, ai_conf / 100.0))
            outcome = "WIN" if random.random() < prob else "LOSS"
            profit = round(amount * (payout / 100.0), 2) if outcome == "WIN" else -amount
            result_lbl = outcome  # registramos como WIN/LOSS real p/ ranking
            simulated = True
        else:
            simulated = False
            try:
                order = await self.broker.place_trade(asset, sig.direction, amount, expiration)
                await self._notify(msgs.ordem_enviada(
                    asset=asset, direction=sig.direction, amount=amount, tf=tf,
                    mg_level=self.martingale.state.level,
                ))
                outcome = await self.broker.check_result(order.order_id, expiration)
                profit = round(amount * (payout / 100.0), 2) if outcome == "WIN" else (
                    -amount if outcome == "LOSS" else 0.0
                )
                result_lbl = outcome
            except Exception as e:
                logger.exception(f"Falha ao executar ordem: {e}")
                await self._notify(msgs.bot_erro_conexao(str(e)))
                return

        self.state.martingale_level = self.martingale.state.level
        res_for_rank = "WIN" if profit > 0 else ("LOSS" if profit < 0 else "DRAW")

        trade = TradeResult(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            asset=asset, direction=sig.direction, amount=amount,
            expiration=expiration, strategy=sig.strategy,
            score=sig.final_score, ai_confidence=ai_conf,
            result=res_for_rank, profit=profit, timeframe=tf,
            martingale_level=self.martingale.state.level,
        )
        self.state.register_trade(trade)
        self.state.save_history()
        self.strategies.ranking.register_result(sig.strategy, res_for_rank, profit)
        self.martingale.on_result(res_for_rank)

        # win streak
        if res_for_rank == "WIN":
            self._current_win_streak += 1
        else:
            self._current_win_streak = 0

        placar = msgs.placar_ao_vivo(
            wins=self.state.wins, losses=self.state.losses, draws=self.state.draws,
            winrate=self.state.winrate, pnl=self.state.daily_pnl,
            streak_loss=self.state.current_loss_streak,
            mg_level=self.martingale.state.level,
        )

        if simulated:
            await self._notify(msgs.trade_simulado(
                asset=asset, strategy=sig.strategy, tf=tf,
                direction=sig.direction, profit=profit, placar=placar,
            ))
        elif res_for_rank == "WIN":
            await self._notify(msgs.trade_win(
                asset=asset, strategy=sig.strategy, tf=tf,
                profit=profit, placar=placar, streak_wins=self._current_win_streak,
            ))
        elif res_for_rank == "LOSS":
            await self._notify(msgs.trade_loss(
                asset=asset, strategy=sig.strategy, tf=tf,
                profit=profit, placar=placar,
                streak_losses=self.state.current_loss_streak,
            ))
        else:
            await self._notify(msgs.trade_draw(asset=asset, tf=tf, placar=placar))

        if res_for_rank == "LOSS":
            await self._notify(msgs.reentrada_inteligente())

    # ---------------- ativos ----------------
    async def _select_assets(self) -> List[str]:
        cfg = self.cfg_manager.config
        if cfg.asset_mode == "manual":
            # modo manual: respeita a lista do usuário, mas filtra só OTCs abertos
            return filter_open_otc_assets(list(cfg.manual_assets))
        try:
            listing = await self.broker.get_assets()
        except Exception as e:
            logger.warning(f"Falha lista ativos: {e}. Fallback manual.")
            return filter_open_otc_assets(list(cfg.manual_assets))

        # C: filtra apenas OTCs, ordena por payout, respeita min_payout
        otc_names = filter_open_otc_assets([a["asset"] for a in listing])
        otc_listing = [a for a in listing if a["asset"] in otc_names and a.get("payout", 0) >= cfg.min_payout]
        ranked = sorted(otc_listing, key=lambda a: a.get("payout", 0), reverse=True)
        return [a["asset"] for a in ranked[:6]]

    # ---------------- utilidades ----------------
    async def _notify(self, text: str) -> None:
        logger.info(text.replace("\n", " | "))
        if self.telegram:
            await self.telegram.send(text)
