"""Captura de SSID da Pocket Option via Playwright (login manual no browser).

Captura o FRAME WebSocket completo de autenticação:
    42["auth",{"session":"...","isDemo":N,"uid":N,"platform":N}]

A biblioteca pocketoptionapi-async aceita esse formato diretamente.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Callable, Optional

_DATA_DIR = Path(os.environ.get(
    "POCKET_DATA_DIR",
    str(Path(__file__).parent.parent / "data"),
))
SESSION_PATH = _DATA_DIR / "session.json"


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
    email: Optional[str] = None,
    password: Optional[str] = None,
    headless: Optional[bool] = None,
) -> Optional[str]:
    """Abre o Chrome do sistema com perfil persistente e captura o frame de auth.

    Fluxo:
      • Primeira vez: abre /cabinet/, redireciona pro login, usuário loga, captura.
      • Próximas vezes: perfil persistente já tem cookies → vai direto pro cabinet,
        Pocket Option estabelece WebSocket sozinho, frame é capturado em segundos.

    Em ambiente headless (Railway/Docker) usa `headless=True` automaticamente e,
    se `email`+`password` forem fornecidos, tenta o login automático.

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

    # Decide headless: parâmetro explícito > env var POCKET_HEADLESS > default False
    if headless is None:
        headless = os.environ.get("POCKET_HEADLESS", "").lower() in ("1", "true", "yes")

    # Perfil persistente: cookies sobrevivem entre execuções
    profile_dir = _DATA_DIR / "chrome_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        _notify("🚀 Abrindo Chrome do sistema...")

        # Tenta usar o Chrome instalado (channel="chrome"); se falhar, cai no Chromium
        ctx = None
        last_err = None
        # Em headless, vai direto pro Chromium (channel=None) — canais "chrome"/"msedge"
        # raramente existem em containers Linux e geram erro de canal não encontrado.
        channels_to_try = (None,) if headless else ("chrome", "chrome-beta", "msedge", None)
        for channel_try in channels_to_try:
            try:
                ctx = await p.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    channel=channel_try,         # None = Chromium do Playwright
                    headless=headless,
                    viewport={"width": 1280, "height": 800},
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--disable-dev-shm-usage",
                        "--no-sandbox",
                    ],
                )
                if channel_try:
                    _notify(f"✅ Usando navegador: {channel_try}")
                else:
                    _notify("✅ Usando Chromium (headless)" if headless else "✅ Usando Chromium")
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

            # Auto-login: se headless e tiver credenciais, preenche o form de login
            if headless and email and password:
                try:
                    await _try_auto_login(page, email, password, prefer_demo, _notify)
                except Exception as e:
                    _notify(f"⚠️ Auto-login falhou: {e}")

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


async def _try_auto_login(page, email: str, password: str, prefer_demo: bool, notify) -> None:
    """Detecta o form de login da Pocket Option e preenche email/senha automaticamente.

    PocketOption redireciona pra /login/ quando não há sessão. O formulário usa
    inputs `type="email"` e `type="password"` + botão `type="submit"`.
    """
    # Espera a página estabilizar (até 15s)
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=15_000)
    except Exception:
        pass

    url = page.url or ""
    if "login" not in url and "auth" not in url:
        # já está logado (cookies persistentes) — nada a fazer
        return

    notify("🔐 Tela de login detectada — preenchendo credenciais...")

    # Preenche email
    email_sel = None
    for sel in ('input[type="email"]', 'input[name="email"]', 'input[name="login"]'):
        try:
            await page.wait_for_selector(sel, timeout=8_000)
            email_sel = sel
            break
        except Exception:
            continue
    if not email_sel:
        notify("⚠️ Não encontrei campo de email — formulário mudou?")
        return

    await page.fill(email_sel, email)
    await asyncio.sleep(0.3)

    pwd_sel = None
    for sel in ('input[type="password"]', 'input[name="password"]'):
        try:
            await page.wait_for_selector(sel, timeout=5_000)
            pwd_sel = sel
            break
        except Exception:
            continue
    if not pwd_sel:
        notify("⚠️ Não encontrei campo de senha — formulário mudou?")
        return

    await page.fill(pwd_sel, password)
    await asyncio.sleep(0.3)

    # Submit: tenta vários seletores comuns + Enter como fallback
    submitted = False
    for sel in (
        'button[type="submit"]',
        'form button:has-text("Sign in")',
        'form button:has-text("Login")',
        'form button:has-text("Entrar")',
    ):
        try:
            await page.click(sel, timeout=3_000)
            submitted = True
            break
        except Exception:
            continue
    if not submitted:
        try:
            await page.press(pwd_sel, "Enter")
            submitted = True
        except Exception:
            pass

    if not submitted:
        notify("⚠️ Não consegui clicar no botão de login.")
        return

    notify("⏳ Enviado — aguardando redirect pro cabinet...")
    # Aguarda navegação pro cabinet (até 20s)
    try:
        await page.wait_for_url("**/cabinet/**", timeout=20_000)
        notify("✅ Login OK — agora aguardando o frame de auth do WebSocket.")
    except Exception:
        notify("⚠️ Não houve redirect — possivelmente captcha/2FA bloqueou o login.")

