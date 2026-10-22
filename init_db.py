# -*- coding: utf-8 -*-
"""
init_db.py —— 漏斗分析项目 · 数据库初始化脚本

作用：
    1. 创建数据库（若不存在）
    2. 建表 user_behavior
    3. 把 user_behavior.csv 导入该表
    4. 校验导入行数

用法：
    1) 先把 .env.example 复制为 .env，填好 MySQL 账号密码
       Windows:  copy .env.example .env
       其他:     cp .env.example .env
    2) 运行：
       python init_db.py
       指定 CSV：  python init_db.py --csv "D:/path/to/user_behavior.csv"
       重建重导：  python init_db.py --drop

说明：
    - 本脚本只负责「把原始数据落库」，不做业务清洗。
      年龄 >90 的异常值、sex/device/os/source 里的 '0'（未填写）
      统一在 notebook 的视图 v_behavior 里处理，保持"原始层 / 清洗层"分离。
    - 重复运行是安全的（幂等）：表已存在则跳过建表，数据会先清空再导入。
"""

import os
import csv
import sys
import argparse
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# ----------------------------------------------------------------------
# 1. 读取 .env 配置
# ----------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST", "127.0.0.1").split(":")[0]
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME_FUNNEL", "funnel_analysis")

if not DB_USER or not DB_PASSWORD:
    sys.exit("未读取到 DB_USER / DB_PASSWORD，请先复制 .env.example 为 .env 并填写。")

# 默认 CSV：优先用项目内 data/ 目录，找不到再退回本机路径
_CSV_CANDIDATES = [
    BASE_DIR / "data" / "user_behavior.csv",
    Path(r"D:\hhh\桌面\数据集\用户行为分析\user_behavior.csv"),
]
DEFAULT_CSV = next((p for p in _CSV_CANDIDATES if p.exists()), _CSV_CANDIDATES[0])

# ----------------------------------------------------------------------
# 2. SQL 模板
# ----------------------------------------------------------------------
CREATE_DB_SQL = (
    f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
    f"DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS `user_behavior` (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    new_user            TINYINT      COMMENT '是否新用户：1=新客, 0=老客',
    age                 SMALLINT     COMMENT '年龄（原始层，含少量>90异常值）',
    sex                 VARCHAR(10)  COMMENT '性别：Female / Male / 0(未填写)',
    market              SMALLINT     COMMENT '市场级别 1-4',
    device              VARCHAR(20)  COMMENT '设备：mobile / desktop / 0',
    operative_system    VARCHAR(20)  COMMENT '操作系统：windows/iOS/android/mac/other/linux/0',
    source              VARCHAR(20)  COMMENT '来源：Direct / Seo / Ads / 0',
    total_pages_visited INT          COMMENT '浏览页面总数',
    home_page           TINYINT      COMMENT '是否到达首页',
    listing_page        TINYINT      COMMENT '是否到达列表页',
    product_page        TINYINT      COMMENT '是否到达商品页',
    payment_page        TINYINT      COMMENT '是否到达支付页',
    confirmation_page   TINYINT      COMMENT '是否到达确认页（成交）',
    KEY idx_new_user (new_user),
    KEY idx_source (source)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

COLS = [
    "new_user", "age", "sex", "market", "device", "operative_system", "source",
    "total_pages_visited", "home_page", "listing_page", "product_page",
    "payment_page", "confirmation_page",
]
STR_COLS = {"sex", "device", "operative_system", "source"}

INSERT_SQL = (
    "INSERT INTO `user_behavior` ("
    + ", ".join(f"`{c}`" for c in COLS)
    + ") VALUES ("
    + ", ".join(f":{c}" for c in COLS)
    + ")"
)

EXPECTED_ROWS = 100000  # 数据集总行数，用于导入后校验


# ----------------------------------------------------------------------
# 3. 工具函数
# ----------------------------------------------------------------------
def make_engine(with_db=True):
    """建 SQLAlchemy engine；with_db=False 用于建库前连到服务器。"""
    pwd = quote_plus(DB_PASSWORD or "")
    tail = DB_NAME if with_db else ""
    url = (
        f"mysql+pymysql://{DB_USER}:{pwd}@{DB_HOST}:{DB_PORT}/"
        f"{tail}?charset=utf8mb4"
    )
    return create_engine(url, pool_pre_ping=True)


def to_value(col, raw):
    """CSV 字符串 -> 数据库值：整数字段转 int，字符串字段保留，空值转 None。"""
    v = (raw or "").strip()
    if v == "" or v.lower() in ("nan", "none", "null"):
        return None
    if col in STR_COLS:
        return v
    return int(float(v))


def load_rows(csv_path):
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in COLS if c not in reader.fieldnames]
        if missing:
            sys.exit(f"CSV 缺少字段：{missing}")
        return [{c: to_value(c, r[c]) for c in COLS} for r in reader]


# ----------------------------------------------------------------------
# 4. 主流程
# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="初始化漏斗分析数据库")
    ap.add_argument("--csv", default=str(DEFAULT_CSV), help="user_behavior.csv 路径")
    ap.add_argument("--drop", action="store_true", help="先删除 user_behavior 表再重建")
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        sys.exit(f"找不到 CSV 文件：{csv_path}\n可用 --csv 指定路径。")

    print(f"连接：{DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}")

    # --- 建库 ---
    eng0 = make_engine(with_db=False)
    with eng0.begin() as conn:
        conn.execute(text(CREATE_DB_SQL))
    eng0.dispose()
    print(f"[1/4] 数据库 `{DB_NAME}` 就绪")

    eng = make_engine()

    # --- 建表 ---
    with eng.begin() as conn:
        if args.drop:
            conn.execute(text("DROP TABLE IF EXISTS `user_behavior`"))
        conn.execute(text(CREATE_TABLE_SQL))
    print("[2/4] 表 `user_behavior` 就绪")

    # --- 读 CSV ---
    rows = load_rows(csv_path)
    print(f"[3/4] 读取 CSV 完成：{len(rows)} 行")

    # --- 清空旧数据（如有）---
    with eng.begin() as conn:
        existed = conn.execute(text("SELECT COUNT(*) FROM `user_behavior`")).scalar()
    if existed:
        print(f"      表内已有 {existed} 行，先清空再导入")
        with eng.begin() as conn:
            conn.execute(text("TRUNCATE TABLE `user_behavior`"))

    # --- 批量写入 ---
    with eng.begin() as conn:
        conn.execute(text(INSERT_SQL), rows)

    # --- 校验 ---
    with eng.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM `user_behavior`")).scalar()
        deal = conn.execute(
            text("SELECT SUM(confirmation_page) FROM `user_behavior`")
        ).scalar()
    print(f"[4/4] 导入完成：{n} 行，其中成交（confirmation_page=1）{deal} 人")

    if len(rows) != EXPECTED_ROWS:
        print(f"提示：本次 CSV 共 {len(rows)} 行，与预期的 {EXPECTED_ROWS} 行不同，请确认数据版本。")
    if n != len(rows):
        print(f"警告：库内行数 {n} 与 CSV 行数 {len(rows)} 不一致！")
    else:
        print("校验通过。接下来打开 funnel_analysis.ipynb，从上到下运行即可。")

    eng.dispose()


if __name__ == "__main__":
    main()
