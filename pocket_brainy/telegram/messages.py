"""
Templates de mensagens personalizadas do Pocket Brainy.

Cada função retorna uma string HTML pronta para Telegram (parse_mode='HTML').

Modos de tom (Msg1):
  - "motivacional": frases variadas, com hype, cuidados explícitos em streaks.
  - "tecnico"     : tom seco, sem frases aleatórias nem alertas emocionais.

Modo de card unificado (Msg3/Msg4):
  - unified_trade_card(stage=...): monta UMA mensagem progressiva (sinal →
    IA → ordem → resultado) que o orquestrador edita no lugar em vez de
    enviar 4 mensagens separadas.

Sparkline (Msg5):
  - equity_sparkline(...) devolve um mini-gráfico ASCII (`▁▂▃▄▅▆▇█`) do PnL
    ao longo dos trades do dia; integrado no placar_ao_vivo quando há ≥2 trades.
"""
from __future__ import annotations

import html
import random
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, List, Optional

_BRT = timezone(timedelta(hours=-3))


# ============================================================================
# Estado de configuração de mensagens (tom + compacto)
# ============================================================================
# Seto lazy via set_mode() pelo orquestrador ao iniciar e sempre que o
# usuário trocar no menu. Assim as funções não precisam receber `tone` em
# todas as chamadas.
_CURRENT_TONE: str = "motivacional"
_COMPACT_MODE: bool = False


def set_mode(tone: str = "motivacional", compact: bool = False) -> None:
    """Ajusta o tom das mensagens e o modo compacto (card único)."""
    global _CURRENT_TONE, _COMPACT_MODE
    _CURRENT_TONE = tone if tone in ("motivacional", "tecnico") else "motivacional"
    _COMPACT_MODE = bool(compact)


def current_tone() -> str:
    return _CURRENT_TONE


def compact_mode() -> bool:
    return _COMPACT_MODE


def _is_tecnico() -> bool:
    return _CURRENT_TONE == "tecnico"


# ------------- utilidades -------------
def _fmt_money(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"$ {sign}{v:.2f}"


def _hoje() -> str:
    return datetime.now(_BRT).strftime("%d/%m/%Y %H:%M")


def _now_hhmm() -> str:
    return datetime.now(_BRT).strftime("%H:%M:%S")


def _rand(*options: str) -> str:
    return random.choice(options)


# ============================================================================
# Msg5 — Sparkline de equity (mini-gráfico ASCII do PnL do dia)
# ============================================================================
_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def equity_sparkline(pnl_series: Iterable[float], width: int = 16) -> str:
    """Gera um sparkline unicode a partir de uma série de PnLs acumulados.

    - Aceita qualquer iterável numérico (profits por trade OU PnL acumulado).
    - Aqui tratamos `pnl_series` como PnL *acumulado* trade-a-trade.
    - Normaliza o range entre min e max e mapeia em 8 níveis.
    - Se todos os valores forem iguais (ou <2 pontos), retorna string vazia.
    - `width` limita a quantidade de caracteres — se a série for maior,
      reduz uniformemente (aggregação simples por índice).
    """
    values = list(pnl_series)
    if len(values) < 2:
        return ""
    # reduz para `width` pontos preservando formato geral
    if len(values) > width:
        step = len(values) / float(width)
        reduced = [values[min(len(values) - 1, int(i * step))] for i in range(width)]
        values = reduced

    vmin = min(values)
    vmax = max(values)
    rng = vmax - vmin
    if rng <= 1e-9:
        # série plana — mostra um traço neutro
        return _SPARK_CHARS[3] * len(values)

    out = []
    n = len(_SPARK_CHARS) - 1
    for v in values:
        idx = int(round((v - vmin) / rng * n))
        idx = max(0, min(n, idx))
        out.append(_SPARK_CHARS[idx])
    return "".join(out)


def _cumulative_pnl(profits: Iterable[float]) -> List[float]:
    acc = 0.0
    out: List[float] = []
    for p in profits:
        acc += float(p)
        out.append(acc)
    return out


def equity_sparkline_from_profits(profits: Iterable[float], width: int = 16) -> str:
    """Conveniência: recebe lista de profits por trade e desenha sparkline do acumulado."""
    return equity_sparkline(_cumulative_pnl(profits), width=width)


# ============================================================================
# Ciclo de vida do bot
# ============================================================================
def bot_iniciado(simulacao: bool, timeframes: List[str], ia: bool,
                 saldo: float, ativos_modo: str,
                 conta_demo: bool = False, otc_count: int = 0,
                 avg_payout: float = 0.0, sessao: str = "",
                 min_score: float = 5.0, smart_reentry: bool = True,
                 scalper_mode: bool = False) -> str:
    conta = "🟢 Demo" if conta_demo else "🔴 Real"
    payout_line = f"📊 Payout médio OTC: <b>{avg_payout:.1f}%</b>\n" if avg_payout > 0 else ""
    otc_line = f"🔢 Ativos OTC disponíveis: <b>{otc_count}</b>\n" if otc_count > 0 else ""
    sessao_line = f"🕐 Sessão: <b>{sessao}</b>\n" if sessao else ""
    reentry_icon = "✅" if smart_reentry else "❌"

    if _is_tecnico():
        mode_tag = " | MODE: SCALPER" if scalper_mode else ""
        return (
            f"▶ <b>BOT ON</b>{mode_tag} — {_hoje()}\n"
            f"Conta: {conta} | Saldo: <b>$ {saldo:.2f}</b>\n"
            f"TFs: {', '.join(timeframes)} | IA: {'BYPASS' if scalper_mode else ('ON' if ia else 'OFF')} | "
            f"Score mín: {min_score:.1f}\n"
            f"Ativos: {ativos_modo} | Reentrada: {reentry_icon} | "
            f"Sessão: {sessao or '—'}\n"
            f"OTCs: {otc_count} | Payout médio: {avg_payout:.1f}%"
        )

    if scalper_mode:
        return (
            f"🎯 <b>SCALPER SNIPER MODE — ON</b>\n\n"
            f"📅 Início: <code>{_hoje()}</code>\n"
            f"Conta: {conta}\n"
            f"💵 Saldo inicial: <b>$ {saldo:.2f}</b>\n"
            f"{sessao_line}"
            f"{otc_line}"
            f"{payout_line}"
            f"⚡ Timeframes ultra-curtos: <b>{', '.join(timeframes)}</b>\n"
            f"🔫 Modo: <b>Sniper exclusivo</b> (estratégias normais OFF)\n"
            f"🧠 IA DeepSeek: <b>BYPASS</b> (latência crítica)\n"
            f"🎯 Score mínimo: <b>{min_score:.1f}</b> · Filtros tripla camada\n"
            f"🎯 Ativos: <b>{ativos_modo}</b>\n\n"
            f"<i>Modo agressivo — só entradas cirúrgicas. Boa caça! 🎯</i>"
        )

    saudacao = _rand(
        "🚀 <b>Pocket Brainy ligado!</b>",
        "🧠 <b>Bot iniciado — pronto pra caçar sinais!</b>",
        "⚡ <b>Pocket Brainy em operação.</b>",
        "🎯 <b>Bot online e afiado.</b>",
    )
    return (
        f"{saudacao}\n\n"
        f"📅 Início: <code>{_hoje()}</code>\n"
        f"Conta: {conta}\n"
        f"💵 Saldo inicial: <b>$ {saldo:.2f}</b>\n"
        f"{sessao_line}"
        f"{otc_line}"
        f"{payout_line}"
        f"⏱️ Timeframes: <b>{', '.join(timeframes)}</b>\n"
        f"🧠 IA DeepSeek: <b>{'ATIVA' if ia else 'INATIVA'}</b>\n"
        f"🎯 Score mínimo: <b>{min_score:.1f}</b>\n"
        f"🔁 Reentrada inteligente: {reentry_icon} <b>{'ON' if smart_reentry else 'OFF'}</b>\n"
        f"🎯 Ativos: <b>{ativos_modo}</b>\n\n"
        f"<i>Boa sorte — bom trading! 🍀</i>"
    )


def bot_parado(pnl: float, wins: int, losses: int, winrate: float, trades: int) -> str:
    if _is_tecnico():
        return (
            f"■ <b>BOT OFF</b>\n"
            f"Trades: {trades} | W/L: {wins}/{losses} | "
            f"WR: {winrate:.1f}% | PnL: <b>{_fmt_money(pnl)}</b>"
        )
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
    if _is_tecnico():
        return f"🔄 Reconectado. Saldo: <b>$ {saldo:.2f}</b>"
    return (
        f"🔄 <b>Reconectado ao Pocket Option.</b>\n"
        f"💵 Saldo atual: <b>$ {saldo:.2f}</b>"
    )


def bot_erro_conexao(erro: str) -> str:
    if _is_tecnico():
        return f"⚠ Falha de conexão: <code>{html.escape(str(erro))}</code>"
    return (
        f"⚠️ <b>Falha de conexão.</b>\n"
        f"<code>{html.escape(str(erro))}</code>\n"
        f"<i>Tentaremos reconectar automaticamente.</i>"
    )


# ============================================================================
# Análise e sinal
# ============================================================================
def sinal_detectado(asset: str, tf: str, strategy: str, direction: str,
                    score: float, confidence: float, payout: float,
                    notes: str = "", tf_confluence: bool = False) -> str:
    arrow = "🟢⬆️ CALL" if direction == "CALL" else "🔴⬇️ PUT"
    notes_line = f"\n<i>📌 {html.escape(notes)}</i>" if notes else ""
    conf_line = "\n🔗 <b>Confluência multi-TF</b> <i>(+1.0 bônus)</i>" if tf_confluence else ""

    if _is_tecnico():
        return (
            f"📡 <b>Sinal</b>  {_now_hhmm()}\n"
            f"<b>{asset}</b> {tf} · {strategy}\n"
            f"{arrow} · Score {score:.2f} · Conf {confidence:.0f}% · Payout {payout:.1f}%"
            f"{conf_line}{notes_line}"
        )

    hype = _rand("🔎 Sinal identificado!", "🎯 Setup encontrado!",
                 "📡 Sinal detectado!", "🦲 Oportunidade pinçada!")
    return (
        f"{hype}\n\n"
        f"💱 <b>{asset}</b>  •  ⏱️ {tf}  •  🧠 {strategy}\n"
        f"📈 Direção: {arrow}\n"
        f"🎯 Score: <b>{score:.2f}</b>  •  ✅ Confiança: <b>{confidence:.1f}%</b>  •  💰 Payout: <b>{payout:.1f}%</b>"
        f"{conf_line}{notes_line}"
    )


def ia_skip_compact(asset: str, tf: str, direction: str, rationale: str) -> str:
    """Mensagem curta quando IA veta o sinal (usada inclusive no modo compact).

    Mostra ativo, TF, direção e o porquê resumido — sem repetir o card todo.
    """
    arrow = "🟢 CALL" if direction == "CALL" else "🔴 PUT"
    # Limita rationale pra não estourar mensagem.
    short = rationale.strip()
    if len(short) > 160:
        short = short[:157].rstrip() + "..."
    return (
        f"⏭️ <b>IA pulou</b> {arrow} <b>{asset}</b> {tf}\n"
        f"<i>{html.escape(short)}</i>"
    )


def ia_validando(operar: bool, confianca: float, rationale: str, cached: bool = False) -> str:
    cache_tag = " ⚡" if cached else ""
    if _is_tecnico():
        decisao = "OPERAR" if operar else "IGNORAR"
        color = "🟢" if operar else "🔴"
        return (
            f"🧠 <b>IA</b>{cache_tag} · {color} <b>{decisao}</b> · {confianca:.0f}%\n"
            f"<i>{html.escape(rationale)}</i>"
        )

    badge = "🧠 <b>IA DeepSeek</b>"
    cache_tag_v = " ⚡ <i>cache</i>" if cached else ""
    if operar:
        head = _rand("✅ Sinal aprovado pela IA!", "🟢 IA confirmou — podemos entrar!",
                     "🎯 IA: OPERAR.")
        color = "🟢"
    else:
        head = _rand("🛑 IA vetou esta entrada.", "⛔ IA recomendou IGNORAR.",
                     "🔕 IA bloqueou o sinal.")
        color = "🔴"
    return (
        f"{badge}{cache_tag_v}\n"
        f"{head}\n"
        f"• Decisão: {color} <b>{'OPERAR' if operar else 'IGNORAR'}</b>  •  Confiança: <b>{confianca:.0f}%</b>\n"
        f"<i>💬 {html.escape(rationale)}</i>\n"
    )


def ordem_enviada(asset: str, direction: str, amount: float, tf: str, mg_level: int = 0) -> str:
    arrow = "🟢 CALL" if direction == "CALL" else "🔴 PUT"
    mg_txt = f" · MG{mg_level}" if mg_level > 0 else ""
    _tf_secs = {"M1": 60, "M5": 300, "M15": 900}.get(tf, 60)
    exp_txt = f"{_tf_secs // 60} min" if _tf_secs >= 60 else f"{_tf_secs}s"

    if _is_tecnico():
        return (
            f"📤 <b>Ordem</b>  {_now_hhmm()}\n"
            f"{arrow} · <b>{asset}</b> {tf} · <b>$ {amount:.2f}</b> · exp {exp_txt}{mg_txt}"
        )

    mg_block = f"\n🎲 Martingale nível <b>{mg_level}</b>" if mg_level > 0 else ""
    return (
        f"📤 <b>Ordem enviada!</b>\n"
        f"{arrow}  •  <b>{asset}</b>  •  {tf}\n"
        f"💵 Valor: <b>$ {amount:.2f}</b>  •  ⏳ Expira em: <b>{exp_txt}</b>{mg_block}\n"
        f"<i>Aguardando resultado…</i>"
    )


# ============================================================================
# Resultados
# ============================================================================
def trade_win(asset: str, strategy: str, tf: str, profit: float,
              placar: str, streak_wins: int = 0,
              saldo_atual: float = 0.0) -> str:
    saldo_txt = f"\n💼 Saldo: <b>$ {saldo_atual:.2f}</b>" if saldo_atual > 0 else ""

    if _is_tecnico():
        streak_txt = f" · streak {streak_wins}" if streak_wins >= 2 else ""
        return (
            f"🟢 <b>WIN</b>{streak_txt}  {_now_hhmm()}\n"
            f"<b>{asset}</b> {tf} · {strategy} · <b>{_fmt_money(profit)}</b>\n\n"
            f"{placar}{saldo_txt}"
        )

    frases = [
        "🎉 <b>WIN!</b> Deu bom!",
        "🟢 <b>VITÓRIA!</b> Lucro garantido!",
        "💚 <b>WIN!</b> Cirúrgico!",
        "🚀 <b>WIN!</b> No alvo!",
        "✨ <b>WIN!</b> Impecável!",
        "🏆 <b>WIN!</b> Excelente entrada!",
        "⚡ <b>WIN!</b> Perfeito!",
    ]
    if streak_wins >= 3:
        streak_txt = f"\n🔥 <b>{streak_wins} wins seguidos!</b> Mantendo o ritmo!"
    elif streak_wins >= 2:
        streak_txt = f"\n🔥 <i>{streak_wins} wins seguidos</i>"
    else:
        streak_txt = ""
    return (
        f"{random.choice(frases)}{streak_txt}\n\n"
        f"💱 <b>{asset}</b>  •  {tf}  •  {strategy}\n"
        f"💰 Lucro: <b>{_fmt_money(profit)}</b>\n\n"
        f"{placar}{saldo_txt}"
    )


def trade_loss(asset: str, strategy: str, tf: str, profit: float,
               placar: str, streak_losses: int = 0,
               saldo_atual: float = 0.0) -> str:
    saldo_txt = f"\n💼 Saldo: <b>$ {saldo_atual:.2f}</b>" if saldo_atual > 0 else ""

    if _is_tecnico():
        # Tom técnico: apenas registra o fato. Sem alerta emocional, sem
        # "cuidado!" — o placar já mostra o streak.
        streak_txt = f" · streak {streak_losses}" if streak_losses >= 2 else ""
        return (
            f"🔴 <b>LOSS</b>{streak_txt}  {_now_hhmm()}\n"
            f"<b>{asset}</b> {tf} · {strategy} · <b>{_fmt_money(profit)}</b>\n\n"
            f"{placar}{saldo_txt}"
        )

    frases_leve = [
        "🔴 <b>LOSS.</b> Segue o jogo.",
        "😐 <b>LOSS.</b> Faz parte.",
        "📉 <b>LOSS.</b> Próxima!",
        "🔴 <b>LOSS.</b> Filtros são para isso.",
    ]
    frases_alerta = [
        "⚠️ <b>LOSS.</b> Atenção ao streak!",
        "🚨 <b>LOSS.</b> Reavaliando filtros…",
        "🛑 <b>LOSS.</b> Cuidado com o gerenciamento!",
    ]
    frase = random.choice(frases_alerta if streak_losses >= 2 else frases_leve)
    streak_txt = f"\n⚠️ <i>Streak de {streak_losses} losses — cuidado!</i>" if streak_losses >= 2 else ""
    return (
        f"{frase}\n\n"
        f"💱 <b>{asset}</b>  •  {tf}  •  {strategy}\n"
        f"💸 Prejuízo: <b>{_fmt_money(profit)}</b>\n\n"
        f"{placar}{streak_txt}{saldo_txt}"
    )


def trade_draw(asset: str, tf: str, placar: str,
               saldo_atual: float = 0.0) -> str:
    saldo_txt = f"\n💼 Saldo: <b>$ {saldo_atual:.2f}</b>" if saldo_atual > 0 else ""
    if _is_tecnico():
        return (
            f"⚪ <b>DRAW</b>  {_now_hhmm()}\n"
            f"<b>{asset}</b> {tf} · valor devolvido\n\n"
            f"{placar}{saldo_txt}"
        )
    return (
        f"⚪ <b>DRAW</b> (empate)\n\n"
        f"💱 <b>{asset}</b>  •  {tf}\n"
        f"<i>Valor devolvido — nada ganho, nada perdido.</i>\n\n"
        f"{placar}{saldo_txt}"
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


# ============================================================================
# Placar & resumo
# ============================================================================
def placar_ao_vivo(wins: int, losses: int, draws: int, winrate: float,
                   pnl: float, streak_loss: int, mg_level: int,
                   sparkline: str = "") -> str:
    """Placar compacto. `sparkline` (Msg5) é opcional — mostra tendência intraday do PnL."""
    emoji_pnl = "📈" if pnl > 0 else ("📉" if pnl < 0 else "➖")
    mg_txt = f"\n🎲 Martingale: nível <b>{mg_level}</b>" if mg_level > 0 else ""
    total = wins + losses + draws
    filled = min(int(winrate / 10), 10)
    wr_bar = "█" * filled + "░" * (10 - filled)

    # Em tom técnico não enfiamos alerta — o sparkline + streak_loss já
    # comunicam a tendência sem pressão emocional.
    if _is_tecnico():
        warn = f"\n<i>streak loss {streak_loss}</i>" if streak_loss >= 2 else ""
        spark_line = f"\n📉 <code>{sparkline}</code>" if sparkline else ""
        return (
            f"📊 {total} trade{'s' if total != 1 else ''}  ·  "
            f"🟢 {wins} · 🔴 {losses} · ⚪ {draws}\n"
            f"🎯 {winrate:.1f}% <code>[{wr_bar}]</code>\n"
            f"{emoji_pnl} PnL: <b>{_fmt_money(pnl)}</b>{mg_txt}{spark_line}{warn}"
        )

    warn = "\n🚨 <b>Atenção: streak de losses!</b>" if streak_loss >= 2 else ""
    spark_line = f"\n📉 Tendência: <code>{sparkline}</code>" if sparkline else ""
    return (
        f"📊 <b>Placar ao vivo</b>  •  {total} trade{'s' if total != 1 else ''}\n"
        f"🟢 {wins}W  🔴 {losses}L  ⚪ {draws}D\n"
        f"🎯 {winrate:.1f}%  <code>[{wr_bar}]</code>\n"
        f"{emoji_pnl} PnL: <b>{_fmt_money(pnl)}</b>{mg_txt}{spark_line}{warn}"
    )


def placar_compact(wins: int, losses: int, winrate: float, pnl: float) -> str:
    """Versão inline ultra-curta para caber no card de trade."""
    emoji_pnl = "📈" if pnl > 0 else ("📉" if pnl < 0 else "➖")
    return f"🟢 {wins}W / 🔴 {losses}L | 🎯 {winrate:.0f}% | {emoji_pnl} {_fmt_money(pnl)}"


def resumo_diario(data: str, wins: int, losses: int, draws: int,
                  winrate: float, pnl: float, trades: int,
                  melhor_estrategia: Optional[str] = None,
                  sparkline: str = "") -> str:
    emoji = "🏆" if pnl > 0 else ("📊" if pnl == 0 else "📉")
    spark_line = f"\n📉 Curva do dia: <code>{sparkline}</code>" if sparkline else ""

    if _is_tecnico():
        best = f"\nMelhor: <b>{melhor_estrategia}</b>" if melhor_estrategia else ""
        return (
            f"{emoji} <b>Resumo {data}</b>\n"
            f"Trades: {trades} · W/L/D: {wins}/{losses}/{draws}\n"
            f"WR: {winrate:.1f}% · PnL: <b>{_fmt_money(pnl)}</b>"
            f"{best}{spark_line}"
        )

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
    if sparkline:
        corpo += f"• 📉 Curva do dia: <code>{sparkline}</code>\n"
    if pnl > 0:
        corpo += "\n<i>🎉 Sessão no verde — excelente trabalho!</i>"
    elif pnl < 0:
        corpo += "\n<i>💪 Amanhã é outro dia. Mantenha a disciplina.</i>"
    else:
        corpo += "\n<i>➖ Sessão neutra.</i>"
    return corpo


# ============================================================================
# Gestão de risco
# ============================================================================
def stop_win_atingido(pnl: float, trades: int) -> str:
    if _is_tecnico():
        return (
            f"🎯 <b>STOP WIN</b> · PnL <b>{_fmt_money(pnl)}</b> · {trades} trades\n"
            f"<i>Bot pausado.</i>"
        )
    return (
        f"🎯 <b>STOP WIN ATINGIDO!</b> 🎉\n"
        f"💰 Lucro do dia: <b>{_fmt_money(pnl)}</b>\n"
        f"📊 Trades: <b>{trades}</b>\n\n"
        f"<i>Meta batida. Bot pausado automaticamente — trave os ganhos!</i>"
    )


def stop_loss_atingido(pnl: float, trades: int) -> str:
    if _is_tecnico():
        return (
            f"🛑 <b>STOP LOSS</b> · PnL <b>{_fmt_money(pnl)}</b> · {trades} trades\n"
            f"<i>Bot pausado.</i>"
        )
    return (
        f"🛑 <b>STOP LOSS ATINGIDO.</b>\n"
        f"💸 Prejuízo do dia: <b>{_fmt_money(pnl)}</b>\n"
        f"📊 Trades: <b>{trades}</b>\n\n"
        f"<i>Proteção ativada — bot pausado. Respire, revise e volte amanhã.</i>"
    )


def streak_loss_atingido(streak: int) -> str:
    if _is_tecnico():
        return f"🛑 <b>Streak {streak} losses.</b> Bot pausado."
    return (
        f"🚨 <b>Streak de loss atingido ({streak} seguidos).</b>\n"
        f"<i>Bot pausado para evitar tilt. Revise os filtros antes de religar.</i>"
    )


def max_trades_atingido(n: int) -> str:
    if _is_tecnico():
        return f"🚦 Limite diário de {n} trades atingido. Bot pausado."
    return (
        f"🚦 <b>Limite diário de {n} trades atingido.</b>\n"
        f"<i>Bot pausado — volte amanhã fresco!</i>"
    )


def delay_anti_overtrading(restante: float) -> str:
    return (
        f"⏳ <i>Anti-overtrading: aguardando {restante:.0f}s para o próximo sinal…</i>"
    )


# ============================================================================
# Reentrada inteligente
# ============================================================================
def reentrada_inteligente() -> str:
    if _is_tecnico():
        return "🔁 <i>Reentrada inteligente ativa — aguardando novo sinal válido.</i>"
    return (
        f"🔁 <b>Reentrada inteligente ativa.</b>\n"
        f"<i>Após LOSS, próximo sinal precisa passar por:\n"
        f"  • Score ≥ mínimo configurado\n"
        f"  • Filtros de vela e volume\n"
        f"  • Aprovação da IA (se ativa)</i>"
    )


# ============================================================================
# Ranking & estratégias
# ============================================================================
def ranking_estrategias(pretty: str) -> str:
    return f"{pretty}"


def estrategia_alternada(nome: str, ativa: bool) -> str:
    status = "✅ ATIVADA" if ativa else "❌ DESATIVADA"
    return f"🧠 Estratégia <b>{nome}</b> — {status}"


# ============================================================================
# Configurações
# ============================================================================
def config_atualizada(chave: str, valor: Any) -> str:
    return f"✅ <b>{chave}</b> atualizado: <code>{valor}</code>"


def mercado_lateral_detectado(desc: str) -> str:
    if _is_tecnico():
        return f"⚠ Mercado lateral: <i>{html.escape(desc)}</i> · trend strategies off."
    return (
        f"⚠️ <b>Mercado lateral detectado.</b>\n"
        f"<i>{html.escape(desc)}</i>\n"
        f"Estratégias de tendência bloqueadas temporariamente."
    )


def tf_confluencia_detectada(asset: str, direction: str, timeframes: List[str]) -> str:
    """Notifica quando 2+ timeframes concordam na mesma direção para o mesmo ativo."""
    arrow = "⬆️ CALL" if direction == "CALL" else "⬇️ PUT"
    tfs_txt = " + ".join(sorted(set(timeframes)))
    if _is_tecnico():
        return f"🔗 Confluência {tfs_txt} · <b>{asset}</b> {arrow} (+1.0)"
    return (
        f"🔗 <b>Confluência entre timeframes!</b>\n"
        f"💱 {asset}  •  {arrow}\n"
        f"<i>{tfs_txt} apontam a mesma direção — +1.0 bônus no score.</i>"
    )


# ============================================================================
# Cache / IA stats
# ============================================================================
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


# ============================================================================
# Msg3 / Msg4 — Card unificado de trade (mensagem editada no lugar)
# ============================================================================
# O card tem até 4 estágios. A cada transição o orquestrador edita a mesma
# mensagem no Telegram em vez de enviar uma nova.
#   "signal"       → sinal detectado (IA ainda vai validar)
#   "ai_validated" → IA respondeu (OPERAR/IGNORAR)
#   "order_sent"   → ordem enviada (aguardando resultado)
#   "result"       → resultado final (WIN/LOSS/DRAW) + placar
#
# Uso esperado:
#   card = build_trade_card(stage="signal", signal=..., payout=...)
#   → mensagem enviada ao Telegram, guarda message_id
#   card = build_trade_card(stage="ai_validated", ..., ai=...)
#   → edit_message(message_id, card)
#   ... e assim por diante.
# ============================================================================
def build_trade_card(
    *,
    stage: str,
    asset: str,
    timeframe: str,
    strategy: str,
    direction: str,
    score: float,
    confidence: float,
    payout: float,
    notes: str = "",
    tf_confluence: bool = False,
    # --- campos preenchidos após cada etapa (None até existir) ---
    ai_operate: Optional[bool] = None,
    ai_confidence: Optional[float] = None,
    ai_rationale: Optional[str] = None,
    ai_cached: bool = False,
    # ordem
    order_amount: Optional[float] = None,
    order_mg_level: int = 0,
    # resultado
    result: Optional[str] = None,        # "WIN" | "LOSS" | "DRAW"
    result_profit: Optional[float] = None,
    saldo_atual: Optional[float] = None,
    placar: Optional[str] = None,
    streak: Optional[int] = None,        # wins ou losses, conforme resultado
) -> str:
    """Monta UMA mensagem de trade progressiva (sinal → IA → ordem → resultado).

    Projetado para ser editado no lugar (Msg3). Quando o modo compacto está
    ON, `core.bot` usa essa função em vez das quatro mensagens separadas,
    reduzindo drasticamente a poluição do chat (Msg4).
    """
    arrow = "🟢⬆️ CALL" if direction == "CALL" else "🔴⬇️ PUT"
    tecnico = _is_tecnico()

    # === Cabeçalho muda conforme o estágio (estado mais "recente") ===
    header_map = {
        "signal":       "🔎 <b>SINAL</b>",
        "ai_validated": "🧠 <b>IA</b>",
        "order_sent":   "📤 <b>ORDEM ENVIADA</b>",
        "result":       _result_header(result, tecnico),
    }
    header = header_map.get(stage, "🔎 <b>SINAL</b>")

    # === Linha base (sempre presente) ===
    conf_badge = " 🔗" if tf_confluence else ""
    base_line = (
        f"<b>{asset}</b> · {timeframe} · {strategy}{conf_badge}\n"
        f"{arrow} · Score <b>{score:.2f}</b> · Conf <b>{confidence:.0f}%</b> · Payout <b>{payout:.1f}%</b>"
    )
    notes_line = f"\n<i>📌 {html.escape(notes)}</i>" if notes else ""

    lines: List[str] = [f"{header}  <i>{_now_hhmm()}</i>", base_line]
    if notes_line:
        lines[-1] += notes_line

    # === Linha da IA (aparece a partir de ai_validated) ===
    if stage in ("ai_validated", "order_sent", "result") and ai_operate is not None:
        color = "🟢" if ai_operate else "🔴"
        decisao = "OPERAR" if ai_operate else "IGNORAR"
        cache_tag = " ⚡" if ai_cached else ""
        rationale_txt = ""
        if ai_rationale:
            rationale_txt = f"\n  <i>{html.escape(ai_rationale)}</i>"
        lines.append(
            f"🧠 IA{cache_tag}: {color} <b>{decisao}</b>"
            f" · <b>{(ai_confidence or 0):.0f}%</b>{rationale_txt}"
        )

    # === Linha da ordem ===
    if stage in ("order_sent", "result") and order_amount is not None:
        mg_txt = f" · MG{order_mg_level}" if order_mg_level > 0 else ""
        _tf_secs = {"M1": 60, "M5": 300, "M15": 900}.get(timeframe, 60)
        exp_txt = f"{_tf_secs // 60} min" if _tf_secs >= 60 else f"{_tf_secs}s"
        lines.append(
            f"📤 Ordem: <b>$ {order_amount:.2f}</b> · exp <b>{exp_txt}</b>{mg_txt}"
        )
        if stage == "order_sent":
            lines.append("<i>⏳ aguardando resultado…</i>")

    # === Linha do resultado ===
    if stage == "result" and result is not None:
        profit = result_profit if result_profit is not None else 0.0
        if result == "WIN":
            streak_txt = f" · 🔥 streak {streak}" if (streak or 0) >= 2 else ""
            lines.append(
                f"🟢 <b>WIN</b>{streak_txt} · Lucro <b>{_fmt_money(profit)}</b>"
            )
        elif result == "LOSS":
            # Em tom motivacional marca streak com alerta; em técnico, só numérico.
            if tecnico:
                streak_txt = f" · streak {streak}" if (streak or 0) >= 2 else ""
            else:
                streak_txt = f" · ⚠️ streak {streak}" if (streak or 0) >= 2 else ""
            lines.append(
                f"🔴 <b>LOSS</b>{streak_txt} · <b>{_fmt_money(profit)}</b>"
            )
        elif result == "DRAW":
            lines.append("⚪ <b>DRAW</b> · valor devolvido")

        if saldo_atual is not None and saldo_atual > 0:
            lines.append(f"💼 Saldo: <b>$ {saldo_atual:.2f}</b>")

        if placar:
            lines.append("")
            lines.append(placar)

    return "\n".join(lines)


def _result_header(result: Optional[str], tecnico: bool) -> str:
    if result == "WIN":
        return "🟢 <b>WIN</b>" if tecnico else "🎉 <b>WIN!</b>"
    if result == "LOSS":
        return "🔴 <b>LOSS</b>"
    if result == "DRAW":
        return "⚪ <b>DRAW</b>"
    return "✅ <b>RESULTADO</b>"


# ============================================================================
# Loop / Dashboard ao vivo
# ============================================================================
def escaneando_ativos(count: int, timeframes: List[str], sessao: str = "", scalper_mode: bool = False) -> str:
    """Mensagem enviada uma vez quando o loop de trading inicia."""
    tfs = ", ".join(timeframes)
    sess_line = f" · <i>{html.escape(sessao)}</i>" if sessao else ""
    if _is_tecnico():
        prefix = "🎯 SCALPER " if scalper_mode else ""
        return f"{prefix}🔍 Scanning {count} OTC assets | TFs: {tfs}{sess_line}"
    if scalper_mode:
        return (
            f"🎯 <b>SCALPER SNIPER ATIVO</b> — caçando entradas cirúrgicas\n"
            f"🔍 <b>{count} ativos OTC</b> em monitoramento\n"
            f"⚡ Timeframes ultra-curtos: <b>{tfs}</b>{sess_line}\n"
            f"<i>Filtros tripla camada · IA bypass · cooldown agressivo</i>"
        )
    return (
        f"🔍 <b>Escaneando {count} ativos OTC</b>\n"
        f"⏱️ Timeframes: <b>{tfs}</b>{sess_line}\n"
        f"<i>Analisando sinais em tempo real…</i>"
    )


def dashboard_ao_vivo(
    wins: int, losses: int, draws: int, winrate: float, pnl: float,
    adx: float, is_lateral: bool, otc_count: int, timeframes: List[str],
    strategies_on: List[str], updated_at: str,
    mg_level: int = 0,
    tick_count: int = 0,
    scalper_mode: bool = False,
    scalper_loss_streak: int = 0,
    scalper_max_loss_streak: int = 5,
) -> str:
    """Dashboard fixado no chat; editado a cada minuto enquanto o bot roda."""
    emoji_pnl = "📈" if pnl > 0 else ("📉" if pnl < 0 else "➖")
    total = wins + losses + draws
    market_txt = "⚡ Tendência" if not is_lateral else "〰️ Lateral"
    tfs_txt = " · ".join(timeframes)
    mg_txt = f"\n🎲 Martingale nv <b>{mg_level}</b>" if mg_level > 0 else ""
    cycles_txt = f"🔄 <b>{tick_count:,}</b> ciclos  •  " if tick_count > 0 else ""

    if scalper_mode:
        title = "🎯 <b>Pocket Brainy — SCALPER SNIPER</b>"
        streak_txt = (
            f"\n🔥 Streak loss: <b>{scalper_loss_streak}/{scalper_max_loss_streak}</b>"
            if scalper_loss_streak > 0 else ""
        )
        return (
            f"{title}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🟢 {wins}W  🔴 {losses}L  ⚪ {draws}D  •  {total} sniper{'s' if total != 1 else ''}{streak_txt}\n"
            f"🎯 Winrate: <b>{winrate:.1f}%</b>  •  {emoji_pnl} PnL: <b>{_fmt_money(pnl)}</b>{mg_txt}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🔫 <b>Modo:</b> Sniper exclusivo • IA bypass\n"
            f"🔍 <b>{otc_count}</b> ativos  •  TFs ultra: <b>{tfs_txt}</b>\n"
            f"⚙️ Filtros: ATR · Wick · Body · RSI div · Pin/Engulf\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"{cycles_txt}<i>🕐 {updated_at}</i>"
        )

    strats_txt = " · ".join(strategies_on) if strategies_on else "—"
    title = "📊 <b>Pocket Brainy — Ao Vivo</b>"
    return (
        f"{title}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🟢 {wins}W  🔴 {losses}L  ⚪ {draws}D  •  {total} trade{'s' if total != 1 else ''}\n"
        f"🎯 Winrate: <b>{winrate:.1f}%</b>  •  {emoji_pnl} PnL: <b>{_fmt_money(pnl)}</b>{mg_txt}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📈 ADX: <b>{adx:.1f}</b>  •  Mercado: <b>{market_txt}</b>\n"
        f"🔍 <b>{otc_count}</b> ativos  •  TFs: <b>{tfs_txt}</b>\n"
        f"🧠 {html.escape(strats_txt)}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{cycles_txt}<i>🕐 {updated_at}</i>"
    )
