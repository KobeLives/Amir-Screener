"""
Amir Watchlist Screener — V2 Enhanced
Scans small-cap stocks for volume spikes, low float, and momentum signals.
Enhanced with volume-to-float ratio, 52-week range positioning, short interest,
offering history penalties, and support/resistance levels.
Uses free data: yfinance for market data, SEC EDGAR for insider activity & offerings.

Usage:
    python screener_v2.py                  # Run with defaults
    python screener_v2.py --output json    # Output as JSON
    python screener_v2.py --output html    # Output as HTML report
    python screener_v2.py --verbose        # Show debug info
"""

import argparse
import json
import os
import sys
import warnings
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

import config_v2 as config

warnings.filterwarnings("ignore", category=FutureWarning)


# ============================================================
# STEP 1: BUILD THE STOCK UNIVERSE
# ============================================================

def _load_custom_watchlist():
    """
    Load tickers from watchlist.txt in the screener directory.
    One ticker per line, # comments allowed, blank lines ignored.
    This lets Amir add tickers he always wants scanned.
    """
    watchlist_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchlist.txt")
    tickers = []
    if os.path.exists(watchlist_path):
        try:
            with open(watchlist_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        for t in line.split(","):
                            t = t.strip().upper()
                            if t:
                                tickers.append(t)
            if tickers:
                print(f"    Custom watchlist: {len(tickers)} tickers from watchlist.txt")
        except Exception as e:
            print(f"    Warning: Could not read watchlist.txt: {e}")
    return tickers


def _run_finviz_screen(Overview, filters, verbose=False, label=""):
    """Run a single Finviz screen and return list of (ticker, row_data) tuples."""
    results = []
    try:
        foverview = Overview()
        foverview.set_filter(filters_dict=filters)
        df = foverview.screener_view()
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                sym = row.get("Ticker", "")
                if not sym or "." in sym or "-" in sym:
                    continue
                results.append((sym.upper(), row))
            if verbose:
                print(f"    {label}: {len(results)} tickers")
    except Exception as e:
        if verbose:
            print(f"    {label} failed: {str(e)[:80]}")
    return results


def _get_finviz_candidates(verbose=False):
    """
    Comprehensive Finviz market sweep — discovery engine + pre-filter.

    Three layers of scanning:
      Layer A: FULL MARKET SWEEP — scan ALL micro/nano-cap stocks (not just active ones)
               Any stock with relative volume >= 1.0 gets included.
               This is what catches stocks like MRNO early.
      Layer B: ACTIVITY SCREENS — targeted screens for volume spikes, gainers, momentum
      Layer C: SIGNAL SCREENS — Finviz built-in signals (Top Gainers, Unusual Volume, etc.)

    Returns: (tickers_list, finviz_cache_dict)
    """
    tickers = []
    finviz_cache = {}

    try:
        from finvizfinance.screener.overview import Overview

        def cache_row(sym, row):
            """Cache Finviz data for a ticker."""
            if sym not in finviz_cache:
                finviz_cache[sym] = {
                    "finviz_price": row.get("Price", 0),
                    "finviz_change_pct": row.get("Change", 0),
                    "finviz_volume": row.get("Volume", 0),
                    "finviz_rel_volume": row.get("Relative Volume", 0),
                    "finviz_market_cap": row.get("Market Cap", 0),
                    "finviz_sector": row.get("Sector", ""),
                    "finviz_industry": row.get("Industry", ""),
                }

        # =============================================================
        # LAYER A: FULL MARKET SWEEP
        # Scan ALL micro/nano-cap stocks with ANY activity (rel vol >= 1)
        # This is the broadest net — catches everything yfinance misses
        # =============================================================
        print("    Layer A: Full market sweep (all micro/nano caps with activity)...")
        sweep_screens = [
            # ALL nano-caps with any volume above average
            {"Market Cap.": "Nano (under $50mln)", "Relative Volume": "Over 1", "Price": "Over $0.50"},
            # ALL micro-caps with any volume above average
            {"Market Cap.": "Micro ($50mln to $300mln)", "Relative Volume": "Over 1", "Price": "Over $0.50"},
            # ALL nano-caps with decent absolute volume (even if rel vol is low)
            {"Market Cap.": "Nano (under $50mln)", "Current Volume": "Over 50K", "Price": "Over $0.50"},
            # ALL micro-caps with decent absolute volume
            {"Market Cap.": "Micro ($50mln to $300mln)", "Current Volume": "Over 100K", "Price": "Over $0.50"},
            # Small-caps ($300M-$2B) but ONLY if volume is spiking
            {"Market Cap.": "Small (under $2bln)", "Relative Volume": "Over 1.5", "Price": "Over $0.50", "Average Volume": "Over 50K"},
        ]
        layer_a_count = 0
        for i, filters in enumerate(sweep_screens):
            results = _run_finviz_screen(Overview, filters, verbose, f"Sweep {i+1}")
            for sym, row in results:
                tickers.append(sym)
                cache_row(sym, row)
                layer_a_count += 1
        if verbose:
            print(f"    Layer A total: {len(set(t for t in tickers))} unique tickers")

        # =============================================================
        # LAYER B: ACTIVITY SCREENS (targeted)
        # These catch specific patterns — gap-ups, high momentum, etc.
        # =============================================================
        print("    Layer B: Activity screens (gainers, volume spikes, momentum)...")
        activity_screens = [
            # Big gainers today (any cap under $2B)
            {"Market Cap.": "Small (under $2bln)", "Change": "Up 5%", "Price": "Over $0.50", "Average Volume": "Over 50K"},
            # Extreme volume spikes
            {"Market Cap.": "Small (under $2bln)", "Relative Volume": "Over 3", "Price": "Over $0.50"},
            # Gap up today
            {"Market Cap.": "Small (under $2bln)", "Change": "Up 10%", "Price": "Over $0.50"},
            # New 52-week highs in micro/nano
            {"Market Cap.": "Micro ($50mln to $300mln)", "52-Week High/Low": "New High", "Price": "Over $0.50"},
            # Price above SMA20 with upward movement
            {"Market Cap.": "Micro ($50mln to $300mln)", "20-Day Simple Moving Average": "Price above SMA20", "Change": "Up", "Price": "Over $0.50", "Average Volume": "Over 50K"},
            # Nano-cap gainers (these are the runners)
            {"Market Cap.": "Nano (under $50mln)", "Change": "Up 3%", "Price": "Over $0.50"},
            # High volume nano-caps (could be starting a run)
            {"Market Cap.": "Nano (under $50mln)", "Current Volume": "Over 200K", "Price": "Over $0.50"},
        ]
        layer_b_count = 0
        for i, filters in enumerate(activity_screens):
            results = _run_finviz_screen(Overview, filters, verbose, f"Activity {i+1}")
            for sym, row in results:
                tickers.append(sym)
                cache_row(sym, row)
                layer_b_count += 1
        if verbose:
            print(f"    Layer B added: {layer_b_count} results")

        # =============================================================
        # LAYER C: SIGNAL-BASED SCREENS
        # Finviz built-in signals that catch specific trading patterns
        # =============================================================
        print("    Layer C: Signal screens (unusual volume, most volatile, etc.)...")
        signal_screens = [
            # Unusual volume signal + small cap
            {"Market Cap.": "Small (under $2bln)", "Relative Volume": "Over 2", "Average Volume": "Over 50K", "Price": "Over $0.50"},
            # Most volatile small caps
            {"Market Cap.": "Small (under $2bln)", "Volatility": "Week - Over 8%", "Price": "Over $0.50", "Average Volume": "Over 50K"},
            # Short squeeze candidates (high short float + volume)
            {"Market Cap.": "Small (under $2bln)", "Float Short": "Over 15%", "Relative Volume": "Over 1", "Price": "Over $0.50"},
            # Recent IPOs with activity
            {"Market Cap.": "Small (under $2bln)", "IPO Date": "In the last year", "Relative Volume": "Over 1", "Price": "Over $0.50"},
        ]
        layer_c_count = 0
        for i, filters in enumerate(signal_screens):
            results = _run_finviz_screen(Overview, filters, verbose, f"Signal {i+1}")
            for sym, row in results:
                tickers.append(sym)
                cache_row(sym, row)
                layer_c_count += 1
        if verbose:
            print(f"    Layer C added: {layer_c_count} results")

        # =============================================================
        # SUMMARY AND PRE-FILTER
        # =============================================================
        if finviz_cache:
            total_screens = len(sweep_screens) + len(activity_screens) + len(signal_screens)
            print(f"    Finviz returned {len(finviz_cache)} unique tickers across {total_screens} screens")

            # Pre-filter: remove tickers that clearly fail hard gates
            filtered_out = []
            for sym, data in list(finviz_cache.items()):
                mcap = data.get("finviz_market_cap", 0)
                price = data.get("finviz_price", 0)
                vol = data.get("finviz_volume", 0)

                if isinstance(mcap, (int, float)) and mcap > 0 and mcap > config.MAX_MARKET_CAP:
                    filtered_out.append(sym)
                    del finviz_cache[sym]
                elif isinstance(mcap, (int, float)) and mcap > 0 and mcap < config.MIN_MARKET_CAP:
                    filtered_out.append(sym)
                    del finviz_cache[sym]
                elif isinstance(price, (int, float)) and price < config.MIN_PRICE:
                    filtered_out.append(sym)
                    del finviz_cache[sym]
                elif isinstance(vol, (int, float)) and vol > 0 and vol < config.MIN_DAILY_VOLUME:
                    filtered_out.append(sym)
                    del finviz_cache[sym]

            if filtered_out and verbose:
                print(f"    Pre-filter removed {len(filtered_out)} tickers (mcap/price/volume)")
            print(f"    After pre-filter: {len(finviz_cache)} tickers survive -> yfinance")

    except ImportError:
        print("    [!] finvizfinance not installed -- run: pip install finvizfinance")
        print("    Falling back to yfinance + curated list only")
    except Exception as e:
        if verbose:
            print(f"    Finviz error: {str(e)[:80]}")

    return list(set(t.upper() for t in tickers if t.upper() in finviz_cache)), finviz_cache


def get_small_cap_universe(verbose=False):
    """
    Build the stock universe using a multi-source pipeline:
      1. Custom watchlist.txt (Amir's personal picks -- always scanned)
      2. Finviz screener (16-screen 3-layer market sweep + pre-filtering)
         - Layer A: Full sweep of ALL micro/nano caps (rel vol >= 1.0)
         - Layer B: Activity screens (gainers, gap-ups, momentum)
         - Layer C: Signal screens (unusual vol, volatile, short squeeze, IPOs)
         - Pre-filters out stocks that fail market cap/price/volume gates
         - Caches Finviz data (rel volume, sector, etc.) for later use
      3. yfinance Screener API (most_actives, gainers, losers, etc.)
      4. Yahoo premarket/trending movers
      5. Curated seed list (fallback -- ensures minimum coverage)

    Returns: (tickers_list, finviz_cache_dict)
    """
    print("[1/6] Building stock universe...")
    seed_tickers = []

    # Source 1: Custom watchlist file (always scanned, never filtered)
    custom = _load_custom_watchlist()
    seed_tickers.extend(custom)

    # Source 2: Finviz screener (broad scan + pre-filter)
    finviz_tickers, finviz_cache = _get_finviz_candidates(verbose=verbose)
    seed_tickers.extend(finviz_tickers)

    # Source 3: yfinance Screener API
    yf_count = 0
    try:
        from yfinance import Screener
        for screen_name in ["most_actives", "small_cap_gainers", "day_gainers", "day_losers",
                            "aggressive_small_caps", "undervalued_growth_stocks"]:
            try:
                sc = Screener()
                sc.set_default(screen_name, count=100)
                resp = sc.response
                body = resp.get("body", resp) if isinstance(resp, dict) else {}
                for q in body.get("quotes", []):
                    sym = q.get("symbol", "")
                    if sym and "." not in sym and "-" not in sym:
                        seed_tickers.append(sym)
                        yf_count += 1
            except Exception:
                pass
        if yf_count:
            print(f"    yfinance Screener API returned {yf_count} tickers")
    except ImportError:
        pass

    # Source 3b: yf.screen() for older yfinance
    if yf_count < 20:
        for name in ["most_actives", "day_gainers", "small_cap_gainers", "day_losers",
                      "aggressive_small_caps", "undervalued_growth_stocks"]:
            try:
                result = yf.screen(name)
                if result and "quotes" in result:
                    for q in result["quotes"]:
                        if "symbol" in q:
                            seed_tickers.append(q["symbol"])
            except Exception:
                pass

    # Source 4: Yahoo premarket/after-hours movers
    # yfinance can pull trending tickers and premarket data
    premarket_count = 0
    try:
        # Method A: yfinance trending tickers
        trending = yf.Tickers("")  # dummy; we use the search API below
        for search_term in ["premarket movers", "premarket gainers"]:
            try:
                search_results = yf.Search(search_term, max_results=50)
                if hasattr(search_results, "quotes") and search_results.quotes:
                    for q in search_results.quotes:
                        sym = q.get("symbol", "")
                        if sym and "." not in sym and "-" not in sym and len(sym) <= 5:
                            seed_tickers.append(sym.upper())
                            premarket_count += 1
            except Exception:
                pass
    except Exception:
        pass
    # Method B: yfinance Screener for premarket-adjacent lists
    try:
        from yfinance import Screener as YFScreener2
        for pm_screen in ["conservative_foreign_funds", "growth_technology_stocks",
                          "most_shorted_stocks"]:
            try:
                sc2 = YFScreener2()
                sc2.set_default(pm_screen, count=50)
                resp2 = sc2.response
                body2 = resp2.get("body", resp2) if isinstance(resp2, dict) else {}
                for q2 in body2.get("quotes", []):
                    sym2 = q2.get("symbol", "")
                    if sym2 and "." not in sym2 and "-" not in sym2:
                        seed_tickers.append(sym2.upper())
                        premarket_count += 1
            except Exception:
                pass
    except ImportError:
        pass
    if premarket_count:
        print(f"    Yahoo premarket/trending: {premarket_count} tickers")

    # Source 5: Curated seed list (fallback)
    curated = [
        "EDSA", "ANTX", "MULN", "ATER", "PRAX", "IMVT", "MGOL", "CLOV",
        "BBIO", "TGTX", "NUVB", "VRPX", "SAVA", "BIOR", "CNTB", "SLRX",
        "ADTX", "ATNF", "OCUP", "ARDS", "EFTR", "ONCT", "MDXH", "TBIO",
        "GFAI", "BKKT", "AVTE", "LMFA", "CNET", "SOS", "BTBT", "MARA",
        "RIOT", "CIFR", "CLSK", "WULF", "IREN", "CORZ", "BITF",
        "USEG", "VTNR", "REI", "TELL", "NEXT", "ORGN", "OPAL",
        "BBIG", "RDBX", "APRN", "CENN", "NKLA", "GOEV", "FFIE",
        "FCEL", "PLUG", "QS", "LCID", "RIVN", "JOBY",
    ]
    seed_tickers.extend(curated)

    # Deduplicate and clean
    tickers = list(set(t.upper().strip() for t in seed_tickers if t))
    print(f"    Total unique tickers to screen: {len(tickers)}")

    if not tickers:
        print("    TIP: Run with --tickers to screen specific stocks")

    return tickers, finviz_cache


# ============================================================
# STEP 2: PULL DATA FOR EACH TICKER
# ============================================================

def compute_support_resistance(hist, current_price, lookback_days=120):
    """
    Find local support and resistance levels from price history.
    Uses a 5-day rolling window to find local minima (support) and maxima (resistance).
    Returns top 3 support levels and top 3 resistance levels closest to current price.
    """
    if hist.empty or len(hist) < 5:
        return [], []

    # Use only the last lookback_days
    hist_subset = hist.tail(lookback_days)
    if len(hist_subset) < 5:
        return [], []

    support_levels = []
    resistance_levels = []

    # Find local minima (support) and maxima (resistance)
    for i in range(2, len(hist_subset) - 2):
        low = hist_subset["Low"].iloc[i]
        high = hist_subset["High"].iloc[i]

        # Local minimum (support): lower than both neighbors
        if (low < hist_subset["Low"].iloc[i-1] and
            low < hist_subset["Low"].iloc[i-2] and
            low < hist_subset["Low"].iloc[i+1] and
            low < hist_subset["Low"].iloc[i+2]):
            support_levels.append(round(low, 2))

        # Local maximum (resistance): higher than both neighbors
        if (high > hist_subset["High"].iloc[i-1] and
            high > hist_subset["High"].iloc[i-2] and
            high > hist_subset["High"].iloc[i+1] and
            high > hist_subset["High"].iloc[i+2]):
            resistance_levels.append(round(high, 2))

    # Sort by distance to current price
    if support_levels:
        support_levels = sorted(set(support_levels), key=lambda x: abs(x - current_price))
        support_levels = support_levels[:config.SUPPORT_RESISTANCE_NUM_LEVELS]

    if resistance_levels:
        resistance_levels = sorted(set(resistance_levels), key=lambda x: abs(x - current_price))
        resistance_levels = resistance_levels[:config.SUPPORT_RESISTANCE_NUM_LEVELS]

    return support_levels, resistance_levels


def get_stock_data(ticker, verbose=False, no_filter=False):
    """
    Pull all the data Amir needs for a single stock.
    V2 additions: vol_pct_float, range_position, short_interest, support/resistance
    Returns a dict with all metrics, or None if data is insufficient.
    If no_filter=True, skips all filter checks and scores regardless.
    """
    try:
        stock = yf.Ticker(ticker)
        info = stock.info or {}

        # Get price history (need ~60 days for 50-day MA)
        hist = stock.history(period="3mo")
        if hist.empty or len(hist) < config.VOLUME_AVG_PERIOD:
            if verbose:
                print(f"    SKIP {ticker}: Not enough price history ({len(hist) if not hist.empty else 0} days)")
            return None

        # Current price
        current_price = hist["Close"].iloc[-1]
        if not no_filter and current_price < config.MIN_PRICE:
            if verbose:
                print(f"    SKIP {ticker}: Price ${current_price:.2f} below min ${config.MIN_PRICE}")
            return None

        # Market cap
        market_cap = info.get("marketCap", 0)
        if not no_filter and market_cap and (market_cap > config.MAX_MARKET_CAP or market_cap < config.MIN_MARKET_CAP):
            if verbose:
                print(f"    SKIP {ticker}: Market cap ${market_cap:,.0f} outside range ${config.MIN_MARKET_CAP:,.0f}-${config.MAX_MARKET_CAP:,.0f}")
            return None

        # Float shares (with validation)
        float_shares = info.get("floatShares", 0)
        shares_outstanding = info.get("sharesOutstanding", 0)
        float_is_estimated = False

        # Use float if available, otherwise shares outstanding (less accurate)
        if float_shares and float_shares > 0:
            share_count = float_shares
        elif shares_outstanding and shares_outstanding > 0:
            share_count = shares_outstanding
            float_is_estimated = True
            if verbose:
                print(f"    NOTE {ticker}: Using sharesOutstanding ({shares_outstanding:,.0f}) as float proxy (true float unavailable)")
        else:
            share_count = 0

        if not no_filter and share_count and share_count > config.MAX_FLOAT_SHARES:
            if verbose:
                print(f"    SKIP {ticker}: Float {share_count:,.0f} above max {config.MAX_FLOAT_SHARES:,.0f}")
            return None

        # Volume analysis
        today_volume = hist["Volume"].iloc[-1]
        if not no_filter and today_volume < config.MIN_DAILY_VOLUME:
            if verbose:
                print(f"    SKIP {ticker}: Volume {int(today_volume):,} below min {config.MIN_DAILY_VOLUME:,}")
            return None

        # Exclude today from average — compare today vs PRIOR 10 days only
        avg_volume = hist["Volume"].iloc[-(config.VOLUME_AVG_PERIOD + 1):-1].mean()
        volume_ratio = today_volume / avg_volume if avg_volume > 0 else 0

        # V2: Volume as % of float
        vol_pct_float = today_volume / share_count if share_count > 0 else 0
        vol_pct_float_fmt = f"{vol_pct_float * 100:.1f}%"

        # Volume trend (consecutive days increasing)
        recent_volumes = hist["Volume"].iloc[-5:].tolist()
        consecutive_up = 0
        for i in range(len(recent_volumes) - 1, 0, -1):
            if recent_volumes[i] > recent_volumes[i - 1]:
                consecutive_up += 1
            else:
                break

        # Moving averages
        ma_10 = hist["Close"].iloc[-config.SHORT_MA_PERIOD:].mean() if len(hist) >= config.SHORT_MA_PERIOD else None
        ma_50 = hist["Close"].iloc[-config.LONG_MA_PERIOD:].mean() if len(hist) >= config.LONG_MA_PERIOD else None

        # 52-week high/low
        week52_high = info.get("fiftyTwoWeekHigh", hist["High"].max())
        week52_low = info.get("fiftyTwoWeekLow", hist["Low"].min())

        # V2: Range position (0.0 = at low, 1.0 = at high)
        if week52_high and week52_low and week52_high != week52_low:
            range_position = (current_price - week52_low) / (week52_high - week52_low)
            range_position = max(0.0, min(1.0, range_position))  # Clamp to 0-1
        else:
            range_position = 0.5
        range_position_pct = f"{range_position * 100:.0f}%"

        # Daily high/low
        daily_high = hist["High"].iloc[-1]
        daily_low = hist["Low"].iloc[-1]

        # Typical price (HLC/3) — proxy for VWAP
        # NOTE: This is NOT true VWAP (which requires intraday volume-weighted bars).
        # It's the daily typical price: (High + Low + Close) / 3.
        # Named "typical_price" to be honest about what it is.
        typical_price = (hist["High"].iloc[-1] + hist["Low"].iloc[-1] + hist["Close"].iloc[-1]) / 3
        price_vs_typical = "ABOVE" if current_price >= typical_price else "BELOW"

        # V2: Short interest
        short_interest = info.get("shortPercentOfFloat", info.get("shortRatio", 0))
        short_interest_fmt = f"{short_interest * 100:.1f}%" if short_interest else "N/A"

        # V2: Support and Resistance levels
        support_levels, resistance_levels = compute_support_resistance(
            hist, current_price, lookback_days=config.SUPPORT_RESISTANCE_LOOKBACK_DAYS
        )

        # V2: Offering history (penalty check)
        offering_info = check_offering_history(ticker, verbose=verbose)
        offering_penalty = offering_info.get("penalty", 0)

        # Data freshness: when was this price data last updated?
        data_as_of = hist.index[-1]
        if hasattr(data_as_of, 'strftime'):
            data_as_of_str = data_as_of.strftime("%Y-%m-%d %I:%M %p")
        else:
            data_as_of_str = str(data_as_of)
        # Also grab regularMarketTime from info if available (more precise)
        reg_market_time = info.get("regularMarketTime", None)
        if reg_market_time:
            try:
                from datetime import timezone
                rmt = datetime.fromtimestamp(reg_market_time)
                data_as_of_str = rmt.strftime("%Y-%m-%d %I:%M %p")
            except Exception:
                pass

        # Calculate V2 score
        score = calculate_score_v2(
            volume_ratio, consecutive_up, share_count, market_cap, current_price,
            ma_10, ma_50, price_vs_typical, vol_pct_float, range_position, offering_penalty
        )

        # Risk/reward targets
        stop_loss = current_price * (1 - config.MAX_DOWNSIDE_PCT)
        target_price = current_price * (1 + config.MAX_DOWNSIDE_PCT * config.RISK_REWARD_RATIO)

        return {
            "ticker": ticker,
            "price": round(current_price, 2),
            "market_cap": market_cap,
            "market_cap_fmt": format_number(market_cap),
            "float_shares": float_shares,
            "shares_outstanding": shares_outstanding,
            "float_fmt": format_number(float_shares or shares_outstanding) + (" (est)" if float_is_estimated else ""),
            "float_is_estimated": float_is_estimated,
            "today_volume": int(today_volume),
            "today_volume_fmt": format_number(today_volume),
            "avg_volume": int(avg_volume),
            "avg_volume_fmt": format_number(avg_volume),
            "volume_ratio": round(volume_ratio, 1),
            "vol_pct_float": round(vol_pct_float, 4),
            "vol_pct_float_fmt": vol_pct_float_fmt,
            "consecutive_vol_up": consecutive_up,
            "ma_10": round(ma_10, 2) if ma_10 else None,
            "ma_50": round(ma_50, 2) if ma_50 else None,
            "week52_high": round(week52_high, 2) if week52_high else None,
            "week52_low": round(week52_low, 2) if week52_low else None,
            "range_position": round(range_position, 3),
            "range_position_pct": range_position_pct,
            "daily_high": round(daily_high, 2),
            "daily_low": round(daily_low, 2),
            "typical_price_hlc3": round(typical_price, 2),
            "price_vs_typical": price_vs_typical,
            "short_interest": short_interest,
            "short_interest_fmt": short_interest_fmt,
            "support_levels": support_levels,
            "resistance_levels": resistance_levels,
            "offering_count": offering_info.get("count", 0),
            "offering_penalty": offering_penalty,
            "stop_loss": round(stop_loss, 2),
            "target_price": round(target_price, 2),
            "score": score,
            "flags": get_flags(volume_ratio, consecutive_up, price_vs_typical),
            "name": info.get("shortName", ticker),
            "sector": info.get("sector", "Unknown"),
            "industry": info.get("industry", "Unknown"),
            "data_as_of": data_as_of_str,
        }

    except Exception as e:
        if verbose:
            print(f"    Error processing {ticker}: {e}")
        return None


# ============================================================
# SEC EDGAR — CIK CACHE (shared across offering + insider checks)
# ============================================================

_cik_cache = {}  # {ticker: cik_str} — populated once, reused
_cik_data_loaded = False
_cik_data = None


def _get_cik_for_ticker(ticker, headers, verbose=False):
    """
    Lookup CIK for a ticker, using a global cache to avoid
    re-downloading the 5MB company_tickers.json file every time.
    """
    global _cik_cache, _cik_data_loaded, _cik_data

    ticker_upper = ticker.upper()

    # Return from cache if already looked up
    if ticker_upper in _cik_cache:
        return _cik_cache[ticker_upper]

    # Load the full CIK mapping once
    if not _cik_data_loaded:
        try:
            import requests
            cik_resp = requests.get(
                "https://www.sec.gov/files/company_tickers.json",
                headers=headers,
                timeout=10
            )
            if cik_resp.status_code == 200:
                _cik_data = cik_resp.json()
                # Pre-build cache for ALL tickers (fast lookups after this)
                for entry in _cik_data.values():
                    t = entry.get("ticker", "").upper()
                    if t:
                        _cik_cache[t] = str(entry["cik_str"]).zfill(10)
            _cik_data_loaded = True
            if verbose:
                print(f"    SEC CIK data loaded: {len(_cik_cache)} companies cached")
        except Exception as e:
            _cik_data_loaded = True  # Don't retry on failure
            if verbose:
                print(f"    CIK lookup error: {e}")

    return _cik_cache.get(ticker_upper, None)


# ============================================================
# STEP 3: SEC EDGAR — OFFERING HISTORY
# ============================================================

def check_offering_history(ticker, verbose=False):
    """
    Check SEC EDGAR for S-1, S-3 offering filings in the last 12 months.
    Uses CIK lookup then checks recent filings for offering forms.
    Returns dict: {count: N, filings: [{date, form_type}], penalty: 0-15}
    """
    import requests

    headers = {"User-Agent": config.SEC_EDGAR_USER_AGENT}
    cutoff = datetime.now() - timedelta(days=config.OFFERING_LOOKBACK_MONTHS * 30)
    offering_forms = ["S-1", "S-3", "S-1/A", "S-3/A"]

    try:
        # Step 1: Get CIK from cached lookup (avoids re-downloading 5MB file)
        cik = _get_cik_for_ticker(ticker, headers, verbose=verbose)
        if not cik:
            return {"count": 0, "filings": [], "penalty": 0}

        # Step 2: Get recent filings
        filings_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        filings_resp = requests.get(filings_url, headers=headers, timeout=10)
        if filings_resp.status_code != 200:
            return {"count": 0, "filings": [], "penalty": 0}

        filings_data = filings_resp.json()
        recent = filings_data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])

        # Step 3: Find offering filings in lookback period
        offerings = []
        for i, form in enumerate(forms):
            if form in offering_forms and i < len(dates):
                try:
                    filing_date = datetime.strptime(dates[i], "%Y-%m-%d")
                    if filing_date >= cutoff:
                        offerings.append({
                            "date": dates[i],
                            "form_type": form
                        })
                except (ValueError, IndexError):
                    continue

        # Step 4: Calculate penalty
        count = len(offerings)
        penalty = 0
        if count >= 4:
            penalty = config.OFFERING_PENALTY_MAX
        elif count == 3:
            penalty = 10
        elif count == 2:
            penalty = 5
        # 0-1 offerings: no penalty

        if verbose and count > 0:
            print(f"    {ticker}: Found {count} offerings in last {config.OFFERING_LOOKBACK_MONTHS} months (penalty: -{penalty})")

        return {
            "count": count,
            "filings": offerings,
            "penalty": penalty
        }

    except Exception as e:
        if verbose:
            print(f"    Offering check error for {ticker}: {e}")
        return {"count": 0, "filings": [], "penalty": 0}


# ============================================================
# STEP 4: SCORING & RANKING (V2)
# ============================================================

def calculate_score_v2(volume_ratio, consecutive_up, share_count, market_cap, price,
                       ma_10, ma_50, price_vs_typical, vol_pct_float, range_position, offering_penalty):
    """
    Score stocks based on V2 criteria.
    Base score: 100, up to -15 penalty for offerings
    Higher score = more interesting.
    """
    score = 0

    # Volume spike (0-25 pts, reduced from 40)
    if volume_ratio >= config.EXTREME_SPIKE_MULTIPLIER:
        score += config.SCORE_VOLUME_SPIKE_MAX
    elif volume_ratio >= config.VOLUME_SPIKE_MULTIPLIER:
        score += 12 + min(13, (volume_ratio - 3.0) * 3)
    elif volume_ratio >= 2.0:
        score += 6

    # Volume as % of float (NEW, 0-15 pts)
    if vol_pct_float >= config.VOL_FLOAT_HIGH:
        score += config.SCORE_VOL_FLOAT_MAX
    elif vol_pct_float >= config.VOL_FLOAT_MID:
        score += 10
    elif vol_pct_float >= config.VOL_FLOAT_LOW:
        score += 5

    # Consecutive volume increase (0-20 points, same as V1)
    score += min(config.SCORE_VOLUME_TREND_MAX, consecutive_up * 7)

    # Low float preference (0-15 points, same as V1)
    if share_count:
        if share_count <= 5_000_000:
            score += config.SCORE_LOW_FLOAT_MAX
        elif share_count <= 10_000_000:
            score += 10
        elif share_count <= 15_000_000:
            score += 5

    # Price above Typical Price = institutional support (0-10 points, same as V1)
    if price_vs_typical == "ABOVE":
        score += config.SCORE_VWAP_MAX

    # 52-week Range Position (NEW, 0-5 pts)
    # Bottom 25% of range = most room to run
    if range_position <= config.RANGE_POS_LOW_THRESHOLD:
        score += config.SCORE_RANGE_POS_MAX
    elif range_position <= config.RANGE_POS_MID_THRESHOLD:
        score += 3

    # Small market cap preference (0-5 points, reduced from 10)
    if market_cap:
        if market_cap <= 5_000_000:
            score += config.SCORE_SMALL_CAP_MAX
        elif market_cap <= 20_000_000:
            score += 3

    # Price above 10-day MA = short-term momentum (0-5 points, same as V1)
    if ma_10 and price > ma_10:
        score += config.SCORE_MOMENTUM_MAX

    # Subtract offering penalty (0 to -15)
    score -= offering_penalty

    return max(0, round(score, 1))  # Floor at 0


def get_flags(volume_ratio, consecutive_up, price_vs_typical):
    """Generate human-readable flags for why this stock was flagged."""
    flags = []

    if volume_ratio >= config.EXTREME_SPIKE_MULTIPLIER:
        flags.append(f"EXTREME VOL SPIKE ({volume_ratio}x avg)")
    elif volume_ratio >= config.VOLUME_SPIKE_MULTIPLIER:
        flags.append(f"Volume spike ({volume_ratio}x avg)")

    if consecutive_up >= config.CONSECUTIVE_VOLUME_DAYS:
        flags.append(f"Vol increasing {consecutive_up} days straight")

    if price_vs_typical == "BELOW":
        flags.append("BELOW Typical Price — caution")

    return flags


def build_reasoning_v2(c):
    """
    Build a detailed, plain-English reasoning paragraph for each stock.
    V2 additions: vol % float, range position, short interest, support/resistance, offering history
    """
    ticker = c["ticker"]
    lines = []

    # Volume reasoning — the most important signal
    vol_ratio = c["volume_ratio"]
    consec = c["consecutive_vol_up"]
    if vol_ratio >= config.EXTREME_SPIKE_MULTIPLIER:
        lines.append(
            f"VOLUME ALERT: {ticker} traded {format_volume(c['today_volume'])} shares today — "
            f"that's {vol_ratio}x the 10-day average of {format_volume(c['avg_volume'])}. "
            f"This kind of extreme spike in a low-float name often means someone knows something before it's public."
        )
    elif vol_ratio >= config.VOLUME_SPIKE_MULTIPLIER:
        lines.append(
            f"Volume is elevated: {format_volume(c['today_volume'])} shares today vs. "
            f"{format_volume(c['avg_volume'])} average ({vol_ratio}x). Worth watching — "
            f"could be early accumulation ahead of a catalyst."
        )
    else:
        lines.append(
            f"Volume is {format_volume(c['today_volume'])} today vs. "
            f"{format_volume(c['avg_volume'])} average ({vol_ratio}x). "
            f"Not a major spike yet, but passed other filters."
        )

    if consec >= 3:
        lines.append(
            f"Volume has been climbing for {consec} consecutive days — "
            f"this pattern of building volume before news is exactly the setup to watch for."
        )
    elif consec == 2:
        lines.append(f"Volume has ticked up 2 days in a row. Not yet a strong trend, but keep on radar.")

    # V2: Volume as % of Float
    vol_pct = c["vol_pct_float"] * 100
    if vol_pct >= 10:
        lines.append(
            f"Volume represents {c['vol_pct_float_fmt']} of total float — this is a massive signal. "
            f"When volume is this high relative to available shares, even small catalysts can spark explosive moves."
        )
    elif vol_pct >= 5:
        lines.append(
            f"Volume represents {c['vol_pct_float_fmt']} of total float — notable volume concentration. "
            f"If this volume is sustained or grows, look for acceleration."
        )
    elif vol_pct >= 2:
        lines.append(f"Volume represents {c['vol_pct_float_fmt']} of total float — reasonable activity for a micro-cap.")

    # Float / share structure reasoning
    share_count = c["float_shares"] or c["shares_outstanding"]
    if share_count:
        if share_count <= 5_000_000:
            lines.append(
                f"Very low float: only {c['float_fmt']} shares. "
                f"With this few shares in circulation, even moderate buying pressure can cause outsized moves. "
                f"If volume picks up further, this could run fast."
            )
        elif share_count <= 10_000_000:
            lines.append(
                f"Low float at {c['float_fmt']} shares — in the sweet spot for potential big moves "
                f"without needing massive institutional volume."
            )
        elif share_count <= 15_000_000:
            lines.append(
                f"Float is {c['float_fmt']} — manageable, but heavier than the ideal 3-10M range. "
                f"Will need stronger catalysts to see a multi-bagger run."
            )

    # Market cap context
    mc = c["market_cap"]
    if mc:
        if mc <= 5_000_000:
            lines.append(f"Micro-cap territory at {c['market_cap_fmt']} market cap. High risk, high reward potential.")
        elif mc <= 20_000_000:
            lines.append(f"Small cap at {c['market_cap_fmt']}. Still in the zone where catalysts can move the needle.")
        else:
            lines.append(f"Market cap is {c['market_cap_fmt']} — on the larger end of the target range.")

    # Technical positioning
    price = c["price"]
    ma10 = c["ma_10"]
    ma50 = c["ma_50"]
    tech_notes = []
    if ma10 and price > ma10:
        tech_notes.append(f"above the 10-day MA (${ma10})")
    elif ma10:
        tech_notes.append(f"below the 10-day MA (${ma10})")
    if ma50 and price > ma50:
        tech_notes.append(f"above the 50-day MA (${ma50})")
    elif ma50:
        tech_notes.append(f"below the 50-day MA (${ma50})")

    if tech_notes:
        lines.append(f"Price is ${price}, trading {' and '.join(tech_notes)}.")

    # Typical Price
    if c["price_vs_typical"] == "ABOVE":
        lines.append(
            f"Trading ABOVE Typical Price (${c['typical_price_hlc3']}) — institutional buyers are supporting this level. "
            f"Positive sign for continuation."
        )
    else:
        lines.append(
            f"WARNING: Price is BELOW Typical Price (${c['typical_price_hlc3']}). "
            f"This suggests institutional selling pressure. If considering entry, wait for price to reclaim Typical Price."
        )

    # V2: 52-week Range Position
    if c["week52_high"] and c["week52_low"]:
        range_pos_pct = float(c["range_position_pct"].strip("%"))
        lines.append(
            f"52-week range: ${c['week52_low']} — ${c['week52_high']}. "
            f"Stock is at {c['range_position_pct']} of its range. "
            f"{'Near the bottom — potential deep value with room to run.' if range_pos_pct <= 25 else 'Mid-range.' if range_pos_pct <= 60 else 'Near highs — momentum is strong but watch for resistance.'}"
        )

    # V2: Short Interest
    if c["short_interest"] and c["short_interest"] > 0:
        si_pct = c["short_interest"] * 100
        if si_pct > 20:
            lines.append(
                f"Short interest is {c['short_interest_fmt']} — elevated. "
                f"High short interest can create squeeze dynamics if positive news drops."
            )
        elif si_pct > 10:
            lines.append(f"Short interest is {c['short_interest_fmt']} — notable, but not extreme.")

    # V2: Support / Resistance levels
    if c.get("support_levels") or c.get("resistance_levels"):
        support_str = ", ".join(f"${x}" for x in (c.get("support_levels") or [])[:3])
        resistance_str = ", ".join(f"${x}" for x in (c.get("resistance_levels") or [])[:3])
        levels_text = []
        if support_str:
            levels_text.append(f"Support at {support_str}")
        if resistance_str:
            levels_text.append(f"Resistance at {resistance_str}")
        if levels_text:
            lines.append(f"Key levels to watch: {' | '.join(levels_text)}")

    # Risk/reward
    lines.append(
        f"TRADE SETUP (1:5 R/R): Entry at ${price}, stop loss at ${c['stop_loss']} (-10%), "
        f"target ${c['target_price']} (+50%). "
        f"Daily range today was ${c['daily_low']}–${c['daily_high']}."
    )

    # V2: Offering History
    if c.get("offering_count", 0) > 0:
        offering_str = f"SEC filing history: {c['offering_count']} offering(s) in last 12 months."
        if c.get("offering_penalty", 0) > 0:
            offering_str += f" (Score penalty: -{c['offering_penalty']} pts)"
        lines.append(offering_str)

    # Insider activity (same as V1)
    insider = str(c.get("insider_activity", ""))
    if "BUYING" in insider or "SELLING" in insider:
        lines.append(f"INSIDER ACTIVITY: {insider}")
        if "NET: Insider buying" in insider:
            lines.append("Net insider buying detected — bullish signal.")
        elif "NET: Insider selling" in insider:
            lines.append("Net insider selling detected — caution.")
    elif "Form 4" in insider:
        lines.append(f"INSIDER ACTIVITY: {insider}")
    elif insider and insider not in ("Unable to check", "No recent insider filings", "Ticker not found in EDGAR"):
        lines.append(f"Insider check: {insider}.")

    # News
    if c.get("news"):
        lines.append("RECENT NEWS:")
        for h in c["news"][:3]:
            lines.append(f"  - {h[:150]}")

    # V2 Score breakdown
    vol_pts = 0
    if vol_ratio >= config.EXTREME_SPIKE_MULTIPLIER:
        vol_pts = config.SCORE_VOLUME_SPIKE_MAX
    elif vol_ratio >= config.VOLUME_SPIKE_MULTIPLIER:
        vol_pts = 12 + min(13, (vol_ratio - 3.0) * 3)
    elif vol_ratio >= 2.0:
        vol_pts = 6
    vol_pts = round(vol_pts, 1)

    vol_float_pts = 0
    if c["vol_pct_float"] >= config.VOL_FLOAT_HIGH:
        vol_float_pts = config.SCORE_VOL_FLOAT_MAX
    elif c["vol_pct_float"] >= config.VOL_FLOAT_MID:
        vol_float_pts = 10
    elif c["vol_pct_float"] >= config.VOL_FLOAT_LOW:
        vol_float_pts = 5

    trend_pts = min(config.SCORE_VOLUME_TREND_MAX, consec * 7)

    share_count = c.get("float_shares", 0) or 0
    float_pts = 0
    if share_count <= 5_000_000 and share_count > 0:
        float_pts = config.SCORE_LOW_FLOAT_MAX
    elif share_count <= 10_000_000:
        float_pts = 10
    elif share_count <= 15_000_000:
        float_pts = 5

    mcap = c.get("market_cap", 0) or 0
    cap_pts = 0
    if mcap <= 5_000_000 and mcap > 0:
        cap_pts = config.SCORE_SMALL_CAP_MAX
    elif mcap <= 20_000_000:
        cap_pts = 3

    vwap_pts = config.SCORE_VWAP_MAX if c.get("price_vs_typical") == "ABOVE" else 0

    range_pts = 0
    if c["range_position"] <= config.RANGE_POS_LOW_THRESHOLD:
        range_pts = config.SCORE_RANGE_POS_MAX
    elif c["range_position"] <= config.RANGE_POS_MID_THRESHOLD:
        range_pts = 3

    ma_pts = config.SCORE_MOMENTUM_MAX if c.get("ma_10") and c.get("price", 0) > c.get("ma_10", 0) else 0

    penalty_note = f" — Offering penalty: -{c.get('offering_penalty', 0)}" if c.get('offering_penalty', 0) > 0 else ""

    lines.append(
        f"SCORE: {c['score']}/100 (base 100{penalty_note}) — "
        f"Vol Spike: {vol_pts}/{config.SCORE_VOLUME_SPIKE_MAX} | "
        f"Vol/Float: {vol_float_pts}/{config.SCORE_VOL_FLOAT_MAX} | "
        f"Vol Trend: {trend_pts}/{config.SCORE_VOLUME_TREND_MAX} | "
        f"Low Float: {float_pts}/{config.SCORE_LOW_FLOAT_MAX} | "
        f"Range Pos: {range_pts}/{config.SCORE_RANGE_POS_MAX} | "
        f"Small Cap: {cap_pts}/{config.SCORE_SMALL_CAP_MAX} | "
        f"Typical Price: {vwap_pts}/{config.SCORE_VWAP_MAX} | "
        f"Momentum: {ma_pts}/{config.SCORE_MOMENTUM_MAX}"
    )

    c["reasoning"] = "\n\n".join(lines)
    return c


# ============================================================
# STEP 5: SEC EDGAR — INSIDER ACTIVITY (SAME AS V1)
# ============================================================

def check_insider_activity(ticker, verbose=False):
    """
    Check SEC EDGAR for recent Form 4 filings (insider buying/selling).
    Uses the company tickers endpoint to get CIK, then pulls recent filings
    and parses transaction details to determine net buying vs selling.
    Returns a structured summary string.
    """
    import requests
    import xml.etree.ElementTree as ET

    headers = {"User-Agent": config.SEC_EDGAR_USER_AGENT}
    cutoff = datetime.now() - timedelta(days=30)

    try:
        # Step 1: Get CIK from cached lookup
        cik = _get_cik_for_ticker(ticker, headers, verbose=verbose)
        if not cik:
            return "Ticker not found in EDGAR"

        # Step 2: Get recent filings for this CIK
        filings_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        filings_resp = requests.get(filings_url, headers=headers, timeout=10)
        if filings_resp.status_code != 200:
            return "Unable to check filings"

        filings_data = filings_resp.json()
        recent = filings_data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])

        # Step 3: Find Form 4 filings in last 30 days
        form4_filings = []
        for i, form in enumerate(forms):
            if form == "4" and i < len(dates):
                try:
                    filing_date = datetime.strptime(dates[i], "%Y-%m-%d")
                    if filing_date >= cutoff:
                        form4_filings.append({
                            "date": dates[i],
                            "accession": accessions[i].replace("-", ""),
                            "accession_fmt": accessions[i],
                            "doc": primary_docs[i] if i < len(primary_docs) else None
                        })
                except (ValueError, IndexError):
                    continue

        if not form4_filings:
            return "No Form 4 filings in last 30 days"

        # Step 4: Parse up to 5 most recent Form 4 filings for buy/sell details
        import time
        import re
        total_bought = 0
        total_sold = 0
        buy_transactions = 0
        sell_transactions = 0
        insiders = set()
        parsed = 0
        cik_clean = cik.lstrip('0')

        for filing in form4_filings[:5]:  # Limit to 5 to avoid rate limits
            try:
                time.sleep(0.12)  # Rate limit: SEC allows 10 req/sec

                # The primaryDocument from EDGAR is usually an HTML-rendered XSLT version
                # (e.g., xslF345X05/ownership.xml). We need the RAW XML file instead.
                # Strategy: fetch the filing directory listing and find the raw .xml file.
                base_url = f"https://www.sec.gov/Archives/edgar/data/{cik_clean}/{filing['accession']}"
                content = None

                # First, get the directory listing to find the raw XML
                try:
                    dir_resp = requests.get(f"{base_url}/", headers=headers, timeout=10)
                    if dir_resp.status_code == 200:
                        # Find all .xml links that are NOT in xsl* subdirectories
                        xml_links = re.findall(r'href="([^"]+\.xml)"', dir_resp.text)
                        raw_xml_file = None
                        for link in xml_links:
                            # Skip XSLT-rendered files and metadata files
                            if 'xsl' in link.lower() or 'FilingSummary' in link or link.startswith('R'):
                                continue
                            raw_xml_file = link
                            break

                        if raw_xml_file:
                            # Handle both absolute paths (/Archives/...) and relative filenames
                            if raw_xml_file.startswith('/'):
                                xml_url = f"https://www.sec.gov{raw_xml_file}"
                            else:
                                xml_url = f"{base_url}/{raw_xml_file}"
                            doc_resp = requests.get(xml_url, headers=headers, timeout=10)
                            if doc_resp.status_code == 200 and len(doc_resp.text) > 100:
                                content = doc_resp.text
                                if verbose:
                                    print(f"    Fetched raw XML for {ticker}: {raw_xml_file} ({len(content)} chars)")
                        elif verbose:
                            print(f"    No raw XML found in directory listing for {ticker}")
                except Exception as dir_err:
                    if verbose:
                        print(f"    Directory listing error for {ticker}: {dir_err}")

                # Fallback: try primary document directly (might work for some filings)
                if not content and filing.get('doc'):
                    try:
                        doc_url = f"{base_url}/{filing['doc']}"
                        doc_resp = requests.get(doc_url, headers=headers, timeout=10)
                        if doc_resp.status_code == 200 and len(doc_resp.text) > 100:
                            content = doc_resp.text
                    except Exception:
                        pass

                if not content:
                    if verbose:
                        print(f"    Could not fetch any Form 4 document for {ticker} filing {filing['accession_fmt']}")
                    continue

                # Use regex to extract data — works on both XML and HTML renderings
                # Extract insider name
                name_match = re.search(r'<rptOwnerName>([^<]+)</rptOwnerName>', content)
                if name_match:
                    insiders.add(name_match.group(1).strip())

                # Find all transaction blocks and extract shares + acquired/disposed code
                # Pattern matches transactionShares value and transactionAcquiredDisposedCode value
                # These appear in pairs within each transaction block
                tx_blocks = re.findall(
                    r'<transactionAmounts>.*?<transactionShares>.*?<value>([^<]+)</value>.*?'
                    r'<transactionAcquiredDisposedCode>.*?<value>([^<]+)</value>.*?</transactionAmounts>',
                    content, re.DOTALL
                )

                if tx_blocks:
                    for shares_str, ad_code in tx_blocks:
                        try:
                            shares = float(shares_str.strip())
                            code = ad_code.strip()
                            if code == "A":
                                total_bought += shares
                                buy_transactions += 1
                            elif code == "D":
                                total_sold += shares
                                sell_transactions += 1
                        except (ValueError, TypeError):
                            continue
                    parsed += 1
                else:
                    # Fallback: try XML ElementTree parsing
                    try:
                        root = ET.fromstring(content)
                        found_any = False
                        for elem in root.iter():
                            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                            if tag == "rptOwnerName" and elem.text:
                                insiders.add(elem.text.strip())
                            if tag in ("nonDerivativeTransaction", "derivativeTransaction"):
                                shares = 0
                                acquired = None
                                for child in elem.iter():
                                    ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                                    if ctag == "transactionShares":
                                        for val in child.iter():
                                            vtag = val.tag.split("}")[-1] if "}" in val.tag else val.tag
                                            if vtag == "value" and val.text:
                                                try:
                                                    shares = float(val.text)
                                                except ValueError:
                                                    pass
                                    if ctag == "transactionAcquiredDisposedCode":
                                        for val in child.iter():
                                            vtag = val.tag.split("}")[-1] if "}" in val.tag else val.tag
                                            if vtag == "value" and val.text:
                                                acquired = val.text.strip()
                                if acquired == "A":
                                    total_bought += shares
                                    buy_transactions += 1
                                    found_any = True
                                elif acquired == "D":
                                    total_sold += shares
                                    sell_transactions += 1
                                    found_any = True
                        if found_any:
                            parsed += 1
                        elif verbose:
                            print(f"    Parsed XML for {ticker} but no transaction elements found")
                    except Exception as xml_err:
                        if verbose:
                            print(f"    XML fallback failed for {ticker}: {xml_err}")
                            print(f"    Content preview: {content[:200]}")

            except Exception as e:
                if verbose:
                    print(f"    Error processing Form 4 for {ticker}: {e}")
                continue

        # Step 5: Build summary
        total_filings = len(form4_filings)
        parts = [f"{total_filings} Form 4 filing(s) in last 30 days"]

        if buy_transactions > 0 or sell_transactions > 0:
            if buy_transactions > 0:
                bought_fmt = f"{total_bought:,.0f}" if total_bought < 1_000_000 else f"{total_bought/1_000_000:.1f}M"
                parts.append(f"BUYING: {buy_transactions} transaction(s), {bought_fmt} shares acquired")
            if sell_transactions > 0:
                sold_fmt = f"{total_sold:,.0f}" if total_sold < 1_000_000 else f"{total_sold/1_000_000:.1f}M"
                parts.append(f"SELLING: {sell_transactions} transaction(s), {sold_fmt} shares disposed")

            # Net direction
            if total_bought > total_sold:
                parts.append("NET: Insider buying")
            elif total_sold > total_bought:
                parts.append("NET: Insider selling")
            else:
                parts.append("NET: Neutral")

            if insiders:
                parts.append(f"Insiders: {', '.join(list(insiders)[:3])}")
        else:
            parts.append(f"(parsed {parsed} filing(s) but no transaction details found)")

        return " | ".join(parts)

    except Exception as e:
        if verbose:
            print(f"    SEC EDGAR error for {ticker}: {e}")
        return "Unable to check"


# ============================================================
# STEP 6: NEWS CHECK (SAME AS V1)
# ============================================================

def check_news(ticker, verbose=False):
    """
    Check for recent news headlines.
    Uses Finnhub if API key is set, otherwise falls back to yfinance news.
    """
    headlines = []

    # Try yfinance news first (always available, no API key needed)
    try:
        stock = yf.Ticker(ticker)
        news = stock.news
        if news:
            for item in news[:3]:  # Top 3 headlines
                title = item.get("title", "")
                publisher = item.get("publisher", "")
                if title:
                    headlines.append(f"{title} ({publisher})")
    except Exception:
        pass

    # If Finnhub key is set, supplement with Finnhub
    if config.FINNHUB_API_KEY:
        import requests
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            url = f"https://finnhub.io/api/v1/company-news?symbol={ticker}&from={week_ago}&to={today}&token={config.FINNHUB_API_KEY}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                articles = resp.json()
                for a in articles[:3]:
                    headline = a.get("headline", "")
                    source = a.get("source", "")
                    if headline and headline not in [h.split(" (")[0] for h in headlines]:
                        headlines.append(f"{headline} ({source})")
        except Exception:
            pass

    return headlines[:5]  # Max 5 headlines


# ============================================================
# OUTPUT FORMATTING
# ============================================================

def format_number(n):
    """Format large numbers for readability."""
    if not n:
        return "N/A"
    if n >= 1_000_000_000:
        return f"${n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"${n / 1_000_000:.1f}M" if n > 100_000 else f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(int(n))


def format_volume(n):
    if not n:
        return "N/A"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(int(n))


def output_console(candidates):
    """Print results to console."""
    print("\n" + "=" * 80)
    print(f"  AMIR'S WATCHLIST V2 ENHANCED -- {datetime.now().strftime('%B %d, %Y')}")
    print(f"  Report generated: {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}")
    print(f"  {len(candidates)} candidates found")
    # Show data freshness range across all candidates
    data_times = [c.get("data_as_of", "N/A") for c in candidates if c.get("data_as_of")]
    if data_times:
        print(f"  Data source: Yahoo Finance  |  Most recent data: {data_times[0]}")
    print(f"  NOTE: Free tier data is delayed ~15-20 min. Finviz data is end-of-day.")
    print("=" * 80)

    for i, c in enumerate(candidates, 1):
        print(f"\n{'-' * 80}")
        print(f"  #{i}  {c['ticker']} — {c['name']}")
        print(f"  Score: {c['score']}/100  |  {c['sector']} / {c['industry']}")
        print(f"{'-' * 80}")
        print(f"  Data as of: {c.get('data_as_of', 'N/A')}  |  Source: {c.get('source', 'yfinance')}")
        print(f"  Price:      ${c['price']:<10} Market Cap:  {c['market_cap_fmt']}")
        print(f"  Float:      {c['float_fmt']:<10} Vol % Float: {c['vol_pct_float_fmt']}")
        print(f"  Volume:     {format_volume(c['today_volume'])} today  |  {format_volume(c['avg_volume'])} avg  |  {c['volume_ratio']}x spike")
        print(f"  Vol Trend:  {c['consecutive_vol_up']} consecutive days up")
        print(f"  52w Range:  ${c['week52_low']} — ${c['week52_high']}  |  Position: {c['range_position_pct']}")
        print(f"  Daily:      ${c['daily_low']} — ${c['daily_high']}")
        print(f"  MA(10):     ${c['ma_10']}  |  MA(50): ${c['ma_50']}")
        print(f"  Typical Price:       ${c['typical_price_hlc3']} ({c['price_vs_typical']})")
        print(f"  Short Int:  {c['short_interest_fmt']}")
        if c.get("support_levels") or c.get("resistance_levels"):
            support_str = ", ".join(f"${x}" for x in (c.get("support_levels") or [])[:3])
            resistance_str = ", ".join(f"${x}" for x in (c.get("resistance_levels") or [])[:3])
            if support_str:
                print(f"  Support:    {support_str}")
            if resistance_str:
                print(f"  Resistance: {resistance_str}")
        if c.get("offering_count", 0) > 0:
            print(f"  Offerings:  {c['offering_count']} in last 12 months (penalty: -{c['offering_penalty']})")
        print(f"  Entry:      ${c['price']:<10} Stop: ${c['stop_loss']:<10} Target: ${c['target_price']}")

        if c.get("flags"):
            print(f"  Flags:      {' | '.join(c['flags'])}")

        if c.get("insider_activity"):
            print(f"  Insider:    {c['insider_activity']}")

        if c.get("news"):
            print(f"  News:")
            for h in c["news"][:3]:
                print(f"    • {h[:100]}")

        if c.get("reasoning"):
            print(f"\n  {'-' * 40}")
            print(f"  REASONING:")
            for line in c["reasoning"].split("\n\n"):
                wrapped = line[:120] + ("..." if len(line) > 120 else "")
                print(f"  {wrapped}")

    print(f"\n{'=' * 80}")
    print(f"  Generated at {datetime.now().strftime('%I:%M %p ET')} — Not financial advice.")
    print(f"{'=' * 80}\n")


def output_html(candidates, filepath):
    """Generate an HTML watchlist report with V2 enhancements."""
    import html as html_lib

    cards = ""
    for i, c in enumerate(candidates, 1):
        flag_badges = "".join(f'<span class="flag">{f}</span>' for f in c.get("flags", []))
        news_items = "".join(f"<li>{html_lib.escape(h[:150])}</li>" for h in c.get("news", [])[:3])
        vwap_class = "positive" if c["price_vs_typical"] == "ABOVE" else "negative"
        score_class = "score-high" if c["score"] >= 70 else "score-mid" if c["score"] >= 40 else "score-low"

        # Support/Resistance display
        support_str = ", ".join(f"${x}" for x in (c.get("support_levels") or [])[:3])
        resistance_str = ", ".join(f"${x}" for x in (c.get("resistance_levels") or [])[:3])

        # Format reasoning paragraphs as HTML
        reasoning_html = ""
        if c.get("reasoning"):
            for para in c["reasoning"].split("\n\n"):
                escaped = html_lib.escape(para)
                # Bold certain keywords
                for kw in ["VOLUME ALERT:", "WARNING:", "TRADE SETUP", "INSIDER ACTIVITY:", "RECENT NEWS:", "SCORE:"]:
                    escaped = escaped.replace(html_lib.escape(kw), f'<strong>{html_lib.escape(kw)}</strong>')
                reasoning_html += f"<p>{escaped}</p>"

        cards += f"""
        <div class="card">
            <div class="card-header" onclick="this.parentElement.classList.toggle('expanded')">
                <div class="card-rank">#{i}</div>
                <div class="card-ticker">
                    <strong>{c['ticker']}</strong>
                    <span class="card-name">{html_lib.escape(c['name'])}</span>
                </div>
                <div class="card-price">${c['price']}</div>
                <div class="card-score {score_class}">{c['score']}</div>
                <div class="card-vol">
                    <span class="vol-ratio-badge">{c['volume_ratio']}x vol</span>
                </div>
                <div class="card-flags">{flag_badges}</div>
                <div class="card-expand">&#9660;</div>
            </div>
            <div class="card-body">
                <div class="data-freshness" style="font-size:11px;color:#636e72;padding:4px 0 8px 0;">
                    Data as of: {c.get('data_as_of', 'N/A')} | Source: {c.get('source', 'yfinance')} | Delayed ~15-20 min
                </div>
                <div class="card-metrics">
                    <div class="metric">
                        <div class="metric-label">Market Cap</div>
                        <div class="metric-value">{c['market_cap_fmt']}</div>
                    </div>
                    <div class="metric">
                        <div class="metric-label">Float</div>
                        <div class="metric-value">{c['float_fmt']}</div>
                    </div>
                    <div class="metric">
                        <div class="metric-label">Vol % Float</div>
                        <div class="metric-value">{c['vol_pct_float_fmt']}</div>
                    </div>
                    <div class="metric">
                        <div class="metric-label">Volume Today</div>
                        <div class="metric-value">{format_volume(c['today_volume'])}</div>
                    </div>
                    <div class="metric">
                        <div class="metric-label">Avg Volume (10d)</div>
                        <div class="metric-value">{format_volume(c['avg_volume'])}</div>
                    </div>
                    <div class="metric">
                        <div class="metric-label">Vol Trend</div>
                        <div class="metric-value">{c['consecutive_vol_up']}d up</div>
                    </div>
                    <div class="metric">
                        <div class="metric-label">Range Position</div>
                        <div class="metric-value">{c['range_position_pct']}</div>
                    </div>
                    <div class="metric">
                        <div class="metric-label">52w Range</div>
                        <div class="metric-value">${c['week52_low']} — ${c['week52_high']}</div>
                    </div>
                    <div class="metric">
                        <div class="metric-label">Short Interest</div>
                        <div class="metric-value">{c['short_interest_fmt']}</div>
                    </div>
                    <div class="metric">
                        <div class="metric-label">Typical Price</div>
                        <div class="metric-value {vwap_class}">${c['typical_price_hlc3']} ({c['price_vs_typical']})</div>
                    </div>
                    <div class="metric">
                        <div class="metric-label">10-day MA</div>
                        <div class="metric-value">${c['ma_10'] or 'N/A'}</div>
                    </div>
                    <div class="metric">
                        <div class="metric-label">50-day MA</div>
                        <div class="metric-value">${c['ma_50'] or 'N/A'}</div>
                    </div>
                    {f'<div class="metric"><div class="metric-label">Offerings (12m)</div><div class="metric-value">{c["offering_count"]}</div></div>' if c.get("offering_count", 0) > 0 else ''}
                    <div class="metric trade-setup">
                        <div class="metric-label">Stop Loss</div>
                        <div class="metric-value negative">${c['stop_loss']}</div>
                    </div>
                    <div class="metric trade-setup">
                        <div class="metric-label">Target (1:5 R/R)</div>
                        <div class="metric-value positive">${c['target_price']}</div>
                    </div>
                </div>

                {f'<div class="support-resistance-section"><h4>Key Levels</h4><p>Support: {support_str or "N/A"}<br/>Resistance: {resistance_str or "N/A"}</p></div>' if support_str or resistance_str else ""}

                {f'<div class="insider-badge">{html_lib.escape(str(c.get("insider_activity", "")))}</div>' if c.get("insider_activity") and "Form 4" in str(c.get("insider_activity", "")) else ""}

                {f'<div class="news-section"><h4>Recent News</h4><ul>{news_items}</ul></div>' if news_items else ""}

                <div class="reasoning-section">
                    <h4>Analysis & Reasoning</h4>
                    {reasoning_html}
                </div>

                <div class="feedback-section">
                    <h4>Amir's Review</h4>
                    <div class="feedback-buttons">
                        <button class="btn-yes" onclick="markPick('{c['ticker']}', 'yes', this)">Would Trade</button>
                        <button class="btn-no" onclick="markPick('{c['ticker']}', 'no', this)">Would Skip</button>
                    </div>
                    <textarea class="feedback-notes" placeholder="Why? (e.g., 'no catalyst', 'float too high', 'like the setup')..."
                              id="notes-{c['ticker']}"></textarea>
                </div>
            </div>
        </div>
        """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Amir's Watchlist V2 — {datetime.now().strftime('%B %d, %Y')}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0a0e17; color: #c8d6e5; padding: 20px; max-width: 1000px; margin: 0 auto; }}
        h1 {{ color: #00d4aa; font-size: 28px; margin-bottom: 5px; }}
        .subtitle {{ color: #636e72; margin-bottom: 24px; font-size: 14px; }}
        .summary-bar {{ display: flex; gap: 20px; margin-bottom: 24px; padding: 12px 16px; background: #111827; border-radius: 8px; border: 1px solid #1a2332; }}
        .summary-stat {{ text-align: center; }}
        .summary-stat .label {{ font-size: 11px; color: #636e72; text-transform: uppercase; letter-spacing: 1px; }}
        .summary-stat .value {{ font-size: 20px; font-weight: bold; color: #00d4aa; }}

        .card {{ background: #111827; border: 1px solid #1a2332; border-radius: 8px; margin-bottom: 12px; overflow: hidden; transition: border-color 0.2s; }}
        .card:hover {{ border-color: #00d4aa33; }}
        .card.expanded .card-body {{ display: block; }}
        .card.expanded .card-expand {{ transform: rotate(180deg); }}

        .card-header {{ display: flex; align-items: center; padding: 14px 16px; cursor: pointer; gap: 16px; }}
        .card-header:hover {{ background: #1a2332; }}
        .card-rank {{ color: #636e72; font-weight: bold; font-size: 14px; min-width: 30px; }}
        .card-ticker {{ flex: 0 0 180px; }}
        .card-ticker strong {{ color: #00d4aa; font-size: 18px; }}
        .card-name {{ color: #636e72; font-size: 11px; display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 170px; }}
        .card-price {{ font-size: 16px; font-weight: 600; min-width: 70px; }}
        .card-score {{ font-size: 18px; font-weight: bold; min-width: 40px; text-align: center; }}
        .score-high {{ color: #00d4aa; }}
        .score-mid {{ color: #ffd700; }}
        .score-low {{ color: #ff6b6b; }}
        .card-vol {{ min-width: 80px; }}
        .vol-ratio-badge {{ background: #ff6b6b22; color: #ff6b6b; padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; }}
        .card-flags {{ flex: 1; }}
        .card-expand {{ color: #636e72; font-size: 12px; transition: transform 0.2s; }}

        .card-body {{ display: none; padding: 0 16px 16px; border-top: 1px solid #1a2332; }}

        .card-metrics {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; padding: 16px 0; }}
        .metric {{ background: #0a0e17; padding: 10px 12px; border-radius: 6px; }}
        .metric-label {{ font-size: 10px; color: #636e72; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }}
        .metric-value {{ font-size: 14px; font-weight: 600; }}
        .trade-setup {{ border: 1px solid #1a2332; }}

        .positive {{ color: #00d4aa; }}
        .negative {{ color: #ff6b6b; }}

        .flag {{ display: inline-block; background: #ff6b6b22; color: #ff6b6b; padding: 2px 8px; border-radius: 4px; font-size: 11px; margin: 2px; }}
        .insider-badge {{ background: #ffd70022; color: #ffd700; padding: 8px 12px; border-radius: 6px; margin: 12px 0; font-size: 13px; }}
        .support-resistance-section {{ margin: 12px 0; padding: 12px; background: #0a0e17; border-radius: 6px; }}
        .support-resistance-section h4 {{ color: #636e72; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 6px; }}
        .support-resistance-section p {{ font-size: 13px; color: #a0b0c0; }}

        .news-section {{ margin: 12px 0; }}
        .news-section h4 {{ color: #636e72; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px; }}
        .news-section ul {{ list-style: none; }}
        .news-section li {{ font-size: 12px; color: #8395a7; padding: 4px 0; border-bottom: 1px solid #1a2332; }}

        .reasoning-section {{ margin: 16px 0; padding: 16px; background: #0d1321; border-radius: 8px; border-left: 3px solid #00d4aa; }}
        .reasoning-section h4 {{ color: #00d4aa; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 12px; }}
        .reasoning-section p {{ font-size: 13px; line-height: 1.6; color: #a0b0c0; margin-bottom: 10px; }}
        .reasoning-section strong {{ color: #ffd700; }}

        .feedback-section {{ margin: 16px 0; padding: 16px; background: #0d1321; border-radius: 8px; border-left: 3px solid #4a6fa5; }}
        .feedback-section h4 {{ color: #4a6fa5; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 12px; }}
        .feedback-buttons {{ display: flex; gap: 10px; margin-bottom: 10px; }}
        .btn-yes, .btn-no {{ padding: 8px 20px; border: none; border-radius: 6px; cursor: pointer; font-weight: 600; font-size: 13px; transition: all 0.2s; }}
        .btn-yes {{ background: #00d4aa33; color: #00d4aa; border: 1px solid #00d4aa55; }}
        .btn-yes:hover, .btn-yes.active {{ background: #00d4aa; color: #0a0e17; }}
        .btn-no {{ background: #ff6b6b33; color: #ff6b6b; border: 1px solid #ff6b6b55; }}
        .btn-no:hover, .btn-no.active {{ background: #ff6b6b; color: #0a0e17; }}
        .feedback-notes {{ width: 100%; background: #0a0e17; border: 1px solid #1a2332; border-radius: 6px; padding: 10px; color: #c8d6e5; font-family: inherit; font-size: 13px; resize: vertical; min-height: 60px; }}
        .feedback-notes:focus {{ outline: none; border-color: #4a6fa5; }}

        .footer {{ margin-top: 24px; padding: 16px; text-align: center; color: #636e72; font-size: 12px; }}
        .export-bar {{ text-align: center; margin: 20px 0; }}
        .btn-export {{ padding: 12px 30px; background: #00d4aa; color: #0a0e17; border: none; border-radius: 8px; font-weight: 700; font-size: 14px; cursor: pointer; }}
        .btn-export:hover {{ background: #00b894; }}
        .save-status {{ margin-top: 10px; font-size: 14px; font-weight: 600; }}
    </style>
</head>
<body>
    <h1>Amir's Watchlist V2</h1>
    <div class="subtitle">{datetime.now().strftime('%B %d, %Y at %I:%M %p')} -- Click any stock to expand full analysis</div>
    <div style="color:#636e72;font-size:11px;margin-bottom:12px;padding:6px 12px;background:#111827;border-radius:6px;border:1px solid #1a2332;">
        Data source: Yahoo Finance (delayed ~15-20 min) + Finviz (end-of-day) | Report generated: {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')} | Not real-time data
    </div>

    <div class="summary-bar">
        <div class="summary-stat"><div class="label">Candidates</div><div class="value">{len(candidates)}</div></div>
        <div class="summary-stat"><div class="label">Avg Score</div><div class="value">{sum(c['score'] for c in candidates) / len(candidates):.0f}</div></div>
        <div class="summary-stat"><div class="label">Vol/Float > 5%</div><div class="value">{sum(1 for c in candidates if c['vol_pct_float'] >= 0.05)}</div></div>
        <div class="summary-stat"><div class="label">Vol Spikes (3x+)</div><div class="value">{sum(1 for c in candidates if c['volume_ratio'] >= 3)}</div></div>
        <div class="summary-stat"><div class="label">Offering Penalty</div><div class="value negative">{sum(1 for c in candidates if c['offering_penalty'] > 0)}</div></div>
    </div>

    <div style="background: #111827; border: 1px solid #1a2332; border-radius: 8px; padding: 16px; margin-bottom: 24px;">
        <h3 style="color: #636e72; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 12px;">V2 Enhanced Scoring (100 Base, up to -15 Penalty)</h3>
        <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px;">
            <div style="background: #0a0e17; padding: 10px; border-radius: 6px; text-align: center;">
                <div style="color: #00d4aa; font-size: 18px; font-weight: bold;">25</div>
                <div style="color: #636e72; font-size: 11px;">Volume Spike</div>
                <div style="color: #4a5568; font-size: 10px;">3x-10x+ avg vol</div>
            </div>
            <div style="background: #0a0e17; padding: 10px; border-radius: 6px; text-align: center;">
                <div style="color: #ffd700; font-size: 18px; font-weight: bold;">15</div>
                <div style="color: #636e72; font-size: 11px;">Vol/Float Ratio</div>
                <div style="color: #4a5568; font-size: 10px;">Vol as % of float</div>
            </div>
            <div style="background: #0a0e17; padding: 10px; border-radius: 6px; text-align: center;">
                <div style="color: #00d4aa; font-size: 18px; font-weight: bold;">20</div>
                <div style="color: #636e72; font-size: 11px;">Volume Trend</div>
                <div style="color: #4a5568; font-size: 10px;">3+ consec days up</div>
            </div>
            <div style="background: #0a0e17; padding: 10px; border-radius: 6px; text-align: center;">
                <div style="color: #00d4aa; font-size: 18px; font-weight: bold;">15</div>
                <div style="color: #636e72; font-size: 11px;">Low Float</div>
                <div style="color: #4a5568; font-size: 10px;">3M-15M shares</div>
            </div>
            <div style="background: #0a0e17; padding: 10px; border-radius: 6px; text-align: center;">
                <div style="color: #00d4aa; font-size: 18px; font-weight: bold;">10</div>
                <div style="color: #636e72; font-size: 11px;">Typical Price Position</div>
                <div style="color: #4a5568; font-size: 10px;">Price above Typical Price</div>
            </div>
            <div style="background: #0a0e17; padding: 10px; border-radius: 6px; text-align: center;">
                <div style="color: #ffd700; font-size: 18px; font-weight: bold;">5</div>
                <div style="color: #636e72; font-size: 11px;">Range Position</div>
                <div style="color: #4a5568; font-size: 10px;">Bottom 25% of 52w</div>
            </div>
            <div style="background: #0a0e17; padding: 10px; border-radius: 6px; text-align: center;">
                <div style="color: #00d4aa; font-size: 18px; font-weight: bold;">5</div>
                <div style="color: #636e72; font-size: 11px;">Small Cap</div>
                <div style="color: #4a5568; font-size: 10px;">Market cap &lt;$20M</div>
            </div>
            <div style="background: #0a0e17; padding: 10px; border-radius: 6px; text-align: center;">
                <div style="color: #00d4aa; font-size: 18px; font-weight: bold;">5</div>
                <div style="color: #636e72; font-size: 11px;">Momentum</div>
                <div style="color: #4a5568; font-size: 10px;">Price &gt; 10-day MA</div>
            </div>
        </div>
        <div style="margin-top: 10px; display: flex; gap: 8px; justify-content: center;">
            <div style="background: #ff6b6b22; color: #ff6b6b; padding: 6px 14px; border-radius: 6px; font-size: 12px; text-align: center;">
                <strong>-15 max</strong> Offering Penalty (2+ S-1/S-3 filings in 12mo)
            </div>
        </div>
        <div style="margin-top: 8px; text-align: center; color: #4a5568; font-size: 10px;">
            Also displays: Short Interest | Support/Resistance Levels | Sector Tag
        </div>
    </div>

    {cards}

    <div class="export-bar">
        <button class="btn-export" onclick="saveFeedbackHTML()">Save My Feedback</button>
        <div class="save-status" id="save-status"></div>
    </div>

    <div class="footer">Generated by Amir Screener V2 Enhanced — Not financial advice. Risk/reward targets are estimates based on 1:5 R/R ratio.</div>

    <script>
        // Expand first card by default
        document.querySelector('.card')?.classList.add('expanded');

        const feedback = {{}};

        function markPick(ticker, decision, btn) {{
            feedback[ticker] = feedback[ticker] || {{}};
            feedback[ticker].decision = decision;
            // Toggle active state
            const buttons = btn.parentElement.querySelectorAll('button');
            buttons.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            // Store in data attribute for persistence
            btn.closest('.card').setAttribute('data-decision', decision);
        }}

        // Auto-save notes on blur
        document.querySelectorAll('.feedback-notes').forEach(textarea => {{
            textarea.addEventListener('blur', function() {{
                const ticker = this.id.replace('notes-', '');
                feedback[ticker] = feedback[ticker] || {{}};
                feedback[ticker].notes = this.value;
            }});
        }});

        function saveFeedbackHTML() {{
            // Collect all feedback into the DOM before saving
            document.querySelectorAll('.feedback-notes').forEach(textarea => {{
                const ticker = textarea.id.replace('notes-', '');
                if (textarea.value) {{
                    feedback[ticker] = feedback[ticker] || {{}};
                    feedback[ticker].notes = textarea.value;
                }}
            }});

            // Embed feedback as a JSON block in the HTML itself
            let feedbackEl = document.getElementById('embedded-feedback');
            if (!feedbackEl) {{
                feedbackEl = document.createElement('script');
                feedbackEl.id = 'embedded-feedback';
                feedbackEl.type = 'application/json';
                document.body.appendChild(feedbackEl);
            }}
            feedbackEl.textContent = JSON.stringify({{
                reviewed_at: new Date().toISOString(),
                reviewer: "Amir",
                feedback: feedback
            }}, null, 2);

            // Mark buttons as active in the HTML so state persists
            document.querySelectorAll('.card').forEach(card => {{
                const decision = card.getAttribute('data-decision');
                if (decision) {{
                    card.querySelectorAll('.feedback-buttons button').forEach(b => {{
                        b.classList.remove('active');
                        if ((decision === 'yes' && b.classList.contains('btn-yes')) ||
                            (decision === 'no' && b.classList.contains('btn-no'))) {{
                            b.classList.add('active');
                        }}
                    }});
                }}
            }});

            // Save the entire page as a new HTML file
            const html = '<!DOCTYPE html>' + document.documentElement.outerHTML;
            const blob = new Blob([html], {{ type: 'text/html' }});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'watchlist_v2_reviewed_' + new Date().toISOString().slice(0, 10) + '.html';
            a.click();
            URL.revokeObjectURL(url);

            document.getElementById('save-status').textContent = 'Saved! Send this file back to Matthew.';
            document.getElementById('save-status').style.color = '#00d4aa';
        }}

        // On load: restore feedback from embedded JSON if present
        (function() {{
            const el = document.getElementById('embedded-feedback');
            if (el && el.textContent.trim()) {{
                try {{
                    const data = JSON.parse(el.textContent);
                    Object.assign(feedback, data.feedback || {{}});
                    // Restore button states and notes
                    for (const [ticker, fb] of Object.entries(feedback)) {{
                        if (fb.decision) {{
                            const card = document.querySelector(`#notes-${{ticker}}`)?.closest('.card');
                            if (card) {{
                                card.setAttribute('data-decision', fb.decision);
                                card.querySelectorAll('.feedback-buttons button').forEach(b => {{
                                    if ((fb.decision === 'yes' && b.classList.contains('btn-yes')) ||
                                        (fb.decision === 'no' && b.classList.contains('btn-no'))) {{
                                        b.classList.add('active');
                                    }}
                                }});
                            }}
                        }}
                        if (fb.notes) {{
                            const textarea = document.getElementById(`notes-${{ticker}}`);
                            if (textarea) textarea.value = fb.notes;
                        }}
                    }}
                }} catch(e) {{}}
            }}
        }})();
    </script>
</body>
</html>"""

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n  HTML report saved to: {filepath}")


def output_json(candidates, filepath):
    """Save results as JSON."""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump({
            "generated": datetime.now().isoformat(),
            "count": len(candidates),
            "candidates": candidates
        }, f, indent=2)
    print(f"\n  JSON saved to: {filepath}")


# ============================================================
# EMAIL DELIVERY
# ============================================================

def send_email(html_path, candidates, verbose=False):
    """
    Send the watchlist HTML report via Gmail SMTP.
    Attaches the full interactive HTML and includes a summary in the email body.
    V2 edition.
    """
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders
    import os

    if not config.EMAIL_ENABLED or not config.SMTP_PASS:
        print("  Email not configured — skipping. Set EMAIL_ENABLED=True and SMTP_PASS in config_v2.py")
        return False

    try:
        # Build email summary body
        date_str = datetime.now().strftime('%B %d, %Y')
        time_str = datetime.now().strftime('%I:%M %p')

        # Top 5 summary for email body
        top5 = candidates[:5]
        summary_rows = ""
        for i, c in enumerate(top5, 1):
            insider_short = ""
            insider_str = str(c.get("insider_activity", ""))
            if "NET: Insider buying" in insider_str:
                insider_short = " | Insider BUYING"
            elif "NET: Insider selling" in insider_str:
                insider_short = " | Insider SELLING"

            score_color = "#00d4aa" if c["score"] >= 70 else "#ffd700" if c["score"] >= 40 else "#ff6b6b"
            summary_rows += f"""
            <tr>
                <td style="padding: 8px; border-bottom: 1px solid #1a2332; color: #00d4aa; font-weight: bold;">{c['ticker']}</td>
                <td style="padding: 8px; border-bottom: 1px solid #1a2332;">${c['price']}</td>
                <td style="padding: 8px; border-bottom: 1px solid #1a2332; color: {score_color}; font-weight: bold;">{c['score']}</td>
                <td style="padding: 8px; border-bottom: 1px solid #1a2332;">{c['volume_ratio']}x</td>
                <td style="padding: 8px; border-bottom: 1px solid #1a2332;">{c['vol_pct_float_fmt']}</td>
                <td style="padding: 8px; border-bottom: 1px solid #1a2332;">{c['market_cap_fmt']}</td>
                <td style="padding: 8px; border-bottom: 1px solid #1a2332; font-size: 12px;">{c.get('flags', [''])[0] if c.get('flags') else ''}{insider_short}</td>
            </tr>"""

        email_html = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0a0e17; color: #c8d6e5; padding: 24px; max-width: 700px;">
            <h1 style="color: #00d4aa; margin-bottom: 4px;">Amir's Watchlist V2 Enhanced</h1>
            <p style="color: #636e72; margin-bottom: 20px;">{date_str} at {time_str} — {len(candidates)} candidates found</p>

            <h3 style="color: #ffd700; margin-bottom: 8px;">Top Signals</h3>
            <table style="width: 100%; border-collapse: collapse; color: #c8d6e5; font-size: 14px;">
                <tr style="background: #111827;">
                    <th style="padding: 8px; text-align: left; color: #636e72;">Ticker</th>
                    <th style="padding: 8px; text-align: left; color: #636e72;">Price</th>
                    <th style="padding: 8px; text-align: left; color: #636e72;">Score</th>
                    <th style="padding: 8px; text-align: left; color: #636e72;">Vol</th>
                    <th style="padding: 8px; text-align: left; color: #636e72;">Vol/Float</th>
                    <th style="padding: 8px; text-align: left; color: #636e72;">MCap</th>
                    <th style="padding: 8px; text-align: left; color: #636e72;">Signal</th>
                </tr>
                {summary_rows}
            </table>

            <p style="margin-top: 20px; color: #636e72; font-size: 13px;">
                Full interactive report with V2 enhancements (vol/float, range position, short interest, support/resistance, offering history) is attached.
            </p>
            <p style="margin-top: 12px; color: #636e72; font-size: 11px;">
                Generated by Amir Screener V2 Enhanced — Not financial advice.
            </p>
        </div>
        """

        # Create email
        msg = MIMEMultipart()
        msg["From"] = config.EMAIL_FROM
        msg["To"] = config.EMAIL_TO
        if hasattr(config, 'EMAIL_CC') and config.EMAIL_CC:
            msg["Cc"] = config.EMAIL_CC
        msg["Subject"] = f"Amir's Watchlist V2 Enhanced — {date_str} ({len(candidates)} candidates)"

        msg.attach(MIMEText(email_html, "html"))

        # Attach the HTML report
        if os.path.exists(html_path):
            with open(html_path, "rb") as f:
                attachment = MIMEBase("application", "octet-stream")
                attachment.set_payload(f.read())
                encoders.encode_base64(attachment)
                filename = os.path.basename(html_path)
                attachment.add_header("Content-Disposition", f"attachment; filename={filename}")
                msg.attach(attachment)

        # Build recipient list
        recipients = [config.EMAIL_TO]
        if hasattr(config, 'EMAIL_CC') and config.EMAIL_CC:
            recipients.append(config.EMAIL_CC)

        # Send via Gmail SMTP
        print(f"  Sending email to {config.EMAIL_TO} (CC: {getattr(config, 'EMAIL_CC', 'none')})...")
        with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT) as server:
            server.starttls()
            server.login(config.SMTP_USER, config.SMTP_PASS)
            server.sendmail(config.EMAIL_FROM, recipients, msg.as_string())

        print(f"  Email sent successfully!")
        return True

    except Exception as e:
        print(f"  Email failed: {e}")
        return False


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Amir's Watchlist Screener V2 Enhanced")
    parser.add_argument("--output", choices=["console", "html", "json", "all"], default="all", help="Output format")
    parser.add_argument("--verbose", action="store_true", help="Show debug info")
    parser.add_argument("--diagnostic", action="store_true", help="Show raw data for tickers without filtering")
    parser.add_argument("--no-filter", action="store_true", dest="no_filter", help="Score all stocks regardless of filter criteria")
    parser.add_argument("--tickers", nargs="+", help="Screen specific tickers instead of full universe")
    parser.add_argument("--email", action="store_true", help="Send results via email after generating")
    args = parser.parse_args()

    print("\nAMIR WATCHLIST SCREENER V2 ENHANCED")
    print(f"   {datetime.now().strftime('%B %d, %Y %I:%M %p')}")
    print("=" * 50)

    # Step 1: Get universe
    finviz_cache = {}
    if args.tickers:
        tickers = args.tickers
        print(f"[1/6] Screening {len(tickers)} specified tickers...")
    else:
        tickers, finviz_cache = get_small_cap_universe(verbose=args.verbose)
        if finviz_cache:
            print(f"    Finviz data cached for {len(finviz_cache)} tickers (will skip yfinance for pre-filtered rejects)")

    if not tickers:
        print("\nNo tickers found to screen. Try running with --tickers EDSA ANTX to test specific stocks.")
        sys.exit(1)

    # Diagnostic mode: show raw data without filtering
    if args.diagnostic:
        print("\nDIAGNOSTIC MODE — Showing raw data (no filters applied)\n")
        for ticker in tickers:
            try:
                stock = yf.Ticker(ticker)
                info = stock.info or {}
                hist = stock.history(period="3mo")
                if hist.empty:
                    print(f"  {ticker}: No price data available from yfinance")
                    continue
                price = hist["Close"].iloc[-1]
                volume = hist["Volume"].iloc[-1]
                avg_vol = hist["Volume"].iloc[-10:].mean() if len(hist) >= 10 else 0
                mcap = info.get("marketCap", "N/A")
                float_s = info.get("floatShares", "N/A")
                shares = info.get("sharesOutstanding", "N/A")
                name = info.get("shortName", "N/A")
                print(f"  {ticker} ({name}):")
                print(f"    Price:          ${price:.2f}")
                print(f"    Market Cap:     {f'${mcap:,.0f}' if isinstance(mcap, (int, float)) else mcap}")
                print(f"    Float Shares:   {f'{float_s:,.0f}' if isinstance(float_s, (int, float)) else float_s}")
                print(f"    Shares Out:     {f'{shares:,.0f}' if isinstance(shares, (int, float)) else shares}")
                print(f"    Today Volume:   {int(volume):,}")
                print(f"    Avg Vol (10d):  {int(avg_vol):,}")
                print(f"    Vol Ratio:      {volume/avg_vol:.1f}x" if avg_vol > 0 else "    Vol Ratio:      N/A")
                print(f"    History Days:   {len(hist)}")
                print()
                # Show which filters would reject it
                rejections = []
                if price < config.MIN_PRICE:
                    rejections.append(f"Price ${price:.2f} < min ${config.MIN_PRICE}")
                if isinstance(mcap, (int, float)) and mcap > 0:
                    if mcap > config.MAX_MARKET_CAP:
                        rejections.append(f"Market cap ${mcap:,.0f} > max ${config.MAX_MARKET_CAP:,.0f}")
                    if mcap < config.MIN_MARKET_CAP:
                        rejections.append(f"Market cap ${mcap:,.0f} < min ${config.MIN_MARKET_CAP:,.0f}")
                sc = float_s if isinstance(float_s, (int, float)) and float_s > 0 else (shares if isinstance(shares, (int, float)) else 0)
                if sc and sc > config.MAX_FLOAT_SHARES:
                    rejections.append(f"Float/shares {sc:,.0f} > max {config.MAX_FLOAT_SHARES:,.0f}")
                if volume < config.MIN_DAILY_VOLUME:
                    rejections.append(f"Volume {int(volume):,} < min {config.MIN_DAILY_VOLUME:,}")
                if rejections:
                    print(f"    REJECTED by:")
                    for r in rejections:
                        print(f"       - {r}")
                else:
                    print(f"    PASS all filters")
                print()
            except Exception as e:
                print(f"  {ticker}: Error — {e}")
        sys.exit(0)

    # Step 2: Pull data and filter
    # If Finviz gave us data, show source breakdown
    finviz_sourced = sum(1 for t in tickers if t in finviz_cache)
    other_sourced = len(tickers) - finviz_sourced
    print(f"[2/6] Analyzing {len(tickers)} stocks ({finviz_sourced} from Finviz, {other_sourced} from yfinance/curated)...")

    candidates = []
    skipped_by_finviz = 0
    yf_calls = 0
    for i, ticker in enumerate(tickers):
        if args.verbose and i % 10 == 0:
            print(f"    Processing {i+1}/{len(tickers)}...")
        yf_calls += 1
        data = get_stock_data(ticker, verbose=args.verbose, no_filter=args.no_filter)
        if data:
            # Enrich with Finviz source data if available
            if ticker in finviz_cache:
                fv = finviz_cache[ticker]
                data["finviz_rel_volume"] = fv.get("finviz_rel_volume", 0)
                data["finviz_change_pct"] = fv.get("finviz_change_pct", 0)
                data["source"] = "finviz+yfinance"
            else:
                data["source"] = "yfinance"
            candidates.append(data)

    print(f"    {len(candidates)} stocks passed filters ({yf_calls} yfinance API calls)")

    if not candidates:
        print("\nNo stocks matched all filters. Consider relaxing criteria in config_v2.py")
        sys.exit(0)

    # Step 3: Rank by score
    print("[3/6] Ranking candidates...")
    candidates.sort(key=lambda x: x["score"], reverse=True)
    candidates = candidates[:config.MAX_CANDIDATES]

    # Step 4: Enrich with insider activity, news, offerings, and reasoning
    print("[4/6] Checking insider activity, news, offerings & building reasoning...")
    for c in candidates:
        c["insider_activity"] = check_insider_activity(c["ticker"], verbose=args.verbose)
        c["news"] = check_news(c["ticker"], verbose=args.verbose)
        build_reasoning_v2(c)

    # Step 5: Output
    print("[5/6] Generating output...")

    # Build dated output paths in the watchlist-results-v2 folder
    import os
    datetime_str = datetime.now().strftime("%Y-%m-%d_%I%M%p").lower()
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "watchlist-results-v2")
    os.makedirs(output_dir, exist_ok=True)
    html_path = os.path.join(output_dir, f"watchlist_v2_{datetime_str}.html")
    json_path = os.path.join(output_dir, f"watchlist_v2_{datetime_str}.json")

    if args.output in ("console", "all"):
        output_console(candidates)

    if args.output in ("html", "all"):
        output_html(candidates, html_path)

    if args.output in ("json", "all"):
        output_json(candidates, json_path)

    # Step 6: Email (if requested)
    if args.email:
        print("[6/6] Sending email...")
        send_email(html_path, candidates, verbose=args.verbose)

    print("\nDone! Review the watchlist above.")


if __name__ == "__main__":
    main()
