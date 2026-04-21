"""
Filtro de disponibilidade de ativos OTC da Pocket Option por horário.

A Pocket Option opera ativos OTC em janelas horárias.
Esta tabela é baseada no comportamento observado da plataforma (UTC-3 / horário Brasília).
Pode ser refinada conforme experiência real.

Regra geral dos OTCs na PocketOption:
  - Semana: ativos OTC ficam disponíveis quase 24h, mas o payout cai nos horários
    de sobreposição com o mercado real (09:00–17:30 BRT em dias úteis).
  - Fins de semana: todos os OTC ficam disponíveis com payout máximo.

Lógica implementada:
  - Se for fim de semana → todos disponíveis (máxima liquidez OTC).
  - Se for dia útil entre 09:00–17:30 BRT → mercado real aberto, OTC pode
    ter payout reduzido mas segue disponível; usamos a lista da lib.
  - Fora dessas janelas → OTC disponível normalmente.

O filtro principal (`is_otc_session_open`) sinaliza se é boa hora para OTC.
O filtro `filter_open_otc_assets` filtra um lista de nomes de ativos.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import List

# Fuso Brasília (UTC-3)
_BRT = timezone(timedelta(hours=-3))

# Ativos OTC conhecidos na Pocket Option — lista de referência
# A lib retorna os nomes sem sufixo "-OTC", com "_otc" minúsculo.
# Mantemos os dois formatos para compatibilidade.
_ALL_OTC_ASSETS: List[str] = [
    "EURUSD_otc", "GBPUSD_otc", "USDJPY_otc", "USDCHF_otc",
    "USDCAD_otc", "AUDUSD_otc", "NZDUSD_otc", "EURJPY_otc",
    "EURGBP_otc", "AUDJPY_otc", "GBPJPY_otc", "CHFJPY_otc",
    "XAUUSD_otc", "AUDCAD_otc", "CADCHF_otc", "EURCAD_otc",
    "AUDCHF_otc", "EURNZD_otc", "GBPAUD_otc", "GBPCAD_otc",
    "GBPCHF_otc", "NZDCAD_otc", "NZDCHF_otc", "NZDJPY_otc",
    # formatos alternativos com hífen (usado em modo manual)
    "EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC", "XAUUSD-OTC",
    "AUDCAD-OTC", "EURJPY-OTC", "GBPJPY-OTC",
]


def _now_brt() -> datetime:
    return datetime.now(tz=_BRT)


def is_otc_asset(name: str) -> bool:
    """Retorna True se o nome do ativo é um OTC (contém 'otc' case-insensitive)."""
    return "otc" in name.lower()


def is_weekend() -> bool:
    """True se for sábado ou domingo (horário Brasília)."""
    return _now_brt().weekday() >= 5  # 5=sábado, 6=domingo


def is_real_market_hours() -> bool:
    """
    True se o mercado real (forex spot) estiver aberto agora.
    Janela conservadora: seg–sex 08:00–18:00 BRT.
    """
    now = _now_brt()
    if now.weekday() >= 5:
        return False
    return 8 <= now.hour < 18


def is_good_otc_window() -> bool:
    """
    True quando é uma boa janela para operar OTC:
    - Fim de semana (mercado real fechado → OTC dominante)
    - Ou fora do horário principal do mercado real
    """
    return is_weekend() or not is_real_market_hours()


def filter_open_otc_assets(assets: List[str]) -> List[str]:
    """
    Dado um lista de nomes de ativos, retorna apenas os OTCs.
    Se a lista estiver vazia, retorna a lista de OTCs padrão.
    Em dias úteis, aplica prioridade extra para pares com maior liquidez OTC.
    """
    otc = [a for a in assets if is_otc_asset(a)]
    if not otc:
        return _ALL_OTC_ASSETS[:10]  # fallback: top 10 OTCs conhecidos

    if is_weekend():
        # fim de semana → todos disponíveis
        return otc

    # Dia útil: prioriza pares principais (mais estáveis em OTC)
    priority = {"EUR", "GBP", "USD", "JPY", "XAU"}
    def _priority_score(name: str) -> int:
        upper = name.upper()
        return sum(1 for p in priority if p in upper)

    return sorted(otc, key=_priority_score, reverse=True)


def otc_session_label() -> str:
    """Retorna label descritivo da sessão atual para mensagens."""
    now = _now_brt()
    hour = now.hour
    if is_weekend():
        return "🌙 Fim de semana (OTC máximo)"
    if 6 <= hour < 12:
        return "🌅 Manhã BRT"
    if 12 <= hour < 18:
        return "☀️ Tarde BRT (mercado real ativo)"
    return "🌙 Noite BRT (OTC favorável)"
