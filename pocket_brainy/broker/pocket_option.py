"""
Camada de abstração sobre a Pocket Option.

Usa pocketoptionapi-async (ChipaDevTeam) — AsyncPocketOptionClient.

Fluxo de autenticação:
  email + senha → Playwright headless → captura SSID via WebSocket
               → AsyncPocketOptionClient(ssid=..., is_demo=...)

Se Playwright ou a lib falharem, cai em modo MOCK determinístico para
permitir desenvolvimento/testes offline.
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..utils.indicators import Candle
from ..utils.logger import get_logger
from .session import SessionStore

logger = get_logger("broker.pocket_option")


class BrokerError(RuntimeError):
    pass


@dataclass
class TradeOrder:
    asset: str
    direction: str        # CALL | PUT
    amount: float
    expiration: int       # segundos
    order_id: Optional[str] = None


# ─────────────────────────────── SSID extractor ─────────────────────────────

def _extract_ssid_from_frame(payload: str) -> Optional[str]:
    """
    Extrai o token SSID de um frame WebSocket da Pocket Option.
    Formatos esperados:
      - 42["auth",{"ssid":"TOKEN"}]   (Socket.IO)
      - qualquer JSON contendo "ssid":"TOKEN"
    """
    m = re.search(r'"ssid"\s*:\s*"([^"]+)"', payload)
    if m:
        return m.group(1)
    try:
        raw = payload[2:] if payload.startswith("42") else payload
        data = json.loads(raw)
        if isinstance(data, list) and len(data) >= 2 and isinstance(data[1], dict):
            return data[1].get("ssid")
    except Exception:
        pass
    return None


# ─────────────────────────────── Playwright helpers ─────────────────────────

_STEALTH_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _playwright_context_opts() -> dict:
    """Opções de contexto Playwright para reduzir detecção de headless."""
    return {
        "user_agent": _STEALTH_UA,
        "viewport": {"width": 1280, "height": 800},
        "locale": "pt-BR",
        "timezone_id": "America/Sao_Paulo",
        "extra_http_headers": {
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        },
    }


def _build_ws_interceptor(ssid_holder: list):
    """Retorna handler de WebSocket que preenche ssid_holder[0] ao capturar SSID."""
    def on_ws(ws):
        def on_frame(frame):
            if ssid_holder[0]:
                return
            payload = frame if isinstance(frame, str) else getattr(frame, "payload", "")
            if isinstance(payload, str) and "ssid" in payload:
                extracted = _extract_ssid_from_frame(payload)
                if extracted:
                    ssid_holder[0] = extracted
        ws.on("framesent", on_frame)
        ws.on("framereceived", on_frame)
    return on_ws


async def _wait_for_ssid(ssid_holder: list, seconds: int = 40) -> bool:
    """Aguarda até `seconds` segundos pelo SSID."""
    for _ in range(seconds):
        if ssid_holder[0]:
            return True
        await asyncio.sleep(1)
    return False


async def _capture_ssid_with_cookies(cookies: list, demo: bool) -> Dict[str, Any]:
    """
    Usa cookies de sessão salvos para navegar direto ao painel de trading
    e capturar um SSID fresco via WebSocket, SEM precisar de login.
    Funciona enquanto a sessão (cookie) não expirar (~48-72h).
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        raise BrokerError("playwright não instalado.") from e

    ssid_holder = [None]
    trading_url = (
        "https://pocketoption.com/en/cabinet/demo-quick-high-low/"
        if demo
        else "https://pocketoption.com/en/cabinet/quick-high-low/"
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(**_playwright_context_opts())
        await ctx.add_cookies(cookies)
        page = await ctx.new_page()
        page.on("websocket", _build_ws_interceptor(ssid_holder))
        try:
            await page.goto(trading_url, wait_until="domcontentloaded", timeout=30_000)
            found = await _wait_for_ssid(ssid_holder, seconds=30)
        finally:
            await browser.close()

    if not found or not ssid_holder[0]:
        raise BrokerError("Cookies expirados ou SSID não capturado — faça login novamente.")
    return {"ssid": ssid_holder[0]}


async def _login_and_capture_ssid(email: str, password: str, demo: bool = True) -> Dict[str, Any]:
    """
    Faz login na plataforma Pocket Option via Playwright headless,
    intercepta o WebSocket e captura o SSID. Retorna dict {ssid, cookies}.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        raise BrokerError(
            "playwright não instalado. Rode: pip install playwright && playwright install chromium"
        ) from e

    ssid_holder = [None]
    cookies_dump: List[Dict[str, Any]] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(**_playwright_context_opts())
        page = await ctx.new_page()
        page.on("websocket", _build_ws_interceptor(ssid_holder))

        try:
            await page.goto("https://pocketoption.com/en/login", wait_until="domcontentloaded")
            await page.fill('input[name="email"]', email)
            await page.fill('input[name="password"]', password)
            await page.click('button[type="submit"]')
            await page.wait_for_load_state("networkidle", timeout=60_000)
            found = await _wait_for_ssid(ssid_holder, seconds=40)
            cookies_dump = await ctx.cookies()
        finally:
            await browser.close()

    if not found or not ssid_holder[0]:
        raise BrokerError(
            "Não foi possível capturar SSID — verifique credenciais ou proteção anti-bot."
        )
    return {"ssid": ssid_holder[0], "cookies": cookies_dump, "demo": demo}


# ─────────────────────────────── Mock client ────────────────────────────────

# OTC assets usados no mock e como fallback em get_assets
_DEFAULT_OTC_ASSETS = [
    "EURUSD_otc", "GBPUSD_otc", "USDJPY_otc",
    "AUDCAD_otc", "XAUUSD_otc", "EURJPY_otc",
]


class _MockClient:
    """Simula o cliente PocketOption para desenvolvimento offline."""

    def __init__(self, demo: bool):
        self._balance = 1_000.0 if demo else 0.0
        self._orders: Dict[str, Dict] = {}

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        pass

    async def get_balance(self) -> float:
        return self._balance

    async def get_payout(self, asset: str) -> float:
        random.seed(hash(asset) & 0xFFFFFFFF)
        return round(random.uniform(75, 92), 2)

    async def get_assets(self) -> List[Dict[str, Any]]:
        return [
            {"asset": a, "payout": round(random.uniform(75, 92), 2), "open": True}
            for a in _DEFAULT_OTC_ASSETS
        ]

    async def get_candles(self, asset: str, timeframe_s: int, count: int = 120) -> List[Candle]:
        now = int(time.time())
        random.seed((hash(asset) + timeframe_s) & 0xFFFFFFFF)
        price = 1.1000
        out: List[Candle] = []
        for i in range(count):
            drift = random.uniform(-0.001, 0.001)
            o = price
            c = price + drift
            h = max(o, c) + abs(random.uniform(0, 0.0004))
            lo = min(o, c) - abs(random.uniform(0, 0.0004))
            out.append(Candle(now - (count - i) * timeframe_s, o, h, lo, c, volume=random.uniform(50, 500)))
            price = c
        return out

    async def place_trade(self, order: TradeOrder) -> TradeOrder:
        oid = f"mock-{int(time.time() * 1000)}-{random.randint(100, 999)}"
        order.order_id = oid
        self._orders[oid] = {"order": order, "placed_at": time.time(), "status": "OPEN"}
        return order

    async def check_result(self, order_id: str, timeout: float) -> str:
        await asyncio.sleep(min(timeout, 3))
        return random.choice(["WIN", "LOSS", "WIN", "WIN", "LOSS"])


# ─────────────────────────────── Real client wrapper ────────────────────────

class _RealClient:
    """
    Wrapper fino sobre AsyncPocketOptionClient (pocketoptionapi-async 2.x)
    que adapta sua interface à usada internamente pelo broker.
    """

    def __init__(self, ssid: str, demo: bool):
        from pocketoptionapi_async import AsyncPocketOptionClient  # type: ignore
        self._api = AsyncPocketOptionClient(ssid=ssid, is_demo=demo)

    async def connect(self) -> bool:
        return await self._api.connect()

    async def disconnect(self) -> None:
        result = self._api.disconnect()
        if asyncio.iscoroutine(result):
            await result

    async def get_balance(self) -> float:
        balance = await self._api.get_balance()
        return float(balance.balance)

    async def get_payout(self, asset: str) -> float:
        payout = self._api.get_payout(asset)
        return float(payout or 0.0)

    async def get_assets(self) -> List[Dict[str, Any]]:
        try:
            from pocketoptionapi_async import ASSETS  # type: ignore
            asset_names = [n for n in ASSETS.keys() if "otc" in n.lower()]
        except (ImportError, AttributeError, Exception):
            asset_names = list(_DEFAULT_OTC_ASSETS)

        results: List[Dict[str, Any]] = []
        for name in asset_names:
            try:
                payout = self._api.get_payout(name)
                if payout:
                    results.append({"asset": name, "payout": float(payout), "open": True})
            except Exception:
                pass

        # garantia: se a API não retornou nada, usa fallback com payout conservador
        if not results:
            results = [{"asset": a, "payout": 80.0, "open": True} for a in _DEFAULT_OTC_ASSETS]
        return results

    async def get_candles(self, asset: str, timeframe_s: int, count: int = 120) -> List[Candle]:
        lib_candles = await self._api.get_candles(asset, timeframe_s, count)
        result: List[Candle] = []
        for c in lib_candles:
            # lib Candle.timestamp é datetime; converter para int unix
            ts = int(c.timestamp.timestamp()) if hasattr(c.timestamp, "timestamp") else int(c.timestamp)
            result.append(Candle(
                timestamp=ts,
                open=float(c.open),
                high=float(c.high),
                low=float(c.low),
                close=float(c.close),
                volume=float(c.volume or 0.0),
            ))
        return result

    async def place_trade(self, order: TradeOrder) -> TradeOrder:
        from pocketoptionapi_async import OrderDirection  # type: ignore
        direction = (
            OrderDirection.CALL if order.direction.upper() == "CALL" else OrderDirection.PUT
        )
        result = await self._api.place_order(
            asset=order.asset,
            amount=order.amount,
            direction=direction,
            duration=order.expiration,
        )
        order.order_id = result.order_id
        return order

    async def check_result(self, order_id: str, timeout: float) -> str:
        result = await self._api.check_win(order_id, max_wait_time=timeout)
        if result is None:
            return "DRAW"
        status = ""
        if isinstance(result, dict):
            status = str(result.get("status", "")).lower()
        elif hasattr(result, "status"):
            status = str(result.status).lower()
        if "win" in status:
            return "WIN"
        if "lose" in status or "loss" in status:
            return "LOSS"
        return "DRAW"


# ─────────────────────────────── PocketOptionBroker ─────────────────────────

class PocketOptionBroker:
    """
    Fachada que consome um cliente real (pocketoptionapi-async) ou o mock.
    - Autenticação obrigatória por email/senha (captura SSID via Playwright).
    - Reconexão automática com backoff.
    - Fallback automático para MOCK se a API real não estiver disponível.
    """

    def __init__(self, email: str, password: str, demo: bool = True,
                 ssids: Optional[List[str]] = None):
        if not email or not password:
            raise BrokerError("Email e senha do Pocket Option são obrigatórios.")
        self.email = email
        self.password = password
        self.demo = demo
        self.ssids: List[str] = [s.strip() for s in (ssids or []) if s.strip()]
        self.session = SessionStore()
        self._client: Optional[Any] = None
        self._connected = False
        self._lock = asyncio.Lock()

    # ──────────────────────────── conexão ─────────────────────────────────

    async def _try_ssid(self, ssid: str, timeout: float = 35.0) -> bool:
        """Tenta conectar com um SSID específico. Retorna True se bem-sucedido."""
        client = None
        task = None
        try:
            client = _RealClient(ssid=ssid, demo=self.demo)
            logger.info(f"  → Conectando (timeout {timeout:.0f}s)…")
            task = asyncio.create_task(client.connect())
            ok = await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            if ok:
                self._client = client
                return True
            logger.warning("SSID: connect() retornou False (SSID inválido ou expirado).")
        except asyncio.TimeoutError:
            logger.warning(f"SSID: timeout após {timeout:.0f}s — SSID expirado ou inválido.")
            if task and not task.done():
                task.cancel()
        except Exception as e:
            logger.warning(f"SSID falhou: {e}")
            if task and not task.done():
                task.cancel()
        finally:
            # libera conexão em background para não travar o event loop
            if client is not None:
                async def _cleanup(c):
                    try:
                        await asyncio.wait_for(c.disconnect(), timeout=3.0)
                    except Exception:
                        pass
                asyncio.ensure_future(_cleanup(client))
        return False

    async def connect(self, force: bool = False) -> bool:
        async with self._lock:
            if self._connected and not force:
                return True

            # 1. tenta SSID do cache de sessão (gerado por login anterior)
            cached = None if force else self.session.load()
            if cached and cached.get("ssid"):
                logger.info("Tentando SSID do cache de sessão…")
                if await self._try_ssid(cached["ssid"]):
                    logger.info("Conectado com SSID do cache.")
                    self._connected = True
                    return True
                logger.warning("SSID do cache inválido/expirado. Tentando renovar via cookies…")

            # 2. renova SSID automaticamente usando cookies salvos (sem login)
            cookies = self.session.load_cookies()
            if cookies:
                logger.info("Renovando SSID via cookies de sessão (sem login)…")
                try:
                    auth = await _capture_ssid_with_cookies(cookies, self.demo)
                    if await self._try_ssid(auth["ssid"]):
                        self.session.save({"ssid": auth["ssid"]})
                        logger.info("SSID renovado automaticamente via cookies. ✅")
                        self._connected = True
                        return True
                except BrokerError as e:
                    logger.warning(f"Renovação via cookies falhou ({e}). Cookies podem ter expirado.")
                except Exception as e:
                    logger.warning(f"Erro ao renovar via cookies: {e}")

            # 3. tenta SSIDs manuais em ordem (fallback sequencial)
            for i, ssid in enumerate(self.ssids):
                logger.info(f"Tentando SSID manual #{i + 1}/{len(self.ssids)}…")
                if await self._try_ssid(ssid):
                    self.session.save({"ssid": ssid})
                    logger.info(f"Conectado com SSID manual #{i + 1}.")
                    self._connected = True
                    return True
                logger.warning(f"SSID manual #{i + 1} não funcionou (expirado).")

            # 4. tenta login completo via Playwright (captura SSID + cookies frescos)
            logger.info("Autenticando no Pocket Option via email+senha…")
            try:
                auth = await _login_and_capture_ssid(self.email, self.password, self.demo)
                ssid = auth["ssid"]
                if await self._try_ssid(ssid):
                    self.session.save(auth)   # salva SSID + cookies frescos
                    logger.info("Conectado via Playwright. Cookies salvos para próximas renovações.")
                    self._connected = True
                    return True
            except BrokerError as e:
                logger.warning(f"Login Playwright falhou ({e}).")
            except Exception as e:
                logger.warning(f"Playwright erro inesperado ({e}).")

            # 5. fallback final: modo MOCK
            logger.warning("Todos os métodos falharam. Operando em modo MOCK.")
            self._client = _MockClient(self.demo)
            await self._client.connect()
            self._connected = True
            return True

    async def ensure_connected(self) -> None:
        if not self._connected:
            await self.connect()

    async def disconnect(self) -> None:
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
        self._connected = False

    # ──────────────────────────── API unificada ────────────────────────────

    async def get_balance(self) -> float:
        await self.ensure_connected()
        return float(await self._client.get_balance())

    async def get_payout(self, asset: str) -> float:
        await self.ensure_connected()
        return float(await self._client.get_payout(asset) or 0.0)

    async def get_assets(self) -> List[Dict[str, Any]]:
        await self.ensure_connected()
        return await self._client.get_assets()

    async def get_candles(self, asset: str, timeframe_s: int, count: int = 120) -> List[Candle]:
        await self.ensure_connected()
        return await self._client.get_candles(asset, timeframe_s, count)

    async def place_trade(self, asset: str, direction: str, amount: float, expiration: int) -> TradeOrder:
        await self.ensure_connected()
        order = TradeOrder(
            asset=asset, direction=direction.upper(), amount=amount, expiration=expiration
        )
        return await self._client.place_trade(order)

    async def check_result(self, order_id: str, expiration: int) -> str:
        await self.ensure_connected()
        return await self._client.check_result(order_id, float(expiration + 5))
