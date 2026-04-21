"""Handlers de callbacks do Telegram."""
from __future__ import annotations

from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from . import menus
from ..utils.logger import get_logger

logger = get_logger("telegram.handlers")


class Handlers:
    """Conjunto de handlers delegados à interface principal."""

    def __init__(self, iface: "TelegramInterface"):  # noqa: F821
        self.iface = iface

    # --- comandos ---
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return
        await update.message.reply_text(
            "🧠 <b>Pocket Brainy</b>\nEscolha uma opção:",
            reply_markup=menus.main_menu(),
            parse_mode="HTML",
        )

    async def cmd_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await self.cmd_start(update, context)

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return
        await update.message.reply_text(
            self.iface.render_stats(),
            reply_markup=menus.back_menu(),
            parse_mode="HTML",
        )

    # --- callbacks ---
    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()
        if not self._authorized(update):
            return
        data = q.data or ""
        try:
            if data == "menu:main":
                await q.edit_message_text("🧠 Menu principal:", reply_markup=menus.main_menu())
            elif data == "menu:config":
                is_demo = self.iface.bot.cfg_manager.config.po_demo
                await q.edit_message_text(self.iface.render_config(), reply_markup=menus.config_menu(is_demo=is_demo), parse_mode="HTML")
            elif data == "menu:strategies":
                statuses = self.iface.bot.strategies.list_status()
                await q.edit_message_text("🧠 <b>Estratégias</b>\nToque para ativar/desativar:",
                                          reply_markup=menus.strategies_menu(statuses), parse_mode="HTML")
            elif data == "menu:status":
                await q.edit_message_text(self.iface.render_status(), reply_markup=menus.back_menu(), parse_mode="HTML")
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
                msg = await self.iface.bot.start_trading()
                await q.edit_message_text(msg, reply_markup=menus.main_menu(), parse_mode="HTML")
            elif data == "bot:stop":
                msg = await self.iface.bot.stop_trading()
                await q.edit_message_text(msg, reply_markup=menus.main_menu(), parse_mode="HTML")
            elif data == "bot:reconnect":
                msg = await self.iface.bot.reconnect()
                await q.edit_message_text(msg, reply_markup=menus.main_menu(), parse_mode="HTML")
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
            await update.message.reply_text("Use /menu para abrir o painel.", reply_markup=menus.main_menu())
            return
        txt = (update.message.text or "").strip()
        try:
            cfg_mgr = self.iface.bot.cfg_manager
            if awaiting == "entry_amount":
                val = float(txt)
                if val < 1.0:
                    await update.message.reply_text(
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
                    await update.message.reply_text("⚠️ Mínimo é 1 trade aberto. Tente novamente:")
                    return
                cfg_mgr.update(max_open_trades=val)
            elif awaiting == "ssids":
                lines = [l.strip() for l in txt.splitlines() if l.strip()]
                if not lines:
                    await update.message.reply_text("⚠️ Nenhum SSID encontrado. Envie um por linha:")
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
                    await update.message.reply_text(
                        "\n".join(feedback_lines) + "\n\n⚠️ Nenhum SSID válido. Tente novamente:",
                        parse_mode="HTML",
                    )
                    return
                cfg_mgr.update(po_ssids=valid)
                self.iface.user_data.pop(chat_id, None)
                await update.message.reply_text(
                    "\n".join(feedback_lines)
                    + f"\n\n🔑 <b>{len(valid)} SSID(s) configurado(s).</b>\n"
                    "O bot usará em ordem de fallback na próxima conexão.",
                    reply_markup=menus.main_menu(),
                    parse_mode="HTML",
                )
                return
            # sucesso — limpa awaiting e confirma
            self.iface.user_data.pop(chat_id, None)
            await update.message.reply_text(
                f"✅ <b>{awaiting}</b> atualizado para <code>{txt}</code>",
                reply_markup=menus.main_menu(),
                parse_mode="HTML",
            )
        except Exception as e:
            # mantém awaiting para o usuário poder tentar de novo
            await update.message.reply_text(f"⚠️ Valor inválido: <code>{e}</code>. Tente novamente:", parse_mode="HTML")

    # --- helpers ---
    def _authorized(self, update: Update) -> bool:
        cfg_chat = str(self.iface.bot.cfg_manager.config.telegram_chat_id)
        if not cfg_chat:
            return True  # primeira configuração — qualquer mensagem serve
        incoming = str(update.effective_chat.id) if update.effective_chat else ""
        return incoming == cfg_chat

    async def _handle_cfg(self, q, data: str):
        key = data.split(":", 1)[1]
        chat_id = q.message.chat_id
        if key == "entry_amount":
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
                reply_markup=menus.config_menu(is_demo=cfg.po_demo),
            )
        elif key == "sim_toggle":
            cfg = self.iface.bot.cfg_manager.update(
                simulation_mode=not self.iface.bot.cfg_manager.config.simulation_mode)
            await q.edit_message_text(
                f"🧪 Simulação {'ATIVA' if cfg.simulation_mode else 'DESATIVADA'}",
                reply_markup=menus.config_menu(is_demo=cfg.po_demo),
            )
        elif key == "demo_toggle":
            new_demo = not self.iface.bot.cfg_manager.config.po_demo
            self.iface.bot.cfg_manager.update(po_demo=new_demo)
            conta = "DEMO 🟢" if new_demo else "REAL 🔴"
            # Para o bot se estiver rodando e reconecta na nova conta
            if self.iface.bot.state.running:
                await self.iface.bot.stop_trading()
                await q.edit_message_text(
                    f"🔄 Trocando para conta <b>{conta}</b> e reconectando...",
                    parse_mode="HTML",
                )
                await self.iface.bot.reconnect()
            await q.edit_message_text(
                f"✅ Agora operando em conta <b>{conta}</b>. Use ▶️ Iniciar Bot para começar.",
                reply_markup=menus.config_menu(is_demo=new_demo),
                parse_mode="HTML",
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
        elif action == "level":
            self.iface.user_data[q.message.chat_id] = {"awaiting": "mg_level"}
            await q.edit_message_text("Informe o nível máximo do martingale (1-5):")
            return
        elif action == "mult":
            self.iface.user_data[q.message.chat_id] = {"awaiting": "mg_mult"}
            await q.edit_message_text("Informe o multiplicador (ex: 2.2):")
            return
        await q.edit_message_text("🎲 Martingale:", reply_markup=menus.martingale_menu(cfg_mgr.config.martingale))
