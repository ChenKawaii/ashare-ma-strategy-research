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
            UNIQUE(symbol, trade_date)
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

        # 用新浪源，避开东财的风控
        df = ak.stock_zh_a_daily(
            symbol=symbol,  # 新浪接口需要带 sh/sz 前缀
            start_date=start_date.replace('-', ''),
            end_date=end_date.replace('-', '')
        )

        # stock_zh_a_daily 的日期列名是 'date'，不是 'trade_date'
        df = df.rename(columns={
            'date': 'trade_date',
            'open': 'open',
            'close': 'close',
            'high': 'high',
            'low': 'low',
            'volume': 'volume'
        })

        records = [
            (
                symbol,
                str(row['trade_date']),
                float(row['open']),
                float(row['high']),
                float(row['low']),
                float(row['close']),
                float(row['volume']),
                etl_time
            )
            for _, row in df.iterrows()
        ]

        try:
            cursor.executemany('''
                INSERT OR IGNORE INTO ods_daily_price
                (symbol, trade_date, open, high, low, close, volume, etl_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', records)
            inserted = cursor.rowcount
        except Exception as e:
            print(f"  [ODS] 批量写入异常: {e}")
            inserted = 0
        conn.commit()
        print(f"  [ODS] {symbol} 写入 {inserted} 条，etl_time={etl_time}")

    conn.close()

def build_dwd():
    """
    DWD(Data Warehouse Detail) 层：对 ODS 层数据进行清洗、转换，形成结构化的明细数据。
    1. 去重：同一 symbol + trade_date 只保留最新 etl_time 的记录
    2. 标记异常：价格 <= 0、high < low、volume < 0
    3. 只写入有效记录
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 删除旧表，保证每次都是全量重建 DWD 层
    cursor.execute('DROP TABLE IF EXISTS dwd_daily_price')
    # 创建新表
    cursor.execute('''
        CREATE TABLE dwd_daily_price AS
        WITH dedup AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY symbol, trade_date
                       ORDER BY etl_time DESC
                   ) AS rn
            FROM ods_daily_price
        ),
        flagged AS (
            SELECT *,
                   CASE
                       WHEN close <= 0 OR open <= 0 OR high <= 0 OR low <= 0 THEN 1
                       WHEN high < low THEN 1
                       WHEN volume < 0 THEN 1
                       ELSE 0
                   END AS is_invalid
            FROM dedup
            WHERE rn = 1
        )
        SELECT symbol, trade_date, open, high, low, close, volume, etl_time
        FROM flagged
        WHERE is_invalid = 0
    ''')

    conn.commit()

    # 打印 DWD 层的记录数
    cursor.execute('SELECT COUNT(*) FROM ods_daily_price')
    ods_cnt = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM dwd_daily_price')
    dwd_cnt = cursor.fetchone()[0]

    print(f"[DWD] ODS 原始记录: {ods_cnt} 条")
    print(f"[DWD] DWD 有效记录: {dwd_cnt} 条")
    print(f"[DWD] 被过滤/去重: {ods_cnt - dwd_cnt} 条")

    conn.close()

def build_dws():
    """
    DWS(Data Warehouse Summary) 层：对 DWD 层数据进行汇总、计算指标，形成分析所需的汇总数据。
    1. 计算每只股票的日均线（MA）
    2. 标记金叉/死叉信号
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 删除旧表，保证每次都是全量重建 DWS 层
    cursor.execute('DROP TABLE IF EXISTS dws_stock_daily_summary')
    # 创建新表
    cursor.execute('''
        CREATE TABLE dws_stock_daily_summary AS
        WITH base AS (
            SELECT symbol, trade_date, close, high, low, volume
            FROM dwd_daily_price
        ),
        ma_calc AS (
            SELECT
                symbol,
                trade_date,
                close,
                high,
                low,
                volume,
                AVG(close) OVER (
                    PARTITION BY symbol
                    ORDER BY trade_date
                    ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
                ) AS ma5,
                AVG(close) OVER (
                    PARTITION BY symbol
                    ORDER BY trade_date
                    ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                ) AS ma20,
                AVG(volume) OVER (
                    PARTITION BY symbol
                    ORDER BY trade_date
                    ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                ) AS vol_ma20
            FROM base
        )
        SELECT
            symbol,
            trade_date,
            close,
            ROUND(ma5, 2) AS ma5,
            ROUND(ma20, 2) AS ma20,
            ROUND(
                CASE WHEN close > 0 THEN (high - low) / close ELSE NULL END,
                4
            ) AS daily_volatility,
            ROUND(volume / vol_ma20, 4) AS volume_ratio
        FROM ma_calc
    ''')

    conn.commit()

    cursor.execute('SELECT COUNT(*) FROM dwd_daily_price')
    dwd_cnt = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM dws_stock_daily_summary')
    dws_cnt = cursor.fetchone()[0]

    print(f"[DWS] DWD 明细记录: {dwd_cnt} 条")
    print(f"[DWS] DWS 汇总记录: {dws_cnt} 条")

    conn.close()

def build_ads():
    """
    ADS(Application Data Store) 层：对 DWS 层数据进行进一步汇总、计算指标，形成应用所需的分析数据。
    1. ads_golden_cross_signal：金叉死叉信号
    2. ads_stock_summary：每只股票的区间统计
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # ---- ADS 表1：金叉死叉信号 ----
    cursor.execute('DROP TABLE IF EXISTS ads_golden_cross_signal')
    cursor.execute('''
        CREATE TABLE ads_golden_cross_signal AS
        WITH signal_calc AS (
            SELECT
                symbol,
                trade_date,
                close,
                ma5,
                ma20,
                LAG(ma5) OVER (PARTITION BY symbol ORDER BY trade_date) AS prev_ma5,
                LAG(ma20) OVER (PARTITION BY symbol ORDER BY trade_date) AS prev_ma20
            FROM dws_stock_daily_summary
        )
        SELECT
            symbol,
            trade_date,
            close,
            ma5,
            ma20,
            CASE
                WHEN prev_ma5 IS NULL OR prev_ma20 IS NULL THEN NULL
                WHEN prev_ma5 <= prev_ma20 AND ma5 > ma20 THEN '金叉'
                WHEN prev_ma5 >= prev_ma20 AND ma5 < ma20 THEN '死叉'
                ELSE NULL
            END AS signal
        FROM signal_calc
        WHERE signal IS NOT NULL
    ''')

    # ---- ADS 表2：每只股票的区间统计 ----
    cursor.execute('DROP TABLE IF EXISTS ads_stock_summary')
    cursor.execute('''
        CREATE TABLE ads_stock_summary AS
        SELECT
            symbol,
            COUNT(*) AS trading_days,
            MIN(trade_date) AS first_date,
            MAX(trade_date) AS last_date,
            ROUND(MIN(close), 2) AS min_close,
            ROUND(MAX(close), 2) AS max_close,
            ROUND(AVG(close), 2) AS avg_close,
            ROUND((MAX(close) - MIN(close)) / MIN(close) * 100, 2) AS range_pct
        FROM dws_stock_daily_summary
        GROUP BY symbol
    ''')

    conn.commit()

    cursor.execute('SELECT COUNT(*) FROM ads_golden_cross_signal')
    signal_cnt = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM ads_stock_summary')
    summary_cnt = cursor.fetchone()[0]

    print(f"[ADS] 金叉死叉信号记录: {signal_cnt} 条")
    print(f"[ADS] 股票区间统计记录: {summary_cnt} 条")

    conn.close()

def validate():
    """
    验证 ADS 层的数据，确保金叉死叉信号和区间统计的正确性。
    """

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

# ============================================================
# 主程序
# ============================================================
def main():
    # 如果数据库已存在，先删掉（仅用于开发阶段）
    # if os.path.exists(DB_PATH):
    #     os.remove(DB_PATH)
    symbols = ['sh600519', 'sz000858', 'sh600036', 'sz002594']
    start_date = '2023-01-01'
    end_date = '2026-01-01'

    # ODS 层：抽取 + 加载
    extract_to_ods(symbols, start_date, end_date)

    # DWD 层：清洗
    build_dwd()

    # DWS 层：轻度汇总
    build_dws()

    # ADS 层：面向分析的最终结果
    build_ads()

    # 验证 ADS 层的数据
    validate()

if __name__ == '__main__':
    main()