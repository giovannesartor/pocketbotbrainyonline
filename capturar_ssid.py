"""
Abre um navegador real, você faz login manualmente na Pocket Option,
e o script captura o SSID automaticamente e salva no config.
"""
import asyncio
import json
import re
from pathlib import Path


def _extract_ssid(payload: str):
    if not isinstance(payload, str):
        return None
    if "session" not in payload and "auth" not in payload:
        return None
    m = re.search(r'"session"\s*:\s*"([^"]+)"', payload)
    if m:
        return payload  # retorna o frame completo
    return None


async def main():
    from playwright.async_api import async_playwright

    ssid_holder = [None]

    print("=" * 60)
    print("  Abrindo navegador — faça login na Pocket Option")
    print("  O script captura o SSID automaticamente após o login")
    print("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--start-maximized"],
        )
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = await ctx.new_page()

        def on_ws(ws):
            def on_frame(frame):
                if ssid_holder[0]:
                    return
                payload = frame if isinstance(frame, str) else getattr(frame, "payload", "")
                result = _extract_ssid(payload)
                if result:
                    ssid_holder[0] = result
            ws.on("framesent", on_frame)
            ws.on("framereceived", on_frame)

        page.on("websocket", on_ws)

        await page.goto("https://pocketoption.com/en/login", wait_until="domcontentloaded")

        print("\n  ▶ Faça login no navegador que abriu...")
        print("  ▶ Aguardando captura do SSID (até 3 minutos)\n")

        for i in range(180):
            if ssid_holder[0]:
                break
            await asyncio.sleep(1)
            if i % 10 == 0 and i > 0:
                print(f"  Aguardando... {i}s")

        cookies = await ctx.cookies()
        await browser.close()

    if not ssid_holder[0]:
        print("\n❌ SSID não capturado. Tente novamente.")
        return

    ssid = ssid_holder[0]
    print(f"\n✅ SSID capturado com sucesso!")
    print(f"   {ssid[:60]}...")

    # Salva no session.json
    import time
    session_path = Path(__file__).parent / "pocket_brainy" / "data" / "session.json"
    session_path.parent.mkdir(exist_ok=True)
    data = {
        "ssid": ssid,
        "cookies": cookies,
        "_ts": time.time(),
        "_ts_cookies": time.time(),
    }
    with open(session_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Sessão salva em: {session_path}")
    print("   O bot vai usar esse SSID automaticamente.")
    print("   Quando expirar (~4h), ele renova sozinho pelos cookies.")
    print("\n   Pode iniciar o bot agora! 🚀")


asyncio.run(main())
