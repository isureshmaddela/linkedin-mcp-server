"""Technical indicators, forecasts and their track record. Pure functions on lists of daily closes (oldest first).

Indicators: simple and exponential moving averages, RSI (Wilder), MACD (12, 26, 9), Bollinger bands (20, 2) and
annualised volatility.

Forecasts for the next ``h`` trading days come from two simple models and their average:

* **trend line**: a least-squares line through the logarithm of the last :data:`TREND_WINDOW` closes, carried
  forward;
* **smoothing**: Holt's exponential smoothing of the log prices with a damped trend;
* **ensemble**: the average of the two (in log terms).

The band around the ensemble comes from recent volatility (80% of days should land inside it if the past is a
guide, which it often is not). :func:`backtest` replays the models on past data, walking forward: at each past day
it forecasts with only what was known then and compares with what happened, giving each model's direction hit rate
and mean absolute percentage error, next to a "no change" baseline. None of this is financial advice.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

TREND_WINDOW = 60
HOLT_ALPHA = 0.4
HOLT_BETA = 0.1
HOLT_PHI = 0.9
HOLT_WINDOW = 160
BAND_Z = 1.2816  # 80% of a normal distribution lies within +-1.28 standard deviations
MODELS = ("trend", "smoothing", "ensemble")
MODEL_LABELS = {
    "trend": "Trend line",
    "smoothing": "Smoothing (Holt)",
    "ensemble": "Ensemble (average)",
    "naive": "No change (baseline)",
}


# ---------------------------------------------------------------------------
# indicators
# ---------------------------------------------------------------------------


def sma(values: Sequence[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    total = 0.0
    for index, value in enumerate(values):
        total += value
        if index >= n:
            total -= values[index - n]
        out.append(total / n if index >= n - 1 else None)
    return out


def ema(values: Sequence[float], n: int) -> list[float | None]:
    """Seeded with the simple average of the first ``n`` values, then ``k = 2 / (n + 1)``."""
    out: list[float | None] = [None] * len(values)
    if len(values) < n:
        return out
    k = 2 / (n + 1)
    current = sum(values[:n]) / n
    out[n - 1] = current
    for index in range(n, len(values)):
        current = values[index] * k + current * (1 - k)
        out[index] = current
    return out


def rsi(values: Sequence[float], n: int = 14) -> list[float | None]:
    """Wilder's relative strength index (0..100)."""
    out: list[float | None] = [None] * len(values)
    if len(values) <= n:
        return out
    gains = losses = 0.0
    for index in range(1, n + 1):
        change = values[index] - values[index - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain, avg_loss = gains / n, losses / n

    def value(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        return 100 - 100 / (1 + gain / loss)

    out[n] = value(avg_gain, avg_loss)
    for index in range(n + 1, len(values)):
        change = values[index] - values[index - 1]
        avg_gain = (avg_gain * (n - 1) + max(change, 0.0)) / n
        avg_loss = (avg_loss * (n - 1) + max(-change, 0.0)) / n
        out[index] = value(avg_gain, avg_loss)
    return out


def macd(
    values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> dict[str, list[float | None]]:
    fast_line, slow_line = ema(values, fast), ema(values, slow)
    line: list[float | None] = [
        (f - s) if f is not None and s is not None else None
        for f, s in zip(fast_line, slow_line, strict=True)
    ]
    start = next((i for i, v in enumerate(line) if v is not None), len(line))
    tail = ema([v for v in line[start:] if v is not None], signal)
    signal_line: list[float | None] = [None] * start + tail
    histogram: list[float | None] = [
        (m - s) if m is not None and s is not None else None
        for m, s in zip(line, signal_line, strict=True)
    ]
    return {"macd": line, "signal": signal_line, "histogram": histogram}


def bollinger(
    values: Sequence[float], n: int = 20, k: float = 2.0
) -> dict[str, list[float | None]]:
    middle = sma(values, n)
    upper: list[float | None] = []
    lower: list[float | None] = []
    for index, mid in enumerate(middle):
        if mid is None:
            upper.append(None)
            lower.append(None)
            continue
        window = values[index - n + 1 : index + 1]
        sd = math.sqrt(sum((v - mid) ** 2 for v in window) / n)
        upper.append(mid + k * sd)
        lower.append(mid - k * sd)
    return {"middle": middle, "upper": upper, "lower": lower}


def log_returns(values: Sequence[float]) -> list[float]:
    return [
        math.log(values[i] / values[i - 1])
        for i in range(1, len(values))
        if values[i] > 0 and values[i - 1] > 0
    ]


def daily_volatility(values: Sequence[float], n: int = 60) -> float:
    """The standard deviation of the last ``n`` daily log returns (sample)."""
    returns = log_returns(values[-(n + 1) :])
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    return math.sqrt(sum((r - mean) ** 2 for r in returns) / (len(returns) - 1))


def volatility(values: Sequence[float], n: int = 20) -> float:
    """Annualised volatility in percent (daily log-return deviation times the square root of 252)."""
    return daily_volatility(values, n) * math.sqrt(252) * 100


def latest(values: Sequence[float | None]) -> float | None:
    for value in reversed(values):
        if value is not None:
            return value
    return None


def indicators(closes: Sequence[float]) -> dict[str, Any]:
    """The latest value of every indicator (None where there is not enough history)."""
    bands = bollinger(closes)
    lines = macd(closes)

    def r(value: float | None, digits: int = 2) -> float | None:
        return round(value, digits) if value is not None else None

    return {
        "price": r(closes[-1]) if closes else None,
        "sma20": r(latest(sma(closes, 20))),
        "sma50": r(latest(sma(closes, 50))),
        "sma200": r(latest(sma(closes, 200))) if len(closes) >= 200 else None,
        "ema20": r(latest(ema(closes, 20))),
        "rsi14": r(latest(rsi(closes, 14)), 1),
        "macd": r(latest(lines["macd"]), 3),
        "macd_signal": r(latest(lines["signal"]), 3),
        "macd_histogram": r(latest(lines["histogram"]), 3),
        "bollinger_upper": r(latest(bands["upper"])),
        "bollinger_middle": r(latest(bands["middle"])),
        "bollinger_lower": r(latest(bands["lower"])),
        "volatility_pct": r(volatility(closes, 20), 1) if len(closes) > 21 else None,
    }


# ---------------------------------------------------------------------------
# forecasts
# ---------------------------------------------------------------------------


def _logs(closes: Sequence[float]) -> list[float]:
    return [math.log(max(value, 1e-9)) for value in closes]


def trend_forecast(closes: Sequence[float], h: int) -> float:
    """A least-squares line through the log prices of the last :data:`TREND_WINDOW` days, ``h`` days on."""
    logs = _logs(closes[-TREND_WINDOW:])
    n = len(logs)
    if n < 2:
        return float(closes[-1])
    mean_x = (n - 1) / 2
    mean_y = sum(logs) / n
    sxx = sum((x - mean_x) ** 2 for x in range(n))
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in enumerate(logs))
    slope = sxy / sxx if sxx else 0.0
    intercept = mean_y - slope * mean_x
    return math.exp(intercept + slope * (n - 1 + h))


def holt_forecast(closes: Sequence[float], h: int) -> float:
    """Holt's linear exponential smoothing with a damped trend, on log prices."""
    logs = _logs(closes[-HOLT_WINDOW:])
    if len(logs) < 3:
        return float(closes[-1])
    level, trend = logs[0], logs[1] - logs[0]
    for value in logs[1:]:
        previous = level
        level = HOLT_ALPHA * value + (1 - HOLT_ALPHA) * (level + HOLT_PHI * trend)
        trend = HOLT_BETA * (level - previous) + (1 - HOLT_BETA) * HOLT_PHI * trend
    damped = sum(HOLT_PHI**i for i in range(1, h + 1))
    return math.exp(level + damped * trend)


def model_forecast(closes: Sequence[float], h: int, model: str) -> float:
    if model == "trend":
        return trend_forecast(closes, h)
    if model == "smoothing":
        return holt_forecast(closes, h)
    if model == "ensemble":
        return math.exp(
            (math.log(trend_forecast(closes, h)) + math.log(holt_forecast(closes, h)))
            / 2
        )
    if model == "naive":
        return float(closes[-1])
    raise ValueError(f"Unknown model {model}.")


def forecast(closes: Sequence[float], h: int) -> dict[str, Any]:
    """Each model's price ``h`` trading days on, the ensemble's 80% band and the expected change."""
    if len(closes) < 30:
        raise ValueError("At least 30 days of prices are needed for a forecast.")
    last = float(closes[-1])
    values = {model: model_forecast(closes, h, model) for model in MODELS}
    sigma = daily_volatility(closes, 60)
    middle = math.log(values["ensemble"])
    spread = BAND_Z * sigma * math.sqrt(h)
    return {
        "horizon": h,
        "last": round(last, 4),
        "models": {
            model: {
                "label": MODEL_LABELS[model],
                "price": round(price, 4),
                "change_pct": round((price / last - 1) * 100, 2),
            }
            for model, price in values.items()
        },
        "low": round(math.exp(middle - spread), 4),
        "high": round(math.exp(middle + spread), 4),
        "band": "80%",
        "change_pct": round((values["ensemble"] / last - 1) * 100, 2),
    }


def path(closes: Sequence[float], h: int) -> list[dict[str, float]]:
    """The ensemble's forecast for each day 1..h with its band (for the chart)."""
    sigma = daily_volatility(closes, 60)
    out = []
    for step in range(1, h + 1):
        middle = math.log(model_forecast(closes, step, "ensemble"))
        spread = BAND_Z * sigma * math.sqrt(step)
        out.append(
            {
                "step": step,
                "price": round(math.exp(middle), 4),
                "low": round(math.exp(middle - spread), 4),
                "high": round(math.exp(middle + spread), 4),
            }
        )
    return out


def backtest(
    closes: Sequence[float],
    h: int,
    *,
    window: int = 250,
    step: int = 5,
    minimum: int = 80,
) -> dict[str, Any]:
    """Walk forward over (up to) the last ``window`` days: at each origin forecast ``h`` days on using only the
    closes up to that day, then compare with the real close ``h`` days later.

    Direction hit rate = how often the model said up/down correctly; MAPE = the mean absolute percentage error."""
    n = len(closes)
    first = max(minimum, n - window - h)
    origins = list(range(first, n - h, step))
    results: dict[str, dict[str, Any]] = {}
    for model in (*MODELS, "naive"):
        hits = tries = 0
        errors: list[float] = []
        for origin in origins:
            history = closes[: origin + 1]
            last = history[-1]
            actual = closes[origin + h]
            predicted = model_forecast(history, h, model)
            errors.append(abs(predicted - actual) / actual * 100)
            if model == "naive":
                continue
            said = predicted - last
            happened = actual - last
            if said == 0 or happened == 0:
                continue
            tries += 1
            if (said > 0) == (happened > 0):
                hits += 1
        results[model] = {
            "label": MODEL_LABELS[model],
            "tests": len(origins),
            "direction_hit_pct": round(hits / tries * 100, 1)
            if tries and model != "naive"
            else None,
            "mape_pct": round(sum(errors) / len(errors), 2) if errors else None,
        }
    up_days = sum(
        1 for origin in origins if closes[origin + h] > closes[origin]
    )  # how often the price simply went up
    return {
        "horizon": h,
        "tests": len(origins),
        "from": first,
        "models": results,
        "went_up_pct": round(up_days / len(origins) * 100, 1) if origins else None,
    }


# ---------------------------------------------------------------------------
# the signal
# ---------------------------------------------------------------------------


def signal(
    closes: Sequence[float], forecast20: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Bullish, neutral or bearish, with each reason in plain words (a score from -6 to +6)."""
    ind = indicators(closes)
    price = ind["price"] or 0
    reasons: list[dict[str, Any]] = []

    def add(points: int, text: str) -> None:
        reasons.append({"points": points, "text": text})

    if ind["sma50"]:
        if price > ind["sma50"]:
            add(1, "The price is above its 50-day average (an uptrend).")
        else:
            add(-1, "The price is below its 50-day average (a downtrend).")
    if ind["sma50"] and ind["sma200"]:
        if ind["sma50"] > ind["sma200"]:
            add(
                1,
                "The 50-day average is above the 200-day average (a long-term uptrend).",
            )
        else:
            add(
                -1,
                "The 50-day average is below the 200-day average (a long-term downtrend).",
            )
    if ind["rsi14"] is not None:
        if ind["rsi14"] >= 70:
            add(-1, f"RSI is {ind['rsi14']:.0f}: it rose fast and may be overbought.")
        elif ind["rsi14"] <= 30:
            add(1, f"RSI is {ind['rsi14']:.0f}: it fell fast and may be oversold.")
        else:
            add(0, f"RSI is {ind['rsi14']:.0f}: neither overbought nor oversold.")
    if ind["macd_histogram"] is not None:
        if ind["macd_histogram"] > 0:
            add(1, "MACD is above its signal line (momentum is rising).")
        else:
            add(-1, "MACD is below its signal line (momentum is falling).")
    if ind["bollinger_upper"] and price > ind["bollinger_upper"]:
        add(-1, "The price is above the upper Bollinger band (stretched high).")
    elif ind["bollinger_lower"] and price < ind["bollinger_lower"]:
        add(1, "The price is below the lower Bollinger band (stretched low).")
    if forecast20 is not None:
        change = forecast20["change_pct"]
        if change >= 2:
            add(1, f"The models expect about {change:+.1f}% over 20 trading days.")
        elif change <= -2:
            add(-1, f"The models expect about {change:+.1f}% over 20 trading days.")
        else:
            add(
                0,
                f"The models expect little change ({change:+.1f}%) over 20 trading days.",
            )
    score = sum(item["points"] for item in reasons)
    label = "bullish" if score >= 2 else "bearish" if score <= -2 else "neutral"
    return {"label": label, "score": score, "reasons": reasons}
