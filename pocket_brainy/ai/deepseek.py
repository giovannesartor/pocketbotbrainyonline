"""
Integração com DeepSeek — valida sinais antes da execução.

Recursos:
  - Chave central (get_api_key()) — hardcode + override por env.
  - Cache de decisões com bucketização para sinais "parecidos" (economia de tokens).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

import aiohttp

from ..utils.logger import get_logger

logger = get_logger("ai.deepseek")

# Chave lida exclusivamente de variável de ambiente (nunca hardcode em repositório)
DEEPSEEK_API_KEY = ""   # fallback vazio — use a variável DEEPSEEK_API_KEY
DEEPSEEK_ENDPOINT = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

# Cache
CACHE_TTL_SECONDS = 180      # 3 minutos — sinais mudam rápido, especialmente após resultado
CACHE_MAX_SIZE = 256         # LRU simples


def get_api_key() -> str:
    """Retorna a chave da DeepSeek — obrigatoriamente via variável de ambiente."""
    key = os.environ.get("DEEPSEEK_API_KEY") or DEEPSEEK_API_KEY
    if not key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY não definida. "
            "Configure a variável de ambiente DEEPSEEK_API_KEY antes de iniciar o bot."
        )
    return key


@dataclass
class AIDecision:
    operate: bool
    confidence: float
    rationale: str
    cached: bool = False

    @property
    def decision_label(self) -> str:
        return "OPERAR" if self.operate else "IGNORAR"


SYSTEM_PROMPT = (
    "Você é um analista quantitativo especializado em opções binárias OTC. "
    "Recebe um sinal com indicadores e histórico recente e decide se deve OPERAR ou IGNORAR. "
    "Para OTC: RSI<30 (PUT) ou RSI>70 (CALL) com MACD confirmando são entradas de alta qualidade. "
    "ADX alto (>40) indica tendência forte — em OTC isso é FAVORÁVEL se a direção coincidir com a tendência. "
    "Score>9 com confiança>90% e payout>85% = sinal de alta qualidade, prefira OPERAR. "
    "recent_results mostra os últimos resultados neste ativo — 1 LOSS pontual não invalida um sinal forte. "
    "Só recomende IGNORAR se houver razão técnica clara (RSI neutro, MACD divergindo, volatilidade extrema). "
    "Responda APENAS em JSON válido com as chaves: decision ('OPERAR'|'IGNORAR'), "
    "confidence (0-100, int), rationale (string curta, <=280 chars)."
)


# --------------------------- CACHE ---------------------------
def _bucket(value: float, step: float) -> int:
    """Bucketização para chaves estáveis em valores contínuos."""
    return int(round(value / step))


def _results_pattern(recent: list) -> str:
    """Extrai padrão W/L/D para variar a chave de cache com base no histórico recente."""
    out = []
    for r in (recent or []):
        s = str(r).upper()
        if "WIN" in s:
            out.append("W")
        elif "LOSS" in s:
            out.append("L")
        else:
            out.append("D")
    return "".join(out)


def _cache_key(payload: Dict[str, Any]) -> str:
    """
    Sinais similares produzem a MESMA chave de cache.
    Agrupamos valores contínuos em buckets:
      - score      → step 0.5
      - confidence → step 5
      - payout     → step 5
    Inclui padrão de resultados recentes para invalidar cache após losses.
    """
    key_obj = {
        "a": payload.get("asset"),
        "t": payload.get("timeframe"),
        "d": payload.get("direction"),
        "s": payload.get("strategy"),
        "sc": _bucket(float(payload.get("score", 0)), 0.5),
        "cf": _bucket(float(payload.get("confidence", 0)), 5),
        "po": _bucket(float(payload.get("payout", 0)), 5),
        "lat": bool((payload.get("market") or "").lower().find("lateral") >= 0
                    or (payload.get("market") or "").lower().find("sim") >= 0),
        "rr": _results_pattern(payload.get("recent_results", [])),  # padrão de resultados recentes
    }
    raw = json.dumps(key_obj, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class _DecisionCache:
    """LRU simples com TTL."""

    def __init__(self, ttl: float = CACHE_TTL_SECONDS, max_size: int = CACHE_MAX_SIZE):
        self.ttl = ttl
        self.max_size = max_size
        self._store: Dict[str, Tuple[float, AIDecision]] = {}
        self._hits = 0
        self._misses = 0
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[AIDecision]:
        async with self._lock:
            entry = self._store.get(key)
            if not entry:
                self._misses += 1
                return None
            ts, decision = entry
            if time.time() - ts > self.ttl:
                self._store.pop(key, None)
                self._misses += 1
                return None
            # "renovar" posição LRU
            self._store.pop(key)
            self._store[key] = (ts, decision)
            self._hits += 1
            # devolve cópia marcada como cached
            return AIDecision(decision.operate, decision.confidence, decision.rationale, cached=True)

    async def set(self, key: str, decision: AIDecision) -> None:
        async with self._lock:
            self._store[key] = (time.time(), decision)
            while len(self._store) > self.max_size:
                # descarta o mais antigo (primeiro inserido)
                self._store.pop(next(iter(self._store)))

    async def clear(self) -> int:
        """Limpa todo o cache e retorna quantas entradas foram removidas."""
        async with self._lock:
            count = len(self._store)
            self._store.clear()
            self._hits = 0
            self._misses = 0
            return count

    def stats(self) -> Dict[str, int]:
        return {"hits": self._hits, "misses": self._misses, "size": len(self._store)}


# cache global compartilhado
_cache = _DecisionCache()


def _sanitize_for_json(obj):
    """
    Converte tipos numpy (bool_, float_, int_, etc.) para tipos nativos Python
    antes de serializar com json.dumps. Garante compatibilidade com qualquer
    lib que retorne np.bool_ em dicts de confiuência.
    """
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, bool):          # Python bool — antes do int check
        return obj
    if hasattr(obj, 'item'):           # numpy scalar (bool_, float_, int_, ...)
        return obj.item()
    return obj


# --------------------------- CLIENTE ---------------------------
class DeepSeekAI:
    """Cliente assíncrono para DeepSeek com cache de decisões."""

    def __init__(self, timeout: float = 5.0, use_cache: bool = True):
        self.timeout = timeout
        self.use_cache = use_cache
        self.api_key = get_api_key()

    def cache_stats(self) -> Dict[str, int]:
        return _cache.stats()

    async def clear_cache(self) -> int:
        """Limpa o cache global de decisões e retorna a contagem removida."""
        return await _cache.clear()

    async def validate_signal(self, payload: Dict[str, Any]) -> AIDecision:
        if self.use_cache:
            key = _cache_key(payload)
            cached = await _cache.get(key)
            if cached:
                logger.info(f"IA cache HIT ({key}) → {cached.decision_label} ({cached.confidence:.0f}%)")
                return cached
        else:
            key = None

        decision = await self._call_api(payload)

        if self.use_cache and key:
            await _cache.set(key, decision)
        return decision

    async def _call_api(self, payload: Dict[str, Any]) -> AIDecision:
        safe_payload = _sanitize_for_json(payload)
        user_msg = (
            "Analise o sinal a seguir e responda em JSON:\n\n"
            + json.dumps(safe_payload, ensure_ascii=False, indent=2)
        )
        body = {
            "model": DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "max_tokens": 300,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        try:
            aio_timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with aiohttp.ClientSession(timeout=aio_timeout) as sess:
                async with sess.post(DEEPSEEK_ENDPOINT, headers=headers, json=body) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            decision = str(parsed.get("decision", "IGNORAR")).upper()
            conf = float(parsed.get("confidence", 0))
            rationale = str(parsed.get("rationale", ""))[:500]
            return AIDecision(operate=(decision == "OPERAR"), confidence=conf, rationale=rationale)
        except asyncio.TimeoutError:
            fallback_conf = float(safe_payload.get("confidence", 60.0))
            logger.warning(f"IA timeout ({self.timeout}s) — fallback por score ({fallback_conf:.0f}%).")
            return AIDecision(
                operate=True,
                confidence=fallback_conf,
                rationale="IA indisponível (timeout) — fallback por score da estratégia",
            )
        except Exception as e:
            # Qualquer erro de rede/SSL/conexão → fallback por score da estratégia,
            # não bloqueia a operação. Só erros de parsing da resposta retornam IGNORAR.
            err_str = str(e)
            is_connection_err = any(kw in err_str.lower() for kw in (
                "ssl", "connect", "timeout", "network", "certificate", "host", "socket"
            ))
            fallback_conf = float(safe_payload.get("confidence", 60.0))
            if is_connection_err:
                logger.warning(f"IA indisponível ({type(e).__name__}) — fallback por score ({fallback_conf:.0f}%).")
                return AIDecision(
                    operate=True,
                    confidence=fallback_conf,
                    rationale=f"IA indisponível ({type(e).__name__}) — fallback por score da estratégia",
                )
            logger.warning(f"Erro IA (resposta inválida): {e}. Decisão default: IGNORAR.")
            return AIDecision(operate=False, confidence=0.0, rationale=f"Erro IA: {e}")
