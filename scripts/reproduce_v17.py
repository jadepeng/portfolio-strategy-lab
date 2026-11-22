"""Reproduce V17 vs V15/V16 full backtest comparison, stability checks, and report artifacts."""
from __future__ import annotations
from pathlib import Path
import json, sys, zipfile
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fusion_v6 import v6_base, V6Config
from fusion_v7 import V7Config
from fusion_v10 import V10Config, simulate_v10, results, PRICES, TARGETS
from fusion_v11 import V11Config, signals_v11
from v13.regime_v13 import RegimeConfig, signals_v13
from v13.daily_ohlc_2026 import load as load_cn_daily
from v14.market_snapshot import load_weekly
from v14.crowding import build_factors, classify, state_overlay, OverlayStateConfig
from v15.us_snapshot import load_us_prices, load_us_open
from v15.cross_market import us_to_cn_overlay, CrossConfig
from v15.us_strategy import USConfig, us_signals, simulate_us
from v16.overnight import DailyGateConfig, load_event_snapshot
from v16.daily_replay import run_daily_replay
from v17.us_v17 import USV17Config, simulate_us_v17
from v17.cn_v17 import CNV17Config, build_cn_v17_weekly, run_cn_daily_replay_v17


def weekly_metrics(nav: pd.Series, trades: int = 0, turnover: float = 0.0) -> dict:
    r = results(nav)
    calmar = r["cagr"] / abs(r["mdd"]) if r["mdd"] < 0 else float("nan")
    n21_23 = nav.loc["2020-12-25":"2023-12-31"]
    n24_26 = nav.loc["2023-12-25":]
    r21_23 = (n21_23.iloc[-1] / n21_23.iloc[0] - 1.0) * 100
    r24_26 = (n24_26.iloc[-1] / n24_26.iloc[0] - 1.0) * 100
    r26 = (nav.iloc[-1] / nav.loc[nav.index < "2026-01-01"].iloc[-1] - 1.0) * 100
    return dict(
        total_pct=round(float(r["total"]), 2),
        cagr_pct=round(float(r["cagr"]), 2),
        mdd_pct=round(float(r["mdd"]), 2),
        vol_pct=round(float(r["vol"]), 2),
        sharpe=round(float(r["sharpe"]), 3),
        calmar=round(float(calmar), 2),
        pos52w_pct=round(float(r["positive_52w"]), 1),
        r21_23_pct=round(float(r21_23), 2),
        r24_26_pct=round(float(r24_26), 2),
        r26_pct=round(float(r26), 2),
        trades=int(trades),
        turnover=round(float(turnover), 1),
    )


def daily_metrics(df: pd.DataFrame) -> dict:
    nav = df["nav"].astype(float)
    dd = nav / nav.cummax().clip(lower=1.0) - 1.0
    dret = nav.pct_change().dropna()
    sharpe = float(dret.mean() * 252 / (dret.std(ddof=1) * np.sqrt(252))) if dret.std() > 0 else float("nan")
    ret = (nav.iloc[-1] - 1.0) * 100
    mdd = float(dd.min()) * 100
    calmar = ret / abs(mdd) if mdd < 0 else float("nan")
    return dict(
        ret26_pct=round(float(ret), 3),
        mdd26_pct=round(float(mdd), 3),
        sharpe=round(sharpe, 3),
        calmar=round(float(calmar), 2),
        weekly_rebal=int((df["weekly_turnover"] > 0).sum()),
        emergency_days=int((df["emergency_turnover"] > 0).sum()) if "emergency_turnover" in df else 0,
    )


def main():
    out = ROOT / "research_outputs"
    out.mkdir(exist_ok=True)

    prices = pd.read_csv(ROOT / "market_data" / "verified_5asset_weekly_v13.csv", parse_dates=["week_start"]).set_index("week_start")
    market = load_weekly()
    us_p = load_us_prices()
    us_o = load_us_open()
    cn_daily = load_cn_daily()
    events = load_event_snapshot()

    # 1. US Comparison (next_week_open & next_week_close)
    us_comp = []
    us_curves = {}
    for core in (0.0, 0.30):
        for fmode in ("next_week_open", "next_week_close"):
            uc0 = USConfig(qqq_core=core, rebalance_threshold=0.04, fill_mode=fmode)
            sim0 = simulate_us(us_p, us_signals(us_p, uc0), uc0, us_o if fmode == "next_week_open" else None)
            uc1 = USV17Config(qqq_core=core, fill_mode=fmode)
            _, sim1 = simulate_us_v17(us_p, uc1, us_o if fmode == "next_week_open" else None)
            lbl = f"core={int(core*100)}% ({fmode})"
            us_comp.append({"version": "V15_base", "mode": lbl, **weekly_metrics(sim0["nav"], (sim0["turnover"] > 0).sum(), sim0["turnover"].sum())})
            us_comp.append({"version": "V17_opt", "mode": lbl, **weekly_metrics(sim1["nav"], (sim1["turnover"] > 0).sum(), sim1["turnover"].sum())})
            if fmode == "next_week_open" and core == 0.0:
                us_curves["V15_US_0core"] = sim0["nav"]
                us_curves["V17_US_0core"] = sim1["nav"]
            if fmode == "next_week_open" and core == 0.30:
                us_curves["V15_US_30core"] = sim0["nav"]
                us_curves["V17_US_30core"] = sim1["nav"]

    # 2. A-Share Weekly Comparison (2020-2026)
    cfg15 = V10Config(portfolio_vol_brake=True)
    base15 = signals_v11(prices, v6_base("week"), V6Config(), V7Config(red_core=0.30, cash_mode="money_price", money_etf_side_cost=0.0001), cfg15, V11Config(risk_guard=False))
    trend15 = signals_v13(prices, base15, RegimeConfig(hold_weeks=8))
    cn15 = state_overlay(prices, trend15, classify(build_factors(prices, market)), OverlayStateConfig(freeze_weeks=3))
    cn_us15 = us_to_cn_overlay(prices, cn15, us_p, CrossConfig())
    sim_cn15 = simulate_v10(prices, cn_us15, cfg15)

    cfg17, cn_us17, sim_cn17 = build_cn_v17_weekly(prices, market, us_p, CNV17Config())

    cn_comp = [
        {"version": "V15_CN_weekly", **weekly_metrics(sim_cn15["nav"], (sim_cn15["turnover"] > 0).sum(), sim_cn15["turnover"].sum())},
        {"version": "V17_CN_weekly", **weekly_metrics(sim_cn17["nav"], (sim_cn17["turnover"] > 0).sum(), sim_cn17["turnover"].sum())},
    ]

    # 3. A-Share 2026 Daily Comparison
    prev15 = sim_cn15.loc[sim_cn15.index < cn_daily.index[0]].iloc[-1]
    pw15 = prev15[[f"held_{k}" for k in ("tech", "red", "bond", "gold", "money")]].to_numpy(float)
    pc15 = prices.loc[prev15.name, list(PRICES)].to_numpy(float)

    prev17 = sim_cn17.loc[sim_cn17.index < cn_daily.index[0]].iloc[-1]
    pw17 = prev17[[f"held_{k}" for k in ("tech", "red", "bond", "gold", "money")]].to_numpy(float)
    pc17 = prices.loc[prev17.name, list(PRICES)].to_numpy(float)

    daily_comp = []
    d_v16_off = run_daily_replay(cn_daily, cn_us15, pw15, DailyGateConfig(mode="off"), cfg15, events, pc15)
    d_v16_floc = run_daily_replay(cn_daily, cn_us15, pw15, DailyGateConfig(mode="freeze", require_local_weakness=True), cfg15, events, pc15)
    d_v16_hlv = run_daily_replay(cn_daily, cn_us15, pw15, DailyGateConfig(mode="halve", require_local_weakness=False), cfg15, events, pc15)
    d_v17_off = run_cn_daily_replay_v17(cn_daily, cn_us17, pw17, cfg17, events, pc17, DailyGateConfig(mode="off"), "next_friday_close")
    d_v17_floc = run_cn_daily_replay_v17(cn_daily, cn_us17, pw17, cfg17, events, pc17, DailyGateConfig(mode="freeze", require_local_weakness=True), "next_friday_close")
    d_v17_mon = run_cn_daily_replay_v17(cn_daily, cn_us17, pw17, cfg17, events, pc17, DailyGateConfig(mode="freeze", require_local_weakness=True), "next_monday_open")

    for name, df in [
        ("V16_base_off (Fri close)", d_v16_off),
        ("V16_halve (Fri close)", d_v16_hlv),
        ("V16_freeze_local_MA5 (Fri close)", d_v16_floc),
        ("V17_weekly_off (Fri close)", d_v17_off),
        ("V17_weekly_freeze_MA5 (Fri close)", d_v17_floc),
        ("V17_weekly_freeze_MA5 (Mon open)", d_v17_mon),
    ]:
        daily_comp.append({"variant": name, **daily_metrics(df)})

    # Save CSVs & JSON
    pd.DataFrame(us_comp).to_csv(out / "V17_us_comparison.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(cn_comp).to_csv(out / "V17_cn_weekly_comparison.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(daily_comp).to_csv(out / "V17_cn_2026_daily_comparison.csv", index=False, encoding="utf-8-sig")
    d_v17_floc.to_csv(out / "V17_CN_2026_freeze_local_daily_ledger.csv", index_label="date")

    summary = {
        "version": "V17 Optimization & Verification",
        "effective_through": "2026-11-20",
        "us_comparison": us_comp,
        "cn_weekly_comparison": cn_comp,
        "cn_2026_daily_comparison": daily_comp,
        "latest_cn_v17_targets": {k: round(float(v), 4) for k, v in cn_us17.iloc[-1][list(TARGETS)].items()},
        "latest_us_v17_30core_targets": {
            "target_qqq": round(float(simulate_us_v17(us_p, USV17Config(qqq_core=0.30), us_o)[0].iloc[-1]["target_qqq"]), 4),
            "target_bil": round(float(simulate_us_v17(us_p, USV17Config(qqq_core=0.30), us_o)[0].iloc[-1]["target_bil"]), 4),
        },
    }
    (out / "V17_verified_results.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    # Sanity assertions (causality & conservation)
    assert sim_cn17["nav"].notna().all() and (sim_cn17["nav"] > 0).all()
    assert us_curves["V17_US_0core"].notna().all() and (us_curves["V17_US_0core"] > 0).all()
    assert abs(sum(summary["latest_cn_v17_targets"].values()) - 1.0) < 1e-6

    print("=== US COMPARISON ===")
    print(pd.DataFrame(us_comp).to_string(index=False))
    print("\n=== CN WEEKLY COMPARISON ===")
    print(pd.DataFrame(cn_comp).to_string(index=False))
    print("\n=== CN 2026 DAILY COMPARISON ===")
    print(pd.DataFrame(daily_comp).to_string(index=False))
    print("\nLatest CN V17 targets:", summary["latest_cn_v17_targets"])
    print("Latest US V17 (30% core) targets:", summary["latest_us_v17_30core_targets"])


if __name__ == "__main__":
    main()
