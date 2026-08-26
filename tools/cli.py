"""
tools/cli.py — CLI để test 5 tool độc lập (bài 19 + 19B + 20).

Dùng:
    python -m tools.cli price FPT
    python -m tools.cli ohlcv HPG 30
    python -m tools.cli indicators VNM
    python -m tools.cli indicators VNM --days 60
    python -m tools.cli price-intl AAPL
    python -m tools.cli ohlcv-intl TSLA 30
    python -m tools.cli indicators-intl NVDA
    python -m tools.cli news HPG --days 7
    python -m tools.cli sentiment HPG
"""

import argparse
import sys

from tools.result import ToolResult


def _check(result: ToolResult, *, exit_on_error: bool = True) -> bool:
    """Print error message and optionally exit if status is not ok."""
    if result.status != "ok":
        print(f"[{result.status}] {result.message}", file=sys.stderr)
        if exit_on_error:
            sys.exit(1)
        return False
    return True


def cmd_price(args: argparse.Namespace) -> None:
    from tools.price import get_realtime_price
    result = get_realtime_price(args.ticker)
    if _check(result):
        print(f"{args.ticker}: {result.data:,.0f} VND")


def cmd_price_intl(args: argparse.Namespace) -> None:
    from tools.price import get_realtime_price_intl
    result = get_realtime_price_intl(args.ticker)
    if _check(result):
        print(f"{args.ticker}: {result.data:.2f} USD")


def cmd_ohlcv(args: argparse.Namespace) -> None:
    from tools.price import get_historical_ohlcv
    result = get_historical_ohlcv(args.ticker, args.days)
    if _check(result):
        print(f"{args.ticker} — {len(result.data)} phiên gần nhất (VND):")
        print(result.data.tail(5).to_string(index=False))


def cmd_ohlcv_intl(args: argparse.Namespace) -> None:
    from tools.price import get_historical_ohlcv_intl
    result = get_historical_ohlcv_intl(args.ticker, args.days)
    if _check(result):
        print(f"{args.ticker} — {len(result.data)} phiên gần nhất (USD):")
        print(result.data.tail(5).to_string(index=False))


def cmd_indicators(args: argparse.Namespace) -> None:
    from tools.price import get_historical_ohlcv, calculate_indicators
    ohlcv = get_historical_ohlcv(args.ticker, args.days)
    if not _check(ohlcv):
        return
    result = calculate_indicators(ohlcv.data, currency="VND")
    if _check(result):
        print(f"=== Chỉ báo kỹ thuật: {args.ticker} ===")
        print(result.data)


def cmd_indicators_intl(args: argparse.Namespace) -> None:
    from tools.price import get_historical_ohlcv_intl, calculate_indicators
    ohlcv = get_historical_ohlcv_intl(args.ticker, args.days)
    if not _check(ohlcv):
        return
    result = calculate_indicators(ohlcv.data, currency="USD")
    if _check(result):
        print(f"=== Chỉ báo kỹ thuật: {args.ticker} (USD) ===")
        print(result.data)


def cmd_news(args: argparse.Namespace) -> None:
    from tools.price import search_financial_news
    result = search_financial_news(args.ticker, args.days)
    if result.status == "ok":
        print(f"=== Tin tức: {args.ticker} ({args.days} ngày) ===")
        print(result.data)
    else:
        print(f"[{result.status}] {result.message}", file=sys.stderr)
        sys.exit(1)


def cmd_sentiment(args: argparse.Namespace) -> None:
    from tools.price import analyze_market_sentiment
    result = analyze_market_sentiment(args.ticker, args.days)
    if result.status == "ok":
        print(f"=== Sentiment: {args.ticker} ({args.days} ngày) ===")
        print(result.data)
    else:
        print(f"[{result.status}] {result.message}", file=sys.stderr)
        sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.cli",
        description="Kiểm tra tool giá chứng khoán VN và quốc tế.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # price (VN)
    p_price = sub.add_parser("price", help="Giá hiện tại mã VN (VND)")
    p_price.add_argument("ticker", help="Mã CK, ví dụ FPT")

    # price-intl
    p_price_intl = sub.add_parser("price-intl", help="Giá hiện tại mã quốc tế (USD)")
    p_price_intl.add_argument("ticker", help="Mã CK quốc tế, ví dụ AAPL")

    # ohlcv (VN)
    p_ohlcv = sub.add_parser("ohlcv", help="Lịch sử OHLCV mã VN")
    p_ohlcv.add_argument("ticker", help="Mã CK")
    p_ohlcv.add_argument("days", type=int, nargs="?", default=60, help="Số phiên (mặc định 60)")

    # ohlcv-intl
    p_ohlcv_intl = sub.add_parser("ohlcv-intl", help="Lịch sử OHLCV mã quốc tế")
    p_ohlcv_intl.add_argument("ticker", help="Mã CK quốc tế")
    p_ohlcv_intl.add_argument("days", type=int, nargs="?", default=60, help="Số phiên (mặc định 60)")

    # indicators (VN)
    p_ind = sub.add_parser("indicators", help="Chỉ báo kỹ thuật mã VN")
    p_ind.add_argument("ticker", help="Mã CK")
    p_ind.add_argument("--days", type=int, default=100, help="Số phiên lịch sử (mặc định 100)")

    # indicators-intl
    p_ind_intl = sub.add_parser("indicators-intl", help="Chỉ báo kỹ thuật mã quốc tế")
    p_ind_intl.add_argument("ticker", help="Mã CK quốc tế")
    p_ind_intl.add_argument("--days", type=int, default=100, help="Số phiên lịch sử (mặc định 100)")

    # news
    p_news = sub.add_parser("news", help="Tin tức tài chính về mã CK")
    p_news.add_argument("ticker", help="Mã CK, ví dụ HPG")
    p_news.add_argument("--days", type=int, default=7, help="Số ngày tìm kiếm (mặc định 7)")

    # sentiment
    p_sent = sub.add_parser("sentiment", help="Phân tích sentiment thị trường")
    p_sent.add_argument("ticker", help="Mã CK, ví dụ HPG")
    p_sent.add_argument("--days", type=int, default=7, help="Số ngày tìm kiếm (mặc định 7)")

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "price":
        cmd_price(args)
    elif args.command == "price-intl":
        cmd_price_intl(args)
    elif args.command == "ohlcv":
        cmd_ohlcv(args)
    elif args.command == "ohlcv-intl":
        cmd_ohlcv_intl(args)
    elif args.command == "indicators":
        cmd_indicators(args)
    elif args.command == "indicators-intl":
        cmd_indicators_intl(args)
    elif args.command == "news":
        cmd_news(args)
    elif args.command == "sentiment":
        cmd_sentiment(args)


if __name__ == "__main__":
    main()
