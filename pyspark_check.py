from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.window import Window
import os

hadoop_home = r"C:\Users\admin\Desktop\czy_proj\py\aks\hadoop"
os.environ["HADOOP_HOME"] = hadoop_home
os.environ["hadoop.home.dir"] = hadoop_home
os.environ["PATH"] = os.path.join(hadoop_home, "bin") + os.pathsep + os.environ["PATH"]
os.environ["JAVA_TOOL_OPTIONS"] = f"-Djava.library.path={hadoop_home}\\bin"
#1.创建session
jar_path = r'C:\Users\admin\Desktop\czy_proj\py\aks\jdbc-jar\sqlite-jdbc-3.53.4.0.jar'
spark = SparkSession.builder \
    .appName("StockDWS") \
    .master("local[*]") \
    .config("spark.jars", jar_path) \
    .config("spark.driver.extraClassPath", jar_path) \
    .getOrCreate()

#2.从sqlite读取dwd_daily_price
#方式一：先用pandas读取sqlite，再转为spark dataframe
import pandas as pd
import sqlite3

# conn = sqlite3.connect('stock_data.db')
# pdf = pd.read_sql_query("SELECT * FROM dwd_daily_price", conn)
# conn.close()

# df = spark.createDataFrame(pdf)

# 方式二：用 JDBC 直接读（需要下载 sqlite-jdbc 驱动，更“工程化”）
df = spark.read.format("jdbc").option("url", "jdbc:sqlite:stock_data.db") \
    .option("dbtable", "dwd_daily_price").option("driver", "org.sqlite.JDBC").load()


# 3. 用窗口函数算 MA5、MA20（和 SQLite 的逻辑完全对应）
window_5 = Window.partitionBy("symbol").orderBy("trade_date").rowsBetween(-4, 0)
window_20 = Window.partitionBy("symbol").orderBy("trade_date").rowsBetween(-19, 0)

df_dws = df.withColumn("ma5", F.round(F.avg("close").over(window_5), 2)) \
           .withColumn("ma20", F.round(F.avg("close").over(window_20), 2)) \
           .withColumn("vol_ma20", F.avg("volume").over(window_20)) \
           .withColumn("daily_volatility", 
                       F.round((F.col("high") - F.col("low")) / F.col("close"), 4)) \
           .withColumn("volume_ratio", F.round(F.col("volume") / F.col("vol_ma20"), 4))

# 4. 查看结果
df_dws.select("symbol", "trade_date", "close", "ma5", "ma20", "daily_volatility").show(10)

# 5. 存成 Parquet（Spark 的列式存储格式，比 CSV 快）
df_dws.write.mode("overwrite").parquet("dws_stock_daily_summary_spark")

# spark.stop()

import pandas as pd

# Spark 输出
spark_df = pd.read_parquet("dws_stock_daily_summary_spark")
spark_df = spark_df.sort_values(['symbol', 'trade_date'])

# SQLite 结果
import sqlite3
conn = sqlite3.connect('stock_data.db')
sqlite_df = pd.read_sql_query(
    "SELECT symbol, trade_date, ma5, ma20 FROM dws_stock_daily_summary",
    conn
)
conn.close()

# 对比
merged = spark_df.merge(sqlite_df, on=['symbol', 'trade_date'], suffixes=('_spark', '_sqlite'))
diff = merged[(merged['ma5_spark'] - merged['ma5_sqlite']).abs() > 0.01]
print(f"MA5 不一致的记录数: {len(diff)}")
print(f"总记录数: {len(merged)}")