"""
data/global_universe.py — Static universe maps for world indices, commodities, crypto.

Used by tools/global_market.py. No network calls here.
"""

# yfinance tickers → display names for world equity indices
WORLD_INDICES: dict[str, str] = {
    "^GSPC":    "S&P 500",
    "^DJI":     "Dow Jones",
    "^IXIC":    "Nasdaq",
    "^VIX":     "VIX",
    "^N225":    "Nikkei 225",
    "^KS11":    "KOSPI",
    "000001.SS": "Shanghai",
    "^HSI":     "Hang Seng",
}

# yfinance futures tickers for commodities
COMMODITIES: dict[str, dict] = {
    "GC=F": {"name": "Gold",        "unit": "USD/oz"},
    "SI=F": {"name": "Silver",      "unit": "USD/oz"},
    "CL=F": {"name": "WTI Crude",   "unit": "USD/bbl"},
    "BZ=F": {"name": "Brent Crude", "unit": "USD/bbl"},
}

# CoinGecko IDs → display names
CRYPTO_IDS: dict[str, str] = {
    "bitcoin":  "BTC",
    "ethereum": "ETH",
    "ripple":   "XRP",
    "solana":   "SOL",
}

# VN gold conversion: 1 troy oz = 26.666... chỉ = 2.6666... lượng
# 1 lượng = 37.5g, 1 troy oz = 31.1035g → 1 lượng = 1.20565 troy oz
TROY_OZ_PER_LUONG = 1.20565
