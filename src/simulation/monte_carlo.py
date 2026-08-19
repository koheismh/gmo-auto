"""
モンテカルロシミュレーション

破産・追加投入・利益確保を含めた自動売買の期待値を検証する。
10,000回のランダム試行を行い、トータルでプラスになるかを数値で確認する。

使い方:
  python simulate.py                     # デフォルト設定で実行
  python simulate.py --trials 50000      # 試行回数を変更
  python simulate.py --sensitivity       # 感度分析（パラメータ組み合わせ）
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SimulationConfig:
    """シミュレーション設定"""
    initial_capital: float = 100000
    days: int = 30
    num_trials: int = 10000
    win_rate: float = 0.40
    avg_win_pct: float = 0.04
    avg_loss_pct: float = 0.02
    win_std: float = 0.02
    loss_std: float = 0.01
    trades_per_day: int = 4
    bankruptcy_threshold: float = 30000
    top_up_amount: float = 100000
    max_top_ups: int = 10
    profit_take_threshold: float = 200000
    profit_take_amount: float = 100000
    leverage: float = 1.5


@dataclass
class TrialResult:
    """1回の試行結果"""
    final_capital: float = 0.0
    max_capital: float = 0.0
    min_capital: float = 0.0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    bankruptcy_count: int = 0
    top_up_total: float = 0.0
    profit_taken_total: float = 0.0
    max_drawdown_pct: float = 0.0
    max_consecutive_losses: int = 0
    reached_target: bool = False


@dataclass
class SimulationResult:
    """全試行の集計結果"""
    config: SimulationConfig
    trials: list = field(default_factory=list)

    @property
    def num_trials(self) -> int:
        return len(self.trials)

    @property
    def final_capitals(self) -> np.ndarray:
        return np.array([t.final_capital for t in self.trials])

    @property
    def target_reached_count(self) -> int:
        return sum(1 for t in self.trials if t.reached_target)

    @property
    def target_reached_rate(self) -> float:
        return self.target_reached_count / self.num_trials

    @property
    def bankruptcy_trials(self) -> int:
        return sum(1 for t in self.trials if t.bankruptcy_count > 0)

    @property
    def avg_bankruptcy_count(self) -> float:
        return np.mean([t.bankruptcy_count for t in self.trials])

    @property
    def avg_top_up_total(self) -> float:
        return np.mean([t.top_up_total for t in self.trials])

    @property
    def avg_profit_taken(self) -> float:
        return np.mean([t.profit_taken_total for t in self.trials])

    @property
    def total_invested_avg(self) -> float:
        """平均総投入額（初期 + 追加投入）"""
        return np.mean([
            self.config.initial_capital + t.top_up_total
            for t in self.trials
        ])

    @property
    def total_recovered_avg(self) -> float:
        """平均総回収額（利益確保 + 最終残高）"""
        return np.mean([
            t.profit_taken_total + t.final_capital
            for t in self.trials
        ])

    @property
    def net_profit_avg(self) -> float:
        """平均純利益"""
        return self.total_recovered_avg - self.total_invested_avg

    @property
    def profitable_rate(self) -> float:
        """トータルプラスになる確率"""
        count = sum(
            1 for t in self.trials
            if (t.profit_taken_total + t.final_capital) >
               (self.config.initial_capital + t.top_up_total)
        )
        return count / self.num_trials

    @property
    def avg_max_drawdown(self) -> float:
        return np.mean([t.max_drawdown_pct for t in self.trials])

    @property
    def avg_max_consecutive_losses(self) -> float:
        return np.mean([t.max_consecutive_losses for t in self.trials])


def run_single_trial(config: SimulationConfig, rng: np.random.Generator) -> TrialResult:
    """1回の試行を実行"""
    capital = config.initial_capital
    max_capital = capital
    min_capital = capital
    peak_capital = capital

    total_trades = 0
    wins = 0
    losses = 0
    bankruptcy_count = 0
    top_up_total = 0.0
    profit_taken_total = 0.0
    max_drawdown_pct = 0.0
    max_consecutive_losses = 0
    current_consecutive_losses = 0
    reached_target = False

    total_trades_count = config.trades_per_day * config.days

    for _ in range(total_trades_count):
        # 破産チェック
        if capital <= config.bankruptcy_threshold:
            bankruptcy_count += 1
            if bankruptcy_count > config.max_top_ups:
                # 最大追加投入回数を超えた → 終了
                break
            top_up_total += config.top_up_amount
            capital += config.top_up_amount
            peak_capital = capital  # ドローダウン計算のリセット

        # トレード実行
        is_win = rng.random() < config.win_rate

        if is_win:
            # 勝ちトレード: 正規分布でばらつきを付ける（最低0.5%の利益）
            pct = max(0.005, rng.normal(config.avg_win_pct, config.win_std))
            pnl = capital * pct * config.leverage
            capital += pnl
            wins += 1
            current_consecutive_losses = 0
        else:
            # 負けトレード: 正規分布でばらつきを付ける（最低0.5%の損失）
            pct = max(0.005, rng.normal(config.avg_loss_pct, config.loss_std))
            pnl = capital * pct * config.leverage
            capital -= pnl
            losses += 1
            current_consecutive_losses += 1
            max_consecutive_losses = max(max_consecutive_losses, current_consecutive_losses)

        total_trades += 1

        # 最大/最小資金更新
        max_capital = max(max_capital, capital)
        min_capital = min(min_capital, capital)

        # ドローダウン計算
        if capital > peak_capital:
            peak_capital = capital
        drawdown = (peak_capital - capital) / peak_capital if peak_capital > 0 else 0
        max_drawdown_pct = max(max_drawdown_pct, drawdown)

        # 利益確保チェック
        if capital >= config.profit_take_threshold:
            reached_target = True
            profit_taken_total += config.profit_take_amount
            capital -= config.profit_take_amount
            peak_capital = capital  # リセット

    return TrialResult(
        final_capital=capital,
        max_capital=max_capital,
        min_capital=min_capital,
        total_trades=total_trades,
        wins=wins,
        losses=losses,
        bankruptcy_count=bankruptcy_count,
        top_up_total=top_up_total,
        profit_taken_total=profit_taken_total,
        max_drawdown_pct=max_drawdown_pct * 100,
        max_consecutive_losses=max_consecutive_losses,
        reached_target=reached_target,
    )


def run_simulation(config: SimulationConfig, seed: Optional[int] = 42) -> SimulationResult:
    """モンテカルロシミュレーションを実行"""
    rng = np.random.default_rng(seed)
    trials = []

    for _ in range(config.num_trials):
        result = run_single_trial(config, rng)
        trials.append(result)

    return SimulationResult(config=config, trials=trials)


def format_result(result: SimulationResult) -> str:
    """結果を整形して文字列で返す"""
    config = result.config
    finals = result.final_capitals

    lines = []
    lines.append("=" * 70)
    lines.append(f"  モンテカルロシミュレーション結果 ({result.num_trials:,}回試行)")
    lines.append("=" * 70)
    lines.append("")

    # 設定サマリー
    lines.append("【設定】")
    lines.append(f"  初期資金:       {config.initial_capital:,.0f}円")
    lines.append(f"  期間:           {config.days}日")
    lines.append(f"  勝率:           {config.win_rate*100:.1f}%")
    lines.append(f"  平均勝ち:       +{config.avg_win_pct*100:.1f}%")
    lines.append(f"  平均負け:       -{config.avg_loss_pct*100:.1f}%")
    lines.append(f"  トレード/日:    {config.trades_per_day}回")
    lines.append(f"  レバレッジ:     {config.leverage:.1f}倍")
    lines.append(f"  破産ライン:     {config.bankruptcy_threshold:,.0f}円")
    lines.append("")

    # 期待値計算
    expected_per_trade = (config.win_rate * config.avg_win_pct -
                          (1 - config.win_rate) * config.avg_loss_pct) * config.leverage
    lines.append("【理論期待値（1トレードあたり）】")
    lines.append(f"  期待値:         {expected_per_trade*100:+.3f}%")
    lines.append(f"  月間期待値:     {expected_per_trade * config.trades_per_day * config.days * 100:+.1f}%")
    lines.append("")

    # 結果分布
    lines.append("【1ヶ月後の資金分布】")
    lines.append(f"  平均:           {np.mean(finals):,.0f}円")
    lines.append(f"  中央値:         {np.median(finals):,.0f}円")
    lines.append(f"  標準偏差:       {np.std(finals):,.0f}円")
    lines.append(f"  最大:           {np.max(finals):,.0f}円")
    lines.append(f"  最小:           {np.min(finals):,.0f}円")
    lines.append(f"  25パーセンタイル: {np.percentile(finals, 25):,.0f}円")
    lines.append(f"  75パーセンタイル: {np.percentile(finals, 75):,.0f}円")
    lines.append("")

    # 目標達成
    lines.append("【目標達成率】")
    lines.append(f"  20万円到達:     {result.target_reached_count:,} / {result.num_trials:,} "
                 f"({result.target_reached_rate*100:.1f}%)")
    lines.append("")

    # 破産統計
    lines.append("【破産統計】")
    lines.append(f"  破産経験試行:   {result.bankruptcy_trials:,} / {result.num_trials:,} "
                 f"({result.bankruptcy_trials/result.num_trials*100:.1f}%)")
    lines.append(f"  平均破産回数:   {result.avg_bankruptcy_count:.2f}回/試行")
    lines.append(f"  追加投入平均:   {result.avg_top_up_total:,.0f}円")
    lines.append("")

    # トータル収支
    lines.append("【トータル収支】")
    lines.append(f"  総投入額平均:   {result.total_invested_avg:,.0f}円")
    lines.append(f"    (内訳: 初期{config.initial_capital:,.0f} + 追加投入{result.avg_top_up_total:,.0f})")
    lines.append(f"  総回収額平均:   {result.total_recovered_avg:,.0f}円")
    lines.append(f"    (内訳: 利益確保{result.avg_profit_taken:,.0f} + 最終残高{np.mean(finals):,.0f})")
    lines.append(f"  純利益平均:     {result.net_profit_avg:+,.0f}円")
    lines.append(f"  プラス確率:     {result.profitable_rate*100:.1f}%")
    lines.append("")

    # リスク指標
    lines.append("【リスク指標】")
    lines.append(f"  最大DD平均:     {result.avg_max_drawdown:.1f}%")
    lines.append(f"  連続負け平均:   {result.avg_max_consecutive_losses:.1f}回")
    lines.append("")
    lines.append("=" * 70)

    return "\n".join(lines)


def run_sensitivity_analysis(
    base_config: SimulationConfig,
    win_rates: list,
    avg_win_pcts: list,
    avg_loss_pcts: list,
    leverages: list,
    trials_per_combo: int = 2000,
) -> str:
    """感度分析: パラメータ組み合わせごとの期待値を一覧表示"""
    from tabulate import tabulate

    results = []

    for wr in win_rates:
        for awp in avg_win_pcts:
            for alp in avg_loss_pcts:
                for lev in leverages:
                    config = SimulationConfig(
                        initial_capital=base_config.initial_capital,
                        days=base_config.days,
                        num_trials=trials_per_combo,
                        win_rate=wr,
                        avg_win_pct=awp,
                        avg_loss_pct=alp,
                        win_std=base_config.win_std,
                        loss_std=base_config.loss_std,
                        trades_per_day=base_config.trades_per_day,
                        bankruptcy_threshold=base_config.bankruptcy_threshold,
                        top_up_amount=base_config.top_up_amount,
                        max_top_ups=base_config.max_top_ups,
                        profit_take_threshold=base_config.profit_take_threshold,
                        profit_take_amount=base_config.profit_take_amount,
                        leverage=lev,
                    )

                    # 理論期待値
                    ev = (wr * awp - (1 - wr) * alp) * lev

                    sim_result = run_simulation(config, seed=42)

                    results.append({
                        "勝率": f"{wr*100:.0f}%",
                        "勝ち幅": f"{awp*100:.1f}%",
                        "負け幅": f"{alp*100:.1f}%",
                        "レバ": f"{lev:.1f}x",
                        "理論EV/trade": f"{ev*100:+.2f}%",
                        "目標達成率": f"{sim_result.target_reached_rate*100:.1f}%",
                        "破産率": f"{sim_result.bankruptcy_trials/sim_result.num_trials*100:.1f}%",
                        "純利益平均": f"{sim_result.net_profit_avg:+,.0f}",
                        "プラス確率": f"{sim_result.profitable_rate*100:.1f}%",
                    })

    lines = []
    lines.append("=" * 100)
    lines.append("  感度分析結果")
    lines.append("=" * 100)
    lines.append("")
    lines.append(tabulate(results, headers="keys", tablefmt="grid"))
    lines.append("")
    lines.append(f"  ※ 各組み合わせ {trials_per_combo:,}回試行")
    lines.append("")

    return "\n".join(lines)
