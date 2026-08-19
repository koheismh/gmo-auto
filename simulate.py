#!/usr/bin/env python3
"""
モンテカルロシミュレーション実行スクリプト

使い方:
  python simulate.py                          # デフォルト設定で実行
  python simulate.py --trials 50000           # 試行回数を変更
  python simulate.py --win-rate 0.45          # 勝率を変更
  python simulate.py --leverage 2.0           # レバレッジを変更
  python simulate.py --sensitivity            # 感度分析モード
  python simulate.py --config path/to/config  # 設定ファイルを指定

結果の見方:
  - 「プラス確率」が50%以上 → トータルで勝てる見込みあり
  - 「目標達成率」が高い → 月利100%を達成できる確率
  - 「破産率」× 追加投入額 = 覚悟すべきコスト
"""

import argparse
import sys
import time
from pathlib import Path

import yaml

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent))

from src.simulation.monte_carlo import (
    SimulationConfig,
    run_simulation,
    format_result,
    run_sensitivity_analysis,
)


def load_config(config_path: str) -> dict:
    """YAML設定ファイルを読み込む"""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_config_from_args(args, yaml_config: dict) -> SimulationConfig:
    """コマンドライン引数とYAML設定からSimulationConfigを構築"""
    sim_cfg = yaml_config.get("simulation", {})

    return SimulationConfig(
        initial_capital=args.initial_capital or sim_cfg.get("initial_capital", 100000),
        days=args.days or sim_cfg.get("days", 30),
        num_trials=args.trials or sim_cfg.get("num_trials", 10000),
        win_rate=args.win_rate or sim_cfg.get("win_rate", 0.40),
        avg_win_pct=args.avg_win or sim_cfg.get("avg_win_pct", 0.04),
        avg_loss_pct=args.avg_loss or sim_cfg.get("avg_loss_pct", 0.02),
        win_std=sim_cfg.get("win_std", 0.02),
        loss_std=sim_cfg.get("loss_std", 0.01),
        trades_per_day=args.trades_per_day or sim_cfg.get("trades_per_day", 4),
        bankruptcy_threshold=sim_cfg.get("bankruptcy_threshold", 30000),
        top_up_amount=sim_cfg.get("top_up_amount", 100000),
        max_top_ups=sim_cfg.get("max_top_ups", 10),
        profit_take_threshold=sim_cfg.get("profit_take_threshold", 200000),
        profit_take_amount=sim_cfg.get("profit_take_amount", 100000),
        leverage=args.leverage or sim_cfg.get("leverage", 1.5),
    )


def main():
    parser = argparse.ArgumentParser(
        description="仮想通貨自動売買 モンテカルロシミュレーション",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument("--config", type=str, default="config/simulation.yaml",
                        help="設定ファイルパス (default: config/simulation.yaml)")
    parser.add_argument("--trials", type=int, default=None,
                        help="試行回数 (default: 設定ファイルの値)")
    parser.add_argument("--win-rate", type=float, default=None, dest="win_rate",
                        help="勝率 (例: 0.40)")
    parser.add_argument("--avg-win", type=float, default=None,
                        help="平均勝ち率 (例: 0.04)")
    parser.add_argument("--avg-loss", type=float, default=None,
                        help="平均負け率 (例: 0.02)")
    parser.add_argument("--leverage", type=float, default=None,
                        help="実効レバレッジ (例: 1.5)")
    parser.add_argument("--trades-per-day", type=int, default=None, dest="trades_per_day",
                        help="1日のトレード回数 (例: 4)")
    parser.add_argument("--days", type=int, default=None,
                        help="シミュレーション日数 (例: 30)")
    parser.add_argument("--initial-capital", type=float, default=None, dest="initial_capital",
                        help="初期資金 (例: 100000)")
    parser.add_argument("--sensitivity", action="store_true",
                        help="感度分析モードで実行")
    parser.add_argument("--seed", type=int, default=42,
                        help="乱数シード (default: 42, 再現性確保)")

    args = parser.parse_args()

    # 設定ファイル読み込み
    config_path = Path(args.config)
    if config_path.exists():
        yaml_config = load_config(str(config_path))
    else:
        print(f"警告: 設定ファイル '{config_path}' が見つかりません。デフォルト値を使用します。")
        yaml_config = {}

    if args.sensitivity:
        # 感度分析モード
        print("\n感度分析を実行中...\n")
        start = time.time()

        config = build_config_from_args(args, yaml_config)
        sens_cfg = yaml_config.get("sensitivity", {})

        output = run_sensitivity_analysis(
            base_config=config,
            win_rates=sens_cfg.get("win_rates", [0.35, 0.40, 0.45, 0.50]),
            avg_win_pcts=sens_cfg.get("avg_win_pcts", [0.03, 0.04, 0.05, 0.06]),
            avg_loss_pcts=sens_cfg.get("avg_loss_pcts", [0.015, 0.02, 0.025, 0.03]),
            leverages=sens_cfg.get("leverages", [1.0, 1.2, 1.5, 2.0]),
            trials_per_combo=min(args.trials or 2000, 2000),
        )

        elapsed = time.time() - start
        print(output)
        print(f"  実行時間: {elapsed:.1f}秒")
    else:
        # 通常モード
        config = build_config_from_args(args, yaml_config)

        print(f"\nシミュレーション実行中... ({config.num_trials:,}回試行)")
        start = time.time()

        result = run_simulation(config, seed=args.seed)

        elapsed = time.time() - start
        print(f"完了 ({elapsed:.1f}秒)\n")
        print(format_result(result))


if __name__ == "__main__":
    main()
