"""
Score a single ticker at a specific point in time using Amir's criteria.
Shows BOTH V1 and V2 scores side by side.

Usage:
    python score_snapshot.py GSIW --date 2026-03-09              # Score at daily close
    python score_snapshot.py GSIW --date 2026-03-09 --time 10am  # Score at 10am PST
    python score_snapshot.py GSIW --date 2026-03-09 --time 2pm   # Score at 2pm PST
"""
import argparse
import sys
from datetime import datetime, timedelta

import yfinance as yf
import config
import config_v2


def parse_time_pst(time_str):
    """Parse a time string like '10am', '2pm', '6:30am' into EST hour (for yfinance).
    PST/PDT to EST/EDT conversion: add 3 hours."""
    time_str = time_str.lower().strip().replace(' ', '')
    minutes = 0
    if 'am' in time_str or 'pm' in time_str:
        is_pm = 'pm' in time_str
        parts = time_str.replace('am', '').replace('pm', '').split(':')
        hour = int(parts[0])
        if len(parts) > 1:
            minutes = int(parts[1])
        if is_pm and hour != 12:
            hour += 12
        if not is_pm and hour == 12:
            hour = 0
    else:
        parts = time_str.split(':')
        hour = int(parts[0])
        if len(parts) > 1:
            minutes = int(parts[1])
    # Convert PST/PDT to EST/EDT (add 3 hours)
    est_hour = hour + 3
    return hour, minutes, est_hour


def score_v1(volume_ratio, consecutive_up, share_count, market_cap, price, ma_10, ma_50, price_vs_vwap):
    """V1 scoring: 100 point scale."""
    score = 0
    if volume_ratio >= config.EXTREME_SPIKE_MULTIPLIER:
        score += 40
    elif volume_ratio >= config.VOLUME_SPIKE_MULTIPLIER:
        score += 20 + min(20, (volume_ratio - config.VOLUME_SPIKE_MULTIPLIER) * 5)
    elif volume_ratio >= 2.0:
        score += 10
    score += min(20, consecutive_up * 7)
    if share_count:
        if share_count <= 5_000_000:
            score += 15
        elif share_count <= 10_000_000:
            score += 10
        elif share_count <= 15_000_000:
            score += 5
    if market_cap:
        if market_cap <= 5_000_000:
            score += 10
        elif market_cap <= 20_000_000:
            score += 5
    if price_vs_vwap == "ABOVE":
        score += 10
    if ma_10 and price > ma_10:
        score += 5
    return round(score, 1)


def score_v2(volume_ratio, consecutive_up, share_count, market_cap, price,
             ma_10, ma_50, price_vs_vwap, vol_pct_float, range_position, offering_penalty):
    """V2 scoring: 100 base, up to -15 penalty."""
    score = 0
    # Volume spike (0-25)
    if volume_ratio >= config_v2.EXTREME_SPIKE_MULTIPLIER:
        score += config_v2.SCORE_VOLUME_SPIKE_MAX
    elif volume_ratio >= config_v2.VOLUME_SPIKE_MULTIPLIER:
        score += 12 + min(13, (volume_ratio - 3.0) * 3)
    elif volume_ratio >= 2.0:
        score += 6
    # Vol/Float (0-15)
    if vol_pct_float >= config_v2.VOL_FLOAT_HIGH:
        score += config_v2.SCORE_VOL_FLOAT_MAX
    elif vol_pct_float >= config_v2.VOL_FLOAT_MID:
        score += 10
    elif vol_pct_float >= config_v2.VOL_FLOAT_LOW:
        score += 5
    # Volume trend (0-20)
    score += min(config_v2.SCORE_VOLUME_TREND_MAX, consecutive_up * 7)
    # Low float (0-15)
    if share_count:
        if share_count <= 5_000_000:
            score += config_v2.SCORE_LOW_FLOAT_MAX
        elif share_count <= 10_000_000:
            score += 10
        elif share_count <= 15_000_000:
            score += 5
    # VWAP (0-10)
    if price_vs_vwap == "ABOVE":
        score += config_v2.SCORE_VWAP_MAX
    # Range position (0-5)
    if range_position <= config_v2.RANGE_POS_LOW_THRESHOLD:
        score += config_v2.SCORE_RANGE_POS_MAX
    elif range_position <= config_v2.RANGE_POS_MID_THRESHOLD:
        score += 3
    # Small cap (0-5)
    if market_cap:
        if market_cap <= 5_000_000:
            score += config_v2.SCORE_SMALL_CAP_MAX
        elif market_cap <= 20_000_000:
            score += 3
    # Momentum (0-5)
    if ma_10 and price > ma_10:
        score += config_v2.SCORE_MOMENTUM_MAX
    # Offering penalty
    score -= offering_penalty
    return max(0, round(score, 1))


def score_at_date(ticker, target_date, target_time_pst=None):
    """Pull data as of a specific date (and optionally time) and score with both V1 and V2."""
    stock = yf.Ticker(ticker)
    info = stock.info or {}

    # Pull enough history for 50-day MA + buffer
    start = target_date - timedelta(days=90)
    end = target_date + timedelta(days=1)

    hist = stock.history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))
    if hist.empty:
        print(f"No data for {ticker} in that range.")
        sys.exit(1)

    target_str = target_date.strftime("%Y-%m-%d")
    hist_before = hist[hist.index.normalize() <= target_date.strftime("%Y-%m-%d")]
    if hist_before.empty:
        print(f"No data for {ticker} on or before {target_str}")
        sys.exit(1)

    time_label = "daily close"
    intraday_price = None
    intraday_volume = None
    intraday_high = None
    intraday_low = None

    # Try to get intraday data (5-min bars for better premarket resolution)
    try:
        intra_start = target_date.strftime("%Y-%m-%d")
        intra_end = (target_date + timedelta(days=1)).strftime("%Y-%m-%d")
        intraday = None
        for interval in ["5m", "15m", "1h"]:
            try:
                intraday = stock.history(start=intra_start, end=intra_end, interval=interval, prepost=True)
                if intraday is not None and not intraday.empty:
                    print(f"  Using {interval} interval data ({len(intraday)} bars, prepost=True)")
                    break
            except Exception:
                continue
        if intraday is None:
            intraday = stock.history(start=intra_start, end=intra_end, interval="1h")

        if not intraday.empty:
            if target_time_pst:
                pst_hour, pst_min, est_hour = parse_time_pst(target_time_pst)
                time_label = f"{target_time_pst.upper()} PST"
                est_target_minutes = est_hour * 60 + pst_min

                best_bar = None
                best_idx = None
                best_diff = 99999
                for idx in intraday.index:
                    if hasattr(idx, 'hour'):
                        bar_minutes = idx.hour * 60 + idx.minute
                        diff = abs(bar_minutes - est_target_minutes)
                        if diff < best_diff:
                            best_diff = diff
                            best_bar = intraday.loc[idx]
                            best_idx = idx

                if best_bar is not None:
                    intraday_price = best_bar['Close']
                    intraday_volume = best_bar['Volume']
                    intraday_high = best_bar['High']
                    intraday_low = best_bar['Low']
                    print(f"\n  INTRADAY DATA ({time_label}):")
                    print(f"  Timestamp:   {best_idx}")
                    print(f"  Price:       ${intraday_price:.2f}")
                    print(f"  Volume:      {int(intraday_volume):,} (that bar)")
                    print(f"  High/Low:    ${intraday_low:.2f} - ${intraday_high:.2f}")

                    cum_vol = 0
                    for idx in intraday.index:
                        if idx <= best_idx:
                            cum_vol += intraday.loc[idx]['Volume']
                    print(f"  Cumul. Vol:  {int(cum_vol):,} (total up to {time_label})")
                    intraday_volume = cum_vol
                else:
                    print(f"\n  (Could not find bar for {time_label}; using daily close)")
            else:
                for idx in intraday.index:
                    if hasattr(idx, 'hour') and idx.hour in [14, 15]:
                        noon_bar = intraday.loc[idx]
                        print(f"\n  INTRADAY DATA (closest to noon PST):")
                        print(f"  Timestamp:   {idx}")
                        print(f"  Price:       ${noon_bar['Close']:.2f}")
                        print(f"  Volume:      {int(noon_bar['Volume']):,} (that bar)")
                        print(f"  High/Low:    ${noon_bar['Low']:.2f} - ${noon_bar['High']:.2f}")
                        break
    except Exception as e:
        print(f"  (Intraday data unavailable: {e}; using daily close)")

    # Choose price/volume to score with
    if intraday_price is not None:
        close_price = intraday_price
        today_volume = intraday_volume
        daily_high = intraday_high
        daily_low = intraday_low
        print(f"\n  ** Scoring at {time_label} price (${close_price:.2f}) **")
    else:
        row = hist_before.iloc[-1]
        close_price = row["Close"]
        today_volume = row["Volume"]
        daily_high = row["High"]
        daily_low = row["Low"]

    print(f"\nScoring {ticker} as of {target_str} ({time_label})")
    print("=" * 60)

    # Shared metrics
    vol_window = hist_before.iloc[-config.VOLUME_AVG_PERIOD - 1:-1] if len(hist_before) > config.VOLUME_AVG_PERIOD else hist_before.iloc[:-1]
    avg_volume = vol_window["Volume"].mean() if not vol_window.empty else 0
    volume_ratio = today_volume / avg_volume if avg_volume > 0 else 0

    recent_vols = hist_before["Volume"].iloc[-5:].tolist()
    consecutive_up = 0
    for i in range(len(recent_vols) - 1, 0, -1):
        if recent_vols[i] > recent_vols[i - 1]:
            consecutive_up += 1
        else:
            break

    ma_10 = hist_before["Close"].iloc[-config.SHORT_MA_PERIOD:].mean() if len(hist_before) >= config.SHORT_MA_PERIOD else None
    ma_50 = hist_before["Close"].iloc[-config.LONG_MA_PERIOD:].mean() if len(hist_before) >= config.LONG_MA_PERIOD else None

    typical_price = (daily_high + daily_low + close_price) / 3
    price_vs_vwap = "ABOVE" if close_price >= typical_price else "BELOW"

    w52_high = info.get("fiftyTwoWeekHigh", hist_before["High"].max())
    w52_low = info.get("fiftyTwoWeekLow", hist_before["Low"].min())

    market_cap = info.get("marketCap", 0)
    float_shares = info.get("floatShares", 0)
    shares_out = info.get("sharesOutstanding", 0)
    share_count = float_shares or shares_out
    name = info.get("shortName", ticker)

    # V2-specific metrics
    vol_pct_float = today_volume / share_count if share_count > 0 else 0
    if w52_high and w52_low and w52_high != w52_low:
        range_position = (close_price - w52_low) / (w52_high - w52_low)
        range_position = max(0.0, min(1.0, range_position))
    else:
        range_position = 0.5

    # Check SEC offerings (V2 penalty) — try to import, skip if not available
    offering_penalty = 0
    offering_count = 0
    try:
        from screener_v2 import check_offering_history
        offering_info = check_offering_history(ticker, verbose=False)
        offering_penalty = offering_info.get("penalty", 0)
        offering_count = offering_info.get("count", 0)
    except Exception:
        pass

    # Calculate both scores
    v1_score = score_v1(volume_ratio, consecutive_up, share_count, market_cap, close_price, ma_10, ma_50, price_vs_vwap)
    v2_score = score_v2(volume_ratio, consecutive_up, share_count, market_cap, close_price,
                        ma_10, ma_50, price_vs_vwap, vol_pct_float, range_position, offering_penalty)

    # Risk/reward
    stop_loss = close_price * (1 - config.MAX_DOWNSIDE_PCT)
    target_price = close_price * (1 + config.MAX_DOWNSIDE_PCT * config.RISK_REWARD_RATIO)

    # Print results
    print(f"\n  {ticker} - {name}")
    print(f"  Date:          {target_str}")
    print(f"  {'-' * 50}")
    print(f"  Price:         ${close_price:.2f}")
    print(f"  Market Cap:    ${market_cap:,.0f}" if market_cap else "  Market Cap:    N/A")
    print(f"  Float:         {float_shares:,.0f}" if float_shares else f"  Shares Out:    {shares_out:,.0f}" if shares_out else "  Float:         N/A")
    print(f"  {'-' * 50}")
    print(f"  Volume:        {int(today_volume):,}")
    print(f"  Avg Vol (10d): {int(avg_volume):,}")
    print(f"  Vol Ratio:     {volume_ratio:.1f}x {'** EXTREME SPIKE **' if volume_ratio >= 10 else '* SPIKE *' if volume_ratio >= 3 else ''}")
    print(f"  Vol/Float:     {vol_pct_float * 100:.1f}%")
    print(f"  Consec Up:     {consecutive_up} days")
    print(f"  {'-' * 50}")
    print(f"  10-day MA:     ${ma_10:.2f}" if ma_10 else "  10-day MA:     N/A")
    print(f"  50-day MA:     ${ma_50:.2f}" if ma_50 else "  50-day MA:     N/A")
    print(f"  VWAP (approx): ${typical_price:.2f} ({price_vs_vwap})")
    print(f"  52w Range:     ${w52_low:.2f} - ${w52_high:.2f}" if w52_low and w52_high else "")
    print(f"  Range Pos:     {range_position * 100:.0f}%")
    print(f"  Daily Range:   ${daily_low:.2f} - ${daily_high:.2f}")
    if offering_count:
        print(f"  SEC Offerings: {offering_count} in last 12mo (penalty: -{offering_penalty} pts)")
    print(f"  {'-' * 50}")
    print(f"  Stop Loss:     ${stop_loss:.2f} (-10%)")
    print(f"  Target (1:5):  ${target_price:.2f} (+50%)")

    # ============================================================
    # V1 SCORE BREAKDOWN
    # ============================================================
    print(f"\n  {'=' * 50}")
    print(f"  V1 SCORE BREAKDOWN:")
    print(f"  {'=' * 50}")
    v1_vol = 40 if volume_ratio >= 10 else (20 + min(20, (volume_ratio - 3) * 5)) if volume_ratio >= 3 else 10 if volume_ratio >= 2 else 0
    v1_trend = min(20, consecutive_up * 7)
    v1_float = 15 if share_count and share_count <= 5_000_000 else 10 if share_count and share_count <= 10_000_000 else 5 if share_count and share_count <= 15_000_000 else 0
    v1_cap = 10 if market_cap and market_cap <= 5_000_000 else 5 if market_cap and market_cap <= 20_000_000 else 0
    v1_vwap = 10 if price_vs_vwap == "ABOVE" else 0
    v1_ma = 5 if ma_10 and close_price > ma_10 else 0

    print(f"    Volume spike:    {v1_vol:>3}/40  (ratio: {volume_ratio:.1f}x)")
    print(f"    Volume trend:    {v1_trend:>3}/20  ({consecutive_up} consecutive days)")
    print(f"    Low float:       {v1_float:>3}/15  ({share_count:,.0f} shares)" if share_count else f"    Low float:       {v1_float:>3}/15  (N/A)")
    print(f"    Small cap:       {v1_cap:>3}/10  (${market_cap:,.0f})" if market_cap else f"    Small cap:       {v1_cap:>3}/10  (N/A)")
    print(f"    VWAP position:   {v1_vwap:>3}/10  ({price_vs_vwap})")
    print(f"    Momentum (MA):   {v1_ma:>3}/5   ({'above' if v1_ma > 0 else 'below'} 10d MA)")
    print(f"    {'-' * 40}")
    print(f"    V1 TOTAL:        {v1_score:>3}/100")

    # ============================================================
    # V2 SCORE BREAKDOWN
    # ============================================================
    print(f"\n  {'=' * 50}")
    print(f"  V2 SCORE BREAKDOWN:")
    print(f"  {'=' * 50}")
    v2_vol = 25 if volume_ratio >= 10 else (12 + min(13, (volume_ratio - 3) * 3)) if volume_ratio >= 3 else 6 if volume_ratio >= 2 else 0
    v2_volfloat = 15 if vol_pct_float >= 0.10 else 10 if vol_pct_float >= 0.05 else 5 if vol_pct_float >= 0.02 else 0
    v2_trend = min(20, consecutive_up * 7)
    v2_float = 15 if share_count and share_count <= 5_000_000 else 10 if share_count and share_count <= 10_000_000 else 5 if share_count and share_count <= 15_000_000 else 0
    v2_vwap = 10 if price_vs_vwap == "ABOVE" else 0
    v2_range = 5 if range_position <= 0.25 else 3 if range_position <= 0.50 else 0
    v2_cap = 5 if market_cap and market_cap <= 5_000_000 else 3 if market_cap and market_cap <= 20_000_000 else 0
    v2_ma = 5 if ma_10 and close_price > ma_10 else 0

    print(f"    Vol Spike:       {v2_vol:>3}/25  (ratio: {volume_ratio:.1f}x)")
    print(f"    Vol/Float:       {v2_volfloat:>3}/15  ({vol_pct_float * 100:.1f}% of float)")
    print(f"    Volume trend:    {v2_trend:>3}/20  ({consecutive_up} consecutive days)")
    print(f"    Low float:       {v2_float:>3}/15  ({share_count:,.0f} shares)" if share_count else f"    Low float:       {v2_float:>3}/15  (N/A)")
    print(f"    VWAP position:   {v2_vwap:>3}/10  ({price_vs_vwap})")
    print(f"    Range position:  {v2_range:>3}/5   ({range_position * 100:.0f}% of 52w range)")
    print(f"    Small cap:       {v2_cap:>3}/5   (${market_cap:,.0f})" if market_cap else f"    Small cap:       {v2_cap:>3}/5   (N/A)")
    print(f"    Momentum (MA):   {v2_ma:>3}/5   ({'above' if v2_ma > 0 else 'below'} 10d MA)")
    if offering_penalty:
        print(f"    Offering penalty: -{offering_penalty}/15  ({offering_count} offerings)")
    print(f"    {'-' * 40}")
    print(f"    V2 TOTAL:        {v2_score:>3}/100")

    # ============================================================
    # SIDE-BY-SIDE COMPARISON
    # ============================================================
    print(f"\n  {'=' * 50}")
    print(f"  COMPARISON:  V1 = {v1_score}/100  |  V2 = {v2_score}/100")
    diff = v2_score - v1_score
    if diff > 0:
        print(f"  V2 scores +{diff} higher (Vol/Float & Range Position add value)")
    elif diff < 0:
        print(f"  V1 scores +{abs(diff)} higher (V1 weights volume spike more heavily)")
    else:
        print(f"  Both models agree")
    print(f"  {'=' * 50}")

    # Threshold check
    print(f"\n  TRADE SIGNAL:")
    for label, s in [("V1", v1_score), ("V2", v2_score)]:
        if s >= 60:
            print(f"    {label}: {s}/100 -- HIGH CONVICTION (60+ threshold)")
        elif s >= 50:
            print(f"    {label}: {s}/100 -- WATCHLIST (50+ threshold)")
        else:
            print(f"    {label}: {s}/100 -- Below trading threshold")

    # Filter check
    print(f"\n  FILTER CHECK:")
    checks = [
        ("Price >= $0.50", close_price >= config.MIN_PRICE),
        (f"Market cap <= ${config.MAX_MARKET_CAP:,.0f}", not market_cap or market_cap <= config.MAX_MARKET_CAP),
        (f"Market cap >= ${config.MIN_MARKET_CAP:,.0f}", not market_cap or market_cap >= config.MIN_MARKET_CAP),
        (f"Float <= {config.MAX_FLOAT_SHARES:,.0f}", not share_count or share_count <= config.MAX_FLOAT_SHARES),
        (f"Volume >= {config.MIN_DAILY_VOLUME:,}", today_volume >= config.MIN_DAILY_VOLUME),
    ]
    failed_filters = []
    for label, passed in checks:
        status = "[OK]" if passed else "[!]"
        print(f"    {status} {label}")
        if not passed:
            failed_filters.append(label)

    if failed_filters:
        print(f"\n  [!] Would be filtered out due to: {', '.join(failed_filters)}")
    else:
        print(f"\n  [PASS] Would PASS all filters and appear in the watchlist")

    print(f"\n  Note: Market cap & float are current values (yfinance limitation).")
    print(f"  Price, volume, and MAs are from actual {target_str} data.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Score a ticker at a specific date/time (V1 + V2)")
    parser.add_argument("ticker", help="Stock ticker (e.g., MRNO)")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="Date to score (YYYY-MM-DD), defaults to today")
    parser.add_argument("--time", default=None, help="Time in PST (e.g., 6am, 6:30am, 2pm). If omitted, uses daily close.")
    args = parser.parse_args()

    target = datetime.strptime(args.date, "%Y-%m-%d")
    score_at_date(args.ticker.upper(), target, target_time_pst=args.time)
