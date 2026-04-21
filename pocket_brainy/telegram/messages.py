"""
Templates de mensagens personalizadas do Pocket Brainy.

Cada função retorna uma string HTML pronta para Telegram (parse_mode='HTML').
Frases têm variações (random.choice) para não ficarem monótonas.
"""
from __future__ import annotations

import random
from datetime import datetime
from typing import Any, Dict, List, Optional


# ------------- utilidades -------------
def _fmt_money(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"$ {sign}{v:.2f}"


def _hoje() -> str:
    return datetime.now().strftime("%d/%m/%Y %H:%M")


def _rand(*options: str) -> str:
    return random.choice(options)


# ------------- ciclo de vida do bot -------------
def bot_iniciado(simulacao: bool, timeframes: List[str], ia: bool,
                 saldo: float, ativos_modo: str,
                 conta_demo: bool = False, otc_count: int = 0,
                 avg_payout: float = 0.0, sessao: str = "") -> str:
    modo = "🧪 SIMULAÇÃO" if simulacao else "💰 CONTA REAL"
    conta = "🟢 Demo" if conta_demo else "🔴 Real"
    saudacao = _rand(
        "🚀 <b>Pocket Brainy ligado!</b>",
        "🧠 <b>Bot iniciado — pronto pra caçar sinais!</b>",
        "⚡ <b>Pocket Brainy em operação.</b>",
        "🎯 <b>Bot online e afiado.</b>",
    )
    payout_line = f"📊 Payout médio OTC: <b>{avg_payout:.1f}%</b>\n" if avg_payout > 0 else ""
    otc_line = f"🔢 Ativos OTC disponíveis: <b>{otc_count}</b>\n" if otc_count > 0 else ""
    sessao_line = f"🕐 Sessão: <b>{sessao}</b>\n" if sessao else ""
    return (
        f"{saudacao}\n\n"
        f"📅 Início: <code>{_hoje()}</code>\n"
        f"{modo} | Conta: {conta}\n"
        f"💵 Saldo inicial: <b>$ {saldo:.2f}</b>\n"
        f"{sessao_line}"
        f"{otc_line}"
        f"{payout_line}"
        f"⏱️ Timeframes: <b>{', '.join(timeframes)}</b>\n"
        f"🧠 IA DeepSeek: <b>{'ATIVA' if ia else 'INATIVA'}</b>\n"
        f"🎯 Ativos: <b>{ativos_modo}</b>\n\n"
        f"<i>Boa sorte — bom trading! 🍀</i>"
    )


def bot_parado(pnl: float, wins: int, losses: int, winrate: float, trades: int) -> str:
    motivo = _rand(
        "Encerramento solicitado.",
        "Sessão finalizada manualmente.",
        "Bot desligado pelo usuário.",
    )
    emoji_final = "🎉" if pnl > 0 else ("😐" if pnl == 0 else "💪")
    return (
        f"⏹️ <b>Pocket Brainy parado.</b>\n"
        f"<i>{motivo}</i>\n\n"
        f"📊 <b>Sessão encerrada</b>\n"
        f"• Trades: <b>{trades}</b>\n"
        f"• Wins: <b>{wins}</b> | Losses: <b>{losses}</b>\n"
        f"• Winrate: <b>{winrate:.1f}%</b>\n"
        f"• PnL: <b>{_fmt_money(pnl)}</b> {emoji_final}\n\n"
        f"<i>Até a próxima! 👋</i>"
    )


def bot_reconectado(saldo: float) -> str:
    return (
        f"🔄 <b>Reconectado ao Pocket Option.</b>\n"
        f"💵 Saldo atual: <b>$ {saldo:.2f}</b>"
    )


def bot_erro_conexao(erro: str) -> str:
    return (
        f"⚠️ <b>Falha de conexão.</b>\n"
        f"<code>{erro}</code>\n"
        f"<i>Tentaremos reconectar automaticamente.</i>"
    )


# ------------- análise e sinal -------------
def sinal_detectado(asset: str, tf: str, strategy: str, direction: str,
                    score: float, confidence: float, payout: float,
                    notes: str = "") -> str:
    arrow = "🟢⬆️ CALL" if direction == "CALL" else "🔴⬇️ PUT"
    hype = _rand("🔎 Sinal identificado!", "🎯 Setup encontrado!",
                 "📡 Sinal detectado!", "🧲 Oportunidade pinçada!")
    notes_line = f"\n<i>📌 {notes}</i>" if notes else ""
    return (
        f"{hype}\n\n"
        f"💱 Ativo: <b>{asset}</b>\n"
        f"⏱️ Timeframe: <b>{tf}</b>\n"
        f"🧠 Estratégia: <b>{strategy}</b>\n"
        f"📈 Direção: {arrow}\n"
        f"🎯 Score final: <b>{score:.2f}</b>\n"
        f"✅ Confiança: <b>{confidence:.1f}%</b>\n"
        f"💰 Payout: <b>{payout:.1f}%</b>{notes_line}"
    )


def ia_validando(operar: bool, confianca: float, rationale: str, cached: bool = False) -> str:
    badge = "🧠 IA DeepSeek"
    if cached:
        badge += " ⚡(cache)"
    if operar:
        head = _rand("✅ Sinal aprovado pela IA!", "🟢 IA confirmou — podemos entrar!",
                     "🎯 IA: OPERAR.")
    else:
        head = _rand("🛑 IA vetou esta entrada.", "⛔ IA recomendou IGNORAR.",
                     "🔕 IA bloqueou o sinal.")
    return (
        f"{badge}\n"
        f"{head}\n"
        f"• Decisão: <b>{'OPERAR' if operar else 'IGNORAR'}</b>\n"
        f"• Confiança: <b>{confianca:.0f}%</b>\n"
        f"• Justificativa: <i>{rationale}</i>"
    )


def ordem_enviada(asset: str, direction: str, amount: float, tf: str, mg_level: int = 0) -> str:
    arrow = "🟢 CALL" if direction == "CALL" else "🔴 PUT"
    mg_txt = f" | MG nv.{mg_level}" if mg_level > 0 else ""
    return (
        f"📤 <b>Ordem enviada.</b>\n"
        f"{arrow} | <b>{asset}</b> | {tf} | {_fmt_money(amount).replace('+', '')}{mg_txt}\n"
        f"<i>Aguardando expiração…</i>"
    )


# ------------- resultados -------------
def trade_win(asset: str, strategy: str, tf: str, profit: float,
              placar: str, streak_wins: int = 0) -> str:
    frases = [
        "🎉 <b>WIN!</b> Deu bom!",
        "🟢 <b>VITÓRIA!</b> Lucro garantido!",
        "💚 <b>WIN!</b> Cirúrgico!",
        "🚀 <b>WIN!</b> No alvo!",
        "✨ <b>WIN!</b> Show de bola!",
    ]
    streak_txt = f" 🔥 <i>{streak_wins} wins seguidos!</i>\n" if streak_wins >= 3 else ""
    return (
        f"{random.choice(frases)}\n"
        f"{streak_txt}"
        f"💱 {asset} | {tf} | {strategy}\n"
        f"💰 Lucro: <b>{_fmt_money(profit)}</b>\n"
        f"{placar}"
    )


def trade_loss(asset: str, strategy: str, tf: str, profit: float,
               placar: str, streak_losses: int = 0) -> str:
    frases_leve = [
        "🔴 <b>LOSS.</b> Segue o jogo.",
        "😐 <b>LOSS.</b> Faz parte.",
        "📉 <b>LOSS.</b> Próxima!",
    ]
    frases_alerta = [
        "⚠️ <b>LOSS.</b> Atenção ao streak!",
        "🚨 <b>LOSS.</b> Reavaliando filtros…",
    ]
    frase = random.choice(frases_alerta if streak_losses >= 2 else frases_leve)
    streak_txt = f"\n⚠️ <i>Streak de {streak_losses} losses — cuidado!</i>" if streak_losses >= 2 else ""
    return (
        f"{frase}\n"
        f"💱 {asset} | {tf} | {strategy}\n"
        f"💸 Prejuízo: <b>{_fmt_money(profit)}</b>\n"
        f"{placar}{streak_txt}"
    )


def trade_draw(asset: str, tf: str, placar: str) -> str:
    return (
        f"⚪ <b>DRAW</b> (empate)\n"
        f"💱 {asset} | {tf}\n"
        f"<i>Valor devolvido — nada ganho, nada perdido.</i>\n"
        f"{placar}"
    )


def trade_simulado(asset: str, strategy: str, tf: str, direction: str,
                   profit: float, placar: str) -> str:
    outcome = "🟢 WIN" if profit > 0 else "🔴 LOSS"
    return (
        f"🧪 <b>Operação simulada</b>\n"
        f"{outcome} | 💱 {asset} {direction} {tf} | {strategy}\n"
        f"💰 Resultado: <b>{_fmt_money(profit)}</b>\n"
        f"{placar}"
    )


# ------------- placar & resumo -------------
def placar_ao_vivo(wins: int, losses: int, draws: int, winrate: float,
                   pnl: float, streak_loss: int, mg_level: int) -> str:
    emoji_pnl = "📈" if pnl > 0 else ("📉" if pnl < 0 else "➖")
    mg_txt = f" | MG nv.{mg_level}" if mg_level > 0 else ""
    warn = " 🚨" if streak_loss >= 2 else ""
    return (
        f"📊 <b>Placar ao vivo</b>\n"
        f"🟢 {wins}W | 🔴 {losses}L | ⚪ {draws}D | "
        f"🎯 {winrate:.1f}% | {emoji_pnl} {_fmt_money(pnl)}{mg_txt}{warn}"
    )


def resumo_diario(data: str, wins: int, losses: int, draws: int,
                  winrate: float, pnl: float, trades: int,
                  melhor_estrategia: Optional[str] = None) -> str:
    emoji = "🏆" if pnl > 0 else ("📊" if pnl == 0 else "📉")
    corpo = (
        f"{emoji} <b>Resumo da sessão — {data}</b>\n\n"
        f"• Trades executados: <b>{trades}</b>\n"
        f"• 🟢 Wins: <b>{wins}</b>\n"
        f"• 🔴 Losses: <b>{losses}</b>\n"
        f"• ⚪ Draws: <b>{draws}</b>\n"
        f"• 🎯 Winrate: <b>{winrate:.1f}%</b>\n"
        f"• 💰 PnL total: <b>{_fmt_money(pnl)}</b>\n"
    )
    if melhor_estrategia:
        corpo += f"• 🥇 Melhor estratégia: <b>{melhor_estrategia}</b>\n"
    if pnl > 0:
        corpo += "\n<i>🎉 Sessão no verde — excelente trabalho!</i>"
    elif pnl < 0:
        corpo += "\n<i>💪 Amanhã é outro dia. Mantenha a disciplina.</i>"
    else:
        corpo += "\n<i>➖ Sessão neutra.</i>"
    return corpo


# ------------- gestão de risco -------------
def stop_win_atingido(pnl: float, trades: int) -> str:
    return (
        f"🎯 <b>STOP WIN ATINGIDO!</b> 🎉\n"
        f"💰 Lucro do dia: <b>{_fmt_money(pnl)}</b>\n"
        f"📊 Trades: <b>{trades}</b>\n\n"
        f"<i>Meta batida. Bot pausado automaticamente — trave os ganhos!</i>"
    )


def stop_loss_atingido(pnl: float, trades: int) -> str:
    return (
        f"🛑 <b>STOP LOSS ATINGIDO.</b>\n"
        f"💸 Prejuízo do dia: <b>{_fmt_money(pnl)}</b>\n"
        f"📊 Trades: <b>{trades}</b>\n\n"
        f"<i>Proteção ativada — bot pausado. Respire, revise e volte amanhã.</i>"
    )


def streak_loss_atingido(streak: int) -> str:
    return (
        f"🚨 <b>Streak de loss atingido ({streak} seguidos).</b>\n"
        f"<i>Bot pausado para evitar tilt. Revise os filtros antes de religar.</i>"
    )


def max_trades_atingido(n: int) -> str:
    return (
        f"🚦 <b>Limite diário de {n} trades atingido.</b>\n"
        f"<i>Bot pausado — volte amanhã fresco!</i>"
    )


def delay_anti_overtrading(restante: float) -> str:
    return (
        f"⏳ <i>Anti-overtrading: aguardando {restante:.0f}s para o próximo sinal…</i>"
    )


# ------------- reentrada inteligente -------------
def reentrada_inteligente() -> str:
    return (
        f"🔁 <b>Reentrada inteligente ativa.</b>\n"
        f"<i>Após LOSS, aguardando um NOVO sinal com:\n"
        f"  • Score acima do mínimo\n"
        f"  • Estratégia válida\n"
        f"  • Aprovação da IA</i>"
    )


# ------------- ranking & estratégias -------------
def ranking_estrategias(pretty: str) -> str:
    return f"{pretty}"


def estrategia_alternada(nome: str, ativa: bool) -> str:
    status = "✅ ATIVADA" if ativa else "❌ DESATIVADA"
    return f"🧠 Estratégia <b>{nome}</b> — {status}"


# ------------- configurações -------------
def config_atualizada(chave: str, valor: Any) -> str:
    return f"✅ <b>{chave}</b> atualizado: <code>{valor}</code>"


def mercado_lateral_detectado(desc: str) -> str:
    return (
        f"⚠️ <b>Mercado lateral detectado.</b>\n"
        f"<i>{desc}</i>\n"
        f"Estratégias de tendência bloqueadas temporariamente."
    )


# ------------- cache / IA stats -------------
def ia_cache_stats(hits: int, misses: int, size: int) -> str:
    total = hits + misses
    taxa = (hits / total * 100) if total else 0.0
    return (
        f"⚡ <b>Cache da IA</b>\n"
        f"• Hits: <b>{hits}</b>\n"
        f"• Misses: <b>{misses}</b>\n"
        f"• Taxa de reaproveitamento: <b>{taxa:.1f}%</b>\n"
        f"• Entradas: <b>{size}</b>\n\n"
        f"<i>TTL: 3 min | Resultados recentes incluídos na chave</i>"
    )


def ia_cache_limpo(removidas: int) -> str:
    return (
        f"🗑️ <b>Cache da IA limpo!</b>\n"
        f"• Entradas removidas: <b>{removidas}</b>\n\n"
        f"<i>Próximos sinais consultarão a IA em tempo real.</i>"
    )


def stats_message(
    by_strategy: List[Dict[str, Any]],
    by_asset: List[Dict[str, Any]],
    by_timeframe: List[Dict[str, Any]],
    best_hour: Optional[Dict[str, Any]],
    total_trades: int,
    daily_pnl: float,
) -> str:
    def _table(items: List[Dict[str, Any]], key: str) -> str:
        if not items:
            return "<i>sem dados</i>"
        return "\n".join(
            f"  {i[key]} — <b>{i['winrate']:.0f}%</b> wr ({i['trades']} trades)"
            for i in items[:5]
        )

    hour_line = ""
    if best_hour:
        hour_line = f"\n⏰ <b>Melhor hora:</b> {best_hour['hour']}h — {best_hour['winrate']:.0f}% wr ({best_hour['trades']} trades)"

    pnl_emoji = "🟢" if daily_pnl >= 0 else "🔴"
    return (
        f"📉 <b>Stats da sessão</b>\n"
        f"Total de trades: <b>{total_trades}</b> | PnL: {pnl_emoji} <b>{_fmt_money(daily_pnl)}</b>"
        f"{hour_line}\n\n"
        f"🧠 <b>Por estratégia:</b>\n{_table(by_strategy, 'strategy')}\n\n"
        f"💱 <b>Por ativo:</b>\n{_table(by_asset, 'asset')}\n\n"
        f"⏱️ <b>Por timeframe:</b>\n{_table(by_timeframe, 'timeframe')}"
    )
