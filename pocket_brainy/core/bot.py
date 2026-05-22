"""
Orquestrador principal — Pocket Brainy.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone, timedelta

BRT = timezone(timedelta(hours=-3))


def _brt_now() -> datetime:
    return datetime.now(BRT)


def _brt_iso() -> str:
    return _brt_now().isoformat(timespec="seconds")
from typing import List, Optional

from ..ai import AIDecision, DeepSeekAI
from ..broker import BrokerError, PocketOptionBroker
from ..broker.pocket_option import _NoSSIDError, _NetworkBlockedError
from ..risk import MartingaleController, RiskManager
from ..strategies import ALL_STRATEGIES, StrategyManager
from ..telegram import messages as msgs
from ..utils.logger import get_logger
from ..utils.otc_schedule import filter_open_otc_assets, otc_session_label
from .config import ConfigManager
from .state import BotState, TradeResult

logger = get_logger("core.bot")

TIMEFRAME_SECONDS = {"S5": 5, "S10": 10, "S30": 30, "M1": 60, "M5": 300, "M15": 900}
_RECONNECT_DELAYS = [5, 15, 45]   # backoff em segundos (3 tentativas)


class PocketBrainyBot:
    def __init__(self):
        self.cfg_manager = ConfigManager()
        self.state = BotState()
        self.state.load_history()
        self.broker: Optional[PocketOptionBroker] = None
        self.strategies = StrategyManager(ALL_STRATEGIES)
        cfg = self.cfg_manager.config
        self.ai = DeepSeekAI(api_key=cfg.deepseek_api_key) if cfg.ai_enabled else None
        self.risk: Optional[RiskManager] = None
        self.martingale: Optional[MartingaleController] = None
        self.telegram = None
        self._trading_task: Optional[asyncio.Task] = None
        self._renewal_task: Optional[asyncio.Task] = None
        self._midnight_task: Optional[asyncio.Task] = None
        self._hot_reload_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._open_tasks: List[asyncio.Task] = []    # trades rodando em background
        self._current_win_streak = 0
        self._consecutive_errors = 0
        self._last_lateral_notify: float = 0.0
        self._last_is_lateral: bool = False
        self._dashboard_message_id: Optional[int] = None
        self._last_dashboard_update: float = 0.0
        self._last_adx: float = 0.0
        self._otc_count: int = 0
        self._tick_count: int = 0
        self._recent_signals: dict = {}  # chave: (ativo,tf,strat,dir) → timestamp
        self._waiting_for_ssid: bool = False  # modo espera: aguarda /ssid antes de parar
        self._last_ai_decisions: dict = {}   # asset → lista rolling das últimas 3 decisões da IA
        self._load_ai_feedback()  # carrega histórico persistido do disco
        # 🎯 Scalper: contador de losses consecutivos para auto-desligar
        self._scalper_loss_streak: int = 0
        # 💱 B5 — losses consecutivos por ativo no dia (reseta no virar do dia)
        self._asset_loss_streak: dict = {}   # asset → int
        self._asset_blacklist_today: set = set()  # ativos banidos hoje
        self._blacklist_date: str = ""       # YYYY-MM-DD da blacklist atual
        # 🔄 A1 — pausa por motivo (stop_loss/stop_win/etc): ts limite de pausa
        self._pause_reason: str = ""
        self._pause_until_date: str = ""     # YYYY-MM-DD em que pausa expira
        # 🔁 Recaptura automática de SSID — controla disparo único
        self._auto_capture_in_progress: bool = False
        self._consecutive_no_ssid: int = 0
        # 🔍 Logs de scan (visibilidade)
        self._last_scan_log_ts: float = 0.0   # último log periódico de scan
        self._scan_log_interval: float = 60.0  # log a cada 60s no terminal
        self._last_telegram_scan_ts: float = 0.0
        self._telegram_scan_interval: float = 300.0  # telegram a cada 5min
        self._debug_verbose_until: float = 0.0  # /debug liga modo verbose por 5min
        # 🔔 Alertas "quase entrou" (toggle via /alerts on|off)
        self._alerts_enabled: bool = False
        self._alerted_near_misses: set = set()  # dedup notificações
        # 🔍 Por-ativo: último motivo de bloqueio (para /why <asset>)
        self._last_block_reason: dict = {}  # asset → {tf, reason, score, ts}
        # 📊 Hourly summary: snapshot inicial da hora
        self._hour_baseline: dict = {"hour": -1, "wins": 0, "losses": 0, "pnl": 0.0, "trades": 0}
        self._hourly_task: Optional[asyncio.Task] = None
        # 📉 Alerta queda de payout
        self._payout_baseline: dict = {}  # asset → último payout conhecido
        self._payout_alert_task: Optional[asyncio.Task] = None
        # 📌 Pin do placar
        self._pin_message_id: Optional[int] = None
        self._last_pin_text: str = ""
        # 🌙 Resumo diário 23h59
        self._daily_summary_task: Optional[asyncio.Task] = None
        # 🎯 Scalper S10: confirmação dupla — guarda 1ª aparição até a 2ª confirmar
        self._scalper_s10_pending: dict = {}  # (asset, direction) → timestamp 1ª aparição
        # 🎯 Scalper: warmup task (ping a cada 5s pra manter websocket quente)
        self._scalper_warmup_task: Optional[asyncio.Task] = None
        # 💰 Refresh periódico do saldo real do broker (independe de scalper)
        self._balance_refresh_task: Optional[asyncio.Task] = None
        self._last_balance_refresh: float = 0.0
        # 🎯 Scalper: contexto do último trade (para registrar no ranking interno)
        self._last_scalper_ctx: dict = {}  # trade_id ou key → {tf, cores}
        # Sincroniza estado do scalper no manager com config
        self.strategies.set_scalper_only(getattr(cfg, "scalper_mode", False))

        # Aplica tom + modo compacto (Msg1/Msg3/Msg4) para o módulo de mensagens
        # ANTES de qualquer mensagem ser gerada. Handlers de toggle também
        # chamam msgs.set_mode() quando o usuário troca pelo menu.
        msgs.set_mode(
            tone=getattr(cfg, "message_tone", "motivacional"),
            compact=getattr(cfg, "compact_messages", False),
        )

    # ---------------- feedback IA (persistência) ----------------
    def _load_ai_feedback(self) -> None:
        """Carrega o histórico de feedback das decisões da IA do disco."""
        from .config import DATA_DIR
        _path = DATA_DIR / "ai_feedback.json"
        if not _path.exists():
            return
        try:
            with _path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._last_ai_decisions = data
        except Exception as e:
            logger.warning(f"Falha ao carregar ai_feedback.json: {e}")

    def _save_ai_feedback(self) -> None:
        """Persiste o histórico de feedback das decisões da IA no disco."""
        from .config import DATA_DIR
        _path = DATA_DIR / "ai_feedback.json"
        try:
            with _path.open("w", encoding="utf-8") as f:
                json.dump(self._last_ai_decisions, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Falha ao salvar ai_feedback.json: {e}")

    # ---------------- Kelly fracionado ----------------
    def _kelly_amount(self, cfg, base_amount: float, payout: float, sig) -> float:
        """Calcula stake via Kelly fracionado.
          stake = bankroll * fraction * (wr - (1-wr)/b)
        onde b = payout decimal (0.85 → ganha 85% se WIN).
        Usa WR rolling do scalper inteiro (últimos N trades).
        Clamp em [min_mult * base, max_mult * base] por segurança.
        """
        # Bankroll
        bankroll = float(self.state.current_balance or 0.0)
        if bankroll <= 0:
            return base_amount
        # Payout em decimal (broker geralmente devolve %)
        b = float(payout) / 100.0 if payout > 1.5 else float(payout)
        if b <= 0.05:
            return base_amount
        # WR estimado: usa últimos N trades do scalper
        from ..strategies.scalper import SCALPER_RANKING
        try:
            summary = SCALPER_RANKING.stats_summary()
            tf_data = [v for k, v in summary.items() if k.endswith(":_TF_")]
            total_n = sum(int(d.get("n", 0)) for d in tf_data)
            # WR é em %, reconstrói wins ponderado
            total_w = sum(int(d.get("n", 0)) * float(d.get("wr", 0.0)) / 100.0 for d in tf_data)
            min_n = int(getattr(cfg, "kelly_min_trades", 20))
            if total_n < min_n:
                return base_amount  # amostra insuficiente
            wr = total_w / total_n
        except Exception:
            return base_amount
        # Fórmula Kelly: f = (b*p - q) / b = p - q/b   (p=wr, q=1-wr)
        edge = wr - (1.0 - wr) / b
        if edge <= 0:
            return base_amount  # sem edge → não aumenta
        fraction = float(getattr(cfg, "kelly_fraction", 0.25))
        kelly_stake = bankroll * fraction * edge
        # Clamp
        max_mult = float(getattr(cfg, "kelly_max_multiplier", 3.0))
        min_mult = float(getattr(cfg, "kelly_min_multiplier", 0.5))
        kelly_stake = max(base_amount * min_mult, min(base_amount * max_mult, kelly_stake))
        return round(kelly_stake, 2)

    # ---------------- ciclo de vida ----------------
    async def connect(self) -> None:
        cfg = self.cfg_manager.config
        self.broker = PocketOptionBroker(cfg.po_email, cfg.po_password, demo=cfg.po_demo, ssids=cfg.po_ssids)
        await self.broker.connect()
        self.state.connected = True
        # 💰 Saldo inicial: tenta até 3× pra evitar gravar 0 quando o WS demora
        bal = 0.0
        for _ in range(3):
            bal = await self.broker.get_balance()
            if bal > 0:
                break
            await asyncio.sleep(1.5)
        self.state.start_balance = bal
        self.state.current_balance = bal
        self._last_balance_refresh = time.time()
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
            except BrokerError as e:
                logger.error(f"Tentativa {attempt}/3 falhou: {e}")
                if attempt < len(_RECONNECT_DELAYS):
                    await self._notify(
                        f"⚠️ Reconexão {attempt}/3 falhou — tentando em {delay}s...\n<code>{e}</code>"
                    )
                    await asyncio.sleep(delay)
            except Exception as e:
                logger.exception(f"Tentativa {attempt}/3 erro inesperado: {e}")
                if attempt < len(_RECONNECT_DELAYS):
                    await self._notify(
                        f"⚠️ Reconexão {attempt}/3 erro inesperado — tentando em {delay}s...\n<code>{e}</code>"
                    )
                    await asyncio.sleep(delay)

        logger.error("Reconexão falhou após 3 tentativas. Abrindo browser para novo login.")
        self._waiting_for_ssid = True
        self.state.running = False
        self.state.connected = False
        asyncio.create_task(self._trigger_auto_capture())
        return "❌ Reconexão falhou após 3 tentativas. Abrindo navegador para login."

    async def update_ssid(self, ssid: str) -> str:
        """Chamado pelo handler /ssid: atualiza SSID sem reiniciar o bot."""
        if not self.broker:
            # broker ainda não inicializado — cria a conexão do zero
            cfg = self.cfg_manager.config
            self.broker = PocketOptionBroker(cfg.po_email, cfg.po_password, demo=cfg.po_demo, ssids=[ssid])
            try:
                await self.broker.connect()
                self.state.connected = True
                bal = 0.0
                for _ in range(3):
                    bal = await self.broker.get_balance()
                    if bal > 0:
                        break
                    await asyncio.sleep(1.5)
                self.state.start_balance = bal
                self.state.current_balance = bal
                self._last_balance_refresh = time.time()
                self.risk = RiskManager(cfg, self.state)
                self.martingale = MartingaleController(cfg.martingale, cfg.entry_amount)
                self._waiting_for_ssid = False
                return "✅ SSID configurado e conectado com sucesso."
            except Exception as e:
                return f"⚠️ Falha ao conectar com o SSID fornecido: {e}"
        try:
            ok = await self.broker.update_ssid(ssid)
            if not ok:
                return "⚠️ SSID rejeitado (inválido/expirado)."
            self.state.connected = True
            self._waiting_for_ssid = False
            # garante que risk/martingale estão inicializados (podem ser None se
            # connect() foi interrompido por _NoSSIDError antes de os criar)
            if self.risk is None or self.martingale is None:
                cfg = self.cfg_manager.config
                try:
                    balance = await self.broker.get_balance()
                    self.state.start_balance = balance
                    self.state.current_balance = balance
                except Exception:
                    pass
                self.risk = RiskManager(cfg, self.state)
                self.martingale = MartingaleController(cfg.martingale, cfg.entry_amount)
            logger.info("SSID atualizado via /ssid.")
            await self._notify("🔑 SSID atualizado! Bot reconectado.")
            return "✅ SSID atualizado com sucesso."
        except Exception as e:
            return f"⚠️ Falha ao atualizar SSID: {e}"

    async def start_trading(self) -> str:
        if self.state.running:
            return "⚠️ Bot já está rodando."
        try:
            if not self.broker or not self.state.connected:
                await self.connect()
        except _NoSSIDError:
            self._waiting_for_ssid = True
            asyncio.create_task(self._trigger_auto_capture())
            return "🔐 SSID não encontrado. Abrindo navegador para login automático..."
        except BrokerError as e:
            logger.error(f"Conexão inicial falhou: {e}")
            if self.telegram:
                import html
                await self._notify(
                    f"❌ <b>Bot não conseguiu conectar.</b>\n\n"
                    f"<code>{html.escape(str(e))}</code>"
                )
            return f"⚠️ Não foi possível conectar: {e}"

        # segurança: se connect() foi interrompido antes de criar risk/martingale,
        # inicializa agora (ex: fluxo _auto_capture_and_connect)
        if self.risk is None or self.martingale is None:
            cfg_now = self.cfg_manager.config
            if self.state.start_balance == 0.0 and self.broker:
                try:
                    bal = await self.broker.get_balance()
                    self.state.start_balance = bal
                    self.state.current_balance = bal
                except Exception:
                    pass
            self.risk = RiskManager(cfg_now, self.state)
            self.martingale = MartingaleController(cfg_now.martingale, cfg_now.entry_amount)

        self.state.running = True
        self.state.reset_daily()
        self._current_win_streak = 0
        self._waiting_for_ssid = False
        self._tick_count = 0
        self._recent_signals = {}  # limpa deduplicação ao iniciar

        cfg = self.cfg_manager.config

        # F: coletar info enriquecida para a notificação (ANTES de criar as tasks)
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

        self._otc_count = otc_count  # define ANTES de criar o loop

        self._trading_task = asyncio.create_task(self._trading_loop(), name="trading-loop")
        self._renewal_task = asyncio.create_task(self._ssid_renewal_loop(), name="ssid-renewal")
        self._midnight_task = asyncio.create_task(self._midnight_reset_loop(), name="midnight-reset")
        self._hourly_task = asyncio.create_task(self._hourly_summary_loop(), name="hourly-summary")
        # 💰 Refresh do saldo (sempre ativo enquanto o bot estiver rodando)
        self._balance_refresh_task = asyncio.create_task(self._balance_refresh_loop(), name="balance-refresh")
        # ♻️ Hot reload de config + 🛡️ Watchdog do broker
        self._hot_reload_task = asyncio.create_task(self._hot_reload_loop(), name="hot-reload")
        self._watchdog_task = asyncio.create_task(self._broker_watchdog_loop(), name="broker-watchdog")
        # 🔬 Backtest contínuo em background
        try:
            from ..strategies.backtest import backtest_loop
            self._backtest_task = asyncio.create_task(backtest_loop(self), name="backtest-loop")
        except Exception as _eb:
            logger.warning(f"Falha ao iniciar backtest_loop: {_eb}")
        # 📉 Alerta queda de payout
        if getattr(cfg, "payout_drop_alert_enabled", True):
            self._payout_alert_task = asyncio.create_task(
                self._payout_drop_loop(), name="payout-drop"
            )
        # 🌙 Resumo diário às 23h59 BRT
        if getattr(cfg, "daily_summary_enabled", True):
            self._daily_summary_task = asyncio.create_task(
                self._daily_summary_loop(), name="daily-summary"
            )

        await self._notify(msgs.bot_iniciado(
            simulacao=False,
            timeframes=cfg.scalper_timeframes if cfg.scalper_mode else cfg.timeframes,
            ia=cfg.ai_enabled,
            saldo=self.state.start_balance,
            ativos_modo=cfg.asset_mode,
            conta_demo=cfg.po_demo,
            otc_count=otc_count,
            avg_payout=avg_payout,
            sessao=otc_session_label(),
            min_score=cfg.scalper_min_score if cfg.scalper_mode else cfg.min_score,
            smart_reentry=cfg.smart_reentry,
            scalper_mode=cfg.scalper_mode,
        ))
        await self._notify(self.strategies.ranking.pretty())

        # --- Dashboard ao vivo: envia e fixa no chat ---
        strats_on_init = [s["name"] for s in self.strategies.list_status() if s["enabled"]]
        _scalp_init = bool(getattr(cfg, "scalper_mode", False))
        _dash_tfs_init = cfg.scalper_timeframes if _scalp_init else cfg.timeframes
        dash_init = msgs.dashboard_ao_vivo(
            wins=0, losses=0, draws=0, winrate=0.0, pnl=0.0,
            adx=0.0, is_lateral=False,
            otc_count=otc_count, timeframes=_dash_tfs_init,
            strategies_on=strats_on_init,
            updated_at=_brt_now().strftime("%H:%M:%S BRT"),
            tick_count=0,
            scalper_mode=_scalp_init,
            scalper_loss_streak=0,
            scalper_max_loss_streak=cfg.scalper_max_loss_streak,
        )
        dash_id = await self._notify_send(dash_init)
        if dash_id and self.telegram:
            await self.telegram.pin_message(dash_id)
            self._dashboard_message_id = dash_id
            self._last_dashboard_update = time.time()

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
        if self._midnight_task:
            self._midnight_task.cancel()
            try:
                await self._midnight_task
            except (asyncio.CancelledError, Exception):
                pass
        # 🎯 Cancela warmup do scalper se estiver rodando
        if self._scalper_warmup_task and not self._scalper_warmup_task.done():
            self._scalper_warmup_task.cancel()
            try:
                await self._scalper_warmup_task
            except (asyncio.CancelledError, Exception):
                pass
            self._scalper_warmup_task = None
        # ♻️🛡️ Cancela hot reload + watchdog + refresh saldo
        for _t_attr in ("_hot_reload_task", "_watchdog_task", "_hourly_task",
                        "_payout_alert_task", "_daily_summary_task",
                        "_balance_refresh_task"):
            _t = getattr(self, _t_attr, None)
            if _t and not _t.done():
                _t.cancel()
                try:
                    await _t
                except (asyncio.CancelledError, Exception):
                    pass
                setattr(self, _t_attr, None)
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
            data=_brt_now().strftime("%d/%m/%Y"),
            wins=s.wins, losses=s.losses, draws=s.draws,
            winrate=s.winrate, pnl=s.daily_pnl, trades=s.trades_today,
            melhor_estrategia=melhor,
            sparkline=self._day_sparkline(),
        ))

    # ---------------- captura automática de SSID (Chrome do sistema) ----------------
    async def _trigger_auto_capture(self) -> None:
        """Dispara _auto_capture_and_connect garantindo que só roda uma instância.

        Pode ser chamado de qualquer ponto (watchdog, trading_loop, startup) sem
        risco de abrir vários Chromes em paralelo.
        """
        if self._auto_capture_in_progress:
            logger.info("Auto-captura já em andamento — ignorando novo disparo.")
            return
        self._auto_capture_in_progress = True
        try:
            await self._auto_capture_and_connect()
        finally:
            self._auto_capture_in_progress = False

    async def _auto_capture_and_connect(self) -> None:
        """Abre o Chrome do sistema com perfil persistente e captura o SSID automaticamente.

        • 1ª vez: usuário faz login manualmente, captura é automática.
        • Próximas vezes: perfil persistente já tem cookies → captura em ~5s sem interação.
        """
        try:
            from ..utils.capture_ssid import capture_ssid_async
        except ImportError:
            await self._notify(
                "❌ <b>Playwright não instalado.</b>\n"
                "Envie o SSID manualmente: <code>/ssid SEU_TOKEN</code>"
            )
            return

        await self._notify(
            "🌐 <b>Abrindo Chrome para capturar SSID...</b>\n\n"
            "<i>Se for a primeira vez, faça login. Nas próximas execuções "
            "a captura será automática (perfil persistente).</i>"
        )

        async def _progress_async(msg: str) -> None:
            try:
                await self._notify(msg)
            except Exception:
                pass

        def _progress(msg: str) -> None:
            try:
                logger.info(msg)
                asyncio.create_task(_progress_async(msg))
            except Exception:
                pass

        ssid = await capture_ssid_async(
            on_progress=_progress,
            prefer_demo=self.cfg_manager.config.po_demo,
            email=self.cfg_manager.config.po_email,
            password=self.cfg_manager.config.po_password,
        )

        if not ssid:
            self._waiting_for_ssid = True
            await self._notify(
                "❌ <b>Captura automática falhou</b> (timeout 3 min).\n\n"
                "Pressione <b>▶️ Iniciar</b> para tentar novamente, ou envie:\n"
                "<code>/ssid SEU_TOKEN</code>"
            )
            return

        await self._notify("🔑 SSID capturado automaticamente! Conectando...")
        result = await self.update_ssid(ssid)
        if not self.state.connected:
            await self._notify(
                f"⚠️ SSID capturado mas falha na conexão.\n<code>{result}</code>"
            )
            return

        # Inicia o loop de trading
        await self.start_trading()

    # ---------------- renovação automática de SSID ----------------
    async def _ssid_renewal_loop(self) -> None:
        """Renova o SSID periodicamente via cookies. Intervalo configurável."""
        cfg = self.cfg_manager.config
        # 🔑 SSID renewal preventivo: usa intervalo configurável (default 90min)
        if getattr(cfg, "ssid_preventive_renewal", True):
            interval_min = max(15, int(cfg.ssid_renewal_interval_minutes))
        else:
            interval_min = 210  # legado: 3.5h
        RENEWAL_INTERVAL = interval_min * 60
        await asyncio.sleep(RENEWAL_INTERVAL)
        while self.state.running:
            try:
                # Pega o SSID atual da sessão e força reconnect — se o servidor
                # ainda aceita o token, a sessão é estendida; se não, alerta no Telegram.
                sess = self.broker.session.load()
                current_ssid = (sess or {}).get("ssid", "")
                if not current_ssid:
                    await self._notify(
                        "⚠️ <b>Sem SSID em cache.</b>\n"
                        "🤖 Disparando captura automática via Chrome..."
                    )
                    asyncio.create_task(self._trigger_auto_capture())
                    await asyncio.sleep(RENEWAL_INTERVAL)
                    continue

                async with self.broker._lock:
                    if self.broker._client is not None:
                        try:
                            await asyncio.wait_for(self.broker._client.disconnect(), timeout=3.0)
                        except Exception:
                            pass
                        self.broker._client = None
                    self.broker._connected = False
                    ok = await self.broker._try_ssid(current_ssid)
                    if ok:
                        self.broker._connected = True
                        logger.info("🔑 SSID renovado preventivamente (reconnect). ✅")
                    else:
                        await self._notify(
                            "⚠️ <b>SSID expirado!</b>\n"
                            "🤖 Disparando captura automática via Chrome..."
                        )
                        asyncio.create_task(self._trigger_auto_capture())

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Falha na renovação automática de SSID: {e}")

            await asyncio.sleep(RENEWAL_INTERVAL)

    # ---------------- ♻️ Hot reload de config ----------------
    async def _hot_reload_loop(self) -> None:
        """Monitora config.json e aplica mudanças sem reiniciar o bot."""
        from pathlib import Path as _P
        cfg_path = _P(self.cfg_manager.path)
        if not cfg_path.exists():
            return
        last_mtime = cfg_path.stat().st_mtime
        while self.state.running:
            try:
                cfg = self.cfg_manager.config
                interval = max(2, int(getattr(cfg, "hot_reload_interval_seconds", 5)))
                await asyncio.sleep(interval)
                if not getattr(cfg, "hot_reload_config", True):
                    continue
                mtime = cfg_path.stat().st_mtime
                # Se o save foi feito pelo próprio bot (cfg_manager.update), não notifica
                internal = getattr(self.cfg_manager, "_last_internal_mtime", 0.0)
                if mtime != last_mtime and abs(mtime - internal) > 0.5:
                    last_mtime = mtime
                    self.cfg_manager.reload()
                    await self._notify(
                        "♻️ <b>Config recarregada</b> a partir do arquivo.\n"
                        "<i>Mudanças aplicadas sem reiniciar.</i>"
                    )
                    logger.info("Hot reload: config.json modificado, aplicado.")
                else:
                    last_mtime = mtime
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Hot reload falhou: {e}")

    # ---------------- 🛡️ Watchdog do broker ----------------
    async def _broker_watchdog_loop(self) -> None:
        """Detecta desconexões repetidas do broker e força reset completo."""
        from collections import deque as _dq
        disconnect_history: _dq = _dq(maxlen=10)
        check_interval = 30
        while self.state.running:
            try:
                await asyncio.sleep(check_interval)
                cfg = self.cfg_manager.config
                if not getattr(cfg, "broker_watchdog_enabled", True):
                    continue
                connected = True
                try:
                    await asyncio.wait_for(self.broker.get_balance(), timeout=10)
                except Exception:
                    connected = False
                if not connected:
                    now = time.time()
                    disconnect_history.append(now)
                    window = cfg.broker_watchdog_window_seconds
                    recent = [t for t in disconnect_history if now - t <= window]
                    if len(recent) >= cfg.broker_watchdog_max_disconnects:
                        await self._notify(
                            f"🛡️ <b>Watchdog disparou:</b> {len(recent)} desconexões "
                            f"em {window}s. Reconectando broker do zero…"
                        )
                        try:
                            await self.broker.connect(force=True, reset_state=True)
                            await self._notify("✅ <b>Broker reconectado pelo watchdog.</b>")
                            disconnect_history.clear()
                        except _NoSSIDError:
                            await self._notify(
                                "🔐 <b>SSID expirou.</b> Disparando captura automática…"
                            )
                            disconnect_history.clear()
                            await self._trigger_auto_capture()
                        except Exception as e:
                            await self._notify(f"❌ Watchdog falhou ao reconectar: <code>{e}</code>")
                            # tenta auto-capture como último recurso
                            await self._trigger_auto_capture()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Watchdog erro: {e}")

    # ---------------- 📊 Resumo horário ----------------
    async def _hourly_summary_loop(self) -> None:
        """Envia resumo executivo por Telegram a cada virada de hora BRT."""
        while self.state.running:
            try:
                now = _brt_now()
                next_hour = (now + timedelta(hours=1)).replace(
                    minute=0, second=5, microsecond=0
                )
                wait = (next_hour - now).total_seconds()
                await asyncio.sleep(max(wait, 30))
                if not self.state.running:
                    return
                # Snapshot da hora que acabou
                base = self._hour_baseline
                cur_hour = (now - timedelta(hours=1)).hour
                wins_h = self.state.wins - base["wins"]
                losses_h = self.state.losses - base["losses"]
                trades_h = self.state.trades_today - base["trades"]
                pnl_h = self.state.daily_pnl - base["pnl"]
                # Top/pior par da hora
                hist_h = [
                    t for t in self.state.history[-200:]
                    if getattr(t, "timestamp", "").startswith(now.strftime("%Y-%m-%d"))
                    and t.result in ("WIN", "LOSS")
                ][-trades_h:] if trades_h > 0 else []
                pair_pnl: dict = {}
                for t in hist_h:
                    pair_pnl[t.asset] = pair_pnl.get(t.asset, 0.0) + t.profit
                best_pair = max(pair_pnl.items(), key=lambda kv: kv[1]) if pair_pnl else ("—", 0.0)
                worst_pair = min(pair_pnl.items(), key=lambda kv: kv[1]) if pair_pnl else ("—", 0.0)
                wr_h = (wins_h / max(wins_h + losses_h, 1)) * 100.0
                if trades_h > 0:
                    await self._notify(
                        f"⏰ <b>Resumo {cur_hour:02d}h BRT</b>\n"
                        f"• Trades: <b>{trades_h}</b> ({wins_h}W/{losses_h}L)\n"
                        f"• WR: <b>{wr_h:.1f}%</b>\n"
                        f"• PnL: <b>$ {pnl_h:+.2f}</b>\n"
                        f"• 🏆 Melhor: <b>{best_pair[0]}</b> (${best_pair[1]:+.2f})\n"
                        f"• 💀 Pior: <b>{worst_pair[0]}</b> (${worst_pair[1]:+.2f})"
                    )
                # Atualiza baseline
                self._hour_baseline = {
                    "hour": now.hour,
                    "wins": self.state.wins,
                    "losses": self.state.losses,
                    "pnl": self.state.daily_pnl,
                    "trades": self.state.trades_today,
                }
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning(f"Hourly summary erro: {e}")
                await asyncio.sleep(60)

    # ---------------- reset diário ----------------
    async def _midnight_reset_loop(self) -> None:
        """Aguarda meia-noite BRT e reseta os contadores diários automaticamente."""
        while self.state.running:
            now = _brt_now()
            next_midnight = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            wait_seconds = (next_midnight - now).total_seconds()
            try:
                await asyncio.sleep(wait_seconds)
            except asyncio.CancelledError:
                return
            if self.state.running:
                self.state.reset_daily()
                self._current_win_streak = 0
                logger.info("Contadores diários resetados à meia-noite BRT.")
                await self._notify(
                    "🌙 <b>Virada do dia!</b>\n"
                    "<i>Contadores diários resetados automaticamente.</i>"
                )

    # ---------------- loop ----------------
    async def _trading_loop(self) -> None:
        logger.info("Iniciando loop de trading.")
        _cfg_scan = self.cfg_manager.config
        _scalp = bool(getattr(_cfg_scan, "scalper_mode", False))
        _scan_tfs = _cfg_scan.scalper_timeframes if _scalp else _cfg_scan.timeframes
        await self._notify(msgs.escaneando_ativos(
            self._otc_count, _scan_tfs, otc_session_label(), scalper_mode=_scalp,
        ))
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
                # 🚀 Re-scan agressivo: se scalper sem trade há >5min, encurta sleep
                _cfg2 = self.cfg_manager.config
                if _cfg2.scalper_mode:
                    _last_t = self.state.last_trade_time or 0.0
                    if time.time() - _last_t > 300:
                        await asyncio.sleep(0.3)  # modo "caça rápida"
                    else:
                        await asyncio.sleep(0.5)
                else:
                    await asyncio.sleep(1.0)
        finally:
            logger.info("Loop encerrado.")

    async def _scalper_warmup_loop(self) -> None:
        """🎯 Mantém o websocket "quente" enquanto o scalper estiver ativo.

        Faz um get_balance leve a cada 5s — reduz latência de execução nas entradas
        rápidas (S10/S30) onde cada ms conta. Aproveita pra atualizar o saldo
        em memória (state.current_balance) — assim o /status sempre mostra valor
        recente da corretora, e não fica "travado" no saldo do início da sessão.
        """
        logger.info("🎯 Scalper warmup iniciado (ping 5s).")
        try:
            while self.state.running and self.cfg_manager.config.scalper_mode:
                try:
                    bal = await self.broker.get_balance()
                    if bal > 0:
                        self.state.current_balance = bal
                        if self.state.start_balance == 0.0:
                            self.state.start_balance = bal
                        self._last_balance_refresh = time.time()
                except Exception:
                    pass  # silencioso — é só warmup
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("🎯 Scalper warmup encerrado.")

    async def _balance_refresh_loop(self) -> None:
        """💰 Mantém state.current_balance sincronizado com a corretora.

        Roda SEMPRE enquanto o bot estiver ativo (não depende de scalper). O
        warmup do scalper já faz isso a cada 5s, então aqui usamos 15s pra não
        bater na API à toa.
        """
        logger.info("💰 Balance refresh iniciado (a cada 15s).")
        try:
            while self.state.running:
                try:
                    if self.broker and self.state.connected:
                        bal = await self.broker.get_balance()
                        if bal > 0:
                            self.state.current_balance = bal
                            if self.state.start_balance == 0.0:
                                self.state.start_balance = bal
                            self._last_balance_refresh = time.time()
                except Exception as e:
                    logger.debug(f"balance refresh: {e}")
                await asyncio.sleep(15)
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("💰 Balance refresh encerrado.")

    async def refresh_balance(self) -> float:
        """Força um get_balance fresco da corretora e atualiza state.

        Usado pelo Telegram (/saldo, menu Status) pra evitar mostrar valor em
        cache. Retorna 0.0 se desconectado.
        """
        if not self.broker or not self.state.connected:
            return 0.0
        try:
            bal = await self.broker.get_balance()
            if bal > 0:
                self.state.current_balance = bal
                if self.state.start_balance == 0.0:
                    self.state.start_balance = bal
                self._last_balance_refresh = time.time()
            return bal
        except Exception as e:
            logger.warning(f"refresh_balance falhou: {e}")
            return self.state.current_balance

    async def _tick(self) -> None:
        cfg = self.cfg_manager.config
        self._tick_count += 1

        # 🎯 Sincroniza estado do scalper no manager (toggle dinâmico)
        self.strategies.set_scalper_only(cfg.scalper_mode)

        # 🎯 Warmup websocket: liga/desliga task de ping conforme estado do scalper
        if cfg.scalper_mode and (self._scalper_warmup_task is None or self._scalper_warmup_task.done()):
            self._scalper_warmup_task = asyncio.create_task(self._scalper_warmup_loop(), name="scalper-warmup")
        elif not cfg.scalper_mode and self._scalper_warmup_task and not self._scalper_warmup_task.done():
            self._scalper_warmup_task.cancel()
            self._scalper_warmup_task = None

        # Auto-desliga scalper DESABILITADO: usuário tem controle total via botão.
        # Só notifica quando atinge streak (sem desligar).
        if (
            cfg.scalper_mode
            and cfg.scalper_max_loss_streak > 0
            and self._scalper_loss_streak >= cfg.scalper_max_loss_streak
            and not getattr(self, "_scalper_warned_streak", False)
        ):
            await self._notify(
                f"⚠️ <b>Scalper em streak ruim</b>\n"
                f"{self._scalper_loss_streak} losses consecutivos. "
                f"Continua operando — desligue manualmente se quiser pausar."
            )
            self._scalper_warned_streak = True

        # --- Dashboard ao vivo: edita a cada 60s ---
        if self._dashboard_message_id and time.time() - self._last_dashboard_update >= 60:
            strats_on = [s["name"] for s in self.strategies.list_status() if s["enabled"]]
            _scalp_d = bool(cfg.scalper_mode)
            _dash_tfs = cfg.scalper_timeframes if _scalp_d else cfg.timeframes
            dash = msgs.dashboard_ao_vivo(
                wins=self.state.wins, losses=self.state.losses, draws=self.state.draws,
                winrate=self.state.winrate, pnl=self.state.daily_pnl,
                adx=self._last_adx, is_lateral=self._last_is_lateral,
                otc_count=self._otc_count, timeframes=_dash_tfs,
                strategies_on=strats_on,
                updated_at=_brt_now().strftime("%H:%M:%S BRT"),
                mg_level=self.state.martingale_level,
                tick_count=self._tick_count,
                scalper_mode=_scalp_d,
                scalper_loss_streak=self._scalper_loss_streak,
                scalper_max_loss_streak=cfg.scalper_max_loss_streak,
            )
            if self.telegram:
                await self.telegram.edit_message(self._dashboard_message_id, dash)
            self._last_dashboard_update = time.time()

        dec = self.risk.can_trade()
        if not dec.allow:
            # 🔄 A1 — Pausa "soft": loop continua vivo, retoma no próximo dia BRT
            cfg = self.cfg_manager.config
            _today_brt = _brt_now().strftime("%Y-%m-%d")
            if "Stop Win" in dec.reason:
                if self._pause_reason != "stop_win":
                    await self._notify(msgs.stop_win_atingido(self.state.daily_pnl, self.state.trades_today))
                    self._pause_reason = "stop_win"
                    self._pause_until_date = _today_brt
                # 🛑 Stop Win SEMPRE para o bot de vez (ignora auto_resume_next_day)
                await self._notify("🛑 <b>Bot parado</b> — Stop Win atingido.")
                self.state.running = False
                return
            if "Stop Loss" in dec.reason:
                if self._pause_reason != "stop_loss":
                    await self._notify(msgs.stop_loss_atingido(self.state.daily_pnl, self.state.trades_today))
                    self._pause_reason = "stop_loss"
                    self._pause_until_date = _today_brt
                # 🛑 Stop Loss SEMPRE para o bot de vez (ignora auto_resume_next_day)
                await self._notify("🛑 <b>Bot parado</b> — Stop Loss atingido.")
                self.state.running = False
                return
            if "Streak" in dec.reason:
                if self._pause_reason != "streak":
                    await self._notify(msgs.streak_loss_atingido(self.state.current_loss_streak))
                    self._pause_reason = "streak"
                    self._pause_until_date = _today_brt
                if not getattr(cfg, "auto_resume_next_day", True):
                    self.state.running = False
                    return
                await asyncio.sleep(60)
                return
            if "Máx" in dec.reason:
                if self._pause_reason != "max_trades":
                    await self._notify(msgs.max_trades_atingido(self.state.trades_today))
                    self._pause_reason = "max_trades"
                    self._pause_until_date = _today_brt
                if not getattr(cfg, "auto_resume_next_day", True):
                    self.state.running = False
                    return
                await asyncio.sleep(60)
                return
            # apenas delay — silencioso
            await asyncio.sleep(2)
            return

        # 🔄 A1 — virou de dia? reseta pausa, blacklist e streak
        cfg = self.cfg_manager.config
        _today_brt = _brt_now().strftime("%Y-%m-%d")
        if self._pause_reason and self._pause_until_date and self._pause_until_date != _today_brt:
            await self._notify(
                f"🔄 <b>Auto-resume</b>\nNovo dia BRT — pausa anterior ({self._pause_reason}) liberada."
            )
            self._pause_reason = ""
            self._pause_until_date = ""
            # Reseta contadores diários (PnL/streak/trades) pra liberar risk.can_trade()
            try:
                self.state.reset_daily()
            except Exception:
                pass
        if self._blacklist_date != _today_brt:
            if self._asset_blacklist_today:
                logger.info(f"🔄 Reset blacklist diária ({len(self._asset_blacklist_today)} ativos liberados)")
            self._asset_blacklist_today = set()
            self._asset_loss_streak = {}
            self._blacklist_date = _today_brt

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

        lateral_alert = None

        async def _analyze_combo(asset: str, tf: str):
            tf_seconds = TIMEFRAME_SECONDS.get(tf, 60)
            try:
                candles = await self.broker.get_candles(asset, tf_seconds, 80)
                self._consecutive_no_ssid = 0
            except _NoSSIDError as e:
                logger.warning(f"Falha candles {asset} {tf}: {e}")
                self._consecutive_no_ssid += 1
                # Após 5 falhas consecutivas por falta de SSID → auto-captura
                if self._consecutive_no_ssid >= 5:
                    self._consecutive_no_ssid = 0
                    asyncio.create_task(self._trigger_auto_capture())
                return None
            except Exception as e:
                logger.warning(f"Falha candles {asset} {tf}: {e}")
                return None
            if not candles:
                return None
            # Usa apenas velas fechadas — exclui a última (ainda em formação)
            closed_candles = candles[:-1] if len(candles) > 1 else candles
            # 🎯 No modo scalper, usa thresholds próprios (mais rígidos)
            _ms = cfg.scalper_min_score if cfg.scalper_mode else cfg.min_score
            _mc = cfg.scalper_min_confidence if cfg.scalper_mode else cfg.min_assertiveness
            # 🚀 BYPASS: nos primeiros 5 trades do dia, baixa o min_score em 1.0
            # para coletar amostra rapidamente (auto-tuning depende de histórico).
            if cfg.scalper_mode and self.state.trades_today < 5:
                _ms = max(_ms - 1.0, 7.5)
                _mc = max(_mc - 10.0, 55.0)
            # ⏰ Heatmap-driven score adjustment
            if cfg.scalper_mode:
                from ..strategies.time_stats import TIME_STATS
                _hour_brt = _brt_now().hour
                # 1) Ajuste por WR da hora
                _ms += TIME_STATS.hour_score_adjust(_hour_brt)
                # 2) Ajuste granular por combo (asset×hora×TF)
                _ms += TIME_STATS.combo_score_adjust(asset, _hour_brt, tf)
                # 3) Hora bloqueada (WR<35% ou combo<30%) → não analisa
                if _ms >= 99.0:
                    return None
            analysis = self.strategies.analyze_asset(
                asset, closed_candles, tf,
                min_score=_ms,
                min_confidence=_mc,
                regime_filter_enabled=getattr(cfg, "regime_filter_enabled", True),
                regime_min_trades=int(getattr(cfg, "regime_min_trades", 20)),
                regime_min_wr=float(getattr(cfg, "regime_min_wr", 45.0)),
            )
            # 🎯 EXPLORATION BONUS: ativos não operados hoje recebem +0.5 no score
            # para incentivar diversificação e descobrir bons pares novos.
            if cfg.scalper_mode and analysis.best_signal:
                _today_str = _brt_now().strftime("%Y-%m-%d")
                _operated_today = any(
                    t.asset == asset and getattr(t, "timestamp", "").startswith(_today_str)
                    for t in self.state.history[-200:]
                )
                if not _operated_today:
                    analysis.best_signal.ranking_bonus += 0.5
                    analysis.best_signal.notes = (analysis.best_signal.notes + " | 🔎 Exploration").strip(" |")
            # Modo Sniper: aceita apenas sinais da estratégia "Sniper"
            # (removido)
            # Filtro de posição da vela: rejeita sinal se vela fechou em zona de indecisão
            if analysis.best_signal and closed_candles:
                _last_c = closed_candles[-1]
                _rng = _last_c.high - _last_c.low
                if _rng > 1e-9:
                    _pos = (_last_c.close - _last_c.low) / _rng
                    _d = analysis.best_signal.direction
                    if (_d == "CALL" and _pos < 0.55) or (_d == "PUT" and _pos > 0.45):
                        analysis.best_signal = None  # vela fechou em zona de indecisão

            # Bônus de confirmação: vela anterior também fechou na zona favorável (+0.5)
            if analysis.best_signal and len(closed_candles) >= 2:
                _prev_c = closed_candles[-2]
                _prev_rng = _prev_c.high - _prev_c.low
                if _prev_rng > 1e-9:
                    _prev_pos = (_prev_c.close - _prev_c.low) / _prev_rng
                    _d = analysis.best_signal.direction
                    if (_d == "CALL" and _prev_pos >= 0.55) or (_d == "PUT" and _prev_pos <= 0.45):
                        analysis.best_signal.ranking_bonus += 0.5  # duas velas consecutivas confirmam

            # B — Bônus de padrão de vela: Pin Bar ou Engulfing na direção do sinal (+1.0)
            if analysis.best_signal and len(closed_candles) >= 2:
                from ..strategies.base import BaseStrategy as _BS
                _last_c = closed_candles[-1]
                _prev_c = closed_candles[-2]
                _d = analysis.best_signal.direction
                if _BS.is_pin_bar(_last_c, _d) or _BS.is_engulfing(_prev_c, _last_c, _d):
                    analysis.best_signal.ranking_bonus += 1.0
                    analysis.best_signal.notes += " | 📌 Pin Bar" if _BS.is_pin_bar(_last_c, _d) else " | 📌 Engulfing"

            # C — Consistência de tendência: pelo menos 2 de 3 velas anteriores concordam (+0.5)
            if analysis.best_signal and len(closed_candles) >= 4:
                _d = analysis.best_signal.direction
                _last3 = closed_candles[-4:-1]  # 3 velas antes da atual
                _agreeing = 0
                for _c in _last3:
                    _c_rng = _c.high - _c.low
                    if _c_rng < 1e-9:
                        continue
                    _c_pos = (_c.close - _c.low) / _c_rng
                    if (_d == "CALL" and _c_pos >= 0.5) or (_d == "PUT" and _c_pos <= 0.5):
                        _agreeing += 1
                if _agreeing >= 2:
                    analysis.best_signal.ranking_bonus += 0.5

            payout = await self.broker.get_payout(asset)
            _forming = candles[-1] if len(candles) > 1 else None
            # 🔍 Tracking para /why <asset>
            if cfg.scalper_mode:
                if analysis.best_signal:
                    self._last_block_reason[asset] = {
                        "tf": tf, "reason": "✅ aprovado",
                        "score": round(analysis.best_signal.final_score, 2),
                        "min_score": _ms,
                        "ts": time.time(),
                    }
                else:
                    self._last_block_reason[asset] = {
                        "tf": tf,
                        "reason": "❌ filtros do scalper bloquearam",
                        "score": 0.0, "min_score": _ms,
                        "ts": time.time(),
                    }
            return (asset, tf, analysis, payout, _forming)

        # 🎯 Modo scalper: usa TFs próprios (S10/S30/M1)
        _active_tfs = cfg.scalper_timeframes if cfg.scalper_mode else cfg.timeframes
        combos = [(a, tf) for tf in _active_tfs for a in assets]

        # 🔍 Reset contadores de scan antes de cada ciclo (scalper)
        if cfg.scalper_mode:
            from ..strategies.scalper import reset_scan_stats, SCAN_STATS
            reset_scan_stats()

        results = await asyncio.gather(*[_analyze_combo(a, tf) for a, tf in combos], return_exceptions=True)

        # 🔍 Log periódico de scan no terminal (a cada N segundos; debug = 5s)
        _verbose = time.time() < self._debug_verbose_until
        _scan_log_period = 5.0 if _verbose else self._scan_log_interval
        if cfg.scalper_mode and (time.time() - self._last_scan_log_ts) >= _scan_log_period:
            from ..strategies.scalper import SCAN_STATS as _SS
            logger.info(
                f"🔍 SCAN: {_SS['total']} combos | "
                f"❌ wick:{_SS['wick']} ATR_low:{_SS['atr_low']} ATR_high:{_SS['atr_high']} "
                f"doji_p:{_SS['doji_prev']} doji_l:{_SS['doji_last']} prev_clean:{_SS['prev_clean']} "
                f"sem_núcleo:{_SS['no_core']} empate:{_SS['core_tie']} confirm:{_SS['confirms']} "
                f"score_baixo:{_SS['low_score']} payout_baixo:{_SS.get('payout_low', 0)} | ✅ aprovados:{_SS['approved']} | "
                f"🏆 best_score:{_SS['best_score']:.2f}"
            )
            if _verbose and _SS["near_misses"]:
                for nm in _SS["near_misses"][-5:]:
                    logger.info(f"   ⚠️ near-miss: {nm[0]} {nm[1]} {nm[2]} score={nm[3]:.2f}")
            self._last_scan_log_ts = time.time()

            # 📈 ASCII equity curve no terminal (últimas 30 trades)
            try:
                _hist = [t for t in self.state.history[-30:] if t.result in ("WIN", "LOSS", "DRAW")]
                if len(_hist) >= 2:
                    _eq = []
                    _cum = 0.0
                    for t in _hist:
                        _cum += t.profit
                        _eq.append(_cum)
                    _lo, _hi = min(_eq), max(_eq)
                    _rng = (_hi - _lo) or 1.0
                    _h = 6
                    _w = min(len(_eq), 60)
                    _step = max(1, len(_eq) // _w)
                    _samples = _eq[::_step][:_w]
                    _grid = [[" "] * _w for _ in range(_h)]
                    for x, v in enumerate(_samples):
                        y = int((1 - (v - _lo) / _rng) * (_h - 1))
                        _grid[y][x] = "█"
                    _zero_y = int((1 - (0 - _lo) / _rng) * (_h - 1)) if _lo <= 0 <= _hi else None
                    if _zero_y is not None:
                        for x in range(_w):
                            if _grid[_zero_y][x] == " ":
                                _grid[_zero_y][x] = "·"
                    _chart = "\n".join("".join(row) for row in _grid)
                    logger.info(f"📈 Equity (últimas {len(_hist)}): hi=${_hi:+.2f} lo=${_lo:+.2f} now=${_eq[-1]:+.2f}\n{_chart}")
            except Exception:
                pass

            # 🔔 Notificação opt-in "quase entrou"
            if self._alerts_enabled and _SS["near_misses"]:
                _min_score = cfg.scalper_min_score
                for nm in _SS["near_misses"][-10:]:
                    _key = f"{nm[0]}:{nm[1]}:{nm[2]}:{round(nm[3],1)}"
                    if _key in self._alerted_near_misses:
                        continue
                    if nm[3] >= _min_score - 0.5:
                        self._alerted_near_misses.add(_key)
                        await self._notify(
                            f"⚠️ <b>Quase entrou:</b> {nm[0]} {nm[1]} {nm[2]} "
                            f"score=<b>{nm[3]:.2f}</b> (min={_min_score})"
                        )
                # mantém set pequeno
                if len(self._alerted_near_misses) > 200:
                    self._alerted_near_misses = set(list(self._alerted_near_misses)[-100:])

        # 📲 Log no Telegram a cada 5min (resumo executivo)
        if cfg.scalper_mode and (time.time() - self._last_telegram_scan_ts) >= self._telegram_scan_interval:
            from ..strategies.scalper import SCAN_STATS as _SS
            from ..strategies.time_stats import TIME_STATS as _TS
            # Calcula o min_score EFETIVO atual (mesmo ajuste aplicado no _analyze_combo)
            _h_now = _brt_now().hour
            _eff_ms = cfg.scalper_min_score + _TS.hour_score_adjust(_h_now)
            if _h_now >= 20 or _h_now < 6:
                _eff_ms = max(_eff_ms, 9.0)
            elif 7 <= _h_now < 11:
                _eff_ms = max(_eff_ms - 0.3, 7.0)
            _msg = (
                f"🔍 <b>Scan dos últimos 5min</b>\n"
                f"• Analisados: <b>{_SS['total']}</b> combos\n"
                f"• Aprovados (scalper): <b>{_SS['approved']}</b>\n"
                f"• Best score: <b>{_SS['best_score']:.2f}</b> "
                f"(min efetivo={_eff_ms:.1f})\n"
            )
            if _SS["near_misses"]:
                _msg += "\n<i>Quase entrou:</i>\n"
                for nm in _SS["near_misses"][-5:]:
                    _msg += f"• {nm[0]} {nm[1]} {nm[2]} score={nm[3]:.2f}\n"
            await self._notify(_msg)
            self._last_telegram_scan_ts = time.time()

        # Coleta itens válidos e alertas laterais
        valid_items = []
        _adx_values: List[float] = []
        _any_lateral = False
        for item in results:
            if item is None or isinstance(item, Exception):
                continue
            asset, tf, analysis, payout, forming_candle = item
            if analysis.market_state:
                _adx_values.append(analysis.market_state.adx_value)
                # 🎯 Scalper: ignora flag lateral (filtro foi calibrado para M5/M15)
                if analysis.market_state.is_lateral and not cfg.scalper_mode:
                    _any_lateral = True
                    lateral_alert = analysis.market_state.description
            if not analysis.best_signal:
                continue
            if payout < cfg.min_payout:
                # 📉 Log/contador de rejeições por payout (granular)
                try:
                    from ..strategies.scalper import SCAN_STATS as _SS_p
                    _SS_p["payout_low"] = _SS_p.get("payout_low", 0) + 1
                    logger.info(
                        f"💰 Rejeitado por payout: {asset} {tf} "
                        f"sig={analysis.best_signal.direction} "
                        f"payout={payout:.1f}% < min={cfg.min_payout:.1f}% "
                        f"score={analysis.best_signal.final_score:.2f}"
                    )
                except Exception:
                    pass
                continue
            # 💱 B5 — ativo blacklisted no dia (4 losses seguidos)
            if (cfg.scalper_mode and getattr(cfg, "asset_blacklist_enabled", True)
                    and asset in self._asset_blacklist_today):
                continue
            # 🌙 A2 — Pausa hora tóxica (WR < 40% nas últimas N trades dessa hora)
            if cfg.scalper_mode and getattr(cfg, "toxic_hour_pause_enabled", True):
                try:
                    from ..strategies.time_stats import TIME_STATS
                    _h = _brt_now().hour
                    _min_n = int(getattr(cfg, "toxic_hour_min_trades", 15))
                    _thr = float(getattr(cfg, "toxic_hour_wr_threshold", 40.0)) / 100.0
                    _hr = TIME_STATS.hour_wr(_h, min_n=_min_n)
                    if _hr is not None and _hr[0] < _thr:
                        continue
                except Exception:
                    pass
            # 🔗 B6 — Em hora abaixo do "ouro": exige >=N núcleos + multi-TF
            if cfg.scalper_mode and getattr(cfg, "confluence_off_hours_enabled", True):
                try:
                    from ..strategies.time_stats import TIME_STATS
                    _h = _brt_now().hour
                    _hr = TIME_STATS.hour_wr(_h, min_n=10)
                    _wr_pct = (_hr[0] * 100.0) if _hr else 100.0  # sem dado = não pune
                    if _wr_pct < float(getattr(cfg, "confluence_off_hours_wr", 55.0)):
                        _confl = analysis.best_signal.confluence or {}
                        _core_keys = ("tick_momentum","ema_cross","rsi_extremo","vwap_touch",
                                      "bb_squeeze_break","stoch_reversal","fractal_pivot","ha_strong")
                        _ncores = sum(1 for k in _core_keys if _confl.get(k))
                        _need_cores = int(getattr(cfg, "confluence_off_hours_min_cores", 2))
                        _need_tf = bool(getattr(cfg, "confluence_off_hours_require_tf", True))
                        _has_tf = (analysis.best_signal.tf_confluence_bonus or 0) > 0
                        if _ncores < _need_cores or (_need_tf and not _has_tf):
                            continue
                except Exception:
                    pass
            # 🔬 Filtro estrito para TFs micro (S5/S10): só passa "100% certeiro"
            if (cfg.scalper_mode and getattr(cfg, "scalper_micro_strict_enabled", True)
                    and tf in (getattr(cfg, "scalper_micro_strict_tfs", ["S5", "S10"]) or [])):
                try:
                    _need_score = float(getattr(cfg, "scalper_micro_strict_min_score", 8.5))
                    if (analysis.best_signal.final_score or 0) < _need_score:
                        continue
                    if getattr(cfg, "scalper_micro_strict_require_tf", True):
                        if (analysis.best_signal.tf_confluence_bonus or 0) <= 0:
                            continue
                    _need_cores_m = int(getattr(cfg, "scalper_micro_strict_min_cores", 3))
                    if _need_cores_m > 0:
                        _confl_m = analysis.best_signal.confluence or {}
                        _ck = ("tick_momentum","ema_cross","rsi_extremo","vwap_touch",
                               "bb_squeeze_break","stoch_reversal","fractal_pivot","ha_strong")
                        _nc_m = sum(1 for k in _ck if _confl_m.get(k))
                        if _nc_m < _need_cores_m:
                            continue
                except Exception:
                    pass
            valid_items.append((asset, tf, analysis, payout, forming_candle))

        # Atualiza ADX médio e estado lateral para o dashboard
        if _adx_values:
            self._last_adx = sum(_adx_values) / len(_adx_values)
        self._last_is_lateral = _any_lateral

        # Aplica bônus de confluência entre timeframes: mesmo ativo+direção em 2+ TFs → +1.0
        _tf_dir_map: dict = {}
        for asset, tf, analysis, payout, _fc in valid_items:
            key = (asset, analysis.best_signal.direction)
            _tf_dir_map.setdefault(key, []).append(tf)

        for asset, tf, analysis, payout, _fc in valid_items:
            key = (asset, analysis.best_signal.direction)
            # Confluência: M15 ganha bônus maior (operação mais segura)
            _siblings = _tf_dir_map.get(key, [])
            if len(_siblings) >= 2:
                analysis.best_signal.tf_confluence_bonus = 1.5 if tf == "M15" else 0.7
                # 🔁 Cross-TF reforçado: se sinal em S30/S10 e M1 também concorda → +0.4 extra
                if tf in ("S10", "S30") and "M1" in _siblings:
                    analysis.best_signal.tf_confluence_bonus += 0.4
                # ou se M1 com confluência S30 (timeframes adjacentes)
                if tf == "M1" and ("S30" in _siblings or "S10" in _siblings):
                    analysis.best_signal.tf_confluence_bonus += 0.3
            # Bônus standalone M15: incentiva mais entradas em timeframe seguro
            if tf == "M15":
                analysis.best_signal.ranking_bonus += 0.5

        # Ordena por final_score e seleciona candidatos para múltiplas entradas
        valid_items.sort(key=lambda x: x[2].best_signal.final_score, reverse=True)

        _now = time.time()
        available_slots = max(0, cfg.max_open_trades - len(self._open_tasks))

        if not valid_items or available_slots == 0:
            if lateral_alert and (_now - self._last_lateral_notify > 3600):
                await self._notify(msgs.mercado_lateral_detectado(lateral_alert))
                self._last_lateral_notify = _now
            await asyncio.sleep(2)
            return

        # Filtra candidatos: dedup por ativo + entry timing da vela em formação
        seen_assets: dict = {}  # asset → direction permitida (multi-TF mesma direção)
        candidates = []
        for asset, tf, analysis, payout, forming_candle in valid_items:
            sig = analysis.best_signal
            # 🔁 Multi-TF mesma direção: permite 2ª entrada no MESMO ativo se direção bate
            if asset in seen_assets:
                if seen_assets[asset] != sig.direction:
                    continue  # direção diferente → ignora (evita risco oposto)
                # mesma direção → segue, mas só se score do segundo for forte
                if sig.final_score < cfg.scalper_min_score + 0.5:
                    continue
            _sig_key = f"{asset}:{tf}:{sig.strategy}:{sig.direction}"
            # 🚫 Anti sinal repetido OPOSTO: bloqueia flip BUY↔SELL em <30s
            _opp_dir = "PUT" if sig.direction == "CALL" else "CALL"
            _opp_key_prefix = f"{asset}:"
            for _k, _t in self._recent_signals.items():
                if not _k.startswith(_opp_key_prefix):
                    continue
                if _k.endswith(f":{_opp_dir}") and (_now - _t) < 30:
                    sig = None  # marca pra pular
                    break
            if sig is None:
                continue
            sig = analysis.best_signal  # restaura
            # 🎯 Cooldown próprio do scalper (override do TTL por TF)
            if cfg.scalper_mode:
                _sig_ttl = max(cfg.scalper_cooldown_seconds, TIMEFRAME_SECONDS.get(tf, 60))
            else:
                _sig_ttl = TIMEFRAME_SECONDS.get(tf, 60)
            if _now - self._recent_signals.get(_sig_key, 0) < _sig_ttl:
                continue
            # Verificação da vela em formação: rejeita se já reverteu >60% contra o sinal
            # 🔭 Pre-signal scout: vela em formação fortemente alinhada (>70%) reduz threshold
            _scout_aligned = False
            if forming_candle is not None:
                _fc_range = forming_candle.high - forming_candle.low
                if _fc_range > 1e-9:
                    _fc_pos = (forming_candle.close - forming_candle.low) / _fc_range
                    if (sig.direction == "CALL" and _fc_pos < 0.40) or \
                       (sig.direction == "PUT"  and _fc_pos > 0.60):
                        continue  # vela atual reverteu — aguarda próximo candle
                    # Scout alinhado: CALL com preço em 70%+ alto / PUT em 30%- baixo
                    if (sig.direction == "CALL" and _fc_pos >= 0.70) or \
                       (sig.direction == "PUT"  and _fc_pos <= 0.30):
                        _scout_aligned = True
            # 🎯 S5 sniper: exige threshold MUITO mais alto (11.0+) e 3+ núcleos
            if tf == "S5":
                _confl = sig.confluence or {}
                _core_count = sum(1 for k, v in _confl.items()
                                  if k in ("tick_momentum","ema_cross","rsi_extremo","vwap_touch",
                                           "bb_squeeze_break","stoch_reversal","fractal_pivot","ha_strong")
                                  and v)
                _s5_min = 11.0 - (0.3 if _scout_aligned else 0.0)
                if sig.final_score < _s5_min or _core_count < 3:
                    continue
            # 🎯 Filtro de round number: preço perto de número redondo psicológico
            #   <0.0005 (forex) ou <5 pips (JPY) → exige score +0.5
            try:
                _last_close = forming_candle.close if forming_candle else None
                if _last_close and _last_close > 0:
                    _is_jpy = _last_close > 10  # JPY pairs ~100-150
                    _step = 0.01 if _is_jpy else 0.0001
                    _round_dist_threshold = 0.05 if _is_jpy else 0.0005
                    # encontra próximo round number "grande" (00 / 50 no último dígito do pip)
                    if _is_jpy:
                        _next_round = round(_last_close * 2) / 2  # múltiplos de 0.50
                    else:
                        _next_round = round(_last_close * 200) / 200  # múltiplos de 0.0050
                    _dist = abs(_last_close - _next_round)
                    if _dist < _round_dist_threshold:
                        _bonus_required = 0.5 - (0.2 if _scout_aligned else 0.0)
                        if sig.final_score < cfg.scalper_min_score + _bonus_required:
                            continue
            except Exception:
                pass
            seen_assets[asset] = sig.direction
            candidates.append((asset, tf, analysis, payout))
            if len(candidates) >= available_slots:
                break

        # Limpa pendências S10 antigas (>60s)
        if self._scalper_s10_pending:
            self._scalper_s10_pending = {
                k: v for k, v in self._scalper_s10_pending.items() if _now - v < 60
            }

        if not candidates:
            if lateral_alert and (_now - self._last_lateral_notify > 3600):
                await self._notify(msgs.mercado_lateral_detectado(lateral_alert))
                self._last_lateral_notify = _now
            await asyncio.sleep(2)
            return

        # Registra deduplicação para todos os candidatos selecionados
        for asset, tf, analysis, payout in candidates:
            sig = analysis.best_signal
            self._recent_signals[f"{asset}:{tf}:{sig.strategy}:{sig.direction}"] = _now
        self._recent_signals = {k: v for k, v in self._recent_signals.items() if _now - v < 3600}

        # Processa cada candidato em PARALELO (multi-trade simultâneo)
        if cfg.scalper_mode and len(candidates) > 1:
            await asyncio.gather(
                *[self._process_one_signal(asset, tf, analysis, payout, cfg, _tf_dir_map)
                  for asset, tf, analysis, payout in candidates],
                return_exceptions=True,
            )
        else:
            for asset, tf, analysis, payout in candidates:
                await self._process_one_signal(asset, tf, analysis, payout, cfg, _tf_dir_map)

    # ---------------- processo de sinal individual ----------------
    async def _process_one_signal(
        self,
        asset: str, tf: str, analysis, payout: float, cfg, _tf_dir_map: dict,
    ) -> None:
        """Notifica, valida com IA e despacha trade para um único sinal."""
        sig = analysis.best_signal
        _ms = analysis.market_state
        _market_adx = round(_ms.adx_value, 1) if _ms else 0.0
        _market_bb_width = round(_ms.bb_width, 4) if _ms else 0.0

        # ⏱️ Trava entrada se vela atual já está no último quarto (S30/M1).
        # Reversões 'do nada' acontecem desproporcionalmente nesse intervalo final.
        if cfg.scalper_mode and sig.strategy == "Scalper Sniper" and tf in ("S30", "M1"):
            _tf_secs_lock = TIMEFRAME_SECONDS.get(tf, 60)
            _now_lock = time.time()
            _remaining_lock = _tf_secs_lock - int(_now_lock) % _tf_secs_lock
            if _remaining_lock < (_tf_secs_lock / 4):
                logger.info(
                    f"⏱️ Late-window block: {asset} {tf} {sig.direction} "
                    f"(restam {_remaining_lock}s < {_tf_secs_lock//4}s) — aguarda próxima vela."
                )
                return

        _tf_confluence = sig.tf_confluence_bonus > 0
        if _tf_confluence:
            _confluence_tfs = _tf_dir_map.get((asset, sig.direction), [])
            await self._notify(msgs.tf_confluencia_detectada(asset, sig.direction, _confluence_tfs))

        compact = bool(getattr(cfg, "compact_messages", False))
        # 🎯 Scalper: força card compacto (S10/S30 não dá tempo de ler card grande)
        if sig.strategy == "Scalper Sniper":
            compact = True

        # 📊 Enriquece notes com WR histórico (asset+hora) — ajuda usuário a entender
        try:
            from ..strategies.time_stats import TIME_STATS
            _now_brt = _brt_now()
            _h = _now_brt.hour
            _res = TIME_STATS.combo_wr(asset, _h, tf, min_n=3)
            if _res is not None:
                _wr_combo, _n_combo = _res
                _wr_pct = _wr_combo * 100.0
                _wr_em = "🟢" if _wr_pct >= 60 else ("🟡" if _wr_pct >= 50 else "🔴")
                _hour_note = f"{_wr_em} WR {_h:02d}h {tf}: {_wr_pct:.0f}% (n={_n_combo})"
                if sig.notes:
                    sig.notes = f"{sig.notes} · {_hour_note}"
                else:
                    sig.notes = _hour_note
        except Exception:
            pass

        # 💡 Modo "explicação ON" — adiciona linha "Por quê" simplificada
        if getattr(cfg, "explain_mode", False):
            try:
                _why_parts = []
                # 1) Núcleos do scalper / contagem de confirmações
                _meta = getattr(sig, "metadata", None) or {}
                _cores = _meta.get("cores") or _meta.get("confirmations")
                if isinstance(_cores, (list, tuple)) and _cores:
                    _why_parts.append(f"{len(_cores)} confirmações ({', '.join(str(c) for c in _cores[:4])})")
                elif isinstance(_cores, int):
                    _why_parts.append(f"{_cores} confirmações")
                # 2) ATR vs média
                _atr_ratio = _meta.get("atr_ratio")
                if _atr_ratio:
                    _why_parts.append(f"ATR {float(_atr_ratio):.1f}× média")
                # 3) Confluência multi-TF
                if _tf_confluence:
                    _why_parts.append("multi-TF mesma direção")
                # 4) WR da hora (já calculado acima)
                if "_wr_pct" in dir() and "_n_combo" in dir():
                    try:
                        _why_parts.append(f"WR {_wr_pct:.0f}% nesta hora (n={_n_combo})")
                    except Exception:
                        pass
                # 5) Score acima do mínimo
                _min_sc = (cfg.scalper_min_score if cfg.scalper_mode else cfg.min_score)
                _why_parts.append(f"score {sig.final_score:.1f} ≥ mín {_min_sc:.1f}")
                # 6) Payout OK
                _why_parts.append(f"payout {payout:.0f}%")
                _why = " + ".join(_why_parts)
                _why_line = f"💡 Por quê: {_why}"
                sig.notes = (sig.notes + " · " + _why_line) if sig.notes else _why_line
            except Exception:
                pass

        card_args = dict(
            asset=asset, timeframe=tf, strategy=sig.strategy,
            direction=sig.direction, score=sig.final_score,
            confidence=sig.confidence, payout=payout,
            notes=sig.notes, tf_confluence=_tf_confluence,
        )
        card_message_id: Optional[int] = None

        if compact:
            card_text = msgs.build_trade_card(stage="signal", **card_args)
            card_message_id = await self._notify_send(card_text)
        else:
            await self._notify(msgs.sinal_detectado(
                asset=asset, tf=tf, strategy=sig.strategy,
                direction=sig.direction, score=sig.final_score,
                confidence=sig.confidence, payout=payout, notes=sig.notes,
                tf_confluence=_tf_confluence,
            ))

        # 🎯 Modo scalper: bypass completo da IA (latência mata sinal de 10s/30s)
        _bypass_ai_for_scalper = cfg.scalper_mode and sig.strategy == "Scalper Sniper"

        if cfg.ai_enabled and not _bypass_ai_for_scalper:
            _brt = _brt_now()
            _recent_results = [
                f"{t.result}({t.profit:+.2f})"
                for t in reversed(self.state.history)
                if t.asset == asset
            ][:5]
            _strat_stats = self.strategies.ranking.stats.get(sig.strategy)
            _rolling_wr = _strat_stats.rolling_winrate() if _strat_stats else 0.0
            _ai_history = self._last_ai_decisions.get(asset, [])
            _last_ai = _ai_history[-1] if _ai_history else {}
            # Performance específica desse par (ativo+estratégia)
            _pair_st = self.strategies.pair_stats.get(asset, sig.strategy)
            _pair_block = {
                "trades": _pair_st.trades if _pair_st else 0,
                "winrate": round(_pair_st.rolling_winrate(), 1) if _pair_st else 0.0,
                "sample": _pair_st.rolling_size() if _pair_st else 0,
            }
            # Últimas 5 velas fechadas (OHLCV) — IA "vê" o gráfico
            _last_candles = []
            try:
                _tf_secs = TIMEFRAME_SECONDS.get(tf, 60)
                _all_candles = await self.broker.get_candles(asset, _tf_secs, 30)
                _closed = _all_candles[:-1] if len(_all_candles) > 1 else _all_candles
                for _c in _closed[-5:]:
                    _last_candles.append({
                        "o": round(_c.open, 5), "h": round(_c.high, 5),
                        "l": round(_c.low, 5), "c": round(_c.close, 5),
                        "v": round(_c.volume, 2),
                    })
                # Volume relativo: última vela fechada vs média das 20 anteriores
                _vols = [c.volume for c in _closed[-21:-1]]
                _avg_vol = (sum(_vols) / len(_vols)) if _vols else 0.0
                _vol_ratio = round(_closed[-1].volume / _avg_vol, 2) if _avg_vol > 1e-9 else 0.0
            except Exception:
                _vol_ratio = 0.0
            payload = {
                "asset": asset, "timeframe": tf,
                "direction": sig.direction, "strategy": sig.strategy,
                "score": sig.final_score, "confidence": sig.confidence,
                "market": _ms.description if _ms else "",
                "confluence": sig.confluence, "payout": payout,
                "hour_brt": _brt.hour,
                "weekday": _brt.strftime("%a"),  # Mon, Tue, ...
                "session": otc_session_label(),
                "bb_width": round(_ms.bb_width, 4) if _ms else 0,
                "adx": round(_ms.adx_value, 1) if _ms else 0,
                "recent_results": _recent_results,
                "strategy_rolling_winrate": round(_rolling_wr, 1),
                "pair_performance": _pair_block,
                "volume_ratio": _vol_ratio,  # >1.0 = volume acima da média
                "last_5_candles": _last_candles,
                "total_winrate": round(self.state.winrate, 1),
                "sample_size": self.state.trades_today,
                "last_ai_feedback": _last_ai,
                "last_ai_history": _ai_history,
            }
            decision: AIDecision = await self.ai.validate_signal(payload)
            if compact:
                card_text = msgs.build_trade_card(
                    stage="ai_validated",
                    ai_operate=decision.operate,
                    ai_confidence=decision.confidence,
                    ai_rationale=decision.rationale,
                    ai_cached=decision.cached,
                    **card_args,
                )
                await self._notify_edit(card_message_id, card_text)
            else:
                await self._notify(msgs.ia_validando(
                    operar=decision.operate, confianca=decision.confidence,
                    rationale=decision.rationale, cached=decision.cached,
                ))
            if not decision.operate:
                # Mensagem curta explicando o skip (especialmente útil no modo compact,
                # mas enviada sempre — usuário pediu visibilidade explícita do "porquê").
                try:
                    await self._notify(msgs.ia_skip_compact(
                        asset=asset, tf=tf, direction=sig.direction,
                        rationale=decision.rationale or "sem justificativa",
                    ))
                except Exception:
                    pass
                return
        else:
            _why = "Scalper bypass IA" if _bypass_ai_for_scalper else "IA desabilitada"
            decision = AIDecision(True, sig.confidence, _why)

        self.martingale.base_amount = cfg.entry_amount
        # 🎲 Smart Gale: rejeita gale se score do novo sinal não é claramente maior
        self.martingale.smart_gale = bool(getattr(cfg, "smart_gale", True))
        self.martingale.smart_gale_ratio = float(getattr(cfg, "smart_gale_score_ratio", 1.5))
        if not self.martingale.can_gale(sig.final_score):
            await self._notify(
                f"🎲 <b>Gale bloqueado</b> — sinal novo score <b>{sig.final_score:.2f}</b> "
                f"vs anterior <b>{self.martingale.state.last_score:.2f}</b> "
                f"(precisa ≥{self.martingale.smart_gale_ratio:.1f}×). Reset martingale."
            )
            self.martingale.state.level = 0
        # 💰 Soros: configura
        self.martingale.soros_enabled = bool(getattr(cfg, "soros_enabled", False))
        self.martingale.soros_pct = float(getattr(cfg, "soros_pct", 50.0))
        self.martingale.soros_max_levels = int(getattr(cfg, "soros_max_levels", 2))
        # Memoriza score atual pra próximo gale
        self.martingale.register_signal(sig.final_score)
        amount = self.martingale.next_amount()
        # 💎 Kelly fracionado: ajusta stake só no nível 0 (não em gales)
        if (
            getattr(cfg, "kelly_enabled", False)
            and self.martingale.state.level == 0
            and sig.strategy == "Scalper Sniper"
        ):
            try:
                amount = self._kelly_amount(cfg, amount, payout, sig)
            except Exception as _ek:
                logger.warning(f"Kelly falhou, usando stake base: {_ek}")
        # 📊 C7 — Stake adaptativo por WR da hora (só nível 0 do martingale)
        if (
            getattr(cfg, "adaptive_stake_enabled", True)
            and self.martingale.state.level == 0
            and sig.strategy == "Scalper Sniper"
        ):
            try:
                from ..strategies.time_stats import TIME_STATS
                _h = _brt_now().hour
                _hr = TIME_STATS.hour_wr(_h, min_n=10)
                if _hr is not None:
                    _wr_pct = _hr[0] * 100.0
                    _low = float(getattr(cfg, "adaptive_stake_low_wr", 50.0))
                    _high = float(getattr(cfg, "adaptive_stake_high_wr", 65.0))
                    _low_m = float(getattr(cfg, "adaptive_stake_low_mult", 0.6))
                    _high_m = float(getattr(cfg, "adaptive_stake_high_mult", 1.3))
                    _floor = float(getattr(cfg, "adaptive_stake_min_dollar", 1.0))
                    if _wr_pct < _low:
                        amount = max(_floor, round(amount * _low_m, 2))
                    elif _wr_pct >= _high:
                        amount = round(amount * _high_m, 2)
            except Exception as _eas:
                logger.debug(f"adaptive stake falhou: {_eas}")
        card_ctx = None
        if compact:
            card_ctx = {
                "message_id": card_message_id,
                "card_args": card_args,
                "ai": {
                    "operate": decision.operate,
                    "confidence": decision.confidence,
                    "rationale": decision.rationale,
                    "cached": decision.cached,
                },
            }
        task = asyncio.create_task(
            self._execute(asset, tf, sig, amount, decision.confidence, payout,
                          _market_adx, _market_bb_width, card_ctx=card_ctx),
            name=f"trade-{asset}-{tf}",
        )
        self._open_tasks.append(task)

    # ---------------- martingale "próxima vela" ----------------
    async def _schedule_martingale_next_candle(
        self, asset: str, tf: str, sig, amount: float, ai_conf: float, payout: float,
        market_adx: float, market_bb_width: float, tf_seconds: int,
    ) -> None:
        """Aguarda o início da próxima vela e re-executa o trade na mesma direção.

        Usado quando MartingaleConfig.mode == "next_candle". Não passa por IA nem
        por análise de estratégia — é uma re-entrada forçada do martingale.
        """
        # Espera até o próximo boundary de candle (epoch % tf_seconds == 0) + 1s de margem
        now = time.time()
        wait = tf_seconds - (now % tf_seconds) + 1.0
        try:
            await asyncio.sleep(wait)
        except asyncio.CancelledError:
            return
        # Verifica se o bot ainda está rodando e martingale ainda ativo
        if not self._running:
            return
        cfg = self.cfg_manager.config
        if not cfg.martingale.enabled or self.martingale.state.level == 0:
            return
        try:
            await self._notify(f"⚡ Martingale nv {self.martingale.state.level} — re-entrada {asset} {sig.direction} ({tf})")
        except Exception:
            pass
        await self._execute(
            asset, tf, sig, amount, ai_conf, payout,
            market_adx, market_bb_width, card_ctx=None,
        )

    # ---------------- execução ----------------
    async def _execute(self, asset: str, tf: str, sig, amount: float, ai_conf: float, payout: float,
                       market_adx: float = 0.0, market_bb_width: float = 0.0,
                       card_ctx: Optional[dict] = None) -> None:
        cfg = self.cfg_manager.config
        tf_secs = TIMEFRAME_SECONDS.get(tf, 60)
        # Expiração sempre no fechamento da vela atual.
        # Se restar pouco tempo (<15% do TF, mínimo 10s), pula pro próximo fechamento
        # para evitar ordens com tempo curto demais que o broker rejeita.
        _now_s = time.time()
        _remaining = tf_secs - int(_now_s) % tf_secs
        _min_safe = max(10, int(tf_secs * 0.15))
        if _remaining < _min_safe:
            _remaining += tf_secs
        expiration = _remaining
        # ⏱️ Sinal forte (score≥10) em M1 → estende para M2 (reduz noise random)
        if (
            getattr(cfg, "strong_signal_extend_expiration", True)
            and tf == "M1"
            and getattr(sig, "final_score", 0.0) >= 10.0
        ):
            expiration += 60  # M1 → M2
        elif (
            getattr(cfg, "strong_signal_extend_expiration", True)
            and tf == "M1"
            and getattr(sig, "final_score", 0.0) >= 11.5
        ):
            expiration += 120  # M1 → M3
        self.risk.mark_trade_time()
        self.state.open_trades += 1
        try:
            await self._execute_inner(asset, tf, sig, amount, ai_conf, payout, cfg, expiration,
                                      market_adx, market_bb_width, card_ctx=card_ctx)
        finally:
            self.state.open_trades = max(0, self.state.open_trades - 1)

    async def _execute_inner(
        self, asset: str, tf: str, sig, amount: float,
        ai_conf: float, payout: float, cfg, expiration: int,
        market_adx: float = 0.0, market_bb_width: float = 0.0,
        card_ctx: Optional[dict] = None,
    ) -> None:
        compact = card_ctx is not None
        order = None
        try:
            # 🛡️ Spread/slippage filter — checa se preço ao vivo divergiu muito
            if (
                getattr(cfg, "spread_filter_enabled", True)
                and getattr(sig, "signal_close", 0.0) > 0
                and getattr(sig, "signal_atr", 0.0) > 0
            ):
                try:
                    _tf_secs_chk = TIMEFRAME_SECONDS.get(tf, 60)
                    # Pega ATÉ 2 candles do mesmo TF; o último pode estar formando
                    _live = await self.broker.get_candles(asset, _tf_secs_chk, 2)
                    if _live:
                        _live_close = float(_live[-1].close)
                        _delta = abs(_live_close - sig.signal_close)
                        _max_dev = float(getattr(cfg, "spread_atr_mult", 2.0)) * sig.signal_atr
                        if _delta > _max_dev:
                            try:
                                from ..strategies.scalper import SCAN_STATS as _SS_sp
                                _SS_sp["spread_block"] = _SS_sp.get("spread_block", 0) + 1
                            except Exception:
                                pass
                            logger.info(
                                f"🛡️ Spread filter: {asset} {tf} abortado "
                                f"(Δ={_delta:.5f} > {_max_dev:.5f} = {cfg.spread_atr_mult}×ATR)"
                            )
                            await self._notify(
                                f"🛡️ <b>Ordem abortada — slippage</b>\n"
                                f"<b>{asset}</b> {tf} {sig.direction}\n"
                                f"Preço moveu {_delta:.5f} (> {cfg.spread_atr_mult}×ATR={_max_dev:.5f}) "
                                f"entre sinal e envio — broker com delay."
                            )
                            return
                except Exception as _esp:
                    logger.debug(f"spread filter check falhou: {_esp}")

            # 🔄 Fresh-tick re-evaluation: pega estado mais recente da vela em formação
            # logo antes de enviar a ordem. Se a posição do close mudou contra o sinal
            # (CALL caiu pra <0.55 / PUT subiu pra >0.45), aborta — momentum se inverteu
            # entre análise e envio (causa principal das reversões de último segundo).
            if (
                getattr(cfg, "scalper_mode", False)
                and sig.strategy == "Scalper Sniper"
            ):
                try:
                    _tf_secs_ft = TIMEFRAME_SECONDS.get(tf, 60)
                    _live_ft = await self.broker.get_candles(asset, _tf_secs_ft, 2)
                    if _live_ft:
                        _fc = _live_ft[-1]
                        _fc_rng = _fc.high - _fc.low
                        if _fc_rng > 1e-9:
                            _fc_pos = (_fc.close - _fc.low) / _fc_rng
                            _flipped = (
                                (sig.direction == "CALL" and _fc_pos < 0.55) or
                                (sig.direction == "PUT"  and _fc_pos > 0.45)
                            )
                            if _flipped:
                                logger.info(
                                    f"🔄 Fresh-tick abort: {asset} {tf} {sig.direction} "
                                    f"(_fc_pos={_fc_pos:.2f}) — momentum invertido antes do envio."
                                )
                                await self._notify(
                                    f"🔄 <b>Ordem abortada — fresh-tick</b>\n"
                                    f"<b>{asset}</b> {tf} {sig.direction}\n"
                                    f"Vela em formação virou contra o sinal (pos={_fc_pos:.2f})."
                                )
                                return
                except Exception as _eft:
                    logger.debug(f"fresh-tick check falhou: {_eft}")

            order = await self.broker.place_trade(asset, sig.direction, amount, expiration)
            if compact:
                ai = card_ctx["ai"]
                card_text = msgs.build_trade_card(
                    stage="order_sent",
                    ai_operate=ai["operate"],
                    ai_confidence=ai["confidence"],
                    ai_rationale=ai["rationale"],
                    ai_cached=ai["cached"],
                    order_amount=amount,
                    order_mg_level=self.martingale.state.level,
                    **card_ctx["card_args"],
                )
                await self._notify_edit(card_ctx.get("message_id"), card_text)
            else:
                await self._notify(msgs.ordem_enviada(
                    asset=asset, direction=sig.direction, amount=amount, tf=tf,
                    mg_level=self.martingale.state.level,
                ))
            outcome = await self.broker.check_result(order.order_id, expiration)
            profit = round(amount * (payout / 100.0), 2) if outcome == "WIN" else (
                -amount if outcome == "LOSS" else 0.0
            )
        except asyncio.CancelledError:
            if order is not None:
                # trade enviado mas cancelado antes do resultado — registra para auditoria
                trade = TradeResult(
                    timestamp=_brt_iso(),
                    asset=asset, direction=sig.direction, amount=amount,
                    expiration=expiration, strategy=sig.strategy,
                    score=sig.final_score, ai_confidence=ai_conf,
                    result="CANCELADO", profit=0.0, timeframe=tf,
                    martingale_level=self.martingale.state.level,
                )
                self.state.register_trade(trade)
                self.state.save_history()
                logger.warning(f"Trade {asset} cancelado após envio — registrado como CANCELADO.")
            raise
        except Exception as e:
            logger.exception(f"Falha ao executar ordem: {e}")
            await self._notify(msgs.bot_erro_conexao(str(e)))
            return

        # Atualiza saldo real do broker após resultado
        try:
            self.state.current_balance = await self.broker.get_balance()
        except Exception:
            self.state.current_balance += profit  # estimativa local se broker falhar

        res_for_rank = "WIN" if profit > 0 else ("LOSS" if profit < 0 else "DRAW")

        # 🎯 Tracking de loss streak do scalper (desliga auto após N losses)
        if sig.strategy == "Scalper Sniper":
            if res_for_rank == "LOSS":
                self._scalper_loss_streak += 1
                # 💱 B5 — streak de loss por ativo do dia
                if getattr(cfg, "asset_blacklist_enabled", True):
                    _cur = self._asset_loss_streak.get(asset, 0) + 1
                    self._asset_loss_streak[asset] = _cur
                    _limit = int(getattr(cfg, "asset_blacklist_loss_streak", 4))
                    if _cur >= _limit and asset not in self._asset_blacklist_today:
                        self._asset_blacklist_today.add(asset)
                        await self._notify(
                            f"💱 <b>Ativo banido hoje</b>\n"
                            f"<b>{asset}</b> — {_cur} losses seguidos. "
                            f"Desbloqueia automaticamente amanhã."
                        )
            elif res_for_rank == "WIN":
                self._scalper_loss_streak = 0
                self._scalper_warned_streak = False
                # 💱 B5 — WIN reseta streak do ativo
                if asset in self._asset_loss_streak:
                    self._asset_loss_streak[asset] = 0
            # 🎯 Registra resultado no ranking interno (por TF e por núcleo)
            try:
                from ..strategies.scalper import SCALPER_RANKING
                _confl = sig.confluence or {}
                _cores_used = []
                _name_map = {
                    "tick_momentum": "Tick Momentum",
                    "ema_cross": "EMA Cross",
                    "rsi_extremo": "RSI Extremo",
                    "vwap_touch": "VWAP Touch",
                    "bb_squeeze_break": "BB Squeeze Break",
                    "stoch_reversal": "Stoch Reversal",
                    "fractal_pivot": "Fractal Pivot",
                    "ha_strong": "HA Strong",
                }
                for k, label in _name_map.items():
                    if _confl.get(k):
                        _cores_used.append(label)
                SCALPER_RANKING.register(tf, _cores_used, res_for_rank)
                # 🧬 CoreStats: WR por núcleo individual × hora BRT
                try:
                    from ..strategies.core_stats import CORE_STATS
                    CORE_STATS.register(_cores_used, _brt_now().hour, res_for_rank)
                except Exception as _ec:
                    logger.warning(f"Falha ao registrar core_stats: {_ec}")
                # ⏰ TimeStats: registra WR por hora e por combo
                try:
                    from ..strategies.time_stats import TIME_STATS
                    _now_brt = _brt_now()
                    TIME_STATS.register(
                        asset=asset, tf=tf, hour=_now_brt.hour,
                        result=res_for_rank, date_str=_now_brt.strftime("%Y-%m-%d"),
                    )
                except Exception as _et:
                    logger.warning(f"Falha ao registrar time_stats: {_et}")
                # 🧠 Registra padrão de 3 velas no PatternLearner
                try:
                    from ..strategies.pattern_learner import PATTERN_LEARNER
                    _pat = (sig.confluence or {}).get("pattern_key") if sig.confluence else ""
                    if _pat:
                        PATTERN_LEARNER.register(_pat, res_for_rank, sig.direction)
                except Exception as _ep:
                    logger.warning(f"Falha ao registrar pattern: {_ep}")
            except Exception as _e:
                logger.warning(f"Falha ao registrar scalper ranking: {_e}")

        # 🌡️ Registra resultado por (estratégia, regime) — vale para TODOS os modos
        try:
            _rg = getattr(sig, "market_regime", "") or ""
            if _rg:
                from ..strategies.regime_stats import REGIME_STATS
                REGIME_STATS.register(sig.strategy, _rg, res_for_rank)
        except Exception as _erg:
            logger.debug(f"regime_stats register falhou: {_erg}")

        # 🎯 Auto-tuning global de min_score (ajusta cfg.min_score baseado em
        # WR rolling das últimas N trades — exclui scalper, que tem self-tune próprio).
        if getattr(cfg, "auto_tune_min_score", True) and sig.strategy != "Scalper Sniper":
            try:
                _win = int(getattr(cfg, "auto_tune_window", 50))
                _min_n = int(getattr(cfg, "auto_tune_min_trades", 20))
                _hist = [t for t in self.state.history[-_win:]
                         if getattr(t, "strategy", "") != "Scalper Sniper"
                         and t.result in ("WIN", "LOSS")]
                if len(_hist) >= _min_n:
                    _wins = sum(1 for t in _hist if t.result == "WIN")
                    _wr = (_wins / len(_hist)) * 100.0
                    _floor = float(getattr(cfg, "min_score_floor", 5.5))
                    _ceil = float(getattr(cfg, "min_score_ceiling", 9.5))
                    _cur = float(cfg.min_score)
                    _new = _cur
                    if _wr < 55.0 and _cur < _ceil:
                        _new = min(_ceil, round(_cur + 0.3, 2))
                    elif _wr > 65.0 and _cur > _floor:
                        _new = max(_floor, round(_cur - 0.3, 2))
                    if abs(_new - _cur) >= 0.05:
                        self.cfg_manager.update(min_score=_new)
                        await self._notify(
                            f"🎯 <b>Auto-tune min_score</b>\n"
                            f"WR rolling {len(_hist)} trades = {_wr:.1f}% → "
                            f"min_score: {_cur:.2f} → <b>{_new:.2f}</b>"
                        )
            except Exception as _eat:
                logger.debug(f"auto_tune_min_score falhou: {_eat}")

            # 🎯 Self-tuning de scalper_min_score conforme WR rolling
            if getattr(cfg, "scalper_self_tuning", True):
                try:
                    from ..strategies.scalper import SCALPER_RANKING as _SR
                    _summary = _SR.stats_summary()
                    # Pega só chaves de TF (terminam em ":_TF_")
                    _tf_data = [v for k, v in _summary.items() if k.endswith(":_TF_")]
                    if _tf_data:
                        _total_n = sum(d["n"] for d in _tf_data)
                        if _total_n >= 10:
                            _wins = sum(d["wr"] * d["n"] / 100.0 for d in _tf_data)
                            _avg_wr = (_wins / _total_n) * 100.0
                            _floor = cfg.scalper_min_score_floor
                            _ceil = cfg.scalper_min_score_ceiling
                            _cur = cfg.scalper_min_score
                            _new = _cur
                            if _avg_wr < 50.0 and _cur < _ceil:
                                _new = min(_ceil, round(_cur + 0.3, 2))
                            elif _avg_wr >= 65.0 and _cur > _floor:
                                _new = max(_floor, round(_cur - 0.2, 2))
                            if abs(_new - _cur) >= 0.05:
                                self.cfg_manager.update(scalper_min_score=_new)
                                await self._notify(
                                    f"🎯 <b>Auto-tuning Scalper</b>\n"
                                    f"WR rolling: <b>{_avg_wr:.1f}%</b> ({_total_n} trades)\n"
                                    f"Score mínimo: <code>{_cur}</code> → <code>{_new}</code>"
                                )
                except Exception as _e:
                    logger.warning(f"Falha self-tuning scalper: {_e}")

        # Registra feedback para a próxima consulta IA sobre este ativo (rolling 3 entradas)
        _new_feedback = {
            "result": res_for_rank,
            "direction": sig.direction,
            "strategy": sig.strategy,
            "score": round(sig.final_score, 2),
            "ai_confidence_was": ai_conf,
            "ts": _brt_iso(),
        }
        _history = self._last_ai_decisions.get(asset, [])
        _history.append(_new_feedback)
        self._last_ai_decisions[asset] = _history[-3:]  # mantém somente as 3 mais recentes
        self._save_ai_feedback()

        # Processa resultado ANTES de registrar — assim o nível gravado já é o correto
        self.strategies.ranking.register_result(sig.strategy, res_for_rank, profit)
        self.strategies.pair_stats.register_result(asset, sig.strategy, res_for_rank)
        self.martingale.on_result(res_for_rank, profit=profit)
        self.state.martingale_level = self.martingale.state.level  # sincroniza após on_result

        # 🧠 Auto-disable de estratégia normal com WR baixo (não aplica ao scalper)
        if (getattr(cfg, "auto_disable_bad_strategies", True)
                and sig.strategy != "Scalper Sniper"):
            try:
                _strat_obj = next((s for s in self.strategies.strategies if s.name == sig.strategy), None)
                if _strat_obj and _strat_obj.enabled:
                    _info = self.strategies.ranking.stats.get(sig.strategy)
                    if _info:
                        _recent = _info.recent[-cfg.auto_disable_min_trades:]
                        if len(_recent) >= cfg.auto_disable_min_trades:
                            _wins = _recent.count("WIN")
                            _losses = _recent.count("LOSS")
                            _eff = _wins + _losses
                            if _eff >= cfg.auto_disable_min_trades * 0.7:
                                _wr = (_wins / _eff) * 100.0
                                if _wr < cfg.auto_disable_wr_threshold:
                                    _strat_obj.enabled = False
                                    self.strategies._save_states()
                                    await self._notify(
                                        f"🛑 <b>Estratégia desativada automaticamente</b>\n"
                                        f"<b>{sig.strategy}</b> caiu para WR <b>{_wr:.1f}%</b> "
                                        f"nas últimas {_eff} trades (limite: {cfg.auto_disable_wr_threshold}%).\n"
                                        f"Religue manualmente em /menu → Estratégias quando quiser."
                                    )
            except Exception as _e:
                logger.warning(f"Falha auto-disable: {_e}")

        # ── Martingale modo "próxima vela": agenda re-entrada imediata na mesma direção ──
        mg_mode = getattr(cfg.martingale, "mode", "next_signal")
        if (res_for_rank == "LOSS" and cfg.martingale.enabled
                and self.martingale.state.level > 0
                and mg_mode == "next_candle"):
            tf_seconds = TIMEFRAME_SECONDS.get(tf, 60)
            mg_amount = self.martingale.next_amount()
            mg_task = asyncio.create_task(
                self._schedule_martingale_next_candle(
                    asset, tf, sig, mg_amount, ai_conf, payout,
                    market_adx, market_bb_width, tf_seconds,
                ),
                name=f"mg-{asset}-{tf}",
            )
            self._open_tasks.append(mg_task)

        trade = TradeResult(
            timestamp=_brt_iso(),
            asset=asset, direction=sig.direction, amount=amount,
            expiration=expiration, strategy=sig.strategy,
            score=sig.final_score, ai_confidence=ai_conf,
            result=res_for_rank, profit=profit, timeframe=tf,
            martingale_level=self.martingale.state.level,
            market_adx=market_adx,
            market_bb_width=market_bb_width,
        )
        self.state.register_trade(trade)
        self.state.save_history()

        # 📌 Atualiza pin do placar (a cada N trades)
        try:
            await self._update_scoreboard_pin()
        except Exception as _pe:
            logger.debug(f"pin update falhou: {_pe}")

        # win streak
        if res_for_rank == "WIN":
            self._current_win_streak += 1
        else:
            self._current_win_streak = 0

        # placar com sparkline do dia (Msg5)
        placar = self._placar_text()

        if compact:
            # Modo card único (Msg3/Msg4): edita a mesma mensagem com o resultado final.
            ai = card_ctx["ai"]
            streak_val = (
                self._current_win_streak if res_for_rank == "WIN"
                else self.state.current_loss_streak
            )
            card_text = msgs.build_trade_card(
                stage="result",
                ai_operate=ai["operate"],
                ai_confidence=ai["confidence"],
                ai_rationale=ai["rationale"],
                ai_cached=ai["cached"],
                order_amount=amount,
                order_mg_level=self.martingale.state.level,
                result=res_for_rank,
                result_profit=profit,
                saldo_atual=self.state.current_balance,
                placar=placar,
                streak=streak_val,
                **card_ctx["card_args"],
            )
            await self._notify_edit(card_ctx.get("message_id"), card_text)
        else:
            if res_for_rank == "WIN":
                await self._notify(msgs.trade_win(
                    asset=asset, strategy=sig.strategy, tf=tf,
                    profit=profit, placar=placar, streak_wins=self._current_win_streak,
                    saldo_atual=self.state.current_balance,
                ))
            elif res_for_rank == "LOSS":
                await self._notify(msgs.trade_loss(
                    asset=asset, strategy=sig.strategy, tf=tf,
                    profit=profit, placar=placar,
                    streak_losses=self.state.current_loss_streak,
                    saldo_atual=self.state.current_balance,
                ))
            else:
                await self._notify(msgs.trade_draw(
                    asset=asset, tf=tf, placar=placar,
                    saldo_atual=self.state.current_balance,
                ))

        if res_for_rank == "LOSS" and cfg.smart_reentry:
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

        # Filtra apenas OTCs, ordena por payout, respeita min_payout.
        # Pega os top 20 por payout (fixos) + até 6 extras rotacionados dos demais
        # para que o bot não fique preso sempre nos mesmos pares.
        import random as _rnd
        otc_names = filter_open_otc_assets([a["asset"] for a in listing])
        otc_listing = [a for a in listing if a["asset"] in otc_names and a.get("payout", 0) >= cfg.min_payout]
        ranked = sorted(otc_listing, key=lambda a: a.get("payout", 0), reverse=True)
        top20 = [a["asset"] for a in ranked[:20]]
        extras = [a["asset"] for a in ranked[20:]]
        rotation = _rnd.sample(extras, min(6, len(extras))) if extras else []
        result = top20 + rotation

        # 🎯 Scalper whitelist: prioriza top ativos da sessão (melhor WR rolling)
        if cfg.scalper_mode and getattr(cfg, "scalper_session_whitelist", True):
            try:
                top_scalp = self.strategies.pair_stats.top_scalper_assets(
                    n=cfg.scalper_whitelist_size,
                    min_trades=cfg.scalper_whitelist_min_trades,
                )
                whitelist = [asset for asset, _wr, _n in top_scalp if asset in result]
                if whitelist:
                    # Move whitelist pra frente, mantém o resto na ordem original
                    rest = [a for a in result if a not in whitelist]
                    result = whitelist + rest
            except Exception:
                pass
        return result

    # ---------------- utilidades ----------------
    async def _notify(self, text: str) -> None:
        logger.info(text.replace("\n", " | "))
        if self.telegram:
            await self.telegram.send(text)

    # ---------------- 📉 Alerta queda de payout ----------------
    async def _payout_drop_loop(self) -> None:
        """Monitora payout dos top-N ativos e avisa quedas >= threshold pp."""
        await asyncio.sleep(15)
        while self.state.running:
            try:
                cfg = self.cfg_manager.config
                interval = max(15, int(getattr(cfg, "payout_drop_check_seconds", 60)))
                threshold = float(getattr(cfg, "payout_drop_threshold_pp", 5.0))
                top_n = int(getattr(cfg, "payout_drop_top_assets", 5))
                if not self.broker:
                    await asyncio.sleep(interval)
                    continue
                try:
                    listing = await self.broker.get_assets()
                except Exception:
                    listing = []
                from ..utils.otc_schedule import filter_open_otc_assets
                open_otc = set(filter_open_otc_assets([a["asset"] for a in listing]))
                items = [(a["asset"], float(a["payout"])) for a in listing if a["asset"] in open_otc]
                items.sort(key=lambda x: -x[1])
                top = items[:top_n]
                drops = []
                for asset, payout in top:
                    prev = self._payout_baseline.get(asset)
                    if prev is not None and (prev - payout) >= threshold:
                        drops.append((asset, prev, payout))
                    self._payout_baseline[asset] = payout
                top_assets = {a for a, _ in top}
                for a in list(self._payout_baseline):
                    if a not in top_assets:
                        del self._payout_baseline[a]
                if drops:
                    lines = ["📉 <b>Queda de payout detectada</b>"]
                    for asset, prev, cur in drops:
                        lines.append(
                            f"• <b>{asset}</b>: {prev:.0f}% → <b>{cur:.0f}%</b> (-{prev-cur:.0f}pp)"
                        )
                    lines.append("<i>Considere pausar ou trocar de ativo.</i>")
                    await self._notify("\n".join(lines))
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning(f"Payout drop loop erro: {e}")
                await asyncio.sleep(60)

    # ---------------- 📌 Pin automático do placar ----------------
    async def _update_scoreboard_pin(self) -> None:
        """Cria ou edita uma única mensagem fixada com o placar atual."""
        cfg = self.cfg_manager.config
        if not getattr(cfg, "pin_scoreboard_enabled", True):
            return
        if not self.telegram:
            return
        every_n = max(1, int(getattr(cfg, "pin_update_every_n_trades", 1)))
        total = self.state.wins + self.state.losses + self.state.draws
        if total == 0 or (total % every_n) != 0:
            return
        text = (
            "📌 <b>Placar do dia</b>\n"
            f"{self._placar_text()}\n"
            f"💼 Saldo: <b>$ {self.state.current_balance:.2f}</b>"
        )
        if text == self._last_pin_text:
            return
        if self._pin_message_id is None:
            mid = await self.telegram.send(text)
            if mid:
                self._pin_message_id = mid
                try:
                    await self.telegram.pin_message(mid)
                except Exception:
                    pass
        else:
            ok = await self.telegram.edit_message(self._pin_message_id, text)
            if not ok:
                self._pin_message_id = None
                mid = await self.telegram.send(text)
                if mid:
                    self._pin_message_id = mid
                    try:
                        await self.telegram.pin_message(mid)
                    except Exception:
                        pass
        self._last_pin_text = text

    # ---------------- 🌙 Resumo diário 23h59 BRT ----------------
    async def _daily_summary_loop(self) -> None:
        while self.state.running:
            try:
                now = _brt_now()
                target = now.replace(hour=23, minute=59, second=0, microsecond=0)
                if now >= target:
                    target = target + timedelta(days=1)
                wait = (target - now).total_seconds()
                await asyncio.sleep(max(wait, 30))
                if not self.state.running:
                    return
                await self._send_daily_summary_rich()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning(f"Daily summary erro: {e}")
                await asyncio.sleep(120)

    async def _send_daily_summary_rich(self) -> None:
        """Resumo enriquecido (PnL + melhor/pior hora + ativo champion + recomendação)."""
        now = _brt_now()
        today_str = now.strftime("%Y-%m-%d")
        today_trades = [
            t for t in self.state.history
            if t.timestamp.startswith(today_str) and t.result in ("WIN", "LOSS", "DRAW")
        ]
        if not today_trades:
            await self._notify(
                f"🌙 <b>Resumo do dia {now.strftime('%d/%m')}</b>\nSem trades hoje."
            )
            return
        wins = sum(1 for t in today_trades if t.result == "WIN")
        losses = sum(1 for t in today_trades if t.result == "LOSS")
        draws = sum(1 for t in today_trades if t.result == "DRAW")
        pnl = sum(t.profit for t in today_trades)
        wr = (wins / max(wins + losses, 1)) * 100.0
        hour_pnl: dict = {}
        hour_n: dict = {}
        for t in today_trades:
            try:
                h = int(t.timestamp[11:13])
            except Exception:
                continue
            hour_pnl[h] = hour_pnl.get(h, 0.0) + t.profit
            hour_n[h] = hour_n.get(h, 0) + 1
        eligible = [(h, p) for h, p in hour_pnl.items() if hour_n.get(h, 0) >= 2]
        best_hours = sorted(eligible, key=lambda x: -x[1])[:3]
        worst_hours = [h for h, p in sorted(eligible, key=lambda x: x[1])[:3] if p < 0]
        asset_pnl: dict = {}
        for t in today_trades:
            asset_pnl[t.asset] = asset_pnl.get(t.asset, 0.0) + t.profit
        best_asset = max(asset_pnl.items(), key=lambda kv: kv[1]) if asset_pnl else ("—", 0.0)
        worst_asset = min(asset_pnl.items(), key=lambda kv: kv[1]) if asset_pnl else ("—", 0.0)
        rec_lines = []
        if best_hours:
            blocks = ", ".join(f"{h:02d}h" for h, _ in best_hours)
            rec_lines.append(f"✅ Operar: <b>{blocks}</b>")
        if worst_hours:
            blocks = ", ".join(f"{h:02d}h" for h in worst_hours)
            rec_lines.append(f"🚫 Evitar: <b>{blocks}</b>")
        if best_asset[1] > 0:
            rec_lines.append(f"🏆 Foco em: <b>{best_asset[0]}</b> (+${best_asset[1]:.2f})")
        if worst_asset[1] < 0:
            rec_lines.append(f"💀 Cuidado com: <b>{worst_asset[0]}</b> (${worst_asset[1]:.2f})")
        emoji = "🏆" if pnl > 0 else ("📉" if pnl < 0 else "➖")
        msg = (
            f"🌙 <b>Resumo {now.strftime('%d/%m/%Y')}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"{emoji} PnL: <b>$ {pnl:+.2f}</b>\n"
            f"📊 {len(today_trades)} trades · 🟢 {wins}W · 🔴 {losses}L · ⚪ {draws}D\n"
            f"🎯 WR: <b>{wr:.1f}%</b>\n\n"
            f"<b>📍 Recomendação para amanhã</b>\n"
            + ("\n".join(rec_lines) if rec_lines else "<i>Sem dados suficientes.</i>")
        )
        await self._notify(msg)

    async def _notify_send(self, text: str) -> Optional[int]:
        """Envia e devolve o message_id (para uso em cards editáveis — Msg3/4)."""
        logger.info(text.replace("\n", " | "))
        if not self.telegram:
            return None
        return await self.telegram.send(text)

    async def _notify_edit(self, message_id: Optional[int], text: str) -> bool:
        """Edita mensagem anterior. Se falhar, faz fallback para envio novo."""
        if not self.telegram:
            return False
        if message_id is None:
            await self.telegram.send(text)
            return True
        ok = await self.telegram.edit_message(message_id, text)
        if not ok:
            # fallback: envia novo se edição falhou (ex: mensagem apagada)
            await self.telegram.send(text)
        return ok

    def _day_sparkline(self) -> str:
        """Gera sparkline (Msg5) do PnL acumulado dos trades de hoje."""
        today = _brt_now().strftime("%Y-%m-%d")
        profits: List[float] = [
            t.profit for t in self.state.history
            if t.timestamp.startswith(today) and t.result in ("WIN", "LOSS", "DRAW")
        ]
        if len(profits) < 2:
            return ""
        return msgs.equity_sparkline_from_profits(profits, width=16)

    def _placar_text(self) -> str:
        """Placar ao vivo com sparkline incluso (Msg5)."""
        return msgs.placar_ao_vivo(
            wins=self.state.wins,
            losses=self.state.losses,
            draws=self.state.draws,
            winrate=self.state.winrate,
            pnl=self.state.daily_pnl,
            streak_loss=self.state.current_loss_streak,
            mg_level=self.martingale.state.level if self.martingale else 0,
            sparkline=self._day_sparkline(),
        )
