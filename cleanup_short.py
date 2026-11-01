#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一次性清理脚本：回填 B站记录的真实视频时长，并删除时长低于 3 分钟(180秒)的选题。

背景：tutorial_items 表此前未存储 duration，仅抓取时在内存里按 3 分钟规则过滤，
导致历史 B站记录无法按时长筛选。本脚本：
  1. 给所有 src='B站' 且 duration 为 NULL 的记录，用 bvid 回查 B站 view 接口拿时长并写回；
  2. 删除 duration 已知且 < 180 秒的记录（抓取失败=未知时长者不删，防误删）。
非 B站来源（文章/仓库）无 duration，不受影响。

用法：python3 cleanup_short.py
依赖：requests、pymysql，以及 DB_* 环境变量（同 db_store.py）
"""
import os
import re
import time
import json
import hashlib
import urllib.parse
import requests
import pymysql

DB_HOST = os.environ.get('DB_HOST', 'mysql6.sqlpub.com')
DB_PORT = int(os.environ.get('DB_PORT', '3311'))
DB_USER = os.environ.get('DB_USER', 'yy_aggregator')
DB_PASS = os.environ.get('DB_PASS', '')
DB_NAME = os.environ.get('DB_NAME', 'yy_aggregator')

H = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0 Safari/537.36',
    'Referer': 'https://www.bilibili.com',
}
WBI_TAB = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43,
           5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16,
           24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59,
           6, 63, 57, 62, 11, 36, 20, 34, 44, 52]

# 低于该时长(秒)的视频教程予以删除 / 不抓取（3 分钟 = 180 秒）
SHORT_THRESHOLD = 180
# BACKFILL=0 时跳过"回查时长"步骤，仅删除已确认低于阈值的记录（快速清理用）
BACKFILL = int(os.environ.get('BACKFILL', '1'))


def get_wbi_mixin():
    nav = SESSION.get('https://api.bilibili.com/x/web-interface/nav',
                      headers=H, timeout=15).json()['data']['wbi_img']
    img = re.search(r'([0-9a-f]+)\.png', nav['img_url']).group(1)
    sub = re.search(r'([0-9a-f]+)\.png', nav['sub_url']).group(1)
    return ''.join((img + sub)[i] for i in WBI_TAB)[:32]


def fetch_duration(bvid):
    """回查单个视频时长(秒)，失败返回 None"""
    for attempt in range(3):
        try:
            params = {'bvid': bvid, 'wts': int(time.time())}
            params = {k: params[k] for k in sorted(params)}
            q = urllib.parse.urlencode(params)
            wrid = hashlib.md5((q + MIXIN).encode()).hexdigest()
            r = SESSION.get(
                f'https://api.bilibili.com/x/web-interface/view?{q}&w_rid={wrid}',
                headers=H, timeout=10)
            if r.status_code != 200:
                time.sleep(1.5)
                continue
            d = r.json().get('data') or {}
            dur = d.get('duration')
            if isinstance(dur, int):
                return dur
            return None
        except Exception:
            time.sleep(1.5)
    return None


def main():
    global SESSION, MIXIN
    conn = pymysql.connect(host=DB_HOST, port=DB_PORT, user=DB_USER,
                           password=DB_PASS, database=DB_NAME,
                           charset='utf8mb4', connect_timeout=15)
    try:
        cur = conn.cursor()

        # —— 步骤一：回填时长（B站 view 接口对服务器 IP 易被限流，可按需关闭）——
        if BACKFILL:
            SESSION = requests.Session()
            try:
                SESSION.get('https://api.bilibili.com/x/web-interface/nav', headers=H, timeout=15)
            except Exception:
                pass
            MIXIN = get_wbi_mixin()
            cur.execute("SELECT id, url FROM tutorial_items WHERE src='B站' AND duration IS NULL")
            rows = cur.fetchall()
            print(f'[清理] 待回填 B站记录: {len(rows)} 条')
            ok = skip = fail = 0
            for idx, (rid, url) in enumerate(rows, 1):
                m = re.search(r'(BV[0-9A-Za-z]+)', url or '')
                if not m:
                    skip += 1
                    continue
                dur = fetch_duration(m.group(1))
                if dur is None:
                    fail += 1  # 抓取失败，无法判定，保留
                else:
                    cur.execute("UPDATE tutorial_items SET duration=%s WHERE id=%s", (dur, rid))
                    ok += 1
                if idx % 20 == 0:
                    conn.commit()
                    print(f'  进度 {idx}/{len(rows)} (已写{ok} 失败{fail})')
                time.sleep(0.3)
            conn.commit()
            print(f'[回填完成] 成功写回 {ok} 条，跳过(无bvid){skip} 条，抓取失败保留 {fail} 条')
        else:
            print('[回填] BACKFILL=0，跳过回查时长步骤')

        # 统计将要删除的
        cur.execute("SELECT COUNT(*) FROM tutorial_items WHERE duration IS NOT NULL AND duration<%s", (SHORT_THRESHOLD,))
        to_del = cur.fetchone()[0]
        print(f'[待删除] duration<{SHORT_THRESHOLD}秒 共 {to_del} 条')

        if to_del > 0:
            cur.execute("SELECT id, title, duration, url FROM tutorial_items "
                        "WHERE duration IS NOT NULL AND duration<%s ORDER BY duration", (SHORT_THRESHOLD,))
            samples = cur.fetchall()
            print('  样例(前10):')
            for sid, title, dur, url in samples[:10]:
                print(f'    {dur:>4d}s | {title[:30]} | {url[:40]}')
            cur.execute("DELETE FROM tutorial_items WHERE duration IS NOT NULL AND duration<%s", (SHORT_THRESHOLD,))
            conn.commit()
            print(f'[已删除] 低于{SHORT_THRESHOLD//60}分钟的记录 {to_del} 条')

        # 最终统计
        cur.execute("SELECT COUNT(*), SUM(src='B站'), SUM(duration IS NOT NULL) FROM tutorial_items")
        total, bili, with_dur = cur.fetchone()
        print(f'[结果] 库内剩余 {total} 条 | B站 {bili} 条 | 已带回填时长 {with_dur} 条')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
