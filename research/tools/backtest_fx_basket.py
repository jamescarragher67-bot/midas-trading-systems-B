"""
tools/backtest_fx_basket.py - Candidate 3 full-rigor test.

Stage 0: resolve each pair's actual broker symbol name and confirm real
         specs via mt5.symbol_info() - the "typical" pip-value numbers
         from the scoping pass are NOT trusted here.
Stage 1: run backtest/lsc_engine.py UNCHANGED (same signal, same session
         filter, same M15 timeframe, same everything) independently per
         pair, split in-sample/out-of-sample.
Stage 2: ONLY pairs clearing PF >= EDGE_PF_MIN in BOTH halves are kept.
         Pairs that don't show genuine independent edge are dropped, not
         combined - combining a no-edge leg with an edge leg is just
         diversifying noise into the result, not a real risk reduction.
Stage 3: ONLY IF 2+ pairs pass Stage 2, build a CORRELATED Monte Carlo:
         bucket each surviving pair's trades by calendar day, then shuffle
         the ORDER OF DAYS using the SAME permutation across all pairs
         simultaneously (not independent per-pair shuffles). This preserves
         real cross-pair correlation (e.g. a shared USD-news day hitting
         EURUSD and GBPUSD together stays together in every shuffle)
         instead of manufacturing a diversification benefit that isn't
         actually in the historical data.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np
import MetaTrader5 as mt5

from backtest.lsc_engine import run_lsc_simulation, get_symbol_specs
from research.backtest.metrics import calculate_metrics

# Candidate liquid majors - each entry is a list of name variants to try,
# since this broker's naming convention for FX (vs the confirmed
# XAUUSD.a / XAGUSD.a pattern for metals) has not been checked yet.
CANDIDATE_PAIRS = {
    "EURUSD": ["EURUSD.a", "EURUSD", "EURUSDm"],
    "GBPUSD": ["GBPUSD.a", "GBPUSD", "GBPUSDm"],
    "AUDUSD": ["AUDUSD.a", "AUDUSD", "AUDUSDm"],
    "USDCAD": ["USDCAD.a", "USDCAD", "USDCADm"],
}

EDGE_PF_MIN     = 1.05
BASELINE_RISK_PCT = 0.1
SPREAD_POINTS_DEFAULT = 2   # placeholder ONLY until Stage 0 confirms real typical spread per pair - flagged in output
N_SHUFFLES = 1000
SEED = 42


def resolve_symbol(variants: list) -> str | None:
    for name in variants:
        if mt5.symbol_select(name, True):
            info = mt5.symbol_info(name)
            if info is not None:
                return name
    return None


def stage0_confirm_specs():
    print("=" * 70)
    print("STAGE 0 - confirming real broker specs via symbol_info() (no assumptions)")
    print("=" * 70)
    resolved = {}
    for label, variants in CANDIDATE_PAIRS.items():
        name = resolve_symbol(variants)
        if name is None:
            print(f"  {label}: NOT FOUND under any of {variants} - EXCLUDED, not guessed at")
            continue
        info = mt5.symbol_info(name)
        tick = mt5.symbol_info_tick(name)
        spread_pts = round((tick.ask - tick.bid) / info.point, 1) if tick else None
        print(f"  {label} -> resolved as '{name}'")
        print(f"    point={info.point}  contract_size={info.trade_contract_size}  "
              f"volume_min={info.volume_min}  current_spread={spread_pts}pts")
        resolved[label] = {
            "symbol": name, "point": info.point, "contract_size": info.trade_contract_size,
            "volume_min": info.volume_min, "volume_max": info.volume_max,
            "volume_step": info.volume_step, "spread_points": spread_pts or SPREAD_POINTS_DEFAULT,
        }
    return resolved


def stage1_per_pair_backtest(resolved: dict):
    print("\n" + "=" * 70)
    print("STAGE 1 - per-pair backtest, in-sample/out-of-sample (LSC logic UNCHANGED)")
    print("=" * 70)
    results = {}
    for label, specs in resolved.items():
        print(f"\n--- {label} ({specs['symbol']}) ---")
        config = {
            "initial_balance": 50000.0,
            "risk_pct": BASELINE_RISK_PCT,
            "max_lot_size": 50.0,
            "spread_points": specs["spread_points"],
            "cooldown_bars": 3,
            "max_trades_per_day": 4,
        }
        trades = run_lsc_simulation(specs["symbol"], days=3000, config=config)
        n = len(trades)
        if n < 30:
            print(f"  Only {n} trades - not enough signal to evaluate. EXCLUDED.")
            results[label] = {"trades": trades, "genuine_edge": False, "reason": "insufficient_trades"}
            continue

        half = n // 2
        is_trades, oos_trades = trades[:half], trades[half:]
        m_is  = calculate_metrics(is_trades, 50000.0)
        m_oos = calculate_metrics(oos_trades, 50000.0)
        print(f"  Total: {n} trades | In-sample ({len(is_trades)}): PF {m_is['profit_factor']} "
              f"| Out-of-sample ({len(oos_trades)}): PF {m_oos['profit_factor']}")

        genuine = m_is["profit_factor"] >= EDGE_PF_MIN and m_oos["profit_factor"] >= EDGE_PF_MIN
        print(f"  Genuine independent edge (PF >= {EDGE_PF_MIN} both halves)? {'YES' if genuine else 'NO'}")
        results[label] = {
            "trades": trades, "genuine_edge": genuine,
            "pf_is": m_is["profit_factor"], "pf_oos": m_oos["profit_factor"], "n_trades": n,
        }
    return results


def stage2_floor_check(surviving: dict, resolved: dict, account_sizes=(10000.0, 50000.0), leverage=10.0):
    """run_lsc_simulation() (Stage 1, kept UNCHANGED per instructions) doesn't
    track lot-floor clamping - it uses LSC's flat max_lot_size cap, not the
    floor-aware formula tools/risk_calibrator.py uses. This is a SEPARATE,
    additive check on top of the unchanged Stage 1 backtest, only for pairs
    that already showed genuine edge - not a modification of Stage 1 itself.
    Skipping this would mean shipping the whole reason today's design pass
    happened without ever actually checking it for this candidate."""
    print("\n" + "=" * 70)
    print("STAGE 2 - lot-floor check for surviving pairs (additive, not part of")
    print("the unchanged LSC backtest above)")
    print("=" * 70)
    for label in surviving:
        specs = resolved[label]
        point, contract_size = specs["point"], specs["contract_size"]
        vol_min = specs["volume_min"]
        for balance in account_sizes:
            sl_dists = []
            for t in surviving[label]["trades"]:
                sl_dist = abs(t["entry"] - t["sl"])
                if sl_dist > 0:
                    sl_dists.append(sl_dist)
            sl_dists = np.array(sl_dists)
            risk_amt = balance * (BASELINE_RISK_PCT / 100)
            raw_lots = risk_amt / (sl_dists * contract_size)
            pct_floored = (raw_lots < vol_min).mean() * 100
            print(f"  {label} @ ${balance:,.0f}: {pct_floored:.1f}% of trades would floor-clamp "
                  f"at {BASELINE_RISK_PCT}% risk (median SL dist {np.median(sl_dists):.5f})")


def bucket_by_day(trades: list) -> dict:
    days = {}
    for t in trades:
        days.setdefault(t["date"], []).append(t)
    return days


def stage3_correlated_basket_mc(surviving: dict, account_size: float = 50000.0,
                                 n_shuffles: int = N_SHUFFLES, seed: int = SEED):
    """Allocates account_size / n_pairs to each surviving pair (equal
    weight - simplest defensible allocation, not optimized), then shuffles
    calendar days IDENTICALLY across all pairs so real cross-pair
    correlation on any given day is preserved in every shuffle."""
    pairs = list(surviving.keys())
    n_pairs = len(pairs)
    per_pair_balance = account_size / n_pairs

    # Build the union of all calendar days any pair traded on, then each
    # pair's per-day pct return (0.0 on days it didn't trade).
    all_days = sorted(set().union(*[bucket_by_day(surviving[p]["trades"]).keys() for p in pairs]))
    day_pct_returns = {p: [] for p in pairs}
    for p in pairs:
        buckets = bucket_by_day(surviving[p]["trades"])
        for day in all_days:
            day_trades = buckets.get(day, [])
            if not day_trades:
                day_pct_returns[p].append(0.0)
                continue
            # pct return of this pair's day relative to ITS OWN running balance -
            # approximated as sum(pnl)/balance_before_day using the pair's own
            # backtest balance path (already computed at its OWN full account
            # size in Stage 1) - rescaled to this pair's equal-weight slice.
            day_pnl = sum(t["pnl"] for t in day_trades)
            balance_before = day_trades[0]["balance_after"] - day_trades[0]["pnl"]
            day_pct_returns[p].append(day_pnl / balance_before if balance_before > 0 else 0.0)

    rng = np.random.default_rng(seed)
    idx = np.arange(len(all_days))
    max_dds = np.zeros(n_shuffles)
    for s in range(n_shuffles):
        shuffled_idx = rng.permutation(idx)
        balance = account_size
        peak = account_size
        max_dd = 0.0
        for day_i in shuffled_idx:
            day_return = sum(day_pct_returns[p][day_i] * (per_pair_balance / account_size) for p in pairs)
            balance *= (1 + day_return)
            if balance > peak:
                peak = balance
            dd = (peak - balance) / peak * 100 if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        max_dds[s] = max_dd

    return {
        "n_pairs": n_pairs, "pairs": pairs,
        "median_dd_pct": round(float(np.median(max_dds)), 2),
        "worst_5pct_dd_pct": round(float(np.percentile(max_dds, 95)), 2),
        "worst_dd_pct": round(float(np.max(max_dds)), 2),
    }


def main():
    if not mt5.initialize():
        print(f"MT5 not connectable: {mt5.last_error()}")
        return

    resolved = stage0_confirm_specs()
    if not resolved:
        print("\n*** STOPPING: no candidate pairs resolved on this broker. ***")
        return

    results = stage1_per_pair_backtest(resolved)

    print("\n" + "=" * 70)
    print("STAGE 1 SUMMARY")
    print("=" * 70)
    surviving = {}
    for label, r in results.items():
        status = "GENUINE EDGE" if r.get("genuine_edge") else "NO EDGE / EXCLUDED"
        print(f"  {label}: {status}"
              + (f"  (PF_IS={r['pf_is']}, PF_OOS={r['pf_oos']}, n={r['n_trades']})" if "pf_is" in r else ""))
        if r.get("genuine_edge"):
            surviving[label] = r

    if len(surviving) < 2:
        print(f"\n*** STOPPING HERE - {len(surviving)} pair(s) showed genuine independent edge. ***")
        print("A basket needs 2+ legs with real individual edge to be worth combining.")
        print("Not building the correlated Monte Carlo on fewer than that - there's nothing")
        print("to diversify, and forcing a 'basket' result out of 0-1 real legs would just be")
        print("reporting a single-instrument result dressed up as a portfolio one.")
        return

    print(f"\n{len(surviving)} pairs cleared both halves - proceeding to floor check + correlated basket Monte Carlo.")
    stage2_floor_check(surviving, resolved)

    print("\n" + "=" * 70)
    print("STAGE 3 - Correlated (day-block shuffle) basket Monte Carlo")
    print("=" * 70)
    for account_size in [10000.0, 50000.0]:
        mc = stage3_correlated_basket_mc(surviving, account_size)
        print(f"\n${account_size:,.0f} basket ({mc['n_pairs']} pairs: {', '.join(mc['pairs'])}):")
        print(f"  median DD: {mc['median_dd_pct']}%   worst-5th-pct: {mc['worst_5pct_dd_pct']}%   "
              f"absolute-worst: {mc['worst_dd_pct']}%")

    print("\nDone.")


if __name__ == "__main__":
    main()
