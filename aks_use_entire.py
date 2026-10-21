import akshare as ak
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


# ============================================================
# 1. 数据获取
# ============================================================
def get_stock_data(stock_symbol, start_date, end_date) -> pd.DataFrame:
    """获取A股历史日线数据"""

    stock_symbol = stock_symbol.lower()
    stock_data = ak.stock_zh_a_daily(symbol=stock_symbol, start_date=start_date, end_date=end_date)
    stock_data['date'] = pd.to_datetime(stock_data['date'])
    stock_data = stock_data.set_index('date').sort_index()
    return stock_data


# ============================================================
# 2. 信号生成（支持任意均线窗口）
# ============================================================
def gen_trade_signal(df, short_window=5, long_window=20) -> pd.DataFrame:
    """
    生成双均线交易信号。

    Parameters:
    df (pd.DataFrame): 含 'close' 列的行情数据
    short_window (int): 短期均线窗口，如 5
    long_window (int): 长期均线窗口，如 20

    Returns:
    pd.DataFrame: 新增 MA_short / MA_long / Signal / Position 列

    #2026/9/15 新增+DM/-DM/ADX指标 便于后续策略优化
    
    #2026/9/16 新增成交量过滤 优化买卖策略

    #2026/9/16 新增RSI过滤 优化买卖策略
    """
    df = df.copy()  # 避免修改原始数据

    df[f'MA{short_window}'] = df['close'].rolling(window=short_window).mean()
    df[f'MA{long_window}'] = df['close'].rolling(window=long_window).mean()

    # 金叉买入(1)，死叉空仓(0)
    df['Signal'] = np.where(
        df[f'MA{short_window}'] > df[f'MA{long_window}'], 1, 0
    )
    df['Position'] = df['Signal'].diff()  # +1 买入，-1 卖出

    # +DM, -DM, ADX指标计算
    # high_diff 相当于今天的最高价减去昨天的最高价
    df['high_diff'] = df['high'].diff()
    # low_diff 相当于昨天的最低价减去今天的最低价
    df['low_diff'] = -df['low'].diff()

    # +DM 和 -DM 的计算逻辑是：
    # 如果今天的最高价比昨天的最高价高，并且这个差值大于最低价的差值，那么 +DM 就等于最高价的差值，否则为 0。
    df['+DM'] = np.where((df['high_diff'] > df['low_diff']) & (df['high_diff'] > 0), df['high_diff'], 0)

    # 如果昨天的最低价比今天的最低价低，并且这个差值大于最高价的差值，那么 -DM 就等于最低价的差值，否则为 0。
    df['-DM'] = np.where((df['low_diff'] > df['high_diff']) & (df['low_diff'] > 0), df['low_diff'], 0)

    # TR (True Range) TR 衡量的是今天价格波动的"真实幅度"，
    # 它不只考虑今天的最高最低，还考虑了昨天收盘价，防止跳空缺口被忽略
    # 它的计算逻辑是：
    #
    # 取今天的最高最低价的差值HL
    # 取今天的最高价和昨天的收盘价的差值HC
    # 取昨天的收盘价和今天的最低价的差值LC
    df['HL'] = df['high'] - df['low']
    df['HC'] = abs(df['high'] - df['close'].shift(1))
    df['LC'] = abs(df['close'].shift(1) - df['low'])
    df['TR'] = df[['HL', 'HC', 'LC']].max(axis=1)

    # 得到的±DM和TR是每日的值，噪音（波动）较大，需要进行平滑处理。
    # 这里使用 Wilder's 平滑方法，类似于指数移动平均，但权重不同。
    """
    平滑处理就是把每天波动的 TR、+DM、-DM 用 Wilder 方法"揉"成更稳定的值，
    再算出 +DI 和 -DI。这样得到的 ADX 才能真实反映趋势强度，而不是被单日波动忽悠
    """
    # 今日平滑值 = 昨日平滑值 + (今日原始值 - 昨日平滑值) / N
    # 其中 N 是平滑周期，通常取 14。

    period = 14
    df['+DM_smooth'] = df['+DM'].ewm(alpha=1/period, adjust=False).mean()
    df['-DM_smooth'] = df['-DM'].ewm(alpha=1/period, adjust=False).mean()
    df['TR_smooth'] = df['TR'].ewm(alpha=1/period, adjust=False).mean()

    df['+DI'] = 100 * (df['+DM_smooth'] / df['TR_smooth'])
    df['-DI'] = 100 * (df['-DM_smooth'] / df['TR_smooth'])


    # 计算 DX 和 ADX
    # DX (Directional Movement Index) 衡量的是趋势的强弱，它的计算逻辑是：
    # 取 +DI 和 -DI 的差值的绝对值，除以它们的和，再乘以 100。
    """
    直观理解
    情况	   +DI -DI	DX	            含义
    多头碾压	40	5	100*35/45 ≈ 78	趋势极强（向上）
    空头碾压	3	45	100*42/48 ≈ 88	趋势极强（向下）
    势均力敌	20	18	100*2/38 ≈ 5	几乎没趋势
    """
    df['DX'] = 100 * (abs(df['+DI'] - df['-DI']) / (df['+DI'] + df['-DI']))

    # ADX(Average Directional Index) 是 DX 的平滑值，衡量趋势强度。
    # DX 是基于 +DI 和 -DI 算出来的，而 +DI/-DI 已经平滑过一次了。
    # 但 DX 本身仍然会有波动，再平滑一次，让 ADX 更稳定、更能反映中期趋势强度。
    df['ADX'] = df['DX'].ewm(alpha=1/period, adjust=False).mean()

    """
    DX 和 ADX 的区别
    指标	 含义	        特点
    DX	    当天多空差距	波动大，反应快
    ADX	    DX 的平滑平均	稳定，反映中期趋势强度
    """

    """
    ADX            含义
    <20            趋势弱，震荡行情
    25~40          趋势明确 顺势交易胜率高
    40~60          趋势强 行情单边
    >60            趋势火热 存在反转风险
    """

    # 计算过去20日平均成交量
    # 上涨需要成交量配合（放量上涨才可信），
    # 下跌不一定需要（缩量下跌也可能是正常调整）。
    # 所以，买入信号通常要求成交量放大，卖出信号不一定要求。
    # df['volume'] 是当天的成交量

    df['Volume_MA20'] = df['volume'].rolling(window=20).mean()

    # 今天成交量是过去20天平均的1.5倍以上 → 明显放量
    # 过滤条件： 今日成交量 > 过去20日成交均量的1.5倍
    df['Volume_Filter'] = df['volume'] > 1.5 * df['Volume_MA20']

    # 新增RSI(Relative Strength Index)指标计算
    # RSI 是一种动量指标，用于衡量价格的超买或超卖状态。
    # 通俗理解：
    # 最近N天里，涨的日子涨了多少，跌的日子跌了多少？
    # 如果涨的力量远大于跌的力量，RSI就高；反之RSI就低。

    """
    数值范围：0 ~ 100
    RSI     数值	含义	市场状态
    > 70	超买	涨得太猛，可能回调
    50 ~ 70	偏强	多头占优
    50	    多空均衡	中性
    30 ~ 50	偏弱	空头占优
    < 30	超卖	跌得太狠，可能反弹

    假设最近5天涨跌情况：

    日期	涨跌	上涨	下跌
    第1天	+2	2	0
    第2天	+3	3	0
    第3天	-1	0	1
    第4天	+2	2	0
    第5天	-1	0	1
    计算：

    平均上涨 = (2+3+0+2+0) / 5 = 1.4

    平均下跌 = (0+0+1+0+1) / 5 = 0.4

    RS = 1.4 / 0.4 = 3.5

    RSI = 100 - 100/(1+3.5) = 77.8（超买）

    解读：涨的力量远大于跌的力量，RSI接近80，说明近期涨得太猛，可能回调。
    """

    df['change'] = df['close'].diff()
    # 上涨：今天比昨天涨了多少（跌的日子记0）
    df['gain'] = np.where(df['change'] > 0, df['change'], 0)

    # 下跌：今天比昨天跌了多少（涨的日子记0，取正值）
    df['loss'] = np.where(df['change'] < 0, -df['change'], 0)

    # 计算平均上涨和平均下跌
    period = 14
    df['avg_gain'] = df['gain'].ewm(alpha=1/period, adjust=False).mean()
    df['avg_loss'] = df['loss'].ewm(alpha=1/period, adjust=False).mean()

    # 计算RS（Relative Strength）
    df['RS'] = df['avg_gain'] / df['avg_loss']

    # 计算RSI
    # RSI = 100 × 平均上涨 / (平均上涨 + 平均下跌)
    df['RSI'] = 100 - (100 / (1 + df['RS']))
    return df

# ============================================================
# 3. 画交易信号图
# ============================================================
def plot_trade_signals(df, stock_symbol, stock_name, short_window=5, long_window=20):
    """
    Plot the stock price along with moving averages and trade signals.

    Parameters:
    df (pd.DataFrame): The DataFrame containing historical stock data and trade signals.
    stock_symbol (str): The stock symbol for labeling the plot.
    """
    try:
        plt.figure(figsize=(14, 7))
        plt.plot(df.index, df['close'], label='Close Price', color='blue')
        plt.plot(df.index, df[f'MA{long_window}'], label=f'{long_window}-Day MA', color='orange')
        plt.plot(df.index, df[f'MA{short_window}'], label=f'{short_window}-Day MA', color='green')

        buy_signals = df[df['Position'] == 1]
        plt.scatter(buy_signals.index, buy_signals['close'], label='Buy Signal', marker='^', color='g', s=100)

        sell_signals = df[df['Position'] == -1]
        plt.scatter(sell_signals.index, sell_signals['close'], label='Sell Signal', marker='v', color='r', s=100)

        plt.title(f'{stock_name} ({stock_symbol}) Price and Trade Signals')
        plt.xlabel('Date')
        plt.ylabel('Price(CNY)')
        plt.legend()
        plt.grid(alpha=0.3)
        plt.savefig(f'{stock_symbol}_{stock_name}_trading_signals.png')  
        print(f"Plot saved as {stock_symbol}_{stock_name}_trading_signals.png")
        plt.close()  
    except Exception as e:
        print(f"Plotting error: {e}")


# ============================================================
# 4. 回测引擎（封装交易逻辑）
# ============================================================
def run_backtest(df, short_window=5, long_window=20,
                 initial_cash=5_000_000,
                 commission_rate=0.0003,
                 stamp_duty_rate=0.001,
                 use_adx_filter=False,
                 adx_threshold=25,
                 use_rsi_filter=False,
                 low_rsi_threshold=50,
                 high_rsi_threshold=70,
                 use_volume_filter=False) -> dict:
    """
    use_adx_rsi_filter: 是否启用 ADX+RSI 过滤
    adx_threshold: ADX 阈值
    low_rsi_threshold: RSI 低阈值
    high_rsi_threshold: RSI 高阈值
    use_volume_filter: 是否启用成交量过滤
    """
    df = gen_trade_signal(df, short_window, long_window)

    hold = 0
    cash = initial_cash
    total_assets = []
    buy_trades, sell_trades = [], []

    for i in range(len(df)):
        price = df['close'].iloc[i]

        if i == 0:
            total_assets.append(initial_cash)
            continue

        # 买入信号 
        buy_condition = df['Position'].iloc[i] == 1 and cash > 100 * price
        if use_adx_filter: # 启用 ADX过滤
            buy_condition = (buy_condition 
                           and df['ADX'].iloc[i] > adx_threshold
                           and df['+DI'].iloc[i] > df['-DI'].iloc[i]
                           and df['RSI'].iloc[i] < high_rsi_threshold)
        
        if use_volume_filter: # 启用成交量过滤
            buy_condition = (buy_condition and df['Volume_Filter'].iloc[i])

        if use_rsi_filter: # 启用RSI过滤
            buy_condition = (buy_condition and df['RSI'].iloc[i] > low_rsi_threshold and df['RSI'].iloc[i] < high_rsi_threshold)

        if buy_condition:
            hands = int(cash // (price * 100))
            if hands > 0:
                hold += hands * 100
                cash -= hands * price * 100 * (1 + commission_rate)
                buy_trades.append({
                    'date': df.index[i], 'price': price,
                    'shares': hands * 100
                })

        # 卖出信号：死叉就卖，不加 ADX / RSI条件 和 成交量过滤条件
        elif df['Position'].iloc[i] == -1 and hold > 0:
            cash += hold * price * (1 - commission_rate - stamp_duty_rate)
            sell_trades.append({
                'date': df.index[i], 'price': price, 'shares': hold
            })
            hold = 0

        total_assets.append(cash + hold * price)

    df['Total Assets'] = total_assets
    df['Cumulative Return'] = df['Total Assets'] / initial_cash - 1

    perf = calc_performance(df, buy_trades, sell_trades, initial_cash)
    perf['trades'] = {'buy': buy_trades, 'sell': sell_trades}
    perf['df'] = df
    return perf

# ============================================================
# 5. 绩效计算（独立函数，便于复用）
# ============================================================
def calc_performance(df, buy_trades, sell_trades, initial_cash) -> dict:
    """计算夏普比率、最大回撤、胜率、盈亏比"""
    # 夏普比率
    daily_ret = df['Total Assets'].pct_change()
    annual_ret = daily_ret.mean() * 252
    annual_vol = daily_ret.std() * np.sqrt(252)
    sharpe = (annual_ret - 0.03) / annual_vol if annual_vol != 0 else np.nan

    # 最大回撤（保留负号）
    running_max = df['Total Assets'].cummax()
    drawdown_pct = (df['Total Assets'] - running_max) / running_max
    max_dd = drawdown_pct.min()

    # 胜率 & 盈亏比（只统计配对完成的交易）
    win_count, loss_count = 0, 0
    total_win, total_loss = 0, 0
    pair_count = min(len(buy_trades), len(sell_trades))
    for i in range(pair_count):
        buy_p = buy_trades[i]['price']
        sell_p = sell_trades[i]['price']
        shares = buy_trades[i]['shares']
        if sell_p > buy_p:
            win_count += 1
            total_win += (sell_p - buy_p) * shares
        else:
            loss_count += 1
            total_loss += (buy_p - sell_p) * shares

    win_rate = win_count / pair_count if pair_count > 0 else np.nan
    profit_factor = total_win / total_loss if total_loss != 0 else np.nan

    return {
        'cumulative_return': df['Cumulative Return'].iloc[-1],
        'sharpe_ratio': sharpe,
        'max_drawdown': max_dd,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'total_trades': pair_count,
        'final_assets': df['Total Assets'].iloc[-1],
    }


# ============================================================
# 6. 参数对比（核心新增功能）
# ============================================================
def compare_params(df, param_pairs) -> pd.DataFrame:
    """
    对多组均线参数跑回测，返回对比表。

    Parameters:
    df (pd.DataFrame): 原始行情数据
    param_pairs (list): [(short, long), ...] 如 [(5,20), (10,30), (5,60)]

    Returns:
    pd.DataFrame: 每组参数的绩效指标对比
    """
    results = []
    for short, long in param_pairs:
        perf = run_backtest(df, short, long)
        results.append({
            '参数组合': f'MA{short}/MA{long}',
            '累计收益率': f"{perf['cumulative_return'] * 100:.2f}%",
            '夏普比率': f"{perf['sharpe_ratio']:.2f}",
            '最大回撤': f"{perf['max_drawdown'] * 100:.2f}%",
            '胜率': f"{perf['win_rate'] * 100:.2f}%",
            '盈亏比': f"{perf['profit_factor']:.2f}",
            '交易次数': perf['total_trades'],
        })
    return pd.DataFrame(results)


# ============================================================
# 7. 主程序
# ============================================================
def main():
    stock_list = {
        'sh600519': '茅台（消费）',
        'sz000858': '五粮液（消费）',
        'sh600036': '招行（金融）',
        'sz002594': '比亚迪（新能源）',
        'sh601318': '中国平安（保险）',
        'sz000333': '美的（家电）',
        'sh600900': '长江电力（公用事业）',
        'sz300750': '宁德时代（新能源）',
        'sh601899': '紫金矿业（资源）',
        'sz000651': '格力（家电）',
    }
    #stocks = ['sh600519', 'sz000858', 'sh600036', 'sz002594']
    for stock_symbol, stock_name in stock_list.items():
        print(f"\n{'='*60}")
        print(f"回测股票: {stock_symbol} ({stock_name})")
    #stock_symbol = 'sh600519'
    # 2026/9/16 修改开始时间 增长回测周期
        start_date = '2020-01-01'
        end_date = '2026-01-01'

        stock_data = get_stock_data(stock_symbol, start_date, end_date)
        signal_data = gen_trade_signal(stock_data, short_window=5, long_window=20)
        plot_trade_signals(signal_data, stock_symbol, stock_name, short_window=5, long_window=20)

        # ---- 单次回测（保留原有功能）----
        perf = run_backtest(stock_data, short_window=5, long_window=20)
        #print(f"\n{'='*60}")
        print(f"单次回测：MA5/MA20")
        print(f"累计收益率: {perf['cumulative_return']*100:.2f}%")
        print(f"夏普比率: {perf['sharpe_ratio']:.2f}")
        print(f"最大回撤: {perf['max_drawdown']*100:.2f}%")
        print(f"胜率: {perf['win_rate']*100:.2f}%")
        print(f"盈亏比: {perf['profit_factor']:.2f}")

        # ---- 参数对比 ----
        param_pairs = [(5, 20), (10, 30), (5, 60), (10, 60), (20, 60)]
        comparison = compare_params(stock_data, param_pairs)

        print(f"\n{'='*60}")
        print("参数对比结果：")
        print(comparison.to_string(index=False))

        # 保存对比表到 CSV（方便贴到 README）
        comparison.to_csv(f'param_comparison_{stock_symbol}_{stock_name}.csv', index=False, encoding='utf-8-sig')
        print(f"\n对比表已保存至 param_comparison_{stock_symbol}_{stock_name}.csv")

        # 基准版
        perf_base = run_backtest(stock_data, 5, 20)
        print(f"基准版\n收益={perf_base['cumulative_return']*100:.2f}%, "
                f"夏普={perf_base['sharpe_ratio']:.2f}, "
                f"回撤={perf_base['max_drawdown']*100:.2f}%, "
                f"交易次数={perf_base['total_trades']}")
    
        # 仅 ADX
        perf_adx = run_backtest(stock_data, 5, 20, use_adx_filter=True, adx_threshold=25)

        # 仅 RSI
        perf_rsi = run_backtest(stock_data, 5, 20, use_rsi_filter=True, low_rsi_threshold=50, high_rsi_threshold=70)

        # 仅成交量
        perf_vol = run_backtest(stock_data, 5, 20, use_volume_filter=True)

        # 打印对比
        for name, perf in [('基准', perf_base), ('仅ADX', perf_adx), 
                        ('仅RSI', perf_rsi), ('仅成交量', perf_vol)]:
            print(f"{name}: 收益={perf['cumulative_return']*100:.2f}%, "
                f"夏普={perf['sharpe_ratio']:.2f}, "
                f"回撤={perf['max_drawdown']*100:.2f}%, "
                f"交易次数={perf['total_trades']}\n")
        print(f"{'='*60}\n")
    # 增加多组RSI参数进行对比
    # for rsi_th in [60, 65, 70, 75, 80]:
    #     perf = run_backtest(stock_data, 5, 20, use_rsi_filter=True, high_rsi_threshold=rsi_th)
    #     print(f"HIGH_RSI<{rsi_th}: 收益={perf['cumulative_return']*100:.2f}%, "
    #         f"夏普={perf['sharpe_ratio']:.2f}, 交易次数={perf['total_trades']}")

    # 增加多组股票进行回测 看不同股票对RSI60过滤的敏感度
    # stock_list = ['sh600519', 'sz000858', 'sh600036', 'sz002594']
    # for symbol in stock_list:
    #     stock_data = get_stock_data(symbol, '2020-01-01', '2026-01-01')
    #     perf_base = run_backtest(stock_data, 5, 20)
    #     perf_rsi = run_backtest(stock_data, 5, 20, use_rsi_filter=True, high_rsi_threshold=60)
    #     print(f"{symbol}: 基准={perf_base['cumulative_return']*100:.2f}%, "
    #         f"RSI<60={perf_rsi['cumulative_return']*100:.2f}%, "
    #         f"交易次数={perf_rsi['total_trades']}")


if __name__ == "__main__":
    main()