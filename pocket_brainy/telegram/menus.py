"""Inline keyboards reutilizáveis."""
from __future__ import annotations

from typing import List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu(scalper_mode: bool = False) -> InlineKeyboardMarkup:
    scalper_label = "🎯 Scalper ON" if scalper_mode else "🎯 Scalper OFF"
    rows = [
        [InlineKeyboardButton("▶️ Iniciar", callback_data="bot:start"),
         InlineKeyboardButton("⏹️ Parar", callback_data="bot:stop"),
         InlineKeyboardButton(scalper_label, callback_data="bot:scalper_toggle")],
        [InlineKeyboardButton("🎯 Decidir Agora", callback_data="menu:decide"),
         InlineKeyboardButton("📊 Monitorar", callback_data="menu:monitor")],
        [InlineKeyboardButton("⚙️ Configurações", callback_data="menu:config"),
         InlineKeyboardButton("🧠 Estratégias", callback_data="menu:strategies")],
        [InlineKeyboardButton("🔧 Manutenção", callback_data="menu:maintenance"),
         InlineKeyboardButton("❓ Ajuda", callback_data="menu:help")],
    ]
    return InlineKeyboardMarkup(rows)


def decide_menu() -> InlineKeyboardMarkup:
    """Sub-menu: ações para decidir o que operar AGORA."""
    rows = [
        [InlineKeyboardButton("🔥 Top da Hora (/now)", callback_data="exec:now")],
        [InlineKeyboardButton("🕐 WR por Hora", callback_data="exec:timestats"),
         InlineKeyboardButton("🎯 Top Núcleos", callback_data="exec:topcores")],
        [InlineKeyboardButton("📈 Heatmap Visual (PNG)", callback_data="exec:heatmap_visual")],
        [InlineKeyboardButton("🏆 Top Ativos", callback_data="exec:topativos"),
         InlineKeyboardButton("🔬 Backtest", callback_data="exec:backtest")],
        [InlineKeyboardButton("⬅️ Voltar", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(rows)


def monitor_menu() -> InlineKeyboardMarkup:
    """Sub-menu: visualizar estado / resultados."""
    rows = [
        [InlineKeyboardButton("📊 Status atual", callback_data="menu:status"),
         InlineKeyboardButton("📈 Resultados", callback_data="menu:results")],
        [InlineKeyboardButton("🏆 Ranking estratégias", callback_data="menu:ranking"),
         InlineKeyboardButton("📊 Stats", callback_data="menu:stats")],
        [InlineKeyboardButton("🔍 Por que não entrou?", callback_data="exec:why")],
        [InlineKeyboardButton("⬅️ Voltar", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(rows)


def maintenance_menu() -> InlineKeyboardMarkup:
    """Sub-menu: manutenção / cache / conexão."""
    rows = [
        [InlineKeyboardButton("⚡ Cache IA", callback_data="menu:ai_cache"),
         InlineKeyboardButton("🗑️ Limpar Cache", callback_data="ai:clear_cache")],
        [InlineKeyboardButton("🔄 Reconectar", callback_data="bot:reconnect")],
        [InlineKeyboardButton("🔑 SSIDs", callback_data="cfg:ssids"),
         InlineKeyboardButton("🔀 Multi-Conta", callback_data="menu:accounts")],
        [InlineKeyboardButton("⬅️ Voltar", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(rows)


def config_menu(
    is_demo: bool = False,
    smart_reentry: bool = True,
    message_tone: str = "motivacional",
    compact_messages: bool = False,
    explain_mode: bool = False,
) -> InlineKeyboardMarkup:
    demo_label = "🟢 Conta Demo (mudar p/ Real)" if is_demo else "🔴 Conta Real (mudar p/ Demo)"
    reentry_label = "🔁 Reentrada Inteligente: ON" if smart_reentry else "🔁 Reentrada Inteligente: OFF"
    tone_label = (
        "💬 Tom: Motivacional"
        if message_tone != "tecnico"
        else "💬 Tom: Técnico"
    )
    compact_label = (
        "📱 Cards compactos: ON" if compact_messages else "📱 Cards compactos: OFF"
    )
    explain_label = (
        "💡 Modo explicação: ON" if explain_mode else "💡 Modo explicação: OFF"
    )
    rows = [
        [InlineKeyboardButton("💰 Valor de Entrada", callback_data="cfg:entry_amount"),
         InlineKeyboardButton("🛑 Stop Loss", callback_data="cfg:stop_loss")],
        [InlineKeyboardButton("🎯 Stop Win", callback_data="cfg:stop_win"),
         InlineKeyboardButton("🎲 Martingale", callback_data="cfg:martingale")],
        [InlineKeyboardButton("📈 Payout Mínimo", callback_data="cfg:min_payout"),
         InlineKeyboardButton("✅ Assertividade", callback_data="cfg:min_assertiveness")],
        [InlineKeyboardButton("🎯 Score Mínimo", callback_data="cfg:min_score"),
         InlineKeyboardButton(reentry_label, callback_data="cfg:smart_reentry_toggle")],
        [InlineKeyboardButton("⏱️ Timeframes", callback_data="cfg:timeframes"),
         InlineKeyboardButton("🕹️ Modo de Ativos", callback_data="cfg:asset_mode")],
        [InlineKeyboardButton("🔢 Máx. Abertos Simultâneos", callback_data="cfg:max_open_trades")],
        [InlineKeyboardButton("� Máx. Trades/Dia", callback_data="cfg:max_trades_day"),
         InlineKeyboardButton("🔥 Streak Loss Máx", callback_data="cfg:max_loss_streak")],
        [InlineKeyboardButton("�🔑 SSIDs Pocket Option", callback_data="cfg:ssids")],
        [InlineKeyboardButton("🔀 Multi-Conta (slots)", callback_data="menu:accounts")],
        [InlineKeyboardButton("🧠 IA", callback_data="cfg:ai_toggle")],
        [InlineKeyboardButton(tone_label, callback_data="cfg:tone_toggle"),
         InlineKeyboardButton(compact_label, callback_data="cfg:compact_toggle")],
        [InlineKeyboardButton(explain_label, callback_data="cfg:explain_toggle")],
        [InlineKeyboardButton(demo_label, callback_data="cfg:demo_toggle")],
        [InlineKeyboardButton("⬅️ Voltar", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(rows)


def accounts_menu(has_real: bool, has_demo: bool, current_demo: bool) -> InlineKeyboardMarkup:
    """Menu de multi-conta — 2 slots (REAL + DEMO) com toggle rápido."""
    cur_label = "DEMO 🟢" if current_demo else "REAL 🔴"
    real_status = "✅ salvo" if has_real else "⬜ vazio"
    demo_status = "✅ salvo" if has_demo else "⬜ vazio"
    rows = [
        [InlineKeyboardButton(f"📍 Atual: {cur_label}", callback_data="acct:noop")],
        [InlineKeyboardButton(f"💾 Salvar SSID atual como REAL ({real_status})", callback_data="acct:save_real")],
        [InlineKeyboardButton(f"💾 Salvar SSID atual como DEMO ({demo_status})", callback_data="acct:save_demo")],
        [InlineKeyboardButton("🔴 Carregar conta REAL", callback_data="acct:load_real"),
         InlineKeyboardButton("🟢 Carregar conta DEMO", callback_data="acct:load_demo")],
        [InlineKeyboardButton("⬅️ Voltar", callback_data="menu:config")],
    ]
    return InlineKeyboardMarkup(rows)


def strategies_menu(statuses: List[dict]) -> InlineKeyboardMarkup:
    rows = []
    for st in statuses:
        icon = "✅" if st["enabled"] else "❌"
        rows.append([InlineKeyboardButton(f"{icon} {st['name']}", callback_data=f"strat:toggle:{st['name']}")])
    rows.append([InlineKeyboardButton("⬅️ Voltar", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def timeframes_menu(selected: List[str]) -> InlineKeyboardMarkup:
    all_tfs = ["M1", "M5", "M15"]
    row = [InlineKeyboardButton(f"{'✅' if tf in selected else '⬜'} {tf}", callback_data=f"tf:toggle:{tf}") for tf in all_tfs]
    return InlineKeyboardMarkup([row, [InlineKeyboardButton("⬅️ Voltar", callback_data="menu:config")]])


def asset_mode_menu(current: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(("✅ " if current == "auto" else "") + "Automático (maior payout)",
                              callback_data="am:set:auto")],
        [InlineKeyboardButton(("✅ " if current == "manual" else "") + "Manual (lista)",
                              callback_data="am:set:manual")],
        [InlineKeyboardButton("✏️ Editar lista manual", callback_data="am:edit")],
        [InlineKeyboardButton("⬅️ Voltar", callback_data="menu:config")],
    ]
    return InlineKeyboardMarkup(rows)


def martingale_menu(mg) -> InlineKeyboardMarkup:
    toggle = "✅ Ligado" if mg.enabled else "❌ Desligado"
    reset = "✅ Reset c/ Win" if mg.reset_after_win else "❌ Reset c/ Win"
    mode_label = "🕒 Modo: Próximo SINAL" if getattr(mg, "mode", "next_signal") == "next_signal" else "⚡ Modo: Próxima VELA"
    rows = [
        [InlineKeyboardButton(toggle, callback_data="mg:toggle")],
        [InlineKeyboardButton(f"Nível máx.: {mg.max_level}", callback_data="mg:level"),
         InlineKeyboardButton(f"Multiplicador: {mg.multiplier}x", callback_data="mg:mult")],
        [InlineKeyboardButton(mode_label, callback_data="mg:mode")],
        [InlineKeyboardButton(reset, callback_data="mg:reset")],
        [InlineKeyboardButton("⬅️ Voltar", callback_data="menu:config")],
    ]
    return InlineKeyboardMarkup(rows)


def back_menu(to: str = "menu:main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Voltar", callback_data=to)]])
