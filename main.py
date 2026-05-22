"""
Pocket Brainy — entrada principal.

Execute com:
    python main.py

Antes de rodar pela primeira vez, edite pocket_brainy/data/config.json
com suas credenciais (Pocket Option + Telegram) — ou rode o bot, envie
/menu no Telegram e configure pelos botões.
"""
from __future__ import annotations

import asyncio
import signal
import sys

from pocket_brainy.core.bot import PocketBrainyBot
from pocket_brainy.telegram.bot import TelegramInterface
from pocket_brainy.utils.logger import setup_logging, get_logger


async def _stdin_ssid_listener(bot: PocketBrainyBot, log) -> None:
    """Lê linhas do terminal — se o usuário digitar 'ssid <token>', atualiza o SSID.

    Permite colar o SSID direto no terminal sem precisar abrir o Telegram.
    Aceita também só o token bruto (sem o prefixo 'ssid').
    """
    loop = asyncio.get_running_loop()
    while True:
        try:
            line = await loop.run_in_executor(None, sys.stdin.readline)
        except Exception:
            return
        if not line:
            await asyncio.sleep(1)
            continue
        line = line.strip()
        if not line:
            continue
        # Aceita 'ssid <token>' OU só o token (se começar com 42[" ou tiver tamanho razoável)
        token = None
        low = line.lower()
        if low.startswith("ssid "):
            token = line[5:].strip()
        elif line.startswith('42["auth"') or (len(line) >= 30 and " " not in line):
            token = line
        if not token:
            continue
        log.info(f"SSID recebido pelo terminal ({len(token)} chars) — atualizando...")
        try:
            result = await bot.update_ssid(token)
            log.info(f"Resultado: {result}")
            log.info("✅ SSID salvo. Use ▶️ Iniciar Bot no Telegram (/menu) para começar a operar.")
        except Exception as e:
            log.error(f"Falha ao processar SSID do terminal: {e}")


async def run() -> None:
    setup_logging("INFO")
    log = get_logger("main")
    log.info("🧠 Pocket Brainy — inicializando...")

    bot = PocketBrainyBot()
    iface = TelegramInterface(bot)
    bot.telegram = iface

    stop_event = asyncio.Event()

    def _handle_signal(*_):
        log.info("Sinal recebido — encerrando.")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            pass

    await iface.start()

    # Listener de stdin para colar SSID direto no terminal
    stdin_task = asyncio.create_task(_stdin_ssid_listener(bot, log))

    # � Standby: NÃO inicia trading automaticamente.
    # Use o Telegram (/menu → ▶️ Iniciar Bot) para configurar conta/credenciais
    # e começar a operar quando estiver pronto.
    log.info("=" * 60)
    log.info("🟡 STANDBY — bot inicializado mas SEM operar.")
    log.info("   Abra o Telegram, mande /menu e clique em ▶️ Iniciar Bot.")
    log.info("   Antes disso você pode ajustar conta (demo/real), valores etc.")
    log.info("=" * 60)

    try:
        await stop_event.wait()
    finally:
        log.info("Encerrando...")
        stdin_task.cancel()
        await bot.stop_trading()
        await iface.stop()
        if bot.broker:
            await bot.broker.disconnect()
        log.info("Finalizado.")


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
