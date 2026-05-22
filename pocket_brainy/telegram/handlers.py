"""Handlers de callbacks do Telegram."""
from __future__ import annotations

from typing import Optional

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from . import menus
from ..utils.logger import get_logger

logger = get_logger("telegram.handlers")


class Handlers:
    """Conjunto de handlers delegados à interface principal."""

    def __init__(self, iface: "TelegramInterface"):  # noqa: F821
        self.iface = iface

    def _main_menu(self):
        """Helper: monta o menu principal já refletindo o estado atual do scalper."""
        try:
            scalper_on = bool(getattr(self.iface.bot.cfg_manager.config, "scalper_mode", False))
        except Exception:
            scalper_on = False
        return menus.main_menu(scalper_mode=scalper_on)

    # --- comandos ---
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return
        await update.effective_message.reply_text(
            "🧠 <b>Pocket Brainy</b>\nEscolha uma opção:",
            reply_markup=self._main_menu(),
            parse_mode="HTML",
        )

    async def cmd_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await self.cmd_start(update, context)

    async def cmd_regime(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """🌡️ Mostra regime atual de cada par + WR de cada estratégia por regime."""
        if not self._authorized(update):
            return
        try:
            from ..strategies.regime_stats import REGIME_STATS
            from ..utils.regime import detect_regime, regime_emoji
        except Exception as e:
            await update.effective_message.reply_text(f"❌ Erro ao importar regime: {e}")
            return

        cfg = self.bot.cfg_manager.config
        lines = ["🌡️ <b>Regime de mercado</b>", "━━━━━━━━━━━━━━━━━━━━"]

        # 1) Regime atual dos pares ativos (M5)
        lines.append("\n<b>📍 Regime atual (M5, últimas 30 candles)</b>")
        try:
            assets = list(getattr(cfg, "assets", []))[:12]
            shown = 0
            for asset in assets:
                try:
                    candles = await self.bot.broker.get_candles(asset, 60, 60)
                    rg = detect_regime(candles, 30) if len(candles) >= 30 else ""
                    if rg:
                        lines.append(f"  {regime_emoji(rg)} <code>{asset:14s}</code> {rg}")
                        shown += 1
                except Exception:
                    continue
            if shown == 0:
                lines.append("  <i>(sem dados — broker offline?)</i>")
        except Exception as e:
            lines.append(f"  <i>(erro: {e})</i>")

        # 2) Matriz WR(estratégia × regime)
        lines.append("\n<b>📊 WR histórico por estratégia × regime</b>")
        matrix = REGIME_STATS.matrix()
        if not matrix:
            lines.append("<i>(sem histórico ainda — opere algumas trades)</i>")
        else:
            regimes_order = ["TREND_UP", "TREND_DOWN", "RANGE", "CHOP"]
            header = "<code>Estratégia        " + " ".join(f"{regime_emoji(r)}" for r in regimes_order) + "</code>"
            lines.append(header)
            for strat in sorted(matrix.keys()):
                cells = []
                for r in regimes_order:
                    if r in matrix[strat]:
                        wr, n = matrix[strat][r]
                        cells.append(f"{wr:4.0f}%({n})")
                    else:
                        cells.append("  -    ")
                lines.append(f"<code>{strat[:18]:18s} {' '.join(cells)}</code>")

        lines.append(
            f"\n⚙️ Filtro: {'✅ ON' if cfg.regime_filter_enabled else '❌ OFF'} "
            f"| min_wr={cfg.regime_min_wr:.0f}% | min_n={cfg.regime_min_trades}"
        )
        await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """📖 Lista de todos os comandos disponíveis com explicação clara."""
        if not self._authorized(update):
            return
        text = (
            "📖 <b>Comandos do Pocket Brainy</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "🎮 <b>Controle</b>\n"
            "• <code>/menu</code> · abrir menu principal (botões)\n"
            "• <code>/ssid</code> &lt;token&gt; · atualizar sessão Pocket\n\n"
            "🎯 <b>Decisão (use ANTES de operar)</b>\n"
            "• <code>/now</code> · 🔥 top ativos pra ESTA hora (com payout ao vivo)\n"
            "• <code>/topativos</code> · ranking geral por WR real\n"
            "• <code>/timestats</code> · WR por hora BRT (qual hora tá quente)\n"
            "• <code>/cores</code> · qual núcleo do scalper tá ouro/lixo por hora\n"
            "• <code>/backtest</code> · ranking baseado em simulação histórica (1000 candles)\n\n"
            "📊 <b>Visualizações</b>\n"
            "• <code>/heatmap</code> · texto: WR por hora\n"
            "• <code>/heatmap_visual</code> · 🎨 imagem PNG: 24h × 7 dias\n"
            "• <code>/topcores</code> · núcleos mais ativos\n"
            "• <code>/regime</code> · 🌡️ regime de mercado + WR por estratégia\n\n"
            "🔍 <b>Diagnóstico</b>\n"
            "• <code>/why</code> · por que o último sinal NÃO entrou\n"
            "• <code>/alerts on|off</code> · notificação 'quase entrou'\n\n"
            "💡 <b>Fluxo recomendado</b>\n"
            "1. Abrir <code>/now</code> → ver top 3-5 ativos da hora\n"
            "2. Conferir <code>/timestats</code> pra confirmar hora boa\n"
            "3. Abrir o menu, ▶️ Iniciar Bot\n"
            "4. Após 1h, <code>/heatmap_visual</code> pra ver onde tá ganhando\n\n"
            "⚙️ <b>Configurações importantes</b>\n"
            "• Min payout: rejeita sinais com payout abaixo (logado em tempo real)\n"
            "• Min score: filtro de qualidade do sinal (8.5 é o atual)\n"
            "• Defesa noturna: 20h-06h força score ≥9 e ATR maior\n"
            "• Manhã agressiva: 7h-11h afrouxa score em -0.3"
        )
        await update.effective_message.reply_text(text, parse_mode="HTML")

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return
        await update.effective_message.reply_text(
            self.iface.render_stats(),
            reply_markup=menus.back_menu(),
            parse_mode="HTML",
        )

    async def cmd_ssid(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Recebe /ssid <TOKEN> e atualiza SSID em tempo de execução."""
        if not self._authorized(update):
            return
        token = " ".join(context.args or []).strip()
        if not token:
            await update.effective_message.reply_text(
                "⚠️ Uso: <code>/ssid SEU_TOKEN_AQUI</code>\n\n"
                "Cole o frame WebSocket completo ou apenas o token.",
                parse_mode="HTML",
            )
            return
        await update.effective_message.reply_text("🔄 Atualizando SSID...", parse_mode="HTML")
        result = await self.iface.bot.update_ssid(token)
        await update.effective_message.reply_text(result, parse_mode="HTML", reply_markup=self._main_menu())

    async def cmd_saldo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """💰 Força um get_balance fresco da corretora (não usa cache local)."""
        if not self._authorized(update):
            return
        bot = self.iface.bot
        if not bot.broker or not bot.state.connected:
            await update.effective_message.reply_text(
                "⚠️ Bot não conectado à corretora. Envie /ssid TOKEN primeiro.",
                parse_mode="HTML",
            )
            return
        msg = await update.effective_message.reply_text("⏳ Consultando corretora…", parse_mode="HTML")
        bal = await bot.refresh_balance()
        cfg = bot.cfg_manager.config
        conta = "DEMO" if cfg.po_demo else "REAL"
        try:
            await msg.edit_text(
                f"💰 <b>Saldo {conta}</b>: $ {bal:.2f}\n"
                f"<i>Fonte: corretora (não-cacheado)</i>",
                parse_mode="HTML",
            )
        except Exception:
            await update.effective_message.reply_text(
                f"💰 Saldo {conta}: $ {bal:.2f}", parse_mode="HTML"
            )

    async def cmd_debug(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """🔍 Liga modo verbose por 5min + mostra snapshot do scan atual."""
        if not self._authorized(update):
            return
        import time as _time
        bot = self.iface.bot
        bot._debug_verbose_until = _time.time() + 300  # 5 min
        try:
            from ..strategies.scalper import SCAN_STATS as _SS
            cfg = bot.cfg_manager.config
            txt = (
                "🔍 <b>DEBUG ON (5 min)</b>\n\n"
                f"• Combos no scan atual: <b>{_SS['total']}</b>\n"
                f"• ❌ wick: {_SS['wick']}  ATR_low: {_SS['atr_low']}  ATR_high: {_SS['atr_high']}\n"
                f"• ❌ doji_prev: {_SS['doji_prev']}  doji_last: {_SS['doji_last']}  prev_clean: {_SS['prev_clean']}\n"
                f"• ❌ sem_núcleo: {_SS['no_core']}  empate: {_SS['core_tie']}\n"
                f"• ❌ confirmações: {_SS['confirms']}  score_baixo: {_SS['low_score']}\n"
                f"• ✅ aprovados: <b>{_SS['approved']}</b>\n"
                f"• 🏆 best_score: <b>{_SS['best_score']:.2f}</b> (min={cfg.scalper_min_score})\n"
            )
            if _SS["near_misses"]:
                txt += "\n<i>Quase entrou:</i>\n"
                for nm in _SS["near_misses"][-10:]:
                    txt += f"• {nm[0]} {nm[1]} {nm[2]} score={nm[3]:.2f}\n"
        except Exception as e:
            txt = f"❌ Erro lendo SCAN_STATS: <code>{e}</code>"
        await update.effective_message.reply_text(txt, parse_mode="HTML")

    async def cmd_topares(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """🏆 Ranking de pares OTC por WR rolling."""
        if not self._authorized(update):
            return
        try:
            top = self.iface.bot.strategies.pair_stats.top_assets_overall(n=10, min_trades=5)
        except Exception as e:
            await update.effective_message.reply_text(f"❌ Erro: <code>{e}</code>", parse_mode="HTML")
            return
        if not top:
            await update.effective_message.reply_text(
                "📊 Sem dados ainda. Aguarde algumas trades.", parse_mode="HTML"
            )
            return
        lines = ["🏆 <b>Top 10 pares OTC</b> (mín. 5 trades):", ""]
        for i, (asset, wr, w, l, total) in enumerate(top, 1):
            emoji = "🟢" if wr >= 60 else ("🟡" if wr >= 50 else "🔴")
            lines.append(f"{i}. {emoji} <b>{asset}</b> — {wr:.1f}% ({w}W/{l}L em {total})")
        await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")

    async def cmd_scalpstats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """🎯 Estatísticas internas do Scalper Sniper."""
        if not self._authorized(update):
            return
        try:
            from ..strategies.scalper import SCALPER_RANKING
            summary = SCALPER_RANKING.stats_summary()
        except Exception as e:
            await update.effective_message.reply_text(f"❌ Erro: <code>{e}</code>", parse_mode="HTML")
            return
        if not summary:
            await update.effective_message.reply_text(
                "🎯 Scalper sem histórico ainda.", parse_mode="HTML"
            )
            return
        # Separa TFs das combos de cores
        tf_lines, core_lines = [], []
        for key, d in sorted(summary.items(), key=lambda kv: -kv[1]["wr"]):
            n, wr = d["n"], d["wr"]
            emoji = "🟢" if wr >= 65 else ("🟡" if wr >= 50 else "🔴")
            line = f"{emoji} <code>{key}</code> — {wr:.1f}% ({n})"
            if key.endswith(":_TF_"):
                tf_lines.append(line.replace(":_TF_", ""))
            else:
                core_lines.append(line)
        out = ["🎯 <b>Scalper Sniper Stats</b>"]
        if tf_lines:
            out += ["", "<b>Por Timeframe:</b>"] + tf_lines[:5]
        if core_lines:
            out += ["", "<b>Top combos de cores:</b>"] + core_lines[:8]
        await update.effective_message.reply_text("\n".join(out), parse_mode="HTML")

    # --- callbacks ---
    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()
        if not self._authorized(update):
            return
        data = q.data or ""
        try:
            if data == "menu:main":
                await q.edit_message_text("🧠 Menu principal:", reply_markup=self._main_menu())
            elif data == "menu:decide":
                await q.edit_message_text(
                    "🎯 <b>Decidir Agora</b>\n<i>Use estes atalhos antes de iniciar o bot pra escolher pares quentes.</i>",
                    reply_markup=menus.decide_menu(), parse_mode="HTML",
                )
            elif data == "menu:monitor":
                await q.edit_message_text(
                    "📊 <b>Monitorar</b>\n<i>Veja o estado do bot e resultados em tempo real.</i>",
                    reply_markup=menus.monitor_menu(), parse_mode="HTML",
                )
            elif data == "menu:maintenance":
                await q.edit_message_text(
                    "🔧 <b>Manutenção</b>\n<i>Cache, conexão e gerenciamento de contas.</i>",
                    reply_markup=menus.maintenance_menu(), parse_mode="HTML",
                )
            elif data == "menu:help":
                await self.cmd_help(update, context)
            elif data.startswith("exec:"):
                # Atalhos de comandos via botões
                action = data.split(":", 1)[1]
                fn_map = {
                    "now": self.cmd_now,
                    "timestats": self.cmd_timestats,
                    "topcores": self.cmd_topcores,
                    "topativos": self.cmd_topativos,
                    "heatmap_visual": self.cmd_heatmap_visual,
                    "backtest": self.cmd_backtest,
                    "why": self.cmd_why,
                }
                fn = fn_map.get(action)
                if fn:
                    await fn(update, context)
            elif data == "menu:config":
                await q.edit_message_text(self.iface.render_config(), reply_markup=self._config_markup(), parse_mode="HTML")
            elif data == "menu:strategies":
                statuses = self.iface.bot.strategies.list_status()
                await q.edit_message_text("🧠 <b>Estratégias</b>\nToque para ativar/desativar:",
                                          reply_markup=menus.strategies_menu(statuses), parse_mode="HTML")
            elif data == "menu:status":
                await q.edit_message_text(await self.iface.render_status_live(), reply_markup=menus.back_menu(), parse_mode="HTML")
            elif data == "menu:results":
                await q.edit_message_text(self.iface.render_results(), reply_markup=menus.back_menu(), parse_mode="HTML")
            elif data == "menu:ranking":
                await q.edit_message_text(self.iface.bot.strategies.ranking.pretty(),
                                          reply_markup=menus.back_menu(), parse_mode="HTML")
            elif data == "menu:stats":
                await q.edit_message_text(self.iface.render_stats(), reply_markup=menus.back_menu(), parse_mode="HTML")
            elif data == "menu:ai_cache":
                from . import messages as _m
                stats = self.iface.bot.ai.cache_stats()
                await q.edit_message_text(
                    _m.ia_cache_stats(stats["hits"], stats["misses"], stats["size"]),
                    reply_markup=menus.back_menu(), parse_mode="HTML",
                )
            elif data == "ai:clear_cache":
                from . import messages as _m
                removed = await self.iface.bot.ai.clear_cache()
                await q.edit_message_text(
                    _m.ia_cache_limpo(removed),
                    reply_markup=menus.back_menu(), parse_mode="HTML",
                )
            elif data == "bot:start":
                import html as _html
                msg = await self.iface.bot.start_trading()
                # msg pode conter texto de erro com <> — escapa se não for HTML estruturado
                safe_msg = msg if msg.startswith("<") or "<b>" in msg or "<code>" in msg else _html.escape(msg)
                await q.edit_message_text(safe_msg, reply_markup=self._main_menu(), parse_mode="HTML")
            elif data == "bot:stop":
                import html as _html
                msg = await self.iface.bot.stop_trading()
                safe_msg = msg if "<b>" in msg or "<code>" in msg else _html.escape(msg)
                await q.edit_message_text(safe_msg, reply_markup=self._main_menu(), parse_mode="HTML")
            elif data == "bot:reconnect":
                import html as _html
                msg = await self.iface.bot.reconnect()
                safe_msg = msg if "<b>" in msg or "<code>" in msg else _html.escape(msg)
                await q.edit_message_text(safe_msg, reply_markup=self._main_menu(), parse_mode="HTML")
            elif data == "bot:scalper_toggle":
                cfg = self.iface.bot.cfg_manager.config
                new_state = not bool(getattr(cfg, "scalper_mode", False))
                self.iface.bot.cfg_manager.update(scalper_mode=new_state)
                # Sincroniza imediatamente com o manager
                try:
                    self.iface.bot.strategies.set_scalper_only(new_state)
                except Exception:
                    pass
                if new_state:
                    txt = (
                        "🎯 <b>Scalper Sniper ATIVADO</b>\n\n"
                        "• Timeframes: <code>S10 / S30 / M1</code>\n"
                        f"• Score mínimo: <code>{cfg.scalper_min_score}</code> | "
                        f"Confiança: <code>{cfg.scalper_min_confidence}%</code>\n"
                        f"• Cooldown: <code>{cfg.scalper_cooldown_seconds}s</code> por sinal\n"
                        f"• Auto-stop após <code>{cfg.scalper_max_loss_streak}</code> losses seguidos\n"
                        "• <b>IA bypass</b> (latência crítica em scalp)\n\n"
                        "⚠️ Modo agressivo — apenas entradas com confluência tripla."
                    )
                else:
                    txt = "🎯 <b>Scalper Sniper DESATIVADO</b>\nVoltando às estratégias normais."
                await q.edit_message_text(txt, reply_markup=self._main_menu(), parse_mode="HTML")
            elif data == "menu:accounts":
                cfg = self.iface.bot.cfg_manager.config
                await q.edit_message_text(
                    "🔀 <b>Multi-Conta</b>\n\n"
                    "Salve seus 2 SSIDs e alterne com 1 clique.\n"
                    "<i>Para salvar: capture o SSID normalmente, depois clique em \"Salvar como\".</i>",
                    reply_markup=menus.accounts_menu(
                        has_real=bool(cfg.account_slot_real_ssid),
                        has_demo=bool(cfg.account_slot_demo_ssid),
                        current_demo=cfg.po_demo,
                    ),
                    parse_mode="HTML",
                )
            elif data.startswith("acct:"):
                await self._handle_account_slot(q, data)
            elif data.startswith("strat:toggle:"):
                name = data.split(":", 2)[2]
                new_state = self.iface.bot.strategies.toggle(name)
                statuses = self.iface.bot.strategies.list_status()
                await q.edit_message_text(
                    f"Estratégia <b>{name}</b> agora está {'ATIVA' if new_state else 'INATIVA'}.",
                    reply_markup=menus.strategies_menu(statuses), parse_mode="HTML",
                )
            elif data.startswith("cfg:"):
                await self._handle_cfg(q, data)
            elif data.startswith("tf:toggle:"):
                tf = data.split(":", 2)[2]
                cfg = self.iface.bot.cfg_manager.config
                tfs = set(cfg.timeframes)
                tfs.symmetric_difference_update({tf})
                self.iface.bot.cfg_manager.update(timeframes=sorted(tfs))
                await q.edit_message_text("⏱️ Timeframes:", reply_markup=menus.timeframes_menu(sorted(tfs)))
            elif data.startswith("am:"):
                await self._handle_asset_mode(q, data)
            elif data.startswith("mg:"):
                await self._handle_martingale(q, data)
            else:
                await q.edit_message_text(f"❓ Ação desconhecida: {data}", reply_markup=menus.back_menu())
        except BadRequest as e:
            # "Message is not modified" é inofensivo (usuário reclicou o mesmo botão).
            msg = str(e).lower()
            if "message is not modified" in msg or "message to edit not found" in msg:
                return
            logger.exception(f"BadRequest no callback {data}: {e}")
            try:
                await q.edit_message_text(f"⚠️ Erro: {e}", reply_markup=menus.back_menu())
            except Exception:
                pass
        except Exception as e:
            logger.exception(f"Erro no callback {data}: {e}")
            try:
                await q.edit_message_text(f"⚠️ Erro: {e}", reply_markup=menus.back_menu())
            except Exception:
                pass

    # --- input de texto livre (para ajustar valores) ---
    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return
        # IMPORTANTE: awaiting é armazenado em self.iface.user_data (keyed by chat_id),
        # NÃO em context.user_data (que é o user_data do PTB, objeto diferente).
        chat_id = update.effective_chat.id
        awaiting = self.iface.user_data.get(chat_id, {}).get("awaiting")
        if not awaiting:
            await update.effective_message.reply_text("Use /menu para abrir o painel.", reply_markup=self._main_menu())
            return
        txt = (update.message.text or "").strip().replace(",", ".")  # aceita vírgula como separador decimal
        try:
            cfg_mgr = self.iface.bot.cfg_manager
            if awaiting == "min_score":
                val = float(txt)
                if val < 1.0 or val > 10.0:
                    await update.effective_message.reply_text(
                        "⚠️ Score mínimo deve ser entre <b>1.0</b> e <b>10.0</b>. Tente novamente:",
                        parse_mode="HTML",
                    )
                    return
                cfg_mgr.update(min_score=val)
            elif awaiting == "entry_amount":
                val = float(txt)
                if val < 1.0:
                    await update.effective_message.reply_text(
                        "⚠️ Valor mínimo de entrada é <b>$ 1.00</b>. Tente novamente:",
                        parse_mode="HTML",
                    )
                    return  # mantém awaiting para o usuário poder tentar de novo
                cfg_mgr.update(entry_amount=val)
            elif awaiting == "stop_win":
                pct = txt.endswith("%")
                cfg_mgr.update(stop_win=float(txt.rstrip("%")), stop_win_is_percent=pct)
            elif awaiting == "stop_loss":
                pct = txt.endswith("%")
                cfg_mgr.update(stop_loss=float(txt.rstrip("%")), stop_loss_is_percent=pct)
            elif awaiting == "min_payout":
                cfg_mgr.update(min_payout=float(txt))
            elif awaiting == "min_assertiveness":
                cfg_mgr.update(min_assertiveness=float(txt))
            elif awaiting == "mg_level":
                cfg_mgr.update_martingale(max_level=int(txt))
            elif awaiting == "mg_mult":
                cfg_mgr.update_martingale(multiplier=float(txt))
            elif awaiting == "manual_assets":
                assets = [a.strip().upper() for a in txt.split(",") if a.strip()]
                cfg_mgr.update(manual_assets=assets)
            elif awaiting == "max_open_trades":
                val = int(txt)
                if val < 1:
                    await update.effective_message.reply_text("⚠️ Mínimo é 1 trade aberto. Tente novamente:")
                    return
                cfg_mgr.update(max_open_trades=val)
            elif awaiting == "max_trades_day":
                val = int(txt)
                if val < 1 or val > 1000:
                    await update.effective_message.reply_text("⚠️ Valor entre <b>1</b> e <b>1000</b>. Tente novamente:", parse_mode="HTML")
                    return
                cfg_mgr.update(max_trades_per_day=val)
            elif awaiting == "max_loss_streak":
                val = int(txt)
                if val < 1 or val > 20:
                    await update.effective_message.reply_text("⚠️ Valor entre <b>1</b> e <b>20</b>. Tente novamente:", parse_mode="HTML")
                    return
                cfg_mgr.update(max_loss_streak=val)
            elif awaiting == "ssids":
                lines = [l.strip() for l in txt.splitlines() if l.strip()]
                if not lines:
                    await update.effective_message.reply_text("⚠️ Nenhum SSID encontrado. Envie um por linha:")
                    return
                feedback_lines = []
                valid = []
                for i, s in enumerate(lines):
                    if len(s) < 8:
                        feedback_lines.append(f"❌ SSID #{i+1}: muito curto (parece inválido)")
                    else:
                        valid.append(s)
                        feedback_lines.append(f"✅ SSID #{i+1}: <code>{s[:16]}…</code> salvo")
                if not valid:
                    await update.effective_message.reply_text(
                        "\n".join(feedback_lines) + "\n\n⚠️ Nenhum SSID válido. Tente novamente:",
                        parse_mode="HTML",
                    )
                    return
                cfg_mgr.update(po_ssids=valid)
                self.iface.user_data.pop(chat_id, None)
                await update.effective_message.reply_text(
                    "\n".join(feedback_lines)
                    + f"\n\n🔑 <b>{len(valid)} SSID(s) configurado(s).</b>\n"
                    "O bot usará em ordem de fallback na próxima conexão.",
                    reply_markup=self._main_menu(),
                    parse_mode="HTML",
                )
                return
            # sucesso — limpa awaiting e confirma
            self.iface.user_data.pop(chat_id, None)
            await update.effective_message.reply_text(
                f"✅ <b>{awaiting}</b> atualizado para <code>{txt}</code>",
                reply_markup=self._main_menu(),
                parse_mode="HTML",
            )
        except Exception as e:
            # mantém awaiting para o usuário poder tentar de novo
            await update.effective_message.reply_text(f"⚠️ Valor inválido: <code>{e}</code>. Tente novamente:", parse_mode="HTML")

    # --- helpers ---
    def _config_markup(self):
        """Monta o config_menu refletindo todos os toggles atuais."""
        cfg = self.iface.bot.cfg_manager.config
        return menus.config_menu(
            is_demo=cfg.po_demo,
            smart_reentry=cfg.smart_reentry,
            message_tone=getattr(cfg, "message_tone", "motivacional"),
            compact_messages=getattr(cfg, "compact_messages", False),
            explain_mode=getattr(cfg, "explain_mode", False),
        )

    # ────────────────── novos comandos de observabilidade ──────────────────
    async def cmd_timestats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """⏰ Mostra WR por hora + ajuste de score sendo aplicado agora."""
        if not self._authorized(update):
            return
        try:
            from ..strategies.time_stats import TIME_STATS
            from datetime import datetime, timezone, timedelta
            _BRT = timezone(timedelta(hours=-3))
            now_h = datetime.now(_BRT).hour
            data = TIME_STATS.hour_summary()
        except Exception as e:
            await update.effective_message.reply_text(f"❌ Erro: <code>{e}</code>", parse_mode="HTML")
            return
        if not data:
            await update.effective_message.reply_text(
                "⏰ Sem dados por hora ainda.", parse_mode="HTML"
            )
            return
        lines = [f"⏰ <b>WR por hora BRT</b> (agora: {now_h:02d}h)", ""]
        for h in sorted(data.keys()):
            d = data[h]
            wr, n = d["wr"], int(d["n"])
            if wr >= 60:
                emoji, adj = "🟢", "-0.5"
            elif wr >= 45:
                emoji, adj = "🟡", " 0.0"
            elif wr >= 35:
                emoji, adj = "🟠", "+1.0"
            else:
                emoji, adj = "🔴", "BLOCK"
            mark = " ◀️" if h == now_h else ""
            lines.append(f"{emoji} <b>{h:02d}h</b> · {wr:>5.1f}% · n={n:>3} · adj={adj}{mark}")
        # Defesa noturna
        if now_h >= 20 or now_h < 6:
            lines.append("\n🌙 <b>Modo defesa noturna ativo</b> (min_score≥9.0)")
        await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")

    async def cmd_cores(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """🧬 Mostra WR de cada núcleo do scalper por hora BRT."""
        if not self._authorized(update):
            return
        try:
            from ..strategies.core_stats import CORE_STATS, CORE_NAMES
            data = CORE_STATS.summary()
        except Exception as e:
            await update.effective_message.reply_text(f"❌ Erro: <code>{e}</code>", parse_mode="HTML")
            return
        lines = ["🧬 <b>WR por núcleo × hora BRT</b>", ""]
        any_data = False
        for core in CORE_NAMES:
            hours = data.get(core, {})
            if not hours:
                continue
            any_data = True
            # Ranqueia melhores horas
            sorted_h = sorted(hours.items(), key=lambda kv: -kv[1]["wr"])
            top = sorted_h[:3]
            bot_ = [h for h in sorted_h if h[1]["wr"] < 45][:2]
            ttxt = ", ".join(f"{h:02d}h({d['wr']:.0f}%·{int(d['n'])})" for h, d in top)
            btxt = ", ".join(f"{h:02d}h({d['wr']:.0f}%)" for h, d in bot_) or "—"
            lines.append(f"<b>{core}</b>")
            lines.append(f"  🟢 Top: {ttxt}")
            lines.append(f"  🔴 Ruim: {btxt}")
        if not any_data:
            lines.append("Sem amostra suficiente ainda (precisa ≥8 trades por core×hora).")
        await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")

    async def cmd_backtest(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """🔬 Mostra ranking baseado em backtest contínuo."""
        if not self._authorized(update):
            return
        try:
            from ..strategies.backtest import top_ranked, load_ranking
            top = top_ranked(limit=15, min_n=20)
            full = load_ranking()
        except Exception as e:
            await update.effective_message.reply_text(f"❌ Erro: <code>{e}</code>", parse_mode="HTML")
            return
        if not top:
            await update.effective_message.reply_text(
                "🔬 Backtest sem dados ainda. Roda a cada 1h em background — primeiro run 5min após start.",
                parse_mode="HTML",
            )
            return
        ts = next(iter(full.values())).get("ts", "?") if full else "?"
        lines = [f"🔬 <b>Backtest Ranking</b> (top 15, min 20 sinais)", f"<i>Última run: {ts}</i>", ""]
        for i, row in enumerate(top, 1):
            emoji = "🟢" if row["wr"] >= 60 else ("🟡" if row["wr"] >= 50 else "🔴")
            lines.append(f"{i:2d}. {emoji} <b>{row['combo']}</b> · WR {row['wr']:.1f}% · n={row['n']}")
        await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")

    async def cmd_now(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """🎯 Mostra os ativos+TF mais lucrativos PRA HORA ATUAL.

        Combina time_stats granular (asset×hora×TF) com payout atual ao vivo.
        Use /now pra decidir qual par operar agora.
        """
        if not self._authorized(update):
            return
        try:
            from ..strategies.time_stats import TIME_STATS
            from ..strategies.backtest import load_ranking
            from datetime import datetime, timezone, timedelta
        except Exception as e:
            await update.effective_message.reply_text(f"❌ Erro: <code>{e}</code>", parse_mode="HTML")
            return
        _BRT = timezone(timedelta(hours=-3))
        now = datetime.now(_BRT)
        h = now.hour
        # 1) Top combos pela HORA atual (real)
        top_hour = TIME_STATS.top_assets_for_hour(h, min_n=3, limit=10)
        # 2) Backtest ranking como fallback
        bt = load_ranking()
        # 3) Tenta enriquecer com payout ao vivo
        broker = getattr(self.iface.bot, "broker", None)
        async def _payout(asset: str) -> float:
            if not broker:
                return 0.0
            try:
                return float(await broker.get_payout(asset) or 0.0)
            except Exception:
                return 0.0

        lines = [
            f"🎯 <b>Operar AGORA — {h:02d}h BRT</b>",
            f"<i>{now.strftime('%d/%m %H:%M')} · baseado em histórico real desta hora</i>",
            "",
        ]
        if not top_hour:
            lines.append("⚠️ Sem histórico suficiente pra esta hora ainda (precisa ≥3 trades por combo).")
            lines.append("")
            # Fallback: mostra top do backtest geral
            if bt:
                lines.append("📊 <b>Fallback — Backtest geral (top 5):</b>")
                bt_rows = sorted(
                    [(k, v) for k, v in bt.items() if v.get("n", 0) >= 30],
                    key=lambda x: -x[1]["wr"],
                )[:5]
                for combo, v in bt_rows:
                    asset_, tf_ = combo.split("|") if "|" in combo else (combo, "?")
                    p = await _payout(asset_)
                    p_em = "✅" if p >= 80 else ("⚠️" if p >= 70 else "❌")
                    lines.append(
                        f"• <b>{asset_}</b> {tf_} · WR backtest {v['wr']:.1f}% · {p_em} payout {p:.0f}%"
                    )
            await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")
            return

        lines.append("<b>Ativo · TF · WR · n · Payout</b>")
        lines.append("─" * 28)
        for r in top_hour:
            p = await _payout(r["asset"])
            p_em = "✅" if p >= 80 else ("⚠️" if p >= 70 else "❌")
            wr_em = "🟢" if r["wr"] >= 60 else ("🟡" if r["wr"] >= 50 else "🔴")
            lines.append(
                f"{wr_em} <b>{r['asset']}</b> {r['tf']} · {r['wr']:.0f}% (n={r['n']}) · {p_em}{p:.0f}%"
            )
        lines.append("")
        lines.append("✅ payout ≥80% · ⚠️ 70-79% · ❌ <70%")
        lines.append("🟢 WR ≥60% · 🟡 50-59% · 🔴 <50%")
        await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")

    async def cmd_heatmap_visual(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """📈 Manda imagem PNG: heatmap 24h × últimos 7 dias com WR colorido."""
        if not self._authorized(update):
            return
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            from matplotlib.colors import LinearSegmentedColormap
            import numpy as np
        except ImportError:
            await update.effective_message.reply_text(
                "❌ <b>matplotlib não instalado</b>\n"
                "Instale: <code>pip install matplotlib</code>",
                parse_mode="HTML",
            )
            return
        try:
            from ..strategies.time_stats import TIME_STATS
            from datetime import datetime, timezone, timedelta
            import io
            import os
        except Exception as e:
            await update.effective_message.reply_text(f"❌ Erro: <code>{e}</code>", parse_mode="HTML")
            return

        days = 7
        matrix = TIME_STATS.heatmap_matrix(days=days)
        if not any(matrix.values()):
            await update.effective_message.reply_text(
                "📈 <b>Heatmap vazio</b> — sem dados nos últimos 7 dias.",
                parse_mode="HTML",
            )
            return

        # Constrói matriz (rows=dias, cols=24 horas) com WR (-1 = sem dado)
        date_keys = sorted(matrix.keys(), reverse=True)
        data = np.full((len(date_keys), 24), np.nan)
        labels_count = np.zeros((len(date_keys), 24), dtype=int)
        for i, d in enumerate(date_keys):
            for h in range(24):
                rec = matrix[d].get(h)
                if not rec:
                    continue
                w, l = rec["w"], rec["l"]
                total = w + l
                if total == 0:
                    continue
                data[i, h] = (w / total) * 100
                labels_count[i, h] = total

        # Cmap: vermelho → amarelo → verde
        cmap = LinearSegmentedColormap.from_list(
            "wr", ["#cc2222", "#ee9933", "#ffdd55", "#88cc44", "#22aa55"], N=256
        )
        cmap.set_bad(color="#222222")

        fig, ax = plt.subplots(figsize=(13, max(3, len(date_keys) * 0.55)))
        masked = np.ma.masked_invalid(data)
        im = ax.imshow(masked, aspect="auto", cmap=cmap, vmin=30, vmax=80)
        ax.set_xticks(range(24))
        ax.set_xticklabels([f"{h:02d}" for h in range(24)], fontsize=9)
        ax.set_yticks(range(len(date_keys)))
        ax.set_yticklabels(date_keys, fontsize=9)
        ax.set_xlabel("Hora BRT", fontsize=10)
        ax.set_title(f"📊 WR por hora · últimos {days} dias", fontsize=12, fontweight="bold")
        # Anotações: número de trades em cada célula
        for i in range(len(date_keys)):
            for h in range(24):
                if labels_count[i, h] > 0 and not np.isnan(data[i, h]):
                    color = "white" if data[i, h] < 50 else "black"
                    ax.text(h, i, f"{int(labels_count[i, h])}", ha="center", va="center",
                            fontsize=7, color=color)
        cbar = plt.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
        cbar.set_label("WR %", fontsize=9)
        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=110, facecolor="white")
        plt.close(fig)
        buf.seek(0)
        await update.effective_message.reply_photo(
            photo=buf,
            caption=(
                f"📈 <b>Heatmap WR — {days} dias</b>\n"
                "🟩 verde: WR alto · 🟧 laranja: médio · 🟥 vermelho: baixo · ⬛ cinza: sem dado\n"
                "Números = total de trades naquela hora"
            ),
            parse_mode="HTML",
        )

    async def cmd_alerts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """🔔 Liga/desliga alertas 'quase entrou'. Uso: /alerts on|off"""
        if not self._authorized(update):
            return
        arg = (context.args[0] if context.args else "").lower()
        bot = self.iface.bot
        if arg == "on":
            bot._alerts_enabled = True
            bot._alerted_near_misses.clear()
            await update.effective_message.reply_text("🔔 Alertas <b>ON</b> — vou avisar quando faltar pouco.", parse_mode="HTML")
        elif arg == "off":
            bot._alerts_enabled = False
            await update.effective_message.reply_text("🔕 Alertas <b>OFF</b>.", parse_mode="HTML")
        else:
            status = "ON" if getattr(bot, "_alerts_enabled", False) else "OFF"
            await update.effective_message.reply_text(
                f"🔔 Status: <b>{status}</b>\nUso: <code>/alerts on</code> ou <code>/alerts off</code>",
                parse_mode="HTML",
            )

    async def cmd_why(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """🔍 /why <ASSET> — mostra por que aquele ativo não está operando."""
        if not self._authorized(update):
            return
        if not context.args:
            await update.effective_message.reply_text(
                "Uso: <code>/why EURUSD-OTC</code>", parse_mode="HTML"
            )
            return
        asset = context.args[0].upper()
        info = self.iface.bot._last_block_reason.get(asset)
        if not info:
            await update.effective_message.reply_text(
                f"⚠️ Sem dados de scan recentes para <b>{asset}</b>.\n"
                "<i>Pode estar fora da watchlist atual.</i>",
                parse_mode="HTML",
            )
            return
        import time as _t
        age = int(_t.time() - info["ts"])
        await update.effective_message.reply_text(
            f"🔍 <b>{asset}</b> ({info['tf']}) — {age}s atrás\n"
            f"• Status: {info['reason']}\n"
            f"• Score: <b>{info['score']:.2f}</b> (min={info['min_score']:.1f})\n"
            f"• Faltou: <b>{max(0, info['min_score'] - info['score']):.2f}</b>",
            parse_mode="HTML",
        )

    async def cmd_topcores(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """🎯 Ranking dos núcleos do scalper (WR + N + status)."""
        if not self._authorized(update):
            return
        try:
            from ..strategies.scalper import SCALPER_RANKING
            data = SCALPER_RANKING.stats_summary()
        except Exception as e:
            await update.effective_message.reply_text(f"❌ Erro: <code>{e}</code>", parse_mode="HTML")
            return
        # filtra só núcleos (chave NÃO termina em :_TF_)
        cores = [(k, v) for k, v in data.items() if not k.endswith(":_TF_")]
        if not cores:
            await update.effective_message.reply_text("🎯 Sem dados de núcleos ainda.", parse_mode="HTML")
            return
        cores.sort(key=lambda kv: (-kv[1]["wr"], -kv[1]["n"]))
        lines = ["🎯 <b>Top núcleos do Scalper</b>", ""]
        for k, v in cores[:15]:
            emoji = "🟢" if v["wr"] >= 60 else ("🟡" if v["wr"] >= 45 else "🔴")
            status = "ON" if v["enabled"] else "OFF"
            lines.append(f"{emoji} <b>{k}</b> — {v['wr']:.1f}% (n={int(v['n'])}) [{status}]")
        await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")

    async def cmd_topativos(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """🏆 Top 10 ativos por PnL hoje."""
        if not self._authorized(update):
            return
        from datetime import datetime, timezone, timedelta
        _BRT = timezone(timedelta(hours=-3))
        today = datetime.now(_BRT).strftime("%Y-%m-%d")
        hist = [
            t for t in self.iface.bot.state.history
            if getattr(t, "timestamp", "").startswith(today)
            and t.result in ("WIN", "LOSS", "DRAW")
        ]
        if not hist:
            await update.effective_message.reply_text("📊 Sem trades hoje ainda.", parse_mode="HTML")
            return
        agg: dict = {}
        for t in hist:
            d = agg.setdefault(t.asset, {"pnl": 0.0, "w": 0, "l": 0})
            d["pnl"] += t.profit
            if t.result == "WIN":
                d["w"] += 1
            elif t.result == "LOSS":
                d["l"] += 1
        ranked = sorted(agg.items(), key=lambda kv: kv[1]["pnl"], reverse=True)
        lines = ["🏆 <b>Top ativos hoje</b> (por PnL)", ""]
        for i, (a, d) in enumerate(ranked[:10], 1):
            tot = d["w"] + d["l"]
            wr = (d["w"] / tot * 100) if tot else 0
            emoji = "🟢" if d["pnl"] > 0 else "🔴"
            lines.append(f"{i}. {emoji} <b>{a}</b> — ${d['pnl']:+.2f} ({d['w']}W/{d['l']}L · {wr:.0f}%)")
        await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")

    async def cmd_heatmap(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """🌡️ Heatmap WR por ativo × hora (top 10 ativos)."""
        if not self._authorized(update):
            return
        from datetime import datetime
        hist = [
            t for t in self.iface.bot.state.history
            if t.result in ("WIN", "LOSS")
        ]
        if not hist:
            await update.effective_message.reply_text("📊 Sem histórico ainda.", parse_mode="HTML")
            return
        # Agg por (asset, hora)
        agg: dict = {}
        asset_total: dict = {}
        for t in hist:
            try:
                ts = datetime.fromisoformat(t.timestamp.replace("Z", "+00:00"))
                hour = ts.hour
            except Exception:
                continue
            k = (t.asset, hour)
            d = agg.setdefault(k, {"w": 0, "l": 0})
            if t.result == "WIN":
                d["w"] += 1
            else:
                d["l"] += 1
            asset_total[t.asset] = asset_total.get(t.asset, 0) + 1
        # Top 10 ativos por nº trades
        top_assets = sorted(asset_total.items(), key=lambda kv: kv[1], reverse=True)[:10]
        if not top_assets:
            await update.effective_message.reply_text("📊 Sem dados suficientes.", parse_mode="HTML")
            return
        # Linha de horas (0-23)
        lines = ["🌡️ <b>Heatmap WR por hora</b>", "<code>"]
        header = "ATIVO       " + "".join(f"{h:02d} " for h in range(0, 24, 2))
        lines.append(header)
        for asset, _ in top_assets:
            row = f"{asset[:11].ljust(11)} "
            for h in range(0, 24, 2):
                # Combina h e h+1
                w = sum(agg.get((asset, hh), {"w": 0})["w"] for hh in (h, h + 1))
                l = sum(agg.get((asset, hh), {"l": 0})["l"] for hh in (h, h + 1))
                tot = w + l
                if tot < 2:
                    row += "·· "
                else:
                    wr = w / tot
                    if wr >= 0.6:
                        row += "🟢 "
                    elif wr >= 0.45:
                        row += "🟡 "
                    else:
                        row += "🔴 "
            lines.append(row)
        lines.append("</code>")
        await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")

    def _authorized(self, update: Update) -> bool:
        cfg_chat = str(self.iface.bot.cfg_manager.config.telegram_chat_id)
        if not cfg_chat:
            return True  # primeira configuração — qualquer mensagem serve
        incoming = str(update.effective_chat.id) if update.effective_chat else ""
        return incoming == cfg_chat

    async def _handle_cfg(self, q, data: str):
        key = data.split(":", 1)[1]
        chat_id = q.message.chat_id
        if key == "min_score":
            self.iface.user_data[chat_id] = {"awaiting": "min_score"}
            cfg = self.iface.bot.cfg_manager.config
            await q.edit_message_text(
                f"🎯 Informe o <b>Score Mínimo</b> para considerar um sinal (atual: <b>{cfg.min_score:.1f}</b>)\n"
                "<i>Valores entre 1.0 e 10.0. Padrão: 5.0</i>",
                parse_mode="HTML",
            )
        elif key == "smart_reentry_toggle":
            cfg = self.iface.bot.cfg_manager.update(smart_reentry=not self.iface.bot.cfg_manager.config.smart_reentry)
            status = "ATIVADA" if cfg.smart_reentry else "DESATIVADA"
            await q.edit_message_text(
                f"🔁 Reentrada Inteligente <b>{status}</b>.",
                reply_markup=self._config_markup(),
                parse_mode="HTML",
            )
        elif key == "tone_toggle":
            current = getattr(self.iface.bot.cfg_manager.config, "message_tone", "motivacional")
            new_tone = "tecnico" if current != "tecnico" else "motivacional"
            self.iface.bot.cfg_manager.update(message_tone=new_tone)
            # propaga para o módulo messages sem precisar reiniciar o bot
            from . import messages as _msgs
            _msgs.set_mode(
                tone=new_tone,
                compact=self.iface.bot.cfg_manager.config.compact_messages,
            )
            label = "Motivacional" if new_tone == "motivacional" else "Técnico"
            await q.edit_message_text(
                f"💬 Tom das mensagens: <b>{label}</b>.\n"
                "<i>Próximas mensagens já usam o novo tom.</i>",
                reply_markup=self._config_markup(),
                parse_mode="HTML",
            )
        elif key == "compact_toggle":
            current = getattr(self.iface.bot.cfg_manager.config, "compact_messages", False)
            new_val = not current
            self.iface.bot.cfg_manager.update(compact_messages=new_val)
            from . import messages as _msgs
            _msgs.set_mode(
                tone=self.iface.bot.cfg_manager.config.message_tone,
                compact=new_val,
            )
            status = "ATIVADOS" if new_val else "DESATIVADOS"
            extra = (
                "\n<i>Cada trade aparece como UMA mensagem editada (sinal → IA → ordem → resultado).</i>"
                if new_val else
                "\n<i>Mensagens separadas (sinal, IA, ordem, resultado).</i>"
            )
            await q.edit_message_text(
                f"📱 Cards compactos <b>{status}</b>.{extra}",
                reply_markup=self._config_markup(),
                parse_mode="HTML",
            )
        elif key == "explain_toggle":
            current = getattr(self.iface.bot.cfg_manager.config, "explain_mode", False)
            new_val = not current
            self.iface.bot.cfg_manager.update(explain_mode=new_val)
            status = "ATIVADO" if new_val else "DESATIVADO"
            extra = (
                "\n<i>Cada sinal mostrará uma linha 💡 <b>Por quê</b> com confirmações, ATR, WR da hora e payout.</i>"
                if new_val else
                "\n<i>Cards voltam ao formato padrão.</i>"
            )
            await q.edit_message_text(
                f"💡 Modo explicação <b>{status}</b>.{extra}",
                reply_markup=self._config_markup(),
                parse_mode="HTML",
            )
        elif key == "entry_amount":
            self.iface.user_data[chat_id] = {"awaiting": "entry_amount"}
            await q.edit_message_text("💰 Informe o novo <b>valor de entrada</b> (ex: 2.50):", parse_mode="HTML")
        elif key == "stop_win":
            self.iface.user_data[chat_id] = {"awaiting": "stop_win"}
            await q.edit_message_text("🎯 Informe o <b>Stop Win</b> (ex: 25 ou 10%):", parse_mode="HTML")
        elif key == "stop_loss":
            self.iface.user_data[chat_id] = {"awaiting": "stop_loss"}
            await q.edit_message_text("🛑 Informe o <b>Stop Loss</b> (ex: 15 ou 5%):", parse_mode="HTML")
        elif key == "min_payout":
            self.iface.user_data[chat_id] = {"awaiting": "min_payout"}
            await q.edit_message_text("📈 Informe o <b>Payout mínimo</b> (%):", parse_mode="HTML")
        elif key == "min_assertiveness":
            self.iface.user_data[chat_id] = {"awaiting": "min_assertiveness"}
            await q.edit_message_text("✅ Informe a <b>Assertividade mínima</b> (%):", parse_mode="HTML")
        elif key == "timeframes":
            cfg = self.iface.bot.cfg_manager.config
            await q.edit_message_text("⏱️ Timeframes:", reply_markup=menus.timeframes_menu(cfg.timeframes))
        elif key == "asset_mode":
            cfg = self.iface.bot.cfg_manager.config
            await q.edit_message_text("🕹️ Modo de ativos:", reply_markup=menus.asset_mode_menu(cfg.asset_mode))
        elif key == "martingale":
            cfg = self.iface.bot.cfg_manager.config
            await q.edit_message_text("🎲 Martingale:", reply_markup=menus.martingale_menu(cfg.martingale))
        elif key == "max_open_trades":
            cfg = self.iface.bot.cfg_manager.config
            self.iface.user_data[q.message.chat_id] = {"awaiting": "max_open_trades"}
            await q.edit_message_text(
                f"🔢 Informe o <b>máximo de trades abertos simultâneos</b> (atual: {cfg.max_open_trades}):\n"
                "<i>Ao atingir esse limite, o bot para de analisar até liberar uma vaga.</i>",
                parse_mode="HTML",
            )
        elif key == "max_trades_day":
            cfg = self.iface.bot.cfg_manager.config
            self.iface.user_data[q.message.chat_id] = {"awaiting": "max_trades_day"}
            await q.edit_message_text(
                f"📆 Informe o <b>máximo de trades por dia</b> (atual: {cfg.max_trades_per_day}):\n"
                "<i>Ao atingir esse número, o bot encerra operações do dia.</i>",
                parse_mode="HTML",
            )
        elif key == "max_loss_streak":
            cfg = self.iface.bot.cfg_manager.config
            self.iface.user_data[q.message.chat_id] = {"awaiting": "max_loss_streak"}
            await q.edit_message_text(
                f"🔥 Informe o <b>streak de losses máximo</b> (atual: {cfg.max_loss_streak}):\n"
                "<i>Ao atingir essa sequência de derrotas, o bot para automaticamente.</i>",
                parse_mode="HTML",
            )
        elif key == "ssids":
            cfg = self.iface.bot.cfg_manager.config
            count = len(cfg.po_ssids)
            current = "\n".join(f"  #{i+1}: <code>{s[:12]}…</code>" for i, s in enumerate(cfg.po_ssids)) if cfg.po_ssids else "  <i>Nenhum configurado</i>"
            self.iface.user_data[q.message.chat_id] = {"awaiting": "ssids"}
            await q.edit_message_text(
                f"🔑 <b>SSIDs Pocket Option</b> ({count} configurado(s)):\n{current}\n\n"
                "Envie os SSIDs, <b>um por linha</b>.\n"
                "O bot tenta o primeiro, se falhar usa o segundo, e assim por diante.\n"
                "<i>Substituirá todos os SSIDs atuais.</i>\n\n"
                "📋 <b>Como obter um SSID válido:</b>\n"
                "1. Abra pocketoption.com no navegador e faça login\n"
                "2. Pressione <b>F12</b> → aba <b>Network</b> → filtre por <b>WS</b>\n"
                "3. Clique na conexão WebSocket → aba <b>Messages</b>\n"
                "4. Procure uma mensagem começando com <code>42[\"auth\",</code>\n"
                "5. Copie a mensagem <b>completa</b> e envie aqui\n\n"
                "Formato esperado:\n"
                "<code>42[\"auth\",{\"session\":\"TOKEN\",\"isDemo\":0,\"uid\":123,\"platform\":1}]</code>",
                parse_mode="HTML",
            )
        elif key == "ai_toggle":
            cfg = self.iface.bot.cfg_manager.update(ai_enabled=not self.iface.bot.cfg_manager.config.ai_enabled)
            await q.edit_message_text(
                f"🧠 IA {'ATIVADA' if cfg.ai_enabled else 'DESATIVADA'}",
                reply_markup=self._config_markup(),
            )
        elif key == "demo_toggle":
            cfg = self.iface.bot.cfg_manager.config
            new_demo = not cfg.po_demo
            slot = cfg.account_slot_demo_ssid if new_demo else cfg.account_slot_real_ssid
            conta = "DEMO 🟢" if new_demo else "REAL 🔴"
            if not slot:
                # Sem slot salvo: precisa capturar SSID da nova conta no Chrome
                await q.edit_message_text(
                    f"⚠️ <b>Sem SSID salvo para conta {conta}</b>\n\n"
                    f"1. Troque para a conta {conta} no Chrome (pocketoption.com)\n"
                    f"2. Vou disparar a captura automática agora\n"
                    f"3. Depois use o menu de Contas para salvar o slot",
                    parse_mode="HTML",
                )
                # Atualiza flag e dispara captura
                self.iface.bot.cfg_manager.update(po_demo=new_demo)
                if self.iface.bot.state.running:
                    await self.iface.bot.stop_trading()
                import asyncio as _aio
                _aio.create_task(self.iface.bot._trigger_auto_capture())
                return
            # Slot existe: para o bot, atualiza flag, carrega SSID da conta correta
            self.iface.bot.cfg_manager.update(po_demo=new_demo)
            if self.iface.bot.state.running:
                await self.iface.bot.stop_trading()
            await q.edit_message_text(
                f"🔄 Trocando para conta <b>{conta}</b>...",
                parse_mode="HTML",
            )
            try:
                await self.iface.bot.broker.update_ssid(slot)
                await q.edit_message_text(
                    f"✅ Agora operando em conta <b>{conta}</b>. Use ▶️ Iniciar Bot.",
                    reply_markup=self._config_markup(),
                    parse_mode="HTML",
                )
            except Exception as e:
                await q.edit_message_text(
                    f"❌ Falha ao carregar SSID {conta}: <code>{e}</code>\n\n"
                    f"O SSID pode ter expirado. Capture novamente.",
                    reply_markup=self._config_markup(),
                    parse_mode="HTML",
                )

    async def _handle_account_slot(self, q, data: str):
        """🔀 Multi-conta: salva/carrega slots REAL e DEMO."""
        _, action = data.split(":", 1)
        cfg = self.iface.bot.cfg_manager.config
        if action == "noop":
            await q.answer()
            return
        # Pega SSID atual da sessão
        try:
            sess = self.iface.bot.broker.session.load()
            current_ssid = (sess or {}).get("ssid", "")
        except Exception:
            current_ssid = ""
        if action == "save_real":
            if not current_ssid:
                await q.answer("⚠️ Nenhum SSID ativo para salvar", show_alert=True)
                return
            self.iface.bot.cfg_manager.update(account_slot_real_ssid=current_ssid)
            await q.answer("✅ SSID atual salvo como REAL")
        elif action == "save_demo":
            if not current_ssid:
                await q.answer("⚠️ Nenhum SSID ativo para salvar", show_alert=True)
                return
            self.iface.bot.cfg_manager.update(account_slot_demo_ssid=current_ssid)
            await q.answer("✅ SSID atual salvo como DEMO")
        elif action in ("load_real", "load_demo"):
            is_demo = action == "load_demo"
            slot = cfg.account_slot_demo_ssid if is_demo else cfg.account_slot_real_ssid
            if not slot:
                await q.answer("⚠️ Slot vazio. Salve primeiro.", show_alert=True)
                return
            try:
                self.iface.bot.cfg_manager.update(po_demo=is_demo)
                if self.iface.bot.state.running:
                    await self.iface.bot.stop_trading()
                # update_ssid já desconecta, salva sessão e reconecta
                await self.iface.bot.broker.update_ssid(slot)
                conta = "DEMO 🟢" if is_demo else "REAL 🔴"
                await q.answer(f"✅ Conta {conta} carregada")
            except Exception as e:
                await q.answer(f"❌ Falha: {e}", show_alert=True)
                return
        # Recarrega o menu
        cfg = self.iface.bot.cfg_manager.config
        await q.edit_message_reply_markup(
            reply_markup=menus.accounts_menu(
                has_real=bool(cfg.account_slot_real_ssid),
                has_demo=bool(cfg.account_slot_demo_ssid),
                current_demo=cfg.po_demo,
            )
        )

    async def _handle_asset_mode(self, q, data: str):
        _, action, *rest = data.split(":")
        if action == "set":
            mode = rest[0]
            self.iface.bot.cfg_manager.update(asset_mode=mode)
            cfg = self.iface.bot.cfg_manager.config
            await q.edit_message_text(f"✅ Modo de ativos: <b>{mode}</b>",
                                      reply_markup=menus.asset_mode_menu(cfg.asset_mode), parse_mode="HTML")
        elif action == "edit":
            self.iface.user_data[q.message.chat_id] = {"awaiting": "manual_assets"}
            await q.edit_message_text(
                "✏️ Envie os ativos separados por vírgula (ex: EURUSD-OTC, XAUUSD-OTC):",
                parse_mode="HTML"
            )

    async def _handle_martingale(self, q, data: str):
        _, action = data.split(":", 1)
        cfg_mgr = self.iface.bot.cfg_manager
        cfg = cfg_mgr.config
        if action == "toggle":
            cfg_mgr.update_martingale(enabled=not cfg.martingale.enabled)
        elif action == "reset":
            cfg_mgr.update_martingale(reset_after_win=not cfg.martingale.reset_after_win)
        elif action == "mode":
            current = getattr(cfg.martingale, "mode", "next_signal")
            new_mode = "next_candle" if current == "next_signal" else "next_signal"
            cfg_mgr.update_martingale(mode=new_mode)
        elif action == "level":
            self.iface.user_data[q.message.chat_id] = {"awaiting": "mg_level"}
            await q.edit_message_text("Informe o nível máximo do martingale (1-5):")
            return
        elif action == "mult":
            self.iface.user_data[q.message.chat_id] = {"awaiting": "mg_mult"}
            await q.edit_message_text("Informe o multiplicador (ex: 2.2):")
            return
        await q.edit_message_text("🎲 Martingale:", reply_markup=menus.martingale_menu(cfg_mgr.config.martingale))
