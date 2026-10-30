#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日教程选题聚合器（19 个平台 + 最近更新优先）
数据源: OSChina, 博客园, CSDN, 简书, 思否, Gitee, 微信搜狗, StackOverflow, Medium,
        V2EX, 掘金, 少数派, B站, Hacker News, GitHub, Product Hunt, Dev.to, Lobsters, Reddit
筛选: 关键词 -> 仅保留各源"最近时效窗口"内的更新 -> 按源限量(防刷屏) -> 按时间倒序
用法: SERVERCHAN_KEY=xxx python aggregator.py   （不传 key 则仅本地生成 daily_report.md）
"""
import os
import time
import html
import hashlib
import urllib.parse
import datetime as dt
import xml.etree.ElementTree as ET
from collections import defaultdict
from email.utils import parsedate_to_datetime
import requests
from bs4 import BeautifulSoup
import db_store

# ---------------- 配置 ----------------
# 服务器/建站类关键词（命中 -> A 类，强引流雨云）
SERVER_KW = ['免费域名', '域名', 'vps', '虚拟主机', '主机', '建站', '网站', '服务器',
             '云服务器', 'cloudflare', '宝塔', '免备案', '对象存储', '图床', '博客',
             '部署', 'docker', 'self-host', 'selfhosted', 'hosting', 'server', 'dns', 'ssl',
             # 新增：网站/域名/网络
             '网站搭建', '静态网站', '个人网站', '独立站', '域名注册', 'dns解析', 'cdn',
             '加速', '防护', 'waf', '反向代理', '内网穿透', 'frp', 'ddns', '反代',
             # 新增：硬件/面板/云厂商
             'nas', '软路由', '1panel', 'aapanel', '云主机', '轻量应用服务器', '雨云',
             '腾讯云', '阿里云', '华为云', '节点', 'lnmp', 'lamp', 'wordpress', '面板',
             # 新增：容器/云原生/存储
             '容器', 'kubernetes', 'k8s', 'serverless', '云函数', '负载均衡', '云存储',
             '主机面板', '运维', 'linux服务器', '公网ip', '端口映射']
# 工具/AI 类关键词（命中 -> B 类，泛流量涨粉）
TOOL_KW = ['ai', '人工智能', '开源', '免费', '工具', 'open source', 'free tool',
           'ai tool', 'tutorial', '教程', 'productivity', '效率',
           # 新增：软件/资源推荐
           '神器', '软件推荐', '应用推荐', '开源项目', '白嫖', '免费软件', 'saas',
           '笔记软件', '截图', '录屏', '下载工具', '网盘', '自动化', '脚本工具',
           '浏览器插件', '代理', '加速器', '效率工具', '实用工具', '好用的',
           '资源分享', '干货', '合集', '轻量级', '跨平台', '自托管']

# 各数据源的"最近时效窗口"(天)。高频更新源窗口短，内容发布慢的源窗口长，避免无料可发。
SOURCE_MAX_AGE = {
    'HackerNews': 3, 'GitHub': 7, 'ProductHunt': 7, 'V2EX': 14,
    'Dev.to': 14, 'Lobsters': 14, 'Reddit': 7, 'B站': 30, '少数派': 30, '掘金': 21,
    # 新增源
    'OSChina': 7, '博客园': 7, 'CSDN': 14, '简书': 21, '思否': 14, 'Gitee': 30,
    '微信搜狗': 14, 'StackOverflow': 7, 'Medium': 14,
}
# 每个源最多展示多少条（核心：防止单个平台刷屏，保证多平台分布）
PER_SOURCE_LIMIT = 6

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0 Safari/537.36'
}
TIMEOUT = 15
NOW = dt.datetime.now()


def classify(text):
    t = text.lower()
    a = any(k.lower() in t for k in SERVER_KW)
    b = any(k.lower() in t for k in TOOL_KW)
    if a and b:
        return 'AB'
    if a:
        return 'A'
    if b:
        return 'B'
    return None


def ts_to_dt(ts):
    try:
        return dt.datetime.fromtimestamp(int(ts))
    except Exception:
        return None


def parse_pubdate(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
        if d is None:
            return None
        return d.replace(tzinfo=None)
    except Exception:
        return None


def parse_rss(content):
    out = []
    try:
        root = ET.fromstring(content)
        for it in root.iter('item'):
            title = (it.findtext('title') or '').strip()
            link = (it.findtext('link') or '').strip()
            desc = (it.findtext('description') or '').strip()
            pub = (it.findtext('pubDate') or '').strip()
            out.append((title, link, desc, pub))
    except Exception:
        pass
    return out


# ---------------- 各平台抓取 ----------------

def fetch_oschina():
    """开源中国：移动 API 返回 JSON，自带标题/链接/时间/摘要，零反爬"""
    items = []
    try:
        r = requests.get('https://www.oschina.net/action/apiv2/news?catalog=-1&pageToken=&page=1',
                         headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            print('[OSChina] 状态码', r.status_code)
            return items
        for n in r.json().get('result', {}).get('items', []):
            title = n.get('title', '')
            if not title:
                continue
            pub = n.get('pubDate', '')
            d = None
            if pub:
                try:
                    d = dt.datetime.strptime(pub, '%Y-%m-%d %H:%M:%S')
                except Exception:
                    d = None
            items.append({'title': title, 'url': n.get('href', ''),
                          'desc': (n.get('body') or '')[:160], 'src': 'OSChina',
                          'hot': str(n.get('commentCount', 0)),
                          'cat': classify(title + ' ' + (n.get('body') or '')), 'date': d})
    except Exception as e:
        print('[OSChina] 失败:', e)
    return items


def fetch_cnblogs():
    """博客园：RSS 实为 Atom 格式（<feed>/<entry>），自带标题/链接/更新时间"""
    items = []
    try:
        r = requests.get('https://www.cnblogs.com/rss', headers=HEADERS, timeout=TIMEOUT)
        NS = '{http://www.w3.org/2005/Atom}'
        root = ET.fromstring(r.content)
        for e in root.iter(NS + 'entry'):
            title = (e.findtext(NS + 'title') or '').strip()
            link = e.find(NS + 'link')
            href = link.get('href') if link is not None else ''
            updated = e.findtext(NS + 'updated') or ''
            d = None
            if updated:
                try:
                    d = dt.datetime.strptime(updated[:19], '%Y-%m-%dT%H:%M:%S')
                except Exception:
                    d = None
            items.append({'title': title, 'url': href, 'desc': '', 'src': '博客园',
                          'hot': '', 'cat': classify(title), 'date': d})
    except Exception as e:
        print('[博客园] 失败:', e)
    return items


def fetch_csdn():
    """CSDN：热榜接口被反爬(521)，改用首页推荐流（静态可见文章链接）"""
    items = []
    try:
        r = requests.get('https://www.csdn.net/', headers=HEADERS, timeout=TIMEOUT)
        soup = BeautifulSoup(r.text, 'html.parser')
        for a in soup.select('a[href*="/article/details/"]'):
            t = a.get_text(strip=True)
            if t and len(t) > 5:
                items.append({'title': t, 'url': a.get('href', ''), 'desc': '',
                              'src': 'CSDN', 'hot': '', 'cat': classify(t), 'date': None})
    except Exception as e:
        print('[CSDN] 失败:', e)
    return items


def fetch_jianshu():
    """简书：首页文章链接"""
    items = []
    try:
        r = requests.get('https://www.jianshu.com/', headers=HEADERS, timeout=TIMEOUT)
        soup = BeautifulSoup(r.text, 'html.parser')
        for a in soup.select('a[href*="/p/"]'):
            t = a.get_text(strip=True)
            if t and len(t) > 4:
                items.append({'title': t, 'url': 'https://www.jianshu.com' + a.get('href', ''),
                              'desc': '', 'src': '简书', 'hot': '', 'cat': classify(t), 'date': None})
    except Exception as e:
        print('[简书] 失败:', e)
    return items


def fetch_segmentfault():
    """思否 SegmentFault：首页技术文章（响应较慢，加一次重试）"""
    items = []
    for attempt in range(2):
        try:
            r = requests.get('https://segmentfault.com/', headers=HEADERS,
                             timeout=TIMEOUT + 5 * attempt)
            soup = BeautifulSoup(r.text, 'html.parser')
            for a in soup.select('a[href*="/a/"]'):
                t = a.get_text(strip=True)
                if t and len(t) > 4:
                    items.append({'title': t, 'url': 'https://segmentfault.com' + a.get('href', ''),
                                  'desc': '', 'src': '思否', 'hot': '', 'cat': classify(t), 'date': None})
            break
        except Exception as e:
            if attempt == 0:
                print('[思否] 超时，重试中...')
            else:
                print('[思否] 失败:', e)
    return items


def fetch_gitee():
    """Gitee：静态页仅含"热门项目"，最新列表为 SPA 抓不到，用热门项目顶替"""
    items = []
    try:
        r = requests.get('https://gitee.com/all?order=latest&page=1', headers=HEADERS, timeout=TIMEOUT)
        soup = BeautifulSoup(r.text, 'html.parser')
        for a in soup.select('a.popular-project-title'):
            t = a.get_text(strip=True)
            href = a.get('href', '')
            if t and href:
                items.append({'title': t, 'url': 'https://gitee.com' + href, 'desc': '',
                              'src': 'Gitee', 'hot': '', 'cat': classify(t), 'date': None})
    except Exception as e:
        print('[Gitee] 失败:', e)
    return items


def fetch_weixin():
    """微信搜狗：搜索公众号文章，结果链接为 /link?url= 加密跳转（偶发频控空页，多关键词轮询提高命中）"""
    items = []
    for q in ['免费域名', 'VPS', '云服务器', '建站教程', '宝塔面板']:
        if len(items) >= 15:
            break
        try:
            url = 'https://weixin.sogou.com/weixin?type=2&query=' + urllib.parse.quote(q)
            r = requests.get(url, headers={**HEADERS, 'Referer': 'https://weixin.sogou.com'}, timeout=TIMEOUT)
            soup = BeautifulSoup(r.text, 'html.parser')
            for box in soup.select('div.txt-box'):
                a = box.select_one('h3 a') or box.select_one('a')
                if not a:
                    continue
                t = a.get_text(strip=True)
                href = a.get('href', '')
                if not href.startswith('http'):
                    href = 'https://weixin.sogou.com' + href
                href = href.replace('&amp;', '&')
                items.append({'title': t, 'url': href, 'desc': '', 'src': '微信搜狗',
                              'hot': '', 'cat': classify(t), 'date': None})
        except Exception as e:
            print('[微信搜狗] 失败:', e)
    return items


def fetch_stackoverflow():
    """Stack Overflow：官方免费 API（无需 key），返回最近活动问题"""
    items = []
    try:
        url = ('https://api.stackexchange.com/2.3/questions?order=desc&sort=activity'
               '&site=stackoverflow&pagesize=20')
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            print('[StackOverflow] 状态码', r.status_code)
            return items
        for q in r.json().get('items', []):
            title = q.get('title', '')
            if not title:
                continue
            items.append({'title': title, 'url': q.get('link', ''), 'desc': '',
                          'src': 'StackOverflow', 'hot': str(q.get('score', 0)),
                          'cat': classify(title), 'date': ts_to_dt(q.get('last_activity_date'))})
    except Exception as e:
        print('[StackOverflow] 失败:', e)
    return items


def fetch_medium():
    """Medium：官方 RSS（境外，部署到 GitHub Actions 后可通）"""
    items = []
    try:
        r = requests.get('https://medium.com/feed', headers=HEADERS, timeout=TIMEOUT)
        for title, link, desc, pub in parse_rss(r.content):
            if not title:
                continue
            items.append({'title': title, 'url': link, 'desc': html.unescape(desc)[:160],
                          'src': 'Medium', 'hot': '', 'cat': classify(title + ' ' + desc),
                          'date': parse_pubdate(pub)})
    except Exception as e:
        print('[Medium] 失败:', e)
    return items


def fetch_v2ex():
    """V2EX：国内技术社区，节点最新主题，自带 Unix 时间戳"""
    items = []
    try:
        nodes = ['vps', 'cloud', 'programmer', 'create', 'share', 'dns']
        for n in nodes:
            url = f'https://www.v2ex.com/api/topics/show.json?node_name={n}'
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code != 200:
                print(f'[V2EX] 节点 {n} 状态码 {r.status_code}')
                continue
            for t in r.json():
                title = t.get('title', '')
                content = (t.get('content') or '')
                created = t.get('created')
                items.append({'title': title, 'url': t.get('url', ''),
                              'desc': content[:160], 'src': 'V2EX',
                              'hot': str(t.get('replies', 0)),
                              'cat': classify(title + ' ' + content),
                              'date': ts_to_dt(created) if created else None})
            time.sleep(1)  # 避让 V2EX 限流
    except Exception as e:
        print('[V2EX] 失败:', e)
    return items


def fetch_juejin():
    """掘金：国内技术社区，官方推荐流接口，ctime 为毫秒时间戳"""
    items = []
    try:
        data = {'id_type': 2, 'cursor': '0', 'limit': 30, 'sort_type': 200}
        r = requests.post('https://api.juejin.cn/recommend_api/v1/article/recommend_all_feed',
                          json=data, headers={**HEADERS, 'Content-Type': 'application/json'},
                          timeout=TIMEOUT)
        if r.status_code != 200:
            print('[掘金] 状态码', r.status_code)
            return items
        for a in r.json().get('data', []):
            info = a.get('article_info', {})
            title = info.get('title', '')
            if not title:
                continue
            ctime = info.get('ctime')
            items.append({'title': title,
                          'url': 'https://juejin.cn/post/' + str(info.get('article_id', '')),
                          'desc': info.get('brief_content', '')[:160], 'src': '掘金',
                          'hot': str(info.get('digg_count', 0)),
                          'cat': classify(title + ' ' + info.get('brief_content', '')),
                          'date': ts_to_dt(ctime / 1000) if ctime else None})
    except Exception as e:
        print('[掘金] 失败:', e)
    return items


def fetch_devto():
    items = []
    try:
        r = requests.get('https://dev.to/feed', headers=HEADERS, timeout=TIMEOUT)
        for title, link, desc, pub in parse_rss(r.content):
            if not title:
                continue
            items.append({'title': title, 'url': link,
                          'desc': html.unescape(desc)[:160], 'src': 'Dev.to',
                          'hot': '', 'cat': classify(title + ' ' + desc),
                          'date': parse_pubdate(pub)})
    except Exception as e:
        print('[Dev.to] 失败:', e)
    return items


def fetch_lobsters():
    items = []
    try:
        r = requests.get('https://lobste.rs/rss', headers=HEADERS, timeout=TIMEOUT)
        for title, link, desc, pub in parse_rss(r.content):
            if not title:
                continue
            items.append({'title': title, 'url': link,
                          'desc': html.unescape(desc)[:160], 'src': 'Lobsters',
                          'hot': '', 'cat': classify(title + ' ' + desc),
                          'date': parse_pubdate(pub)})
    except Exception as e:
        print('[Lobsters] 失败:', e)
    return items


def fetch_github():
    items = []
    try:
        since = NOW.strftime('%Y-%m-%d')
        url = (f'https://api.github.com/search/repositories?q=stars:%3E300+pushed:%3E{since}'
               f'&sort=updated&order=desc&per_page=50')
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200:
            for repo in r.json().get('items', []):
                name = repo.get('full_name', '')
                desc = repo.get('description') or ''
                pushed = repo.get('pushed_at', '')
                try:
                    d = dt.datetime.strptime(pushed, '%Y-%m-%dT%H:%M:%SZ') if pushed else None
                except Exception:
                    d = None
                items.append({'title': name, 'url': repo.get('html_url', ''), 'desc': desc,
                              'src': 'GitHub', 'hot': str(repo.get('stargazers_count', 0)),
                              'cat': classify(name + ' ' + desc), 'date': d})
        else:
            print('[GitHub] 状态码', r.status_code)
    except Exception as e:
        print('[GitHub] 失败:', e)
    return items


def fetch_hackernews():
    items = []
    try:
        ts = int(time.time()) - 3 * 86400
        for q in ['free vps', 'self-hosted', 'free domain', 'ai tool', 'open source',
                  'free hosting', 'self-host', 'free ssl', 'productivity tool', 'free ai']:
            url = ('https://hn.algolia.com/api/v1/search?query=' + urllib.parse.quote(q) +
                   f'&numericFilters=created_at_i>{ts}&hitsPerPage=15')
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT).json()
            for h in r.get('hits', []):
                title = h.get('title') or h.get('story_title') or ''
                if not title:
                    continue
                link = h.get('url') or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
                points = h.get('points', 0) or 0
                items.append({'title': title, 'url': link, 'desc': '', 'src': 'HackerNews',
                              'hot': str(points), 'cat': classify(title),
                              'date': ts_to_dt(h.get('created_at_i'))})
    except Exception as e:
        print('[HN] 失败:', e)
    return items


def fetch_producthunt():
    items = []
    try:
        r = requests.get('https://www.producthunt.com/feed', headers=HEADERS, timeout=TIMEOUT)
        for title, link, desc, pub in parse_rss(r.content):
            if not title:
                continue
            items.append({'title': title, 'url': link, 'desc': html.unescape(desc)[:160],
                          'src': 'ProductHunt', 'hot': '', 'cat': classify(title + ' ' + desc),
                          'date': parse_pubdate(pub)})
    except Exception as e:
        print('[PH] 失败:', e)
    return items


def fetch_sspai():
    items = []
    try:
        r = requests.get('https://sspai.com/feed', headers=HEADERS, timeout=TIMEOUT)
        for title, link, desc, pub in parse_rss(r.content):
            if not title:
                continue
            items.append({'title': title, 'url': link, 'desc': html.unescape(desc)[:160],
                          'src': '少数派', 'hot': '', 'cat': classify(title + ' ' + desc),
                          'date': parse_pubdate(pub)})
    except Exception as e:
        print('[sspai] 失败:', e)
    return items


def fetch_reddit():
    items = []
    try:
        for s in ['selfhosted', 'freebies', 'homelab']:
            url = f'https://www.reddit.com/r/{s}/search/.rss?q=free&restrict_sr=1&sort=new&t=week'
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            for title, link, desc, pub in parse_rss(r.content):
                if not title:
                    continue
                items.append({'title': title, 'url': link, 'desc': html.unescape(desc)[:160],
                              'src': f'Reddit/r/{s}', 'hot': '', 'cat': classify(title + ' ' + desc),
                              'date': parse_pubdate(pub)})
    except Exception as e:
        print('[Reddit] 失败:', e)
    return items


def fetch_bilibili():
    items = []
    try:
        for kw in ['免费域名', '免费vps', '建站教程', '宝塔面板', 'cloudflare',
                   '服务器', '云服务器', '网站搭建', 'wordpress', '内网穿透', 'nas', 'cdn']:
            url = 'https://api.bilibili.com/x/web-interface/search/all/v2?keyword=' + urllib.parse.quote(kw)
            r = requests.get(url, headers={**HEADERS, 'Referer': 'https://search.bilibili.com'}, timeout=TIMEOUT)
            data = r.json()
            for res in data.get('data', {}).get('result', []):
                if res.get('result_type') != 'video':
                    continue
                for v in res.get('data', []):
                    title = v.get('title', '').replace('<em class="keyword">', '').replace('</em>', '')
                    bvid = v.get('bvid', '')
                    play = v.get('play', 0) or 0
                    senddate = v.get('senddate') or v.get('pubdate')
                    items.append({'title': title, 'url': f'https://www.bilibili.com/video/{bvid}',
                                  'desc': '', 'src': 'B站', 'hot': str(play),
                                  'cat': classify(title), 'date': ts_to_dt(senddate)})
    except Exception as e:
        print('[B站] 失败:', e)
    return items


def hotnum(i):
    try:
        return int(''.join(filter(str.isdigit, i['hot'])) or 0)
    except Exception:
        return 0


def score(i):
    """优先级评分：分数越高，越适合优先做成"我自己的内容"（B站视频 + 博客图文）。

    设计对齐你的"雨云返佣漏斗"，并重点突出"可二创为教程"的信号——
    因为目标是持续产出教程类内容，所以**教程信号词**是最大权重项。

      · 类别权重：A/AB(服务器·建站,强引流雨云)=60，B(工具·AI,泛流量)=20
      · 教程信号：含 教程/保姆/搭建/配置/申请/部署/安装/使用/入门/实战/从零/手把手/
                  怎么/如何/步骤/图文/速通 等 = +40（最关键：能直接照着做的选题）
      · 钩子强度：含 免费/free/白嫖/0元/零成本/不要钱 = +30（免费教程最易吸量、最易二创）
      · 转化相关：含 建站/wordpress/宝塔/cloudflare/域名/vps/云服务器/雨云/主机/面板/服务器 = +15
      · 新鲜度：3天内 +20，7天内 +10，14天内 +4（无日期或超期 = 0）
      · 热度：>=1000 +10，>=100 +5，>=10 +2（仅作"已被市场验证"的弱信号，不喧宾夺主）
      · 平台友好：B站(本身就是可参照的视频) +8，少数派 +3
    返回整数，存入数据库 priority 字段，并用于简报排序（高分排前）。
    """
    s = 0
    cat = i.get('cat')
    s += 60 if cat in ('A', 'AB') else (20 if cat == 'B' else 0)
    t = ((i.get('title') or '') + ' ' + (i.get('desc') or '')).lower()
    if any(k in t for k in ['教程', '保姆', '搭建', '配置', '申请', '部署', '安装', '使用',
                            '入门', '实战', '从零', '手把手', '怎么', '如何', '步骤', '图文', '速通', '教学']):
        s += 40
    if any(k in t for k in ['免费', 'free', '白嫖', '0元', '零成本', '不要钱']):
        s += 30
    if any(k in t for k in ['建站', 'wordpress', '宝塔', 'cloudflare', '域名', 'vps',
                            '虚拟主机', '云服务器', '雨云', '服务器', '主机', '面板']):
        s += 15
    d = i.get('date')
    if d is not None:
        days = (NOW - d).days
        if days <= 3:
            s += 20
        elif days <= 7:
            s += 10
        elif days <= 14:
            s += 4
    h = hotnum(i)
    if h >= 1000:
        s += 10
    elif h >= 100:
        s += 5
    elif h >= 10:
        s += 2
    if i.get('src') == 'B站':
        s += 8
    elif i.get('src') == '少数派':
        s += 3
    return s


def send_wechat(md, today):
    """通过 Server酱 推送简报到微信（需 SERVERCHAN_KEY）"""
    key = os.environ.get('SERVERCHAN_KEY')
    if not key:
        print('[推送] 未配置 SERVERCHAN_KEY，跳过微信推送')
        return
    try:
        r = requests.post(f'https://sctapi.ftqq.com/{key}.send',
                          data={'title': f'每日选题简报 {today}', 'desp': md}, timeout=TIMEOUT)
        print('[推送]', r.json())
    except Exception as e:
        print('[推送] 失败:', e)


def send_email(md):
    """通过 SMTP 推送简报到邮箱（可选；需配置 SMTP_* 与 EMAIL_*）"""
    import smtplib
    from email.mime.text import MIMEText
    from email.header import Header

    host = os.environ.get('SMTP_HOST')
    port = int(os.environ.get('SMTP_PORT', '465'))
    user = os.environ.get('EMAIL_FROM')
    pwd = os.environ.get('EMAIL_PASS')
    to = os.environ.get('EMAIL_TO')
    if not (host and user and pwd and to):
        print('[邮件] 未配置完整 SMTP 信息（SMTP_HOST/EMAIL_FROM/EMAIL_PASS/EMAIL_TO），跳过邮件推送')
        return
    try:
        today = NOW.strftime('%Y-%m-%d')
        msg = MIMEText(md, 'plain', 'utf-8')
        msg['Subject'] = Header(f'每日选题简报 {today}', 'utf-8').encode()
        msg['From'] = user
        msg['To'] = to
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT)
        else:
            server = smtplib.SMTP(host, port, timeout=TIMEOUT)
            server.starttls()
        server.login(user, pwd)
        server.sendmail(user, [to], msg.as_string())
        server.quit()
        print('[邮件] 已发送至', to)
    except Exception as e:
        print('[邮件] 发送失败:', e)


def main():
    print('== 拉取各平台数据（仅留最近更新）==')
    fetchers = [fetch_oschina, fetch_cnblogs, fetch_csdn, fetch_jianshu, fetch_segmentfault,
                fetch_gitee, fetch_weixin, fetch_stackoverflow, fetch_medium,
                fetch_v2ex, fetch_juejin, fetch_devto, fetch_lobsters,
                fetch_github, fetch_hackernews, fetch_producthunt,
                fetch_sspai, fetch_reddit, fetch_bilibili]
    all_items = []
    for fn in fetchers:
        got = fn()
        print(f'  {fn.__name__:22s} 抓取 {len(got)} 条')
        all_items += got

    # 1) 时效窗口过滤（无时间的源一律保留，最后按热度兜底）
    filtered = []
    skipped_old = 0
    for i in all_items:
        if not i['cat']:
            continue
        mx = SOURCE_MAX_AGE.get(i['src'], 7)
        d = i['date']
        if d is not None and (NOW - d).days > mx:
            skipped_old += 1
            continue
        filtered.append(i)

    # 2) 去重（仅本批次内按 URL 去重）
    seen, dedup = set(), []
    for i in filtered:
        if i['url'] in seen:
            continue
        seen.add(i['url'])
        dedup.append(i)

    # 2.4) 计算每条内容的优先级评分（存入数据库 priority 字段，并用于简报排序）
    for i in dedup:
        i['priority'] = score(i)

    # 2.5) 与数据库比对：剔除历史已入库（已推送过）的内容，只保留"新发现"
    #      这样简报/推送不会重复推送同一链接；数据库也只写新内容。
    if os.environ.get('DB_PASS'):
        try:
            db_store.init_db()
            existing = db_store.get_existing_hashes()  # 历史已见过的 url_hash
            new_items, already = [], 0
            for i in dedup:
                h = hashlib.sha256((i.get('url') or '').encode('utf-8')).hexdigest()
                if h in existing:
                    already += 1
                    continue
                new_items.append(i)
            print(f'[DB] 与历史库比对：本批次 {len(dedup)} 条，已存在 {already} 条，新发现 {len(new_items)} 条')
            db_store.save_items(new_items)  # 仅写入新内容（重复自动 IGNORE）
            report_items = new_items
        except Exception as e:
            print('[DB] 比对/存储失败（退回全量，不影响简报/推送）:', e)
            report_items = dedup
            already = 0
    else:
        print('[DB] 未配置 DB_PASS，跳过数据库比对')
        report_items = dedup
        already = 0

    # 3) 按源分组、组内限量（防刷屏），组内按时间倒序、无时间按热度
    by_src = defaultdict(list)
    for i in report_items:
        by_src[i['src']].append(i)
    limited = []
    for src, arr in by_src.items():
        arr.sort(key=lambda x: (x['date'] or dt.datetime.min, hotnum(x)), reverse=True)
        limited += arr[:PER_SOURCE_LIMIT]

    # 4) 全局排序：按"优先级评分"倒序（分数越高越适合优先放前面），同分按时间倒序
    final = sorted(limited, key=lambda x: (x['priority'], x['date'] or dt.datetime.min), reverse=True)

    A = [i for i in final if i['cat'] in ('A', 'AB')]
    B = [i for i in final if i['cat'] in ('B', 'AB')]

    today = NOW.strftime('%Y-%m-%d')
    md = f"# 📅 每日选题简报 {today}\n\n"
    md += (f"> 共抓取 {len(all_items)} 条，命中 {len(dedup)} 条（时效窗口过滤掉 {skipped_old} 条旧内容）；"
           f"历史已见 {already} 条，本次新发现 {len(report_items)} 条；"
           f"服务器/建站 {len(A)} · 工具/AI {len(B)}\n\n")

    # 平台分布
    dist = defaultdict(int)
    for i in final:
        dist[i['src']] += 1
    md += "> **平台分布**：" + " · ".join(f"{k} {v}" for k, v in sorted(dist.items(), key=lambda x: -x[1])) + "\n\n"

    def fmt(i):
        ds = i['date'].strftime('%m-%d') if i['date'] else '未知日期'
        hot = f" | 热度 {i['hot']}" if i['hot'] else ''
        pri = f" | 优先 {i['priority']}"
        return f"[{i['src']}·{ds}]{hot}{pri} {i['title']}\n   {i['url']}"

    if not final:
        md += "> ℹ️ 今日抓取到的内容均为历史已推送过的选题，无新增，本日报为空。\n\n"
    else:
        # 🔥 今日优先推荐 Top 5：综合优先级最高的，最值得优先做成自己的内容
        md += "## 🔥 今日优先推荐 Top 5（综合优先级最高，最值得优先做成自己的内容）\n"
        for idx, x in enumerate(final[:5], 1):
            md += f"{idx}. {fmt(x)}\n"
        md += "\n"

    md += "## 一、服务器 / 建站（强引流雨云）\n"
    for x in A[:25]:
        md += f"1. {fmt(x)}\n"
    md += "\n## 二、工具 / AI（泛流量涨粉）\n"
    for x in B[:25]:
        md += f"1. {fmt(x)}\n"

    print('\n' + md)
    with open('daily_report.md', 'w', encoding='utf-8') as f:
        f.write(md)

    # —— 通知分发（由 NOTIFY 环境变量决定渠道，默认 wechat）——
    #   wechat = 仅微信(Server酱)   email = 仅邮件   both = 微信+邮件   none = 不推送仅本地
    notify = (os.environ.get('NOTIFY') or 'wechat').lower()
    if notify == 'none':
        print('[通知] NOTIFY=none，仅本地生成 daily_report.md')
    elif notify == 'wechat':
        send_wechat(md, today)
    elif notify == 'email':
        send_email(md)
    elif notify == 'both':
        send_wechat(md, today)
        send_email(md)
    else:
        print(f'[通知] 未知 NOTIFY={notify}，仅本地生成 daily_report.md')


if __name__ == '__main__':
    main()
