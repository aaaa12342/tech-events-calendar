# -*- coding: utf-8 -*-
"""
黑客松数据抓取与合并脚本

用法:
    python scraper/scraper.py            # 正常运行：抓取 + 合并
    python scraper/scraper.py --dry-run  # 只打印结果，不写文件

数据流:
    各数据源(见 SOURCES) --抓取+关键词过滤--> 抓取条目
    data/manual.json (手动维护, 优先级最高)
        |--按 id + 标题去重合并--> data/hackathons.json (前端读取)

数据源优先级(同名活动先到先得):
    manual > ai-pick > huodongxing > modelscope > segmentfault > saikr
"""

import hashlib
import html as _html
import json
import os
import re
import ssl
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# ---------------- 路径 ----------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
MANUAL_FILE = os.path.join(DATA_DIR, 'manual.json')
RANKED_FILE = os.path.join(DATA_DIR, 'ranked.json')
OUTPUT_FILE = os.path.join(DATA_DIR, 'hackathons.json')

# ---------------- 抓取配置 ----------------
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')
TIMEOUT = 20
TZ_BEIJING = timezone(timedelta(hours=8))
TODAY = datetime.now(TZ_BEIJING).date()

# 科技类关键词：用于判定标题是否属于科技活动（过滤跑步/美食等无关活动）
TECH_KEYWORDS = [
    'ai', '人工智能', '大模型', 'llm', 'aigc', 'agent', '智能体', '机器学习',
    '深度学习', 'gpt', 'mcp', '计算机', '算法', '软件', '编程', '程序', '开发',
    '数据', '互联网', '科技', '机器人', '硬件', '嵌入式', 'iot', '物联网',
    '芯片', '智能', '自动化', 'ros', '计算', '数字', '开源', 'riscv', 'fpga',
    '区块链', 'web3', '量子', '自动驾驶', '无人机', '网络安全', '黑客', '极客',
    '电子', '通信', '信息安全', '云计算', '云原生', 'devops', '信创',
    'hackathon', '黑客松', '黑客马拉松', '编程马拉松', '创客',
]

# 活动类型推断规则: (类型, 关键词列表)，按顺序优先匹配（对应前端分类筛选）
CATEGORY_RULES = [
    ('黑客松', ['黑客松', '黑客马拉松', 'hackathon', '编程马拉松', '创客马拉松',
                '创意马拉松', '48小时', '48h', '48 小时', '极客挑战']),
    ('竞赛', ['竞赛', '大赛', '挑战赛', '锦标赛', '精英赛', '比赛', '杯赛',
              '邀请赛', '公开赛', '选拔赛', '国赛', '省赛']),
    ('展会', ['大会', '峰会', '博览会', '展览', '展会', '嘉年华', '开发者日']),
    ('会议', ['论坛', '沙龙', 'meetup', '会议', '研讨会', '交流会', '暑期学校',
              '夏令营', 'workshop', '工作坊', '见面会', '讲座', '分享会', '训练营']),
]

# ---------------- 网络工具 ----------------
_CTG = ssl.create_default_context()
_CTG.check_hostname = False
_CTG.verify_mode = ssl.CERT_NONE
_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),  # 绕过系统代理，避免本地代理干扰
    urllib.request.HTTPSHandler(context=_CTG),
)


def http_get(url, retries=3):
    last_err = None
    for _ in range(retries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            return _OPENER.open(req, timeout=TIMEOUT).read().decode('utf-8', 'ignore')
        except Exception as e:
            last_err = e
    raise last_err


def ts_to_date(ts):
    """unix 时间戳(秒/毫秒自适应) -> 北京时间 YYYY-MM-DD"""
    if not ts:
        return ''
    if ts > 1e12:
        ts /= 1000.0
    return datetime.fromtimestamp(ts, TZ_BEIJING).strftime('%Y-%m-%d')


def parse_date(v):
    """自适应解析日期: unix 时间戳 / ISO 字符串 -> YYYY-MM-DD，失败返回 ''"""
    if v in (None, '', 0):
        return ''
    if isinstance(v, (int, float)):
        return ts_to_date(v)
    s = str(v).strip()
    if re.fullmatch(r'\d{10}', s):
        return ts_to_date(int(s))
    m = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', s)
    if m:
        return '%04d-%02d-%02d' % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return ''


def is_recent(e):
    """优先使用活动结束日期；报名截止不能覆盖仍有效的活动时间。"""
    for key in ('end', 'start', 'reg_deadline'):
        v = e.get(key) or ''
        if not v:
            continue
        try:
            d = datetime.strptime(v, '%Y-%m-%d').date()
        except ValueError:
            continue
        # 无结束日期但有报名信息时，不能推断活动已经结束。
        if key == 'start' and e.get('reg_deadline'):
            return True
        return d >= TODAY - timedelta(days=30)
    return True


def _title_year(title):
    """从标题中提取年份（如 '2014中日黑客马拉松' -> 2014）"""
    m = re.search(r'((?:19|20)\d{2})年?', title)
    return int(m.group(1)) if m else None


def _is_tech(title):
    low = title.lower()
    return any(kw in low for kw in TECH_KEYWORDS)


def _guess_category(title):
    low = title.lower()
    for cat, kws in CATEGORY_RULES:
        if any(kw in low for kw in kws):
            return cat
    return '会议'


def _make_id(prefix, key):
    return '%s-%s' % (prefix, hashlib.md5(str(key).encode('utf-8')).hexdigest()[:10])


# 主要城市列表：活动行城市形如"四川成都"，归一化为"成都"便于同城筛选
MAJOR_CITIES = [
    '北京', '上海', '广州', '深圳', '杭州', '武汉', '成都', '长沙', '南京',
    '西安', '重庆', '天津', '苏州', '厦门', '青岛', '郑州', '宁波', '东莞',
    '佛山', '石家庄', '合肥', '济南', '沈阳', '大连', '福州', '昆明', '贵阳',
    '南昌', '长春', '哈尔滨', '太原', '无锡', '常州', '珠海', '海口', '兰州',
    '乌鲁木齐', '香港', '澳门',
]


def _norm_city(s):
    for c in MAJOR_CITIES:
        if c in s:
            return c
    return s


# ---------------- 数据源 1: AI 活动雷达 (hope0719.github.io/ai-pick) ----------------
# 只保留国内及可线上参与的全球活动，过滤日/美/欧等海外线下场次
AI_PICK_KEEP_REGIONS = ('中国', '全球', '亚太', '亚洲', '线上')
# ai-pick 活动类型 -> 本站分类；未列出的类型（权益福利/开发激励/内容创作等）直接丢弃
AI_PICK_TYPE_MAP = {
    '黑客松': '黑客松',
    'AI竞赛': '竞赛',
    '开发挑战': '竞赛',
    '社区活动': '会议',
}


def fetch_ai_pick():
    """AI 活动雷达的公开数据文件，字段最全（截止/奖励/主办方），保留国内及线上场次"""
    data = json.loads(http_get('https://hope0719.github.io/ai-pick/data.json'))
    out = []
    for a in data.get('activities') or []:
        atype = (a.get('type') or '').strip()
        if atype not in AI_PICK_TYPE_MAP:
            continue  # 丢弃权益福利/开发激励/内容创作等非活动类
        title = (a.get('title') or '').strip()
        if not title or not a.get('url'):
            continue
        region = (a.get('region') or '').strip()
        if region and not any(region.startswith(r) for r in AI_PICK_KEEP_REGIONS):
            continue
        online = ('线上' in region) or ('online' in region.lower()) or region in ('全球', '')
        out.append({
            'id': _make_id('aipick', a['url']),
            'name': title,
            'org': (a.get('vendor') or '').strip(),
            'category': AI_PICK_TYPE_MAP[atype],
            'city': region or ('线上' if online else '未知'),
            'online': online,
            'venue': '',
            'start': parse_date(a.get('startAt')),
            'end': parse_date(a.get('endAt')),
            'reg_deadline': parse_date(a.get('deadline_date')),
            'fee': '',
            'prize': 0,
            'prize_text': (a.get('reward') or '').strip(),
            'url': a['url'],
            'source': 'ai-pick',
        })
    return [e for e in out if is_recent(e)]


# ---------------- 数据源 2: 活动行 ----------------
def fetch_huodongxing():
    """活动行搜索页（带查询参数时服务端渲染），搜多个科技活动关键词。

    页面只显示"MM月DD日"不带年份，且历史活动长期保留在搜索结果里，直接套用
    当前年份会把 2014/2020 等老活动"复活"成今年。每个活动 logo 图片路径形如
    /logo/YYYYMM/...，其中 YYYYMM 即活动真实年月，据此淘汰历史活动。
    """
    out, seen = [], set()
    for kw in ('黑客松', '人工智能', '开发者大会', '机器人', '科技峰会', '大数据'):
        url = 'https://www.huodongxing.com/search?ps=20&pi=0&list=list&qs=%s&st=1,4' \
              % urllib.parse.quote(kw)
        html = http_get(url)
        markers = [m.start() for m in re.finditer(r'<img class="item-logo" src="', html)]
        for idx, pos in enumerate(markers):
            end = markers[idx + 1] if idx + 1 < len(markers) else pos + 4000
            block = html[pos:end]
            lm = re.search(r'logo/(\d{6})/', block)
            if not lm:
                continue
            year = int(lm.group(1)[:4])
            if year < TODAY.year:  # 历史活动，丢弃
                continue
            tm = (re.search(r'class="item-title"[^>]*href="(/event/\d+)[^"]*"[^>]*title="([^"]*)"', block)
                  or re.search(r'class="item-title"[^>]*href="(/event/\d+)[^"]*"[^>]*>([^<]+)</a>', block))
            if not tm:
                continue
            link, title = tm.group(1), _html.unescape(tm.group(2)).strip()
            if link in seen or not title or not _is_tech(title):
                continue
            ty = _title_year(title)
            if ty is not None and not (TODAY.year <= ty <= TODAY.year + 1):
                continue
            seen.add(link)
            dm = re.search(r'class="date-pp">(\d{1,2})月(\d{1,2})日', block)
            start = ''
            if dm:
                try:
                    start = '%04d-%02d-%02d' % (year, int(dm.group(1)), int(dm.group(2)))
                except ValueError:
                    start = ''
            cm = re.search(r'class="item-dress-pp"[^>]*>\s*([^<]{1,20}?)\s*<', block)
            city = _norm_city(cm.group(1).strip()) if cm else ''
            out.append({
                'id': _make_id('hdx', link),
                'name': title,
                'org': '',
                'category': _guess_category(title),
                'city': city or '未知',
                'online': '线上' in city,
                'venue': '',
                'start': start,
                'end': '',
                'reg_deadline': '',
                'fee': '',
                'prize': 0,
                'prize_text': '',
                'url': 'https://www.huodongxing.com' + link,
                'source': 'huodongxing',
            })
    return [e for e in out if is_recent(e)]


# ---------------- 数据源 3: 魔搭社区 ----------------
def fetch_modelscope():
    """魔搭社区比赛/活动 API，按关键词过滤出黑客松类"""
    out, seen = [], set()
    for page in (1, 2, 3):
        data = json.loads(http_get(
            'https://modelscope.cn/api/v1/competitions?pageNumber=%d' % page))
        races = (data.get('Data') or {}).get('Races') or []
        if not races:
            break
        for r in races:
            title = (r.get('Title') or '').strip()
            rid = r.get('Id')
            if not title or rid in seen:
                continue
            seen.add(rid)
            online = (r.get('EventType') or '') != 'offline-event'
            out.append({
                'id': 'ms-%s' % rid,
                'name': title,
                'org': '魔搭社区',
                'category': '竞赛',
                'city': (r.get('EventLocation') or '').strip() or ('线上' if online else '未知'),
                'online': online,
                'venue': '',
                'start': parse_date(r.get('StartTime') or r.get('GmtStart')),
                'end': parse_date(r.get('EndTime') or r.get('GmtEnd')),
                'reg_deadline': parse_date(r.get('RegistrationDeadline')),
                'fee': '',
                'prize': 0,
                'prize_text': '',
                'url': r.get('SignUpUrl') or ('https://modelscope.cn/competition/%s' % rid),
                'source': 'modelscope',
            })
    return [e for e in out if is_recent(e)]


# ---------------- 数据源 4: 思否活动页 ----------------
def fetch_segmentfault():
    """思否活动页（服务端渲染），解析 __NEXT_DATA__ 中的活动列表"""
    events = {}
    for url in ('https://segmentfault.com/events',
                'https://segmentfault.com/events/finished?page=2'):
        for item in _sf_fetch(url):
            events.setdefault(item['id'], item)
    return [e for e in events.values() if is_recent(e)]


def _sf_fetch(url):
    html = http_get(url)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise ValueError('未找到 __NEXT_DATA__，页面结构可能已变化: %s' % url)
    state = json.loads(m.group(1))['props']['pageProps']['initialState']['activity']
    raw = []
    raw.extend(state.get('recommendList') or [])
    raw.extend(state.get('newestList') or [])
    raw.extend((state.get('finishedList') or {}).get('rows') or [])

    out = []
    for e in raw:
        title = (e.get('name') or '').strip()
        if not title or not _is_tech(title):
            continue
        city = (e.get('city_name') or '').strip()
        online = (e.get('category_name') or '') == '线上活动'
        out.append({
            'id': 'sf-%s' % e['id'],
            'name': title,
            'org': '',
            'category': _guess_category(title),
            'city': '线上' if online else (city or '未知'),
            'online': online,
            'venue': (e.get('address') or '').strip(),
            'start': ts_to_date(e.get('start')),
            'end': ts_to_date(e.get('end')),
            'reg_deadline': ts_to_date(e.get('sign_end')),
            'fee': '',
            'prize': 0,
            'prize_text': '',
            'url': 'https://segmentfault.com/e/%s' % e['id'],
            'source': 'segmentfault',
        })
    return out


# ---------------- 数据源 5: 赛氪 ----------------
SAIKR_RELEVANT = [
    'ai', '人工智能', '大模型', '计算机', '编程', '程序设计', '算法',
    '大数据', '软件', '开发', '创客', '黑客', '智能', '机器人',
    '物联网', '数据挖掘', '网络安全', '区块链',
]


def _saikr_relevant(title):
    low = title.lower()
    return any(kw in low for kw in SAIKR_RELEVANT)


def _saikr_date(s):
    """'2026.08.13' -> '2026-08-13'"""
    m = re.search(r'(\d{4})\.(\d{1,2})\.(\d{1,2})', s)
    return '%04d-%02d-%02d' % (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else ''


def _fetch_saikr_page(html):
    """解析赛氪一页 HTML/JSON 片段，返回条目列表"""
    items = []
    anchors = list(re.finditer(r'<a href="(https?://m\.saikr\.com/vse/[^"]+)" class="item">', html))
    for idx, m in enumerate(anchors):
        link = m.group(1)
        end = anchors[idx + 1].start() if idx + 1 < len(anchors) else m.start() + 3000
        block = html[m.start():end]
        tm = re.search(r'<h3 class="item-tit">([^<]+)</h3>', block)
        if not tm:
            continue
        title = _html.unescape(tm.group(1)).strip()
        if not title or not _saikr_relevant(title):
            continue
        reg_deadline = start = end = ''
        infos = re.findall(
            r'<div class="item-info-tit">\s*([^<]+?)\s*</div>\s*<ul class="item-info-ul">\s*<li>\s*([^<]+?)\s*</li>',
            block)
        for label, val in infos:
            label, val = label.strip(), val.strip()
            parts = val.split('-')
            if '报名' in label:
                if len(parts) >= 2:
                    reg_deadline = _saikr_date(parts[1].strip())
            elif '比赛' in label or '时间' in label:
                if len(parts) >= 1:
                    start = _saikr_date(parts[0].strip())
                if len(parts) >= 2:
                    end = _saikr_date(parts[1].strip())
        items.append({
            'id': _make_id('saikr', link),
            'name': title,
            'org': '',
            'category': '竞赛',
            'city': '线上',
            'online': True,
            'venue': '',
            'start': start,
            'end': end,
            'reg_deadline': reg_deadline,
            'fee': '',
            'prize': 0,
            'prize_text': '',
            'url': link,
            'source': 'saikr',
        })
    return items


def fetch_saikr():
    """赛氪竞赛广场（移动版 SSR + 分页接口），保留 AI/计算机类近期竞赛"""
    out, seen = [], set()
    html_pages = []
    html_pages.append(http_get('https://m.saikr.com/vs'))
    for page in (2, 3, 4, 5):
        data = json.loads(http_get('https://m.saikr.com/vs/ajaxGetList?page=%d' % page))
        html_pages.append((data.get('data') or {}).get('list') or '')
    for page_html in html_pages:
        for e in _fetch_saikr_page(page_html):
            if e['id'] in seen:
                continue
            seen.add(e['id'])
            out.append(e)
    return [e for e in out if is_recent(e)]


# ---------------- 数据源 6: ROS 教育基金会 ----------------
def fetch_rosedu():
    """ROS 教育基金会：ROS 暑期学校 + ROSCon China（纯静态 HTML 列表）"""
    html = http_get('https://www.roseducation.org.cn/index.html')
    out, seen = [], set()
    pat = re.compile(
        r'href="(https?://(?:www\.)?roseducation\.org\.cn/ros\d+/|'
        r'https?://roscon\.roseducation\.org\.cn/\d+/index\.html?)"[^>]*>\s*([^<]+?)\s*</a>'
    )
    for m in pat.finditer(html):
        link = m.group(1).rstrip('/')
        title = _html.unescape(m.group(2)).strip()
        if not title or link in seen:
            continue
        seen.add(link)
        # 取链接后 400 字符内的纯文本，解析日期与地点
        tail = re.sub(r'<[^>]+>', ' ', html[m.end():m.end() + 600])
        tail = re.sub(r'\s+', ' ', tail)
        dm = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', tail)
        if not dm:
            continue
        year, mon, day = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
        start = '%04d-%02d-%02d' % (year, mon, day)
        end = ''
        tail_rest = tail[dm.end():]
        em = re.search(r'[-至—]\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', tail_rest)
        if em:
            end = '%04d-%02d-%02d' % (year, int(em.group(1)), int(em.group(2)))
        else:
            em2 = re.search(r'[-至—]\s*(\d{1,2})\s*日', tail_rest)
            if em2:
                end = '%04d-%02d-%02d' % (year, mon, int(em2.group(1)))
        out.append({
            'id': _make_id('rosedu', link),
            'name': title,
            'org': 'ROS 教育基金会',
            'category': _guess_category(title),
            'city': _norm_city(tail) or '未知',
            'online': False,
            'venue': tail.strip(),
            'start': start,
            'end': end,
            'reg_deadline': '',
            'fee': '',
            'prize': 0,
            'prize_text': '',
            'url': link,
            'source': 'rosedu',
        })
    return [e for e in out if is_recent(e)]


SOURCES = [
    ('ai-pick', fetch_ai_pick),
    ('huodongxing', fetch_huodongxing),
    ('modelscope', fetch_modelscope),
    ('segmentfault', fetch_segmentfault),
    ('saikr', fetch_saikr),
    ('rosedu', fetch_rosedu),
]

# ---------------- 榜单赛事下一届时间抓取 ----------------
# 榜单赛事多为「每年一届」：本届结束后，官网通知页会陆续发布下一届的
# 报名/比赛时间。给条目配置 notice_url（官方通知页），脚本每次运行尝试
# 从「下一届(当前年+1)」的通知文字里解析日期；命中则覆盖日期并清除
# ended 标记（该条目自动从「已结束」转回「报名中/待开始」）。
# 抓取失败或解析不到日期时保持静态值，不影响其他来源。

_SEASON_DATE = re.compile(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日')


def _parse_season(text, year):
    """在已含 year 年份的纯文本里解析下一届日期，返回 {字段: 日期}，找不到返回 None。"""
    res = {}
    for m in _SEASON_DATE.finditer(text):
        if int(m.group(1)) != year:
            continue
        d = '%04d-%02d-%02d' % (year, int(m.group(2)), int(m.group(3)))
        ctx = text[max(0, m.start() - 40):m.end() + 40]
        if ('截止' in ctx or '报名' in ctx) and 'reg_deadline' not in res:
            res['reg_deadline'] = d
        elif 'start' not in res and ('开始' in ctx or '开赛' in ctx or '举办' in ctx):
            res['start'] = d
    return res or None


def fetch_ranked_updates(ranked):
    """扫描配置了 notice_url 的榜单赛事，抓取下一届时间并覆盖日期。"""
    next_year = TODAY.year + 1
    out = []
    for e in ranked:
        url = (e.get('notice_url') or '').strip()
        if not url:
            out.append(e)
            continue
        try:
            html = http_get(url)
        except Exception:
            out.append(e)
            continue
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text)
        if str(next_year) not in text:
            out.append(e)
            continue
        hit = _parse_season(text, next_year)
        if not hit:
            out.append(e)
            continue
        e2 = dict(e)
        e2.update(hit)
        e2.pop('ended', None)  # 下一届已发布报名时间，解除「已结束」标记
        print('  [ranked] %s -> %s' % (e2['name'][:20], hit))
        out.append(e2)
    return out


# ---------------- 合并逻辑 ----------------
def _norm_title(t):
    return re.sub(r'[\s·\-—|（）()【】\[\],，。.!！?？:：/\\"\'`]+', '', t).lower()


def merge(source_results, manual, ranked=None):
    """排序：manual 最高优先，其次榜单清单，最后抓取源（按 SOURCES 顺序去重）"""
    out, seen_titles = {}, {}
    for e in manual:
        out[e['id']] = e
        seen_titles[_norm_title(e['name'])] = e['id']
    for e in (ranked or []):
        if e['id'] in out or _norm_title(e['name']) in seen_titles:
            continue
        out[e['id']] = e
        seen_titles[_norm_title(e['name'])] = e['id']
    for _name, items in source_results:
        for e in items:
            if e['id'] in out:
                continue
            nt = _norm_title(e['name'])
            if nt in seen_titles:
                continue
            out[e['id']] = e
            seen_titles[nt] = e['id']
    return sorted(out.values(), key=lambda e: (e.get('start') or '9999'))


def validate_events(events):
    if not isinstance(events, list):
        raise ValueError('活动数据必须为数组')
    seen = set()
    for e in events:
        if not isinstance(e, dict):
            raise ValueError('活动必须为对象')
        for key in ('id', 'name', 'source', 'url'):
            if not isinstance(e.get(key), str) or not e[key].strip():
                raise ValueError('活动缺少字段: %s' % key)
        if e['id'] in seen:
            raise ValueError('重复活动 ID: %s' % e['id'])
        seen.add(e['id'])
        for key in ('start', 'end', 'reg_deadline'):
            if e.get(key):
                datetime.strptime(e[key], '%Y-%m-%d')
        if e.get('start') and e.get('end') and e['start'] > e['end']:
            raise ValueError('活动开始日期晚于结束日期: %s' % e['id'])


def atomic_write(result):
    validate_events(result['events'])
    os.makedirs(DATA_DIR, exist_ok=True)
    path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         dir=DATA_DIR, delete=False) as f:
            path = f.name
            json.dump(result, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(path, OUTPUT_FILE)
    finally:
        if path and os.path.exists(path):
            os.unlink(path)


def main():
    dry_run = '--dry-run' in sys.argv

    old = {}
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, encoding='utf-8') as f:
            old = json.load(f)
    # 老版本首次运行时，从合并数据恢复来源缓存。
    cache = dict(old.get('source_cache') or {})
    source_status = {}
    previous_status = old.get('source_status') or {}
    attempted_at = datetime.now(TZ_BEIJING).strftime('%Y-%m-%d %H:%M')
    for name, _fn in SOURCES:
        cache.setdefault(name, [e for e in old.get('events', []) if e.get('source') == name])

    source_results, errors, successful = [], [], 0
    for name, fn in SOURCES:
        try:
            items = fn()
            validate_events(items)
            if any(e['source'] != name for e in items):
                raise ValueError('来源字段与抓取源不一致')
            if not items and cache.get(name):
                raise ValueError('来源返回零条，保留缓存等待确认')
            cache[name] = items
            successful += 1
            source_status[name] = {'status': 'ok', 'last_success_at': datetime.now(TZ_BEIJING).strftime('%Y-%m-%d %H:%M'),
                                   'attempted_at': attempted_at, 'count': len(items)}
            source_results.append((name, items))
            print('[OK] %s: %d 条' % (name, len(items)))
        except Exception as e:
            errors.append('%s: %s: %s' % (name, type(e).__name__, e))
            print('[FAIL] %s -> %s: %s' % (name, type(e).__name__, e))
            fallback = [e for e in cache.get(name, []) if is_recent(e)]
            source_results.append((name, fallback))
            source_status[name] = {'status': 'cached' if fallback else 'failed',
                                   'last_success_at': previous_status.get(name, {}).get('last_success_at'),
                                   'attempted_at': attempted_at, 'count': len(fallback)}

    if not successful:
        print('[ERROR] 所有抓取源均失败，未改写输出文件')
        sys.exit(1)

    manual = []
    if os.path.exists(MANUAL_FILE):
        with open(MANUAL_FILE, encoding='utf-8') as f:
            manual = json.load(f)
        print('[OK] manual: %d 条' % len(manual))
    else:
        print('[WARN] manual.json 不存在，跳过')

    ranked = []
    if os.path.exists(RANKED_FILE):
        with open(RANKED_FILE, encoding='utf-8') as f:
            ranked = json.load(f)
        print('[OK] ranked: %d 条' % len(ranked))
    else:
        print('[WARN] ranked.json 不存在，跳过')

    ranked = fetch_ranked_updates(ranked)

    merged = merge(source_results, manual, ranked)
    result = {
        'updated_at': datetime.now(TZ_BEIJING).strftime('%Y-%m-%d %H:%M'),
        'counts': {
            'total': len(merged),
            'scraped': sum(len(items) for _n, items in source_results),
            'manual': len(manual),
            'ranked': len(ranked),
        },
        'errors': errors,
        'source_cache': cache,
        'source_status': source_status,
        'events': merged,
    }

    if dry_run:
        for e in merged:
            print('%s | %s | %s | %s | ddl=%s | %s' % (
                e['source'], e['name'][:36], e['city'], e['start'], e['reg_deadline'], e['url'][:60]))
        print('共 %d 条' % len(merged))
        return

    atomic_write(result)
    print('[DONE] 写入 %s，共 %d 条（抓取 %d + 榜单 %d + 手动 %d）' % (
        OUTPUT_FILE, result['counts']['total'], result['counts']['scraped'], len(ranked), len(manual)))

if __name__ == '__main__':
    main()
