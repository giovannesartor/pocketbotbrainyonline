"""Inline keyboards reutilizáveis."""
from __future__ import annotations

from typing import List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("▶️ Iniciar Bot", callback_data="bot:start"),
         InlineKeyboardButton("⏹️ Parar Bot", callback_data="bot:stop")],
        [InlineKeyboardButton("⚙️ Configurações", callback_data="menu:config"),
         InlineKeyboardButton("🧠 Estratégias", callback_data="menu:strategies")],
        [InlineKeyboardButton("📊 Status", callback_data="menu:status"),
         InlineKeyboardButton("📈 Resultados", callback_data="menu:results")],
        [InlineKeyboardButton("🏆 Ranking", callback_data="menu:ranking"),
         InlineKeyboardButton("⚡ Cache IA", callback_data="menu:ai_cache"),
         InlineKeyboardButton("🗑️ Limpar Cache", callback_data="ai:clear_cache")],
        [InlineKeyboardButton("� Stats", callback_data="menu:stats"),
         InlineKeyboardButton("�🔄 Reconectar", callback_data="bot:reconnect")],
    ]
    return InlineKeyboardMarkup(rows)


def config_menu(is_demo: bool = False) -> InlineKeyboardMarkup:
    demo_label = "🟢 Conta Demo (mudar p/ Real)" if is_demo else "🔴 Conta Real (mudar p/ Demo)"
    rows = [
        [InlineKeyboardButton("💰 Valor de Entrada", callback_data="cfg:entry_amount"),
         InlineKeyboardButton("🛑 Stop Loss", callback_data="cfg:stop_loss")],
        [InlineKeyboardButton("🎯 Stop Win", callback_data="cfg:stop_win"),
         InlineKeyboardButton("🎲 Martingale", callback_data="cfg:martingale")],
        [InlineKeyboardButton("📈 Payout Mínimo", callback_data="cfg:min_payout"),
         InlineKeyboardButton("✅ Assertividade", callback_data="cfg:min_assertiveness")],
        [InlineKeyboardButton("⏱️ Timeframes", callback_data="cfg:timeframes"),
         InlineKeyboardButton("🕹️ Modo de Ativos", callback_data="cfg:asset_mode")],
        [InlineKeyboardButton("🔢 Máx. Abertos Simultâneos", callback_data="cfg:max_open_trades")],
        [InlineKeyboardButton("🔑 SSIDs Pocket Option", callback_data="cfg:ssids")],
        [InlineKeyboardButton("🧠 IA", callback_data="cfg:ai_toggle"),
         InlineKeyboardButton("🧪 Simulação", callback_data="cfg:sim_toggle")],
        [InlineKeyboardButton(demo_label, callback_data="cfg:demo_toggle")],
        [InlineKeyboardButton("⬅️ Voltar", callback_data="menu:main")],
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
    rows = [
        [InlineKeyboardButton(toggle, callback_data="mg:toggle")],
        [InlineKeyboardButton(f"Nível máx.: {mg.max_level}", callback_data="mg:level"),
         InlineKeyboardButton(f"Multiplicador: {mg.multiplier}x", callback_data="mg:mult")],
        [InlineKeyboardButton(reset, callback_data="mg:reset")],
        [InlineKeyboardButton("⬅️ Voltar", callback_data="menu:config")],
    ]
    return InlineKeyboardMarkup(rows)


def back_menu(to: str = "menu:main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Voltar", callback_data=to)]])
