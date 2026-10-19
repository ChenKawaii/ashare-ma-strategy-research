import os

# 清除可能存在的代理环境变量
for key in list(os.environ.keys()):
    if key.lower() in ('http_proxy', 'https_proxy', 'all_proxy'):
        del os.environ[key]

# 强制所有请求不走代理
os.environ['NO_PROXY'] = '*'

import sqlite3
import akshare as ak
import pandas as pd
from datetime import datetime

DB_PATH = 'stock_data.db'

# ============================================================
# ODS 层：原样存储 akshare 拉取的原始数据
# ODS (Operational Data Store，操作数据存储层) 是数据仓库的一个层次，主要用于存储从各种数据源抽取的原始数据，通常不进行复杂的清洗和转换。
# ODS 层的数据结构通常与源系统的数据结构相似，便于追溯和审计。
# ============================================================

def create_ods_table(conn):
    """
    创建 ODS 层的表，用于存储原始数据，结构接近 akshare 拉取的数据。
    """
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ods_daily_price (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,          -- 股票代码，如 sh600519
            trade_date TEXT NOT NULL,      -- 交易日期
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            etl_time TEXT NOT NULL,        -- 抽取时间，用于追溯
            UNIQUE(symbol, trade_date, etl_time)
        )
    ''')
    conn.commit()


def extract_to_ods(symbols, start_date, end_date):
    """
    E(xtract) 从 akshare 拉取指定股票的日线行情数据，并存储到 ODS 层。
    L(oad) 将数据存储到 SQLite 数据库的 ODS 表中。
    param symbols: 股票代码列表，如 ['sh600519', 'sz000001']
    param start_date: 开始日期，格式 'YYYY-MM-DD'
    param end_date: 结束日期，格式 'YYYY-MM-DD'
    """
    conn = sqlite3.connect(DB_PATH)
    create_ods_table(conn)
    cursor = conn.cursor()

    etl_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    for symbol in symbols:
        print(f"[ODS] 正在抽取 {symbol} ...")

        # 用 stock_zh_a_hist（东方财富源），列名稳定
        df = ak.stock_zh_a_hist(
            symbol=symbol.replace('sh', '').replace('sz', ''),
            period="daily",
            start_date=start_date.replace('-', ''),
            end_date=end_date.replace('-', ''),
            adjust="qfq"
        )

        # 统一列名（中文 → 英文），这是 ODS 层唯一做的“映射”，不算清洗
        df = df.rename(columns={
            '日期': 'trade_date',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume'
        })

        inserted = 0
        for _, row in df.iterrows():
            try:
                cursor.execute('''
                    INSERT OR IGNORE INTO ods_daily_price
                    (symbol, trade_date, open, high, low, close, volume, etl_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    symbol,
                    str(row['trade_date']),
                    float(row['open']),
                    float(row['high']),
                    float(row['low']),
                    float(row['close']),
                    float(row['volume']),
                    etl_time
                ))
                inserted += 1
            except Exception as e:
                print(f"  [ODS] 写入异常（保留待清洗）: {e}")

        conn.commit()
        print(f"  [ODS] {symbol} 写入 {inserted} 条，etl_time={etl_time}")

    conn.close()

# ============================================================
# 主程序
# ============================================================
def main():
    symbols = ['sh600519', 'sz000858', 'sh600036', 'sz002594']
    start_date = '2023-01-01'
    end_date = '2026-01-01'

    extract_to_ods(symbols, start_date, end_date)


if __name__ == '__main__':
    main()