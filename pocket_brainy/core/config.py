"""Persistência e gestão de configurações em JSON."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils.logger import get_logger

logger = get_logger("core.config")

# Permite redirecionar o diretório de dados (útil em Railway/containers com
# volume montado, p.ex. POCKET_DATA_DIR=/data).
_default_data_dir = Path(__file__).resolve().parent.parent / "data"
DATA_DIR = Path(os.environ.get("POCKET_DATA_DIR", str(_default_data_dir)))
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = DATA_DIR / "config.json"

# Bootstrap: se o config.json não existir no DATA_DIR mas a variável
# POCKET_CONFIG_JSON estiver definida (JSON completo), grava no disco.
_bootstrap_json = os.environ.get("POCKET_CONFIG_JSON")
if _bootstrap_json and not CONFIG_PATH.exists():
    try:
        # valida o JSON antes de escrever
        json.loads(_bootstrap_json)
        CONFIG_PATH.write_text(_bootstrap_json, encoding="utf-8")
        logger.info(f"config.json criado a partir de POCKET_CONFIG_JSON em {CONFIG_PATH}")
    except Exception as e:
        logger.error(f"POCKET_CONFIG_JSON inválido: {e}")


@dataclass
class MartingaleConfig:
    enabled: bool = False
    max_level: int = 2
    multiplier: float = 2.2
    reset_after_win: bool = True
    mode: str = "next_signal"  # "next_signal" | "next_candle"


@dataclass
class BotConfig:
    # Credenciais Pocket Option
    po_email: str = ""
    po_password: str = ""
    po_demo: bool = True  # True = conta demo
    po_ssids: List[str] = field(default_factory=list)  # SSIDs manuais (fallback sequencial)

    # Telegram
    telegram_token: str = ""
    telegram_chat_id: str = ""
    # IDs adicionais que recebem TODAS as notificações em modo "observador"
    # (somente leitura — não podem controlar o bot via comandos/botões).
    telegram_observer_chat_ids: List[str] = field(default_factory=list)

    # Gestão
    entry_amount: float = 2.0
    stop_win: float = 25.0         # $ ou %
    stop_win_is_percent: bool = False
    stop_loss: float = 15.0
    stop_loss_is_percent: bool = False
    max_trades_per_day: int = 50
    max_loss_streak: int = 3
    delay_between_trades: int = 5   # segundos
    max_open_trades: int = 3        # trades simultâneos abertos no máximo

    # Estratégia / sinal
    min_assertiveness: float = 65.0   # %
    min_payout: float = 80.0          # %
    timeframes: List[str] = field(default_factory=lambda: ["M1", "M5"])

    # Ativos
    asset_mode: str = "auto"         # auto | manual
    manual_assets: List[str] = field(default_factory=lambda: ["EURUSD-OTC"])

    # Martingale
    martingale: MartingaleConfig = field(default_factory=MartingaleConfig)

    # Sinal
    min_score: float = 5.0         # score mínimo para considerar um sinal

    # Reentrada inteligente
    smart_reentry: bool = True     # após LOSS, notifica aguardando novo sinal

    # IA
    ai_enabled: bool = True
    deepseek_api_key: str = ""   # chave DeepSeek (alternativa à var DEEPSEEK_API_KEY)

    # UI / Mensagens
    message_tone: str = "motivacional"   # "motivacional" | "tecnico"
    compact_messages: bool = False        # True = card único editado por trade (menos poluição no chat)

    # 🎯 SCALPER MODE — modo cirúrgico em timeframes ultra-curtos
    scalper_mode: bool = False           # True = só ScalperStrategy, sem IA, TFs próprios
    scalper_timeframes: List[str] = field(default_factory=lambda: ["S10", "S30", "M1"])
    scalper_min_score: float = 7.0       # threshold próprio (mais rigoroso)
    scalper_min_confidence: float = 75.0
    scalper_cooldown_seconds: int = 20   # cooldown entre entradas no mesmo ativo
    scalper_max_loss_streak: int = 5     # 3 losses seguidos → desliga scalper

    # 🎯 SCALPER — auto-tuning de score baseado em WR rolling
    scalper_self_tuning: bool = True
    scalper_min_score_floor: float = 6.5
    scalper_min_score_ceiling: float = 8.5

    # 🔬 SCALPER — filtro estrito para TFs micro (S5/S10): só passa "100% certeiro"
    scalper_micro_strict_enabled: bool = True
    scalper_micro_strict_tfs: List[str] = field(default_factory=lambda: ["S5", "S10"])
    scalper_micro_strict_min_score: float = 8.5
    scalper_micro_strict_require_tf: bool = True
    scalper_micro_strict_min_cores: int = 3

    # 🎯 SCALPER — whitelist de top ativos da sessão
    scalper_session_whitelist: bool = True
    scalper_whitelist_size: int = 5
    scalper_whitelist_min_trades: int = 3  # mínimo de trades pra entrar na whitelist

    # 🔄 A1 — Auto-resume após stop-loss/win/streak diário (ao virar de dia BRT)
    auto_resume_next_day: bool = True

    # 🌙 A2 — Pausa hora tóxica (WR<40% nas últimas N trades da hora atual)
    toxic_hour_pause_enabled: bool = True
    toxic_hour_min_trades: int = 15
    toxic_hour_wr_threshold: float = 40.0

    # 💱 B5 — Auto-disable de ativo com 4 losses seguidos no dia
    asset_blacklist_enabled: bool = True
    asset_blacklist_loss_streak: int = 4

    # 🔗 B6 — Exigir confluência forte fora do horário ouro
    confluence_off_hours_enabled: bool = True
    confluence_off_hours_wr: float = 55.0   # abaixo disso vira "off-hour"
    confluence_off_hours_min_cores: int = 2  # exige >=N núcleos
    confluence_off_hours_require_tf: bool = True  # exige multi-TF confluence

    # 📊 C7 — Stake adaptativo por WR da hora atual (multiplicador no entry_amount)
    adaptive_stake_enabled: bool = True
    adaptive_stake_low_wr: float = 50.0       # WR < => penaliza
    adaptive_stake_high_wr: float = 65.0      # WR >= => premia
    adaptive_stake_low_mult: float = 0.6      # 60% do entry_amount em hora ruim
    adaptive_stake_high_mult: float = 1.3     # 130% em hora top
    adaptive_stake_min_dollar: float = 1.0    # piso absoluto da Pocket Option

    # 🧠 Auto-disable de estratégias normais com WR < 35% nas últimas N trades
    auto_disable_bad_strategies: bool = True
    auto_disable_min_trades: int = 30
    auto_disable_wr_threshold: float = 35.0

    # 💰 Soros (anti-martingale): após WIN, próxima entrada usa parte do lucro
    soros_enabled: bool = False
    soros_pct: float = 50.0    # % do lucro a reinvestir (50% = metade do lucro)
    soros_max_levels: int = 2  # máx encadeamentos consecutivos antes de resetar

    # 🎲 Martingale inteligente: só faz gale se score do novo sinal >= score_anterior * X
    smart_gale: bool = True
    smart_gale_score_ratio: float = 1.5

    # 💎 Kelly fracionado (sizing dinâmico): stake = bankroll * fraction * edge
    kelly_enabled: bool = False
    kelly_fraction: float = 0.25       # 1/4 Kelly (mais conservador que full Kelly)
    kelly_min_trades: int = 20         # exige amostra mínima antes de aplicar
    kelly_max_multiplier: float = 3.0  # cap em N× entry_amount (segurança)
    kelly_min_multiplier: float = 0.5  # piso em N× entry_amount

    # 🔬 Backtest contínuo em background (ranking baseado em simulação histórica)
    backtest_enabled: bool = True
    backtest_interval_minutes: int = 60
    backtest_candles: int = 1000
    backtest_max_pairs: int = 30

    # ⏱️ Sinal forte estende expiração (M1 score≥10 → M2; ≥11.5 → M3)
    strong_signal_extend_expiration: bool = True

    # ♻️ Hot reload: monitora config.json e aplica mudanças sem reiniciar
    hot_reload_config: bool = True
    hot_reload_interval_seconds: int = 5

    # 🛡️ Watchdog do broker: reinicia conexão se desconectar N vezes em janela
    broker_watchdog_enabled: bool = True
    broker_watchdog_max_disconnects: int = 3
    broker_watchdog_window_seconds: int = 300

    # 🔑 SSID renewal preventivo (em vez de esperar erro)
    ssid_preventive_renewal: bool = True
    ssid_renewal_interval_minutes: int = 90  # renova a cada N minutos

    # 👥 Multi-account: 2 slots independentes (real + demo) com toggle rápido
    account_slot_real_ssid: str = ""
    account_slot_demo_ssid: str = ""

    # 📉 Alerta de queda de payout — monitora top-N ativos e avisa quedas
    payout_drop_alert_enabled: bool = True
    payout_drop_check_seconds: int = 60
    payout_drop_threshold_pp: float = 5.0   # queda em pontos percentuais
    payout_drop_top_assets: int = 5

    # 📌 Pin automático do placar — atualiza/edita 1 mensagem fixada
    pin_scoreboard_enabled: bool = True
    pin_update_every_n_trades: int = 1

    # 🌙 Resumo diário às 23h59 BRT
    daily_summary_enabled: bool = True

    # 💡 Modo "explicação" — adiciona linha "Por quê" no card de sinal
    explain_mode: bool = False

    # 🛡️ Spread/slippage filter — aborta ordem se preço ao vivo divergiu do
    # close do sinal em mais de N × ATR (broker com delay/reprecificação).
    spread_filter_enabled: bool = True
    spread_atr_mult: float = 2.0

    # 🎯 Auto-tuning de min_score (NÃO scalper) — ajusta global min_score
    # baseado no WR rolling das últimas N trades.
    auto_tune_min_score: bool = True
    auto_tune_min_trades: int = 20
    auto_tune_window: int = 50
    min_score_floor: float = 5.5
    min_score_ceiling: float = 9.5

    # 🌡️ Detecção de regime + filtro estratégia/regime
    regime_filter_enabled: bool = True
    regime_min_trades: int = 20         # exige amostra mín antes de filtrar
    regime_min_wr: float = 45.0         # WR mínimo aceito por (estratégia, regime)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BotConfig":
        mart = data.pop("martingale", {}) if isinstance(data.get("martingale"), dict) else {}
        cfg = cls(**{k: v for k, v in data.items() if k in cls.__annotations__})
        cfg.martingale = MartingaleConfig(**{k: v for k, v in mart.items() if k in MartingaleConfig.__annotations__})
        return cfg

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


class ConfigManager:
    """Gerencia leitura/escrita atômica do config.json."""

    def __init__(self, path: Path = CONFIG_PATH):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._last_internal_mtime: float = 0.0
        self._config: BotConfig = self._load()
        try:
            self._last_internal_mtime = self.path.stat().st_mtime
        except Exception:
            pass

    # ------- IO -------
    def _load(self) -> BotConfig:
        if not self.path.exists():
            cfg = BotConfig()
            self._save(cfg)
        else:
            try:
                with self.path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                cfg = BotConfig.from_dict(data)
            except Exception as e:
                logger.error(f"Falha ao carregar config: {e}. Usando defaults.")
                cfg = BotConfig()
        # Variáveis de ambiente têm prioridade sobre config.json (Railway deploy)
        self._apply_env_overrides(cfg)
        return cfg

    @staticmethod
    def _apply_env_overrides(cfg: BotConfig) -> None:
        """Sobrescreve campos com variáveis de ambiente, se definidas."""
        _str = os.environ.get
        if _str("PO_EMAIL"):
            cfg.po_email = _str("PO_EMAIL")
        if _str("PO_PASSWORD"):
            cfg.po_password = _str("PO_PASSWORD")
        if _str("TELEGRAM_TOKEN"):
            cfg.telegram_token = _str("TELEGRAM_TOKEN")
        if _str("TELEGRAM_CHAT_ID"):
            cfg.telegram_chat_id = _str("TELEGRAM_CHAT_ID")
        if _str("TELEGRAM_OBSERVER_CHAT_IDS"):
            cfg.telegram_observer_chat_ids = [
                s.strip() for s in _str("TELEGRAM_OBSERVER_CHAT_IDS").split(",") if s.strip()
            ]
        if _str("PO_DEMO") is not None:
            cfg.po_demo = _str("PO_DEMO", "true").lower() in ("1", "true", "yes")
        if _str("PO_SSIDS"):
            # aceita múltiplos SSIDs separados por vírgula
            cfg.po_ssids = [s.strip() for s in _str("PO_SSIDS").split(",") if s.strip()]

    def _save(self, cfg: BotConfig) -> None:
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(cfg.to_dict(), f, indent=2, ensure_ascii=False)
        tmp.replace(self.path)
        # Marca mtime após save interno (usado pelo hot reload pra não re-triggerar)
        try:
            self._last_internal_mtime = self.path.stat().st_mtime
        except Exception:
            self._last_internal_mtime = 0.0

    # ------- API pública -------
    @property
    def config(self) -> BotConfig:
        return self._config

    def update(self, **kwargs: Any) -> BotConfig:
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self._config, k):
                    setattr(self._config, k, v)
            self._save(self._config)
            return self._config

    def update_martingale(self, **kwargs: Any) -> BotConfig:
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self._config.martingale, k):
                    setattr(self._config.martingale, k, v)
            self._save(self._config)
            return self._config

    def reload(self) -> BotConfig:
        with self._lock:
            self._config = self._load()
            return self._config
