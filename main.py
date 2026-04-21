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

from pocket_brainy.core.bot import PocketBrainyBot
from pocket_brainy.telegram.bot import TelegramInterface
from pocket_brainy.utils.logger import setup_logging, get_logger


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

    try:
        await stop_event.wait()
    finally:
        log.info("Encerrando...")
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
