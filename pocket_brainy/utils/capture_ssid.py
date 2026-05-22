"""Captura de SSID da Pocket Option via Playwright (login manual no browser).

Captura o FRAME WebSocket completo de autenticação:
    42["auth",{"session":"...","isDemo":N,"uid":N,"platform":N}]

A biblioteca pocketoptionapi-async aceita esse formato diretamente.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Callable, Optional

SESSION_PATH = Path(__file__).parent.parent / "data" / "session.json"


def _is_auth_frame(payload: str) -> bool:
    """True se o payload é o frame de autenticação completo do PocketOption."""
    if not isinstance(payload, str):
        return False
    p = payload.strip()
    if not p.startswith('42["auth",'):
        return False
    # Garante que tem session ou ssid dentro
    return '"session"' in p or '"ssid"' in p


def _validate_auth_frame(payload: str) -> Optional[str]:
    """Valida se o frame é um auth frame válido. Retorna o frame se OK."""
    if not _is_auth_frame(payload):
        return None
    try:
        data = json.loads(payload[2:])
        if not (isinstance(data, list) and len(data) >= 2 and isinstance(data[1], dict)):
            return None
        d = data[1]
        session = d.get("session") or d.get("ssid")
        if not session or len(str(session)) < 10:
            return None
        return payload.strip()
    except Exception:
        return None


async def capture_ssid_async(
    on_progress: Optional[Callable[[str], None]] = None,
    timeout_seconds: int = 180,
    prefer_demo: bool = True,
) -> Optional[str]:
    """Abre o Chrome do sistema com perfil persistente e captura o frame de auth.

    Fluxo:
      • Primeira vez: abre /cabinet/, redireciona pro login, usuário loga, captura.
      • Próximas vezes: perfil persistente já tem cookies → vai direto pro cabinet,
        Pocket Option estabelece WebSocket sozinho, frame é capturado em segundos.

    Retorna o frame Socket.IO completo '42["auth",{...}]' pronto para uso.
    """
    from playwright.async_api import async_playwright

    auth_frame_holder: list[Optional[str]] = [None]

    def _notify(msg: str) -> None:
        if on_progress:
            try:
                on_progress(msg)
            except Exception:
                pass

    # Perfil persistente: cookies sobrevivem entre execuções
    profile_dir = Path(__file__).parent.parent / "data" / "chrome_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        _notify("🚀 Abrindo Chrome do sistema...")

        # Tenta usar o Chrome instalado (channel="chrome"); se falhar, cai no Chromium
        ctx = None
        last_err = None
        for channel_try in ("chrome", "chrome-beta", "msedge", None):
            try:
                ctx = await p.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    channel=channel_try,         # None = Chromium do Playwright
                    headless=False,
                    viewport={"width": 1280, "height": 800},
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--disable-dev-shm-usage",
                    ],
                )
                if channel_try:
                    _notify(f"✅ Usando navegador: {channel_try}")
                else:
                    _notify("✅ Usando Chromium (fallback — Chrome não encontrado)")
                break
            except Exception as e:
                last_err = e
                continue

        if ctx is None:
            _notify(f"❌ Não consegui abrir nenhum navegador: {last_err}")
            return None

        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()

            def on_ws(ws):
                def on_frame(frame):
                    if auth_frame_holder[0]:
                        return
                    payload = frame if isinstance(frame, str) else getattr(frame, "payload", "")
                    if not isinstance(payload, str):
                        return
                    validated = _validate_auth_frame(payload)
                    if validated:
                        auth_frame_holder[0] = validated
                        print(f"\n  [OK] Frame de autenticação capturado ({len(validated)} chars)")

                ws.on("framesent", on_frame)
                ws.on("framereceived", on_frame)

            page.on("websocket", on_ws)

            try:
                # Demo: cabinet/demo-quick-high-low/ ; Real: cabinet/quick-high-low/
                target_url = (
                    "https://pocketoption.com/en/cabinet/demo-quick-high-low/"
                    if prefer_demo else
                    "https://pocketoption.com/en/cabinet/"
                )
                await page.goto(
                    target_url,
                    wait_until="commit",
                    timeout=20_000,
                )
                _notify(f"✅ Página aberta ({'DEMO' if prefer_demo else 'REAL'}). Se não estiver logado, faça login.")
            except Exception as e:
                _notify(f"⚠️ Falha ao carregar página: {e}. Tentando mesmo assim.")

            # Aguarda o frame de auth — filtra pelo isDemo desejado se possível
            target_demo = 1 if prefer_demo else 0
            best_frame = None
            for i in range(timeout_seconds):
                if auth_frame_holder[0]:
                    # Verifica se o isDemo bate com o desejado
                    try:
                        _data = json.loads(auth_frame_holder[0][2:])
                        _frame_demo = int(_data[1].get("isDemo", -1))
                        if _frame_demo == target_demo:
                            break  # match perfeito
                        # Frame errado capturado — guarda como fallback e continua aguardando
                        if best_frame is None:
                            best_frame = auth_frame_holder[0]
                            _notify(f"⚠️ Capturei frame {'REAL' if _frame_demo == 0 else 'DEMO'}, "
                                    f"aguardando {'DEMO' if target_demo == 1 else 'REAL'}...")
                            auth_frame_holder[0] = None  # reseta para capturar novo
                    except Exception:
                        break  # frame estranho, usa mesmo assim
                await asyncio.sleep(1)
                if (i + 1) % 30 == 0:
                    _notify(f"Aguardando frame de auth... {i + 1}s")

            # Se não pegou o ideal, usa o que tiver
            if not auth_frame_holder[0] and best_frame:
                auth_frame_holder[0] = best_frame
                _notify("⚠️ Usando frame disponível (mesmo com isDemo diferente do solicitado).")

            cookies = await ctx.cookies()
        finally:
            try:
                await ctx.close()
            except Exception:
                pass

    if not auth_frame_holder[0]:
        return None

    auth_frame = auth_frame_holder[0]

    SESSION_PATH.parent.mkdir(exist_ok=True)
    data = {
        "ssid": auth_frame,
        "cookies": cookies,
        "_ts": time.time(),
        "_ts_cookies": time.time(),
    }
    with open(SESSION_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return auth_frame

