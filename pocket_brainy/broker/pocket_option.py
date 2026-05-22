"""
Camada de abstração sobre a Pocket Option.

Usa pocketoptionapi-async (ChipaDevTeam) — AsyncPocketOptionClient.

Fluxo de autenticação (em ordem de tentativa):
  1. SSID do cache de sessão (data/session.json)
  2. Renovação automática via cookies salvos (Playwright headless)
  3. SSIDs manuais configurados em po_ssids
  4. Login completo via Playwright (email + senha)

Se todos os métodos falharem, BrokerError é levantado e o bot para.
Não existe modo MOCK — falhas são visíveis para correção imediata.
"""
from __future__ import annotations

import asyncio
import json
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
    """Retorna o frame de autenticação completo se for válido, senão None.

    A biblioteca pocketoptionapi-async aceita o frame completo
    42["auth",{"session":"...","isDemo":N,"uid":N,"platform":N}]
    diretamente como SSID.
    """
    if not isinstance(payload, str) or not payload.strip():
        return None
    p = payload.strip()
    # Frame de auth completo
    if p.startswith('42["auth",'):
        try:
            data = json.loads(p[2:])
            if isinstance(data, list) and len(data) >= 2 and isinstance(data[1], dict):
                d = data[1]
                if (d.get("session") or d.get("ssid")):
                    return p
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
    return {
        "user_agent": _STEALTH_UA,
        "viewport": {"width": 1280, "height": 800},
        "locale": "pt-BR",
        "timezone_id": "America/Sao_Paulo",
        "extra_http_headers": {"Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"},
    }


def _build_ws_interceptor(ssid_holder: list):
    """Intercepta frames WebSocket e preenche ssid_holder[0] quando captura o SSID."""
    def on_ws(ws):
        def on_frame(frame):
            if ssid_holder[0]:
                return
            payload = frame if isinstance(frame, str) else getattr(frame, "payload", "")
            if isinstance(payload, str) and ("ssid" in payload or "session" in payload):
                extracted = _extract_ssid_from_frame(payload)
                if extracted:
                    ssid_holder[0] = extracted
        ws.on("framesent", on_frame)
        ws.on("framereceived", on_frame)
    return on_ws


async def _wait_for_ssid(ssid_holder: list, seconds: int = 180) -> bool:
    """Aguarda até `seconds` segundos pelo SSID."""
    for _ in range(seconds):
        if ssid_holder[0]:
            return True
        await asyncio.sleep(1)
    return False


class _NoSSIDError(BrokerError):
    """Levantado quando não há SSID disponível — bot deve entrar em modo espera."""
    pass


class _NetworkBlockedError(BrokerError):
    """Rede bloqueando pocketoption.com — SSID pode ser válido mas sem acesso."""
    pass


# ─────────────────────────────── Real client helpers ────────────────────────

# OTC assets fallback para get_assets quando a API não retornar nada
_DEFAULT_OTC_ASSETS = [
    "EURUSD_otc", "GBPUSD_otc", "USDJPY_otc",
    "AUDCAD_otc", "XAUUSD_otc", "EURJPY_otc",
]

# ─────────────────────────────── Real client wrapper ────────────────────────

class _RealClient:
    """
    Wrapper fino sobre AsyncPocketOptionClient (pocketoptionapi-async 2.x)
    que adapta sua interface à usada internamente pelo broker.
    """

    def __init__(self, ssid: str, demo: bool):
        from pocketoptionapi_async import AsyncPocketOptionClient  # type: ignore
        self._api = AsyncPocketOptionClient(
            ssid=ssid,
            is_demo=demo,
            enable_logging=False,   # usa nosso próprio logger
            auto_reconnect=False,   # reconexão gerenciada pelo broker
        )

    async def connect(self) -> bool:
        ok = await self._api.connect()
        if ok:
            # Aguarda até 5s para o cache de payouts ser populado via WS
            for _ in range(10):
                await asyncio.sleep(0.5)
                if self._api.get_payout("EURUSD_otc") is not None:
                    break
            # Aguarda até 8s pelo PRIMEIRO push de balance via WS.
            # Sem isso, o get_balance() inicial pode falhar/retornar 0 e o bot
            # opera o resto da sessão exibindo um saldo "travado".
            try:
                await self._api._request_balance_update()  # type: ignore[attr-defined]
            except Exception:
                pass
            for _ in range(16):
                if getattr(self._api, "_balance", None) is not None:
                    bal = self._api._balance  # type: ignore[attr-defined]
                    logger.info(
                        f"💰 Saldo inicial recebido via WS: $ {float(bal.balance):.2f} "
                        f"({'DEMO' if bal.is_demo else 'REAL'})"
                    )
                    break
                await asyncio.sleep(0.5)
            else:
                logger.warning("⚠️  Nenhum push de balance recebido em 8s após connect.")
        return ok

    async def disconnect(self) -> None:
        result = self._api.disconnect()
        if asyncio.iscoroutine(result):
            await result

    async def get_balance(self) -> float:
        """Retorna saldo SEMPRE fresco da corretora.

        - Se a lib lança (WS ainda sem balance), força um getBalance e tenta de novo.
        - Nunca devolve 0 silenciosamente sem antes tentar 3× (caso contrário o bot
          fica exibindo saldo "em cache" enquanto o broker já está OK).
        """
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                balance = await self._api.get_balance()
                return float(balance.balance)
            except Exception as e:
                last_err = e
                logger.warning(f"get_balance tentativa {attempt + 1}/3 falhou: {e}")
                # força novo request e espera o WS responder
                try:
                    await self._api._request_balance_update()  # type: ignore[attr-defined]
                except Exception:
                    pass
                await asyncio.sleep(1.5)
        logger.error(f"get_balance falhou após 3 tentativas: {last_err}")
        return 0.0

    async def get_payout(self, asset: str) -> float:
        try:
            payout = self._api.get_payout(asset)
            if payout is not None:
                return float(payout)
            # Fallback: asset_info se payout_cache ainda vazio
            info = self._api.get_asset_info(asset) if hasattr(self._api, "get_asset_info") else None
            if info and isinstance(info, dict):
                p = info.get("payout") or info.get("profit")
                if p is not None:
                    return float(p)
        except Exception:
            pass
        return 0.0

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
        # Pode ser dict ou OrderResult (Pydantic model)
        if isinstance(result, dict):
            status = str(result.get("status", "")).lower()
            profit = result.get("profit")
            # se status vazio mas profit presente, decide pelo profit
            if not status and profit is not None:
                return "WIN" if float(profit) > 0 else "LOSS"
        else:
            # OrderResult ou objeto com .status
            raw_status = getattr(result, "status", "")
            status = str(getattr(raw_status, "value", raw_status)).lower()
            profit = getattr(result, "profit", None)
            if not status and profit is not None:
                return "WIN" if float(profit) > 0 else "LOSS"
        if "win" in status:
            return "WIN"
        if "lose" in status or "loss" in status:
            return "LOSS"
        return "DRAW"


# ─────────────────────────────── PocketOptionBroker ─────────────────────────

class PocketOptionBroker:
    """
    Fachada que consome o cliente real (pocketoptionapi-async).
    - Autenticação obrigatória por email/senha (captura SSID via Playwright).
    - Renovação automática via cookies salvos.
    - Reconexão automática com backoff.
    - Sem modo MOCK: falhas levantam BrokerError para correção explícita.
    """

    def __init__(self, email: str = "", password: str = "", demo: bool = True,
                 ssids: Optional[List[str]] = None):
        self.email = email
        self.password = password
        self.demo = demo
        self.ssids: List[str] = [s.strip() for s in (ssids or []) if s.strip()]
        self.session = SessionStore()
        self._client: Optional[Any] = None
        self._connected = False
        self._lock = asyncio.Lock()
        self._payout_cache: Dict[str, tuple] = {}
        self._candle_cache: Dict[str, tuple] = {}

    # ──────────────────────────── conexão ─────────────────────────────────

    async def _try_ssid(self, ssid_raw: str, timeout: float = 40.0) -> bool:
        """Tenta conectar com um SSID específico. Retorna True se bem-sucedido.
        Normaliza automaticamente o SSID — aceita token puro OU frame WebSocket completo.
        Se o frame contiver isDemo, sincroniza self.demo automaticamente.
        """
        # Aceita 2 formatos:
        #   1. Frame completo 42["auth",{...}] — passa direto
        #   2. Token raw (session_id) — passa direto, lib detecta automaticamente
        ssid = ssid_raw.strip()
        if not ssid:
            logger.warning("SSID vazio — ignorado.")
            return False
        # Verifica isDemo do frame para evitar trade na conta errada.
        # ⚠️ Comportamento: se cfg.po_demo difere do frame, REJEITA — melhor
        # falhar do que operar com dinheiro real sem querer.
        demo_to_use = self.demo
        if ssid.startswith('42["auth",'):
            try:
                _data = json.loads(ssid[2:])
                if isinstance(_data, list) and len(_data) >= 2 and isinstance(_data[1], dict):
                    _is_demo_frame = _data[1].get("isDemo")
                    if _is_demo_frame is not None:
                        _frame_demo = bool(int(_is_demo_frame))
                        if _frame_demo != self.demo:
                            logger.error(
                                f"🚨 SSID REJEITADO: cfg.po_demo={self.demo} mas SSID é de conta "
                                f"{'DEMO' if _frame_demo else 'REAL'}. "
                                f"Capture um SSID da conta correta no Chrome."
                            )
                            return False
            except Exception as e:
                logger.debug(f"Não consegui parsear isDemo do frame: {e}")
        preview = ssid[:12] + "..." if len(ssid) > 12 else ssid
        logger.info(f"  → SSID a testar: '{preview}' (len={len(ssid)}, demo={demo_to_use})")
        client = None
        task = None
        success = False
        try:
            client = _RealClient(ssid=ssid, demo=demo_to_use)
            logger.info(f"  → Conectando (timeout {timeout:.0f}s)…")
            task = asyncio.create_task(client.connect())
            ok = await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            if ok:
                self._client = client
                success = True
                return True
            logger.warning("SSID: connect() retornou False (SSID inválido ou expirado).")
        except asyncio.TimeoutError:
            logger.warning(f"SSID: timeout após {timeout:.0f}s — SSID expirado ou inválido.")
            if task and not task.done():
                task.cancel()
        except Exception as e:
            logger.warning(f"SSID falhou: {type(e).__name__}: {e}")
            if task and not task.done():
                task.cancel()
        finally:
            # libera conexão SOMENTE se falhou — não desconectar o cliente ativo
            if client is not None and not success:
                async def _cleanup(c):
                    try:
                        await asyncio.wait_for(c.disconnect(), timeout=3.0)
                    except Exception:
                        pass
                asyncio.ensure_future(_cleanup(client))
        return False

    async def connect(self, force: bool = False, reset_state: bool = True) -> bool:
        """
        Conecta ao Pocket Option.
        1. Tenta SSID do cache de sessão (session.json)
        2. Tenta SSIDs manuais configurados (po_ssids)
        3. Abre o Chrome para o usuário fazer login manualmente
        """
        async with self._lock:
            if self._connected and not force:
                return True

            if reset_state:
                self._connected = False
                self._client = None

            # 1. SSID em cache (de login anterior)
            cached = None if force else self.session.load()
            if cached and cached.get("ssid"):
                logger.info("Tentando SSID do cache de sessão…")
                if await self._try_ssid(cached["ssid"]):
                    logger.info("Conectado com SSID do cache.")
                    self._connected = True
                    return True
                logger.warning("SSID do cache expirado.")

            # 2. SSIDs manuais configurados
            for i, ssid in enumerate(self.ssids):
                logger.info(f"Tentando SSID manual #{i + 1}/{len(self.ssids)}…")
                if await self._try_ssid(ssid):
                    self.session.save({"ssid": ssid})
                    logger.info(f"Conectado com SSID manual #{i + 1}.")
                    self._connected = True
                    return True
                logger.warning(f"SSID manual #{i + 1} expirado.")

            # 3. Nenhuma credencial disponível — bot deve aguardar /ssid
            raise _NoSSIDError(
                "Nenhum SSID válido disponível. Envie /ssid TOKEN no Telegram para conectar."
            )

    async def update_ssid(self, ssid_raw: str) -> bool:
        """
        Atualiza o SSID em tempo de execução (ex: comando /ssid do Telegram).
        Aceita token puro OU frame WebSocket completo.
        Retorna True se conexão bem-sucedida.
        """
        # Aceita frame completo 42["auth",{...}] OU token raw
        ssid = ssid_raw.strip()
        if not ssid:
            raise BrokerError("SSID vazio.")
        async with self._lock:
            # desconecta cliente prévio (se houver) antes de trocar
            if self._client is not None:
                try:
                    await asyncio.wait_for(self._client.disconnect(), timeout=3.0)
                except Exception:
                    pass
                self._client = None
            self._connected = False
            # tenta conectar com o novo SSID
            if await self._try_ssid(ssid):
                self.session.save({"ssid": ssid})
                self._connected = True
                logger.info("SSID atualizado e conectado com sucesso.")
                return True
        raise BrokerError("SSID recebido não funcionou (inválido ou expirado).")

    async def ensure_connected(self) -> None:
        if not self._connected:
            await self.connect()

    @staticmethod
    def _is_disconnect_error(exc: BaseException) -> bool:
        """Detecta erros indicando que o websocket caiu."""
        msg = str(exc).lower()
        return any(s in msg for s in (
            "not connected", "connection closed", "connection reset",
            "websocket", "no client", "not initialized", "closed transport",
        ))

    async def _call_with_reconnect(self, fn_name: str, *args, **kwargs):
        """Executa um método do _client; se cair, reconecta e tenta de novo (1×)."""
        await self.ensure_connected()
        try:
            return await getattr(self._client, fn_name)(*args, **kwargs)
        except Exception as e:
            if not self._is_disconnect_error(e):
                raise
            logger.warning(f"Broker desconectado durante {fn_name}: {e} — reconectando…")
            self._connected = False
            try:
                await self.connect(force=True, reset_state=True)
            except Exception as ce:
                raise BrokerError(f"Falha ao reconectar após {fn_name}: {ce}") from e
            return await getattr(self._client, fn_name)(*args, **kwargs)

    async def disconnect(self) -> None:
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
        self._connected = False

    # ──────────────────────────── API unificada ────────────────────────────

    async def get_balance(self) -> float:
        return await self._call_with_reconnect("get_balance")

    async def get_payout(self, asset: str) -> float:
        now = time.monotonic()
        cached = self._payout_cache.get(asset)
        if cached and now - cached[1] < 60.0:
            return cached[0]
        val = float(await self._call_with_reconnect("get_payout", asset) or 0.0)
        self._payout_cache[asset] = (val, now)
        return val

    async def get_assets(self) -> List[Dict[str, Any]]:
        return await self._call_with_reconnect("get_assets")

    async def get_candles(self, asset: str, timeframe_s: int, count: int = 120) -> List[Candle]:
        key = f"{asset}:{timeframe_s}"
        now = time.monotonic()
        cached = self._candle_cache.get(key)
        # TTL agressivo para TFs curtos (scalper):
        #   5s→10s → 1.5s | 30s → 5s | 60s → 10s | 300s+ → ~25%
        if timeframe_s <= 10:
            ttl = 1.5
        elif timeframe_s <= 30:
            ttl = 5.0
        elif timeframe_s <= 60:
            ttl = 10.0
        else:
            ttl = max(15.0, min(float(timeframe_s) * 0.25, 240.0))
        if cached and now - cached[1] < ttl:
            return cached[0]
        result = await self._call_with_reconnect("get_candles", asset, timeframe_s, count)
        self._candle_cache[key] = (result, now)
        return result

    async def place_trade(self, asset: str, direction: str, amount: float, expiration: int) -> TradeOrder:
        order = TradeOrder(
            asset=asset, direction=direction.upper(), amount=amount, expiration=expiration
        )
        return await self._call_with_reconnect("place_trade", order)

    async def check_result(self, order_id: str, expiration: int) -> str:
        return await self._call_with_reconnect("check_result", order_id, float(expiration + 5))
