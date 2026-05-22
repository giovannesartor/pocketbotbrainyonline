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


def get_api_key(override: str = "") -> str:
    """Retorna a chave da DeepSeek.
    Prioridade: override (config.json) → variável DEEPSEEK_API_KEY → DEEPSEEK_API_KEY hardcode.
    """
    key = override or os.environ.get("DEEPSEEK_API_KEY") or DEEPSEEK_API_KEY
    if not key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY não definida.\n"
            "Opções:\n"
            "  1. Adicione \"deepseek_api_key\": \"sua-chave\" em pocket_brainy/data/config.json\n"
            "  2. Defina a variável de ambiente: export DEEPSEEK_API_KEY=sua-chave\n"
            "  3. Desative a IA: \"ai_enabled\": false em config.json"
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
    "Você é um analista quantitativo especializado em opções binárias OTC (Over-The-Counter). "
    "CONTEXTO OTC OBRIGATÓRIO: mercados OTC são sintéticos, operam 24h, têm liquidez artificial e "
    "comportamento de tendência diferente do mercado real. "
    "ADX entre 15 e 25 é COMPLETAMENTE NORMAL em OTC — NÃO vete sinais apenas por ADX baixo. "
    "Só considere ADX problemático se estiver abaixo de 10 junto com RSI entre 45-55 (zona neutra total). "
    "Payout acima de 85% já compensa winrate de 55% — priorize entrar quando o setup técnico está alinhado. "
    "CRITÉRIOS DE QUALIDADE: Score>=8 + confiança>=85% + payout>=80% = sinal forte, prefira OPERAR. "
    "RSI fora da zona neutra (abaixo de 45 ou acima de 55) com MACD confirmando é entrada válida em OTC. "
    "\n\nNOVOS CAMPOS DE CONTEXTO (use ativamente):\n"
    "• last_5_candles: últimas 5 velas OHLCV fechadas. Verifique padrões: sequência de altas/baixas, "
    "corpos crescentes (momentum), pavios longos (rejeição). Se as 3 últimas velas vão contra a direção do sinal, "
    "seja conservador. Se concordam, reforça a entrada.\n"
    "• pair_performance: winrate ESPECÍFICO desta combinação ativo+estratégia. "
    "Se sample>=10 e winrate<45% — fortemente conservador (par tóxico). "
    "Se sample>=10 e winrate>=60% — confie no par, prefira OPERAR.\n"
    "• volume_ratio: volume da última vela vs média 20 anteriores. >1.5 = volume forte (reforça sinal). "
    "<0.5 = volume fraco (cuidado, baixa convicção). 0 = sem dados, ignore.\n"
    "• weekday: dia da semana. Sex/Dom em OTC são noisier. Considere conservadorismo extra.\n"
    "• hour_brt: horário. Madrugada (00-04 BRT) tem menos liquidez — setups precisam estar muito limpos.\n\n"
    "recent_results mostra os últimos resultados neste ativo: 1-2 losses pontuais NÃO invalidam sinal forte. "
    "last_ai_feedback (se presente) mostra o resultado da última decisão da IA neste ativo: "
    "use como aprendizado — se a última decisão de OPERAR resultou em LOSS, seja ligeiramente mais conservador; "
    "se resultou em WIN, confirme se o contexto atual é similar antes de aprovar novamente. "
    "last_ai_history (se presente) lista as últimas 3 decisões neste ativo (mais antiga → mais recente). "
    "Use para detectar padrões: 3 LOSSes consecutivos com a mesma estratégia = estratégia falhando neste ativo. "
    "2+ WINs recentes na mesma direção = momento favorável. "
    "RECOMENDE IGNORAR apenas se houver razão técnica CLARA e ESPECÍFICA: RSI na zona neutra (45-55) + "
    "MACD sem direção + volatilidade extrema (bb_width>0.05) ou streak de 3+ losses consecutivos no ativo, "
    "OU pair_performance com sample>=10 e winrate<40%. "
    "Responda APENAS em JSON válido com as chaves: decision ('OPERAR'|'IGNORAR'), "
    "confidence (0-100, int), rationale (string curta, <=300 chars)."
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
      - hour       → bucket de 4h (madrugada/manhã/tarde/noite)
      - pair_wr    → step 10 (faixas de winrate do par)
      - vol_ratio  → step 0.25 (faixas de volume relativo)
    Inclui padrão de resultados recentes para invalidar cache após losses.
    """
    _pair = payload.get("pair_performance") or {}
    key_obj = {
        "a": payload.get("asset"),
        "t": payload.get("timeframe"),
        "d": payload.get("direction"),
        "s": payload.get("strategy"),
        "sc": _bucket(float(payload.get("score", 0)), 0.5),
        "cf": _bucket(float(payload.get("confidence", 0)), 5),
        "po": _bucket(float(payload.get("payout", 0)), 5),
        "hr": int(payload.get("hour_brt", 0)) // 4,
        "pwr": _bucket(float(_pair.get("winrate", 0)), 10) if _pair.get("sample", 0) >= 6 else -1,
        "vr": _bucket(float(payload.get("volume_ratio", 0)), 0.25),
        "lat": bool((payload.get("market") or "").lower().find("lateral") >= 0
                    or (payload.get("market") or "").lower().find("sim") >= 0),
        "rr": _results_pattern(payload.get("recent_results", [])),
        "fb": (payload.get("last_ai_feedback") or {}).get("result", ""),
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

    def __init__(self, api_key: str = "", timeout: float = 12.0, use_cache: bool = True):
        self.timeout = timeout
        self.use_cache = use_cache
        self.api_key = get_api_key(api_key)

    def cache_stats(self) -> Dict[str, int]:
        return _cache.stats()

    async def clear_cache(self) -> int:
        """Limpa o cache global de decisões e retorna a contagem removida."""
        return await _cache.clear()

    async def validate_signal(self, payload: Dict[str, Any]) -> AIDecision:
        # Auto-aprovação: sinais excepcionais não precisam de voto da IA
        _score = float(payload.get("score", 0))
        _conf = float(payload.get("confidence", 0))
        if _score >= 8.5 and _conf >= 90.0:
            logger.info(f"IA auto-aprovado: score={_score:.2f} conf={_conf:.0f}% — bypass da API.")
            return AIDecision(
                operate=True,
                confidence=_conf,
                rationale=f"Auto-aprovado: score {_score:.1f} + confiança {_conf:.0f}% acima dos limiares excepcionais.",
            )

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
            "max_tokens": 400,
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
            # Erro de resposta inválida (JSON inesperado, quota, etc.)
            # Fallback baseado em score: apenas aprova sinais acima do limiar mínimo robusto.
            _score = float(safe_payload.get("score", 0))
            _fallback_threshold = 7.0
            if _score >= _fallback_threshold:
                logger.warning(
                    f"Erro IA (resposta inválida): {type(e).__name__}. "
                    f"Score={_score:.2f} >= {_fallback_threshold} — fallback OPERAR."
                )
                return AIDecision(
                    operate=True,
                    confidence=fallback_conf,
                    rationale=f"IA indisponível (erro interno) — fallback por score {_score:.1f}",
                )
            logger.warning(
                f"Erro IA (resposta inválida): {type(e).__name__}: {e}. "
                f"Score={_score:.2f} < {_fallback_threshold} — IGNORAR."
            )
            return AIDecision(operate=False, confidence=0.0, rationale=f"Erro IA: {e}")
