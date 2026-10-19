import sqlite3
import pandas as pd

conn = sqlite3.connect('stock_data.db')

# 1. 每只股票的信号数量
signal_cnt = pd.read_sql_query('''
    SELECT symbol, signal, COUNT(*) AS cnt
    FROM ads_golden_cross_signal
    GROUP BY symbol, signal
    ORDER BY symbol, signal
''', conn)
print("各股票金叉/死叉数量：")
print(signal_cnt.to_string(index=False))

# 2. 金叉死叉是否交替出现（不应该连续两个金叉）
signal_seq = pd.read_sql_query('''
    SELECT symbol, trade_date, signal
    FROM ads_golden_cross_signal
    WHERE symbol = 'sh600519'
    ORDER BY trade_date
    LIMIT 10
''', conn)
print("\n茅台前 10 个信号：")
print(signal_seq.to_string(index=False))

# 3. 区间统计
summary = pd.read_sql_query('SELECT * FROM ads_stock_summary', conn)
print("\n股票区间统计：")
print(summary.to_string(index=False))

conn.close()