"""
tools/cli.py — CLI để test 3 tool độc lập.

Dùng:
    python -m tools.cli price FPT
    python -m tools.cli ohlcv HPG 30
    python -m tools.cli indicators VNM
    python -m tools.cli indicators VNM --days 60
"""

import argparse
import sys


def cmd_price(args: argparse.Namespace) -> None:
    from tools.price import get_realtime_price
    price = get_realtime_price(args.ticker)
    print(f"{args.ticker}: {price:,.0f} VND")


def cmd_ohlcv(args: argparse.Namespace) -> None:
    from tools.price import get_historical_ohlcv
    df = get_historical_ohlcv(args.ticker, args.days)
    print(f"{args.ticker} — {len(df)} phiên gần nhất:")
    print(df.tail(5).to_string(index=False))


def cmd_indicators(args: argparse.Namespace) -> None:
    from tools.price import get_historical_ohlcv, calculate_indicators
    df = get_historical_ohlcv(args.ticker, args.days)
    result = calculate_indicators(df)
    print(f"=== Chỉ báo kỹ thuật: {args.ticker} ===")
    print(result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.cli",
        description="Kiểm tra 3 tool giá chứng khoán.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # price
    p_price = sub.add_parser("price", help="Giá hiện tại")
    p_price.add_argument("ticker", help="Mã CK, ví dụ FPT")

    # ohlcv
    p_ohlcv = sub.add_parser("ohlcv", help="Lịch sử OHLCV")
    p_ohlcv.add_argument("ticker", help="Mã CK")
    p_ohlcv.add_argument("days", type=int, nargs="?", default=60, help="Số phiên (mặc định 60)")

    # indicators
    p_ind = sub.add_parser("indicators", help="Chỉ báo kỹ thuật")
    p_ind.add_argument("ticker", help="Mã CK")
    p_ind.add_argument("--days", type=int, default=100, help="Số phiên lịch sử (mặc định 100)")

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "price":
            cmd_price(args)
        elif args.command == "ohlcv":
            cmd_ohlcv(args)
        elif args.command == "indicators":
            cmd_indicators(args)
    except ValueError as e:
        print(f"Lỗi: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Lỗi không mong đợi: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
