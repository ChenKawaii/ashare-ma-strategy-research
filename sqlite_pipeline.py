import sqlite3
import akshare as ak
import pandas as pd

DB_PATH = 'stock_data.db'

# ============================================================
# 1. 建表 + 写入数据
# ============================================================
def init_db():
    """创建数据库和表结构"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_price (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            UNIQUE(symbol, trade_date)
        )
    ''')
    conn.commit()
    return conn


def fetch_and_store(symbols, start_date, end_date):
    """用 akshare 拉取数据，写入 SQLite"""
    conn = init_db()
    cursor = conn.cursor()

    for symbol in symbols:
        print(f"正在拉取 {symbol} ...")
        symbol = symbol.lower()
        df = ak.stock_zh_a_daily(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date
        )

        # 逐行写入，用 INSERT OR IGNORE 避免重复
        for _, row in df.iterrows():
            cursor.execute('''
                INSERT OR IGNORE INTO daily_price
                (symbol, trade_date, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                symbol,
                str(row['date']),
                float(row['open']),
                float(row['high']),
                float(row['low']),
                float(row['close']),
                float(row['volume'])
            ))

        conn.commit()
        print(f"  {symbol} 写入完成，共 {len(df)} 条")

    conn.close()


# ============================================================
# 2. SQL 查询：窗口函数算均线 + LAG 标记金叉死叉
# ============================================================
def query_ma_and_signal(conn, symbol, short=5, long=20):
    """
    用 SQL 窗口函数计算均线，用 LAG 标记金叉/死叉。

    核心 SQL 逻辑：
    1. AVG(close) OVER (PARTITION BY symbol ORDER BY trade_date
                        ROWS BETWEEN N-1 PRECEDING AND CURRENT ROW)
       → 计算 N 日均线
    2. LAG(ma_short) / LAG(ma_long)
       → 取前一天的值，判断交叉
    3. CASE WHEN 昨天 short<=long 且今天 short>long THEN '金叉'
    """
    sql = f'''
    WITH ma_calc AS (
        SELECT
            symbol,
            trade_date,
            close,
            AVG(close) OVER (
                PARTITION BY symbol
                ORDER BY trade_date
                ROWS BETWEEN {short - 1} PRECEDING AND CURRENT ROW
            ) AS ma_short,
            AVG(close) OVER (
                PARTITION BY symbol
                ORDER BY trade_date
                ROWS BETWEEN {long - 1} PRECEDING AND CURRENT ROW
            ) AS ma_long
        FROM daily_price
        WHERE symbol = ?
    ),
    signal_calc AS (
        SELECT
            symbol,
            trade_date,
            close,
            ma_short,
            ma_long,
            LAG(ma_short) OVER (PARTITION BY symbol ORDER BY trade_date) AS prev_short,
            LAG(ma_long)  OVER (PARTITION BY symbol ORDER BY trade_date) AS prev_long
        FROM ma_calc
    )
    SELECT
        symbol,
        trade_date,
        close,
        ROUND(ma_short, 2) AS ma_short,
        ROUND(ma_long, 2)  AS ma_long,
        CASE
            WHEN prev_short IS NULL OR prev_long IS NULL THEN NULL
            WHEN prev_short <= prev_long AND ma_short > ma_long THEN '金叉'
            WHEN prev_short >= prev_long AND ma_short < ma_long THEN '死叉'
            ELSE NULL
        END AS signal
    FROM signal_calc
    ORDER BY trade_date
    '''
    return pd.read_sql_query(sql, conn, params=(symbol,))


# ============================================================
# 3. SQL 查询：GROUP BY 统计交易天数
# ============================================================
def query_trading_days(conn):
    """用 GROUP BY 统计每只股票的交易天数、价格区间"""
    sql = '''
    SELECT
        symbol,
        COUNT(*)                    AS trading_days,
        MIN(trade_date)             AS first_date,
        MAX(trade_date)             AS last_date,
        ROUND(MIN(low), 2)          AS min_price,
        ROUND(MAX(high), 2)         AS max_price,
        ROUND(AVG(close), 2)        AS avg_close
    FROM daily_price
    GROUP BY symbol
    ORDER BY trading_days DESC
    '''
    return pd.read_sql_query(sql, conn)


# ============================================================
# 4. 主程序
# ============================================================
def main():
    symbols = ['sh600519', 'sz000858', 'sh600036', 'sz002594']
    start_date = '2023-01-01'
    end_date = '2026-01-01'

    # 写入数据
    fetch_and_store(symbols, start_date, end_date)

    conn = sqlite3.connect(DB_PATH)

    # 查询1：均线 + 金叉死叉信号
    print("\n" + "=" * 60)
    print("【SQL 窗口函数】MA5/MA20 + 金叉死叉信号（茅台）")
    result = query_ma_and_signal(conn, 'sh600519', short=5, long=20)
    signals = result[result['signal'].notna()]
    print(signals.to_string(index=False))

    # 查询2：GROUP BY 统计交易天数
    print("\n" + "=" * 60)
    print("【SQL GROUP BY】各股票交易天数统计")
    days = query_trading_days(conn)
    print(days.to_string(index=False))

    conn.close()


if __name__ == '__main__':
    main()