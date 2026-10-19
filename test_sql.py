import sqlite3
import pandas as pd

conn = sqlite3.connect('stock_data.db')

# 每只股票的有效记录数
df = pd.read_sql_query('''
    SELECT symbol,
           COUNT(*) AS cnt,
           MIN(trade_date) AS first_date,
           MAX(trade_date) AS last_date,
           MIN(etl_time) AS earliest_etl,
           MAX(etl_time) AS latest_etl
    FROM dwd_daily_price
    GROUP BY symbol
''', conn)
print(df.to_string(index=False))

# 确认没有重复
dup = pd.read_sql_query('''
    SELECT symbol, trade_date, COUNT(*) AS cnt
    FROM dwd_daily_price
    GROUP BY symbol, trade_date
    HAVING cnt > 1
''', conn)
print(f"\n重复记录数: {len(dup)}")

conn.close()