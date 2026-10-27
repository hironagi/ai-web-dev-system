from __future__ import annotations

import argparse
import csv
import itertools
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Iterable


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class StrategyParams:
    opening_bars: int
    breakout_buffer_bps: float
    stop_bps: float
    take_profit_bps: float
    max_hold_bars: int
    direction: str = "both"


@dataclass
class Trade:
    day: str
    side: str
    entry: float
    exit: float
    gross_return: float
    net_return: float
    pnl_yen: float
    reason: str


def load_bars(path: Path) -> list[Bar]:
    bars: list[Bar] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        if not required.issubset(set(reader.fieldnames or [])):
            missing = sorted(required - set(reader.fieldnames or []))
            raise ValueError(f"CSV列が不足しています: {missing}")
        for row in reader:
            bars.append(
                Bar(
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            )
    bars.sort(key=lambda b: b.timestamp)
    if not bars:
        raise ValueError("CSVに価格データがありません。")
    return bars


def group_by_day(bars: Iterable[Bar]) -> dict[str, list[Bar]]:
    out: dict[str, list[Bar]] = {}
    for bar in bars:
        key = bar.timestamp.date().isoformat()
        out.setdefault(key, []).append(bar)
    return out


def _net_return(gross_return: float, fee_bps: float, slippage_bps: float) -> float:
    round_trip_cost = 2.0 * (fee_bps + slippage_bps) / 10_000.0
    return gross_return - round_trip_cost


def simulate_day(
    day: str,
    bars: list[Bar],
    params: StrategyParams,
    *,
    notional_yen: float,
    fee_bps: float,
    slippage_bps: float,
) -> Trade | None:
    if len(bars) <= params.opening_bars:
        return None

    opening = bars[: params.opening_bars]
    range_high = max(b.high for b in opening)
    range_low = min(b.low for b in opening)
    buffer = params.breakout_buffer_bps / 10_000.0
    long_trigger = range_high * (1.0 + buffer)
    short_trigger = range_low * (1.0 - buffer)

    side: str | None = None
    entry = 0.0
    entry_index = -1
    for idx, bar in enumerate(bars[params.opening_bars :], start=params.opening_bars):
        if params.direction in {"both", "long"} and bar.close > long_trigger:
            side = "long"
            entry = bar.close
            entry_index = idx
            break
        if params.direction in {"both", "short"} and bar.close < short_trigger:
            side = "short"
            entry = bar.close
            entry_index = idx
            break

    if side is None:
        return None

    stop_frac = params.stop_bps / 10_000.0
    tp_frac = params.take_profit_bps / 10_000.0
    if side == "long":
        stop = entry * (1.0 - stop_frac)
        take = entry * (1.0 + tp_frac)
    else:
        stop = entry * (1.0 + stop_frac)
        take = entry * (1.0 - tp_frac)

    future = bars[entry_index + 1 : entry_index + 1 + params.max_hold_bars]
    if not future:
        return None

    exit_price = future[-1].close
    reason = "time"
    for bar in future:
        if side == "long":
            hit_stop = bar.low <= stop
            hit_take = bar.high >= take
        else:
            hit_stop = bar.high >= stop
            hit_take = bar.low <= take

        # 同一バー内で両方到達した場合は、楽観バイアスを避けて損切りを先に扱う。
        if hit_stop:
            exit_price = stop
            reason = "stop"
            break
        if hit_take:
            exit_price = take
            reason = "take_profit"
            break

    if side == "long":
        gross = (exit_price - entry) / entry
    else:
        gross = (entry - exit_price) / entry

    net = _net_return(gross, fee_bps, slippage_bps)
    return Trade(
        day=day,
        side=side,
        entry=entry,
        exit=exit_price,
        gross_return=gross,
        net_return=net,
        pnl_yen=notional_yen * net,
        reason=reason,
    )


def run_strategy(
    bars: list[Bar],
    params: StrategyParams,
    *,
    notional_yen: float,
    fee_bps: float,
    slippage_bps: float,
) -> list[Trade]:
    trades: list[Trade] = []
    for day, day_bars in group_by_day(bars).items():
        trade = simulate_day(
            day,
            day_bars,
            params,
            notional_yen=notional_yen,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
        )
        if trade:
            trades.append(trade)
    return trades


def metrics(trades: list[Trade], target_daily_yen: float) -> dict:
    if not trades:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "avg_trade_pnl_yen": 0.0,
            "expectancy_return": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_yen": 0.0,
            "net_pnl_yen": 0.0,
            "target_hit_rate": 0.0,
        }

    wins = [t for t in trades if t.pnl_yen > 0]
    gains = sum(t.pnl_yen for t in trades if t.pnl_yen > 0)
    losses = -sum(t.pnl_yen for t in trades if t.pnl_yen < 0)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for trade in trades:
        equity += trade.pnl_yen
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    return {
        "trades": len(trades),
        "win_rate": len(wins) / len(trades),
        "avg_trade_pnl_yen": mean(t.pnl_yen for t in trades),
        "expectancy_return": mean(t.net_return for t in trades),
        "profit_factor": gains / losses if losses > 0 else float("inf"),
        "max_drawdown_yen": max_dd,
        "net_pnl_yen": sum(t.pnl_yen for t in trades),
        "target_hit_rate": sum(t.pnl_yen >= target_daily_yen for t in trades) / len(trades),
    }


def split_train_test(bars: list[Bar], train_ratio: float = 0.7) -> tuple[list[Bar], list[Bar]]:
    days = sorted(group_by_day(bars))
    if len(days) < 10:
        raise ValueError("最低10営業日分のデータが必要です。")
    cut = max(1, min(len(days) - 1, int(len(days) * train_ratio)))
    train_days = set(days[:cut])
    test_days = set(days[cut:])
    train = [b for b in bars if b.timestamp.date().isoformat() in train_days]
    test = [b for b in bars if b.timestamp.date().isoformat() in test_days]
    return train, test


def parameter_grid() -> Iterable[StrategyParams]:
    for values in itertools.product(
        [3, 6, 12],          # opening_bars
        [0.0, 5.0, 10.0],   # breakout buffer bps
        [20.0, 35.0, 50.0], # stop bps
        [30.0, 50.0, 80.0], # take profit bps
        [3, 6, 12],          # max hold bars
        ["both"],
    ):
        yield StrategyParams(*values)


def rank_candidate(train_metrics: dict) -> float:
    if train_metrics["trades"] < 20:
        return -1e18
    pf = min(train_metrics["profit_factor"], 5.0)
    return (
        train_metrics["avg_trade_pnl_yen"]
        + 200.0 * (pf - 1.0)
        - 0.05 * train_metrics["max_drawdown_yen"]
    )


def research(
    bars: list[Bar],
    *,
    notional_yen: float,
    fee_bps: float,
    slippage_bps: float,
    target_daily_yen: float,
    top_n: int,
) -> dict:
    train, test = split_train_test(bars)
    ranked = []
    for params in parameter_grid():
        train_trades = run_strategy(
            train,
            params,
            notional_yen=notional_yen,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
        )
        train_m = metrics(train_trades, target_daily_yen)
        ranked.append((rank_candidate(train_m), params, train_m))

    ranked.sort(key=lambda x: x[0], reverse=True)
    candidates = []
    for score, params, train_m in ranked[:top_n]:
        test_trades = run_strategy(
            test,
            params,
            notional_yen=notional_yen,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
        )
        test_m = metrics(test_trades, target_daily_yen)
        candidates.append(
            {
                "params": asdict(params),
                "train_score": score,
                "train": train_m,
                "test": test_m,
                "passes_basic_gate": (
                    test_m["trades"] >= 10
                    and test_m["expectancy_return"] > 0
                    and test_m["profit_factor"] > 1.0
                    and test_m["net_pnl_yen"] > 0
                ),
            }
        )

    return {
        "assumptions": {
            "notional_yen": notional_yen,
            "fee_bps_per_side": fee_bps,
            "slippage_bps_per_side": slippage_bps,
            "target_daily_yen": target_daily_yen,
            "train_ratio": 0.7,
            "execution": "bar-close entry; conservative stop-first when stop and target occur in same bar",
        },
        "candidates": candidates,
        "warning": "バックテスト結果は将来の利益を保証しません。実資金投入前に十分な未使用期間・ペーパートレードで再検証してください。",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Daytrade Research v0: CSV価格データからルールを探索・未使用期間で検証")
    parser.add_argument("csv", type=Path, help="timestamp,open,high,low,close,volume 列を持つCSV")
    parser.add_argument("--notional-yen", type=float, default=1_000_000.0, help="1取引あたりの仮想建玉額")
    parser.add_argument("--fee-bps", type=float, default=0.0, help="片道手数料 bps")
    parser.add_argument("--slippage-bps", type=float, default=2.0, help="片道スリッページ bps")
    parser.add_argument("--target-daily-yen", type=float, default=1_000.0, help="1取引日の目標利益（評価指標のみ）")
    parser.add_argument("--top-n", type=int, default=10, help="未使用期間で再検証する上位候補数")
    parser.add_argument("--output", type=Path, default=Path("daytrade_research_result.json"))
    args = parser.parse_args()

    bars = load_bars(args.csv)
    result = research(
        bars,
        notional_yen=args.notional_yen,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        target_daily_yen=args.target_daily_yen,
        top_n=args.top_n,
    )
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
