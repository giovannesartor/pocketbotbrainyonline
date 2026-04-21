"""Biblioteca de indicadores técnicos (implementação vetorizada com numpy)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple

import numpy as np


def _to_array(values: Sequence[float]) -> np.ndarray:
    return np.asarray(values, dtype=float)


class Indicators:
    """Cálculo de indicadores técnicos clássicos usados pelas estratégias."""

    # ----------------- Médias -----------------
    @staticmethod
    def sma(values: Sequence[float], period: int) -> np.ndarray:
        arr = _to_array(values)
        if len(arr) < period:
            return np.full_like(arr, np.nan)
        out = np.full_like(arr, np.nan)
        c = np.cumsum(arr)
        out[period - 1:] = (c[period - 1:] - np.concatenate(([0], c[:-period]))) / period
        return out

    @staticmethod
    def ema(values: Sequence[float], period: int) -> np.ndarray:
        arr = _to_array(values)
        if len(arr) == 0:
            return arr
        k = 2 / (period + 1)
        out = np.full_like(arr, np.nan)
        out[0] = arr[0]
        for i in range(1, len(arr)):
            out[i] = arr[i] * k + out[i - 1] * (1 - k)
        return out

    @staticmethod
    def smma(values: Sequence[float], period: int) -> np.ndarray:
        """Smoothed MA (usada no Alligator)."""
        arr = _to_array(values)
        out = np.full_like(arr, np.nan)
        if len(arr) < period:
            return out
        out[period - 1] = arr[:period].mean()
        for i in range(period, len(arr)):
            out[i] = (out[i - 1] * (period - 1) + arr[i]) / period
        return out

    # ----------------- Osciladores -----------------
    @staticmethod
    def rsi(values: Sequence[float], period: int = 14) -> np.ndarray:
        arr = _to_array(values)
        if len(arr) < period + 1:
            return np.full_like(arr, np.nan)
        deltas = np.diff(arr)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = np.full_like(arr, np.nan)
        avg_loss = np.full_like(arr, np.nan)
        avg_gain[period] = gains[:period].mean()
        avg_loss[period] = losses[:period].mean()
        for i in range(period + 1, len(arr)):
            avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i - 1]) / period
            avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i - 1]) / period
        rs = avg_gain / np.where(avg_loss == 0, 1e-10, avg_loss)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def macd(
        values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        ema_fast = Indicators.ema(values, fast)
        ema_slow = Indicators.ema(values, slow)
        macd_line = ema_fast - ema_slow
        signal_line = Indicators.ema(macd_line, signal)
        hist = macd_line - signal_line
        return macd_line, signal_line, hist

    @staticmethod
    def adx(high: Sequence[float], low: Sequence[float], close: Sequence[float], period: int = 14) -> np.ndarray:
        h = _to_array(high)
        l = _to_array(low)
        c = _to_array(close)
        n = len(c)
        if n < period + 1:
            return np.full(n, np.nan)
        tr = np.zeros(n)
        pdm = np.zeros(n)
        ndm = np.zeros(n)
        for i in range(1, n):
            tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
            up = h[i] - h[i - 1]
            dn = l[i - 1] - l[i]
            pdm[i] = up if up > dn and up > 0 else 0
            ndm[i] = dn if dn > up and dn > 0 else 0
        atr = Indicators.ema(tr, period)
        pdi = 100 * Indicators.ema(pdm, period) / np.where(atr == 0, 1e-10, atr)
        ndi = 100 * Indicators.ema(ndm, period) / np.where(atr == 0, 1e-10, atr)
        dx = 100 * np.abs(pdi - ndi) / np.where((pdi + ndi) == 0, 1e-10, pdi + ndi)
        return Indicators.ema(dx, period)

    # ----------------- Volatilidade -----------------
    @staticmethod
    def bollinger(values: Sequence[float], period: int = 20, k: float = 2.0):
        arr = _to_array(values)
        mid = Indicators.sma(arr, period)
        std = np.full_like(arr, np.nan)
        for i in range(period - 1, len(arr)):
            std[i] = arr[i - period + 1 : i + 1].std()
        return mid - k * std, mid, mid + k * std

    # ----------------- Alligator -----------------
    @staticmethod
    def alligator(median: Sequence[float]):
        jaw = Indicators.smma(median, 13)
        teeth = Indicators.smma(median, 8)
        lips = Indicators.smma(median, 5)
        return jaw, teeth, lips

    # ----------------- Parabolic SAR -----------------
    @staticmethod
    def parabolic_sar(
        high: Sequence[float], low: Sequence[float], af_step: float = 0.02, af_max: float = 0.2
    ) -> np.ndarray:
        h = _to_array(high)
        l = _to_array(low)
        n = len(h)
        sar = np.zeros(n)
        if n < 2:
            return sar
        trend = 1  # 1 = up, -1 = down
        ep = h[0]
        af = af_step
        sar[0] = l[0]
        for i in range(1, n):
            prev = sar[i - 1]
            sar[i] = prev + af * (ep - prev)
            if trend == 1:
                sar[i] = min(sar[i], l[i - 1])
                if l[i] < sar[i]:
                    trend = -1
                    sar[i] = ep
                    ep = l[i]
                    af = af_step
                elif h[i] > ep:
                    ep = h[i]
                    af = min(af + af_step, af_max)
            else:
                sar[i] = max(sar[i], h[i - 1])
                if h[i] > sar[i]:
                    trend = 1
                    sar[i] = ep
                    ep = h[i]
                    af = af_step
                elif l[i] < ep:
                    ep = l[i]
                    af = min(af + af_step, af_max)
        return sar


@dataclass
class Candle:
    """Representação OHLCV canônica usada em todo o sistema."""
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
