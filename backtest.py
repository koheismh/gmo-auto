#!/usr/bin/env python3
"""
バックテスト実行スクリプト

使い方:
  python backtest.py                                    # デフォルト（BTC_JPY, 直近3ヶ月）
  python backtest.py --symbol BTC_JPY --start 20240401 --end 20240630
  python backtest.py --symbol ETH_JPY --start 20240101 --end 20240331
  python backtest.py --no-fetch                         # データ取得をスキップ（キャッシュ使用）

結果の見方:
  - 勝率35%以上、PF1.3以上、最大DD30%以下が合格ライン
  - 合格しなければパラメータ調整 or 戦略変更が必要
  - バックテストの結果は「最低限ダメじゃない」の確認に使う
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.data.candle import get_or_fetch_candles
from src.simulation.backtest import (
    BacktestConfig,
    run_backtest,
    format_backtest_result,
)


def main():
    parser = argparse.ArgumentParser(
        description="仮想通貨自動売買 バックテスト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # デフォルト期間: 3ヶ月前〜昨日
    default_end = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    default_start = (datetime.now() - timedelta(days=91)).strftime("%Y%m%d")

    parser.add_argument("--symbol", type=str, default="BTC_JPY",
                        help="取引ペア (default: BTC_JPY)")
    parser.add_argument("--interval", type=str, default="15min",
                        help="時間足 (default: 15min)")
    parser.add_argument("--start", type=str, default=default_start,
                        help=f"開始日 YYYYMMDD (default: {default_start})")
    parser.add_argument("--end", type=str, default=default_end,
                        help=f"終了日 YYYYMMDD (default: {default_end})")
    parser.add_argument("--capital", type=float, default=100000,
                        help="初期資金 (default: 100000)")
    parser.add_argument("--leverage", type=float, default=1.5,
                        help="レバレッジ (default: 1.5)")
    parser.add_argument("--no-fetch", action="store_true",
                        help="データ取得をスキップ（既存キャッシュを使用）")
    parser.add_argument("--data-dir", type=str, default="data",
                        help="データ保存ディレクトリ (default: data)")

    args = parser.parse_args()

    print(f"\n{'='*70}")
    print(f"  バックテスト: {args.symbol} [{args.start} - {args.end}]")
    print(f"{'='*70}\n")

    # データ取得
    if args.no_fetch:
        # キャッシュから読み込み
        from src.data.candle import load_candles
        filename = f"{args.symbol}_{args.interval}_{args.start}_{args.end}.csv"
        filepath = str(Path(args.data_dir) / filename)
        if not Path(filepath).exists():
            print(f"エラー: キャッシュファイルが見つかりません: {filepath}")
            print("--no-fetch を外して再実行してください")
            sys.exit(1)
        df = load_candles(filepath)
    else:
        df = get_or_fetch_candles(
            symbol=args.symbol,
            interval=args.interval,
            start_date=args.start,
            end_date=args.end,
            data_dir=args.data_dir,
        )

    if len(df) == 0:
        print("エラー: データが取得できませんでした")
        sys.exit(1)

    print(f"\nデータ件数: {len(df)}本")
    print(f"期間: {df['timestamp'].iloc[0]} ~ {df['timestamp'].iloc[-1]}\n")

    # バックテスト実行
    config = BacktestConfig(
        initial_capital=args.capital,
        leverage=args.leverage,
    )

    print("バックテスト実行中...")
    result = run_backtest(df, config)

    # 結果表示
    print("")
    print(format_backtest_result(result, symbol=args.symbol))

    # モンテカルロシミュレーションへのフィードバック
    if result.total_trades > 0:
        print("\n【モンテカルロシミュレーション用パラメータ】")
        print("以下のパラメータでsimulate.pyを実行すると、このバックテスト結果に基づく期待値が確認できます:")
        print(f"  python simulate.py --win-rate {result.win_rate:.2f} "
              f"--avg-win {result.avg_win_pct/100:.4f} "
              f"--avg-loss {abs(result.avg_loss_pct)/100:.4f} "
              f"--leverage {args.leverage}")
        print("")


if __name__ == "__main__":
    main()
