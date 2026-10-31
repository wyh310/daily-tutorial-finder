#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库存储模块：把每天抓取到的选题写入 MySQL，按 URL 去重（重复不保存）。

连接信息通过环境变量配置（不写死在代码里，方便部署到 GitHub Actions 用 Secrets）：
  DB_HOST  默认 mysql6.sqlpub.com
  DB_PORT  默认 3311
  DB_USER  默认 yy_aggregator
  DB_PASS  （必填，你的数据库密码）
  DB_NAME  默认 yy_aggregator

表 tutorial_items 设计：
  - url_hash CHAR(64) 唯一键（SHA256(url)），避免长链接/中文 URL 建唯一索引的长度问题
  - 用 INSERT IGNORE 实现"重复的不保存"
  - priority INT：本工具算出的"优先级评分"，分数越高越适合优先做成自己的内容（见 aggregator.score）
"""
import os
import hashlib
import pymysql

DB_HOST = os.environ.get('DB_HOST', 'mysql6.sqlpub.com')
DB_PORT = int(os.environ.get('DB_PORT', '3311'))
DB_USER = os.environ.get('DB_USER', 'yy_aggregator')
DB_PASS = os.environ.get('DB_PASS', '')
DB_NAME = os.environ.get('DB_NAME', 'yy_aggregator')


def get_conn():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS,
        database=DB_NAME, charset='utf8mb4', connect_timeout=15,
        autocommit=False,
    )


def init_db():
    """建表（首次运行时调用，已存在则跳过）"""
    sql = """
    CREATE TABLE IF NOT EXISTS tutorial_items (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        title       VARCHAR(500) NOT NULL,
        url         TEXT          NOT NULL,
        url_hash    CHAR(64)      NOT NULL,
        src         VARCHAR(50),
        category    VARCHAR(10),
        hot         VARCHAR(30),
        pub_date    DATE,
        description VARCHAR(600),
        priority    INT           NOT NULL DEFAULT 0,
        fetched_at  DATETIME      NOT NULL,
        UNIQUE KEY uniq_url (url_hash)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        # 兼容旧表：若已存在的表没有 priority 字段，补上（ADD COLUMN 失败=字段已存在，忽略）
        try:
            cur.execute(
                "ALTER TABLE tutorial_items ADD COLUMN priority INT NOT NULL DEFAULT 0")
        except Exception:
            pass
        # 兼容旧表：补 duration 字段（视频时长，单位秒；文章类源为 NULL）
        try:
            cur.execute(
                "ALTER TABLE tutorial_items ADD COLUMN duration INT DEFAULT NULL")
        except Exception:
            pass
        conn.commit()
        print('[DB] 表 tutorial_items 已就绪')
    finally:
        conn.close()


def get_existing_hashes():
    """返回库里已有的 url_hash 集合，用于生成简报前过滤"历史已推送"的重复内容"""
    conn = get_conn()
    hashes = set()
    try:
        cur = conn.cursor()
        cur.execute("SELECT url_hash FROM tutorial_items")
        for (h,) in cur.fetchall():
            hashes.add(h)
    except Exception as e:
        print('[DB] 查询已有 hash 失败:', e)
    finally:
        conn.close()
    return hashes


def save_items(items):
    """批量写入，按 url_hash 去重；返回 (新增条数, 跳过重复条数)"""
    if not items:
        return (0, 0)
    conn = get_conn()
    inserted = 0
    skipped = 0
    try:
        cur = conn.cursor()
        for it in items:
            url = it.get('url') or ''
            url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()
            title = (it.get('title') or '')[:500]
            desc = (it.get('desc') or '')[:600]
            hot = str(it.get('hot') or '')
            pub_date = it['date'].date() if it.get('date') else None
            pri = int(it.get('priority') or 0)
            dur = it.get('duration')
            try:
                cur.execute(
                    "INSERT IGNORE INTO tutorial_items "
                    "(title, url, url_hash, src, category, hot, pub_date, description, priority, duration, fetched_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())",
                    (title, url, url_hash, it.get('src'), it.get('cat'),
                     hot, pub_date, desc, pri, dur),
                )
                if cur.rowcount == 1:
                    inserted += 1
                else:
                    skipped += 1
            except Exception as e:
                print('[DB] 单条写入失败:', e)
        conn.commit()
    finally:
        conn.close()
    print(f'[DB] 写入完成：新增 {inserted} 条，跳过重复 {skipped} 条')
    return (inserted, skipped)
