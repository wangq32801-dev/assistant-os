#!/usr/bin/env python3
"""AI Daily Digest - 每日AI资讯采集脚本 v2.0
按信源可信度分级抓取，输出结构化JSON供Hermes LLM分析
信源分级：T0官方 > T1权威媒体 > T2垂直优质 > T3自媒体/聚合
"""
import xml.etree.ElementTree as ET
import urllib.request
import urllib.parse
import json
import re
import sys
from datetime import datetime, timezone, timedelta

BJT = timezone(timedelta(hours=8))

# 信源分级配置
SOURCES = [
    # === T1: 权威/头部AI媒体 ===
    {
        'name': '量子位',
        'tier': 'T1',
        'type': 'rss',
        'url': 'https://www.qbitai.com/feed',
        'max_items': 8,
        'lang': 'zh',
        'desc': '国内AI头部媒体，原创性强'
    },
    
    # === T2: 垂直优质/科技媒体 ===
    {
        'name': 'AI科技评论',
        'tier': 'T2',
        'type': 'leiphone',
        'url': 'https://www.leiphone.com/category/ai',
        'max_items': 6,
        'lang': 'zh',
        'desc': '雷锋网旗下，偏产业应用'
    },
    {
        'name': '澎湃·科技',
        'tier': 'T2',
        'type': 'rss_html',
        'url': 'https://www.thepaper.cn/rss_newsDetail_25.jsp',
        'max_items': 5,
        'lang': 'zh',
        'desc': '澎湃新闻科技版',
        'filter_keywords': ['AI', '人工智能', '大模型', '模型', '智能', 'GPT', 'LLM', '算法', '芯片']
    },
    {
        'name': 'IT之家',
        'tier': 'T2',
        'type': 'rss_html',
        'url': 'https://www.ithome.com/rss/',
        'max_items': 6,
        'lang': 'zh',
        'desc': 'IT科技资讯门户',
        'filter_keywords': ['AI', '人工智能', '大模型', 'GPT', '模型', '智能', '芯片', 'Agent', 'LLM', '算法', 'DeepSeek', 'OpenAI', 'Anthropic']
    },
    
    # === T3: 海外/技术社区 ===
    {
        'name': 'HN·AI',
        'tier': 'T3',
        'type': 'hn_ai',
        'url': 'https://hnrss.org/frontpage?points=30&search=AI&count=8',
        'max_items': 3,
        'lang': 'en',
        'desc': 'Hacker News热门AI话题'
    },
    {
        'name': 'TechCrunch·AI',
        'tier': 'T3',
        'type': 'rss',
        'url': 'https://techcrunch.com/category/artificial-intelligence/feed/',
        'max_items': 2,
        'lang': 'en',
        'desc': '海外科技媒体AI板块'
    },
    # === 微信公众号（专用抓取器，源级容错）—— 维护者自行添加订阅源 ===
    {
        'name': '<SOURCE_NAME>',
        'tier': 'T2',
        'type': 'wechat_recent',
        'url': 'https://mp.weixin.qq.com/s/<WECHAT_ARTICLE_ID>',
        'max_items': 2,
        'lang': 'zh',
        'desc': '<占位示例，替换为自己的订阅源>'
    },
]


def fetch_rss_html(url, source_name, max_items=5, timeout=15):
    """用正则提取不规范的RSS/XML（容错更好）"""
    try:
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/rss+xml,application/xml,text/xml,*/*'
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode('utf-8', errors='replace')
        
        articles = []
        # 匹配 item 或 entry 块
        items = re.findall(r'<item>(.*?)</item>', content, re.DOTALL)
        if not items:
            items = re.findall(r'<entry[^>]*>(.*?)</entry>', content, re.DOTALL)
        
        for item_html in items:
            # 提取标题
            title_m = re.search(r'<title[^>]*>(.*?)</title>', item_html, re.DOTALL)
            title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip() if title_m else ''
            
            # 提取链接
            link = ''
            link_m = re.search(r'<link[^>]*>(.*?)</link>', item_html, re.DOTALL)
            if link_m:
                link = link_m.group(1).strip()
            else:
                link_href = re.search(r'<link[^>]*href="([^"]+)"', item_html)
                if link_href:
                    link = link_href.group(1).strip()
            
            # 提取发布时间
            pub_date = ''
            pd_m = re.search(r'<pubDate>(.*?)</pubDate>', item_html, re.DOTALL)
            if pd_m:
                pub_date = pd_m.group(1).strip()
            else:
                pd_m = re.search(r'<published>(.*?)</published>', item_html, re.DOTALL)
                if pd_m:
                    pub_date = pd_m.group(1).strip()
            
            # 提取摘要
            summary = ''
            desc_m = re.search(r'<description[^>]*>(.*?)</description>', item_html, re.DOTALL)
            if desc_m:
                summary = re.sub(r'<[^>]+>', '', desc_m.group(1)).strip()[:400]
            else:
                sum_m = re.search(r'<summary[^>]*>(.*?)</summary>', item_html, re.DOTALL)
                if sum_m:
                    summary = re.sub(r'<[^>]+>', '', sum_m.group(1)).strip()[:400]
            
            if title:
                articles.append({
                    'title': title,
                    'link': link,
                    'published': pub_date,
                    'summary': summary,
                })
            
            if len(articles) >= max_items:
                break
        
        return articles[:max_items]
    except Exception as e:
        return {'error': str(e)[:80]}


def fetch_rss(url, source_name, max_items=5, timeout=15):
    """通用RSS抓取"""
    try:
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/rss+xml,application/xml,text/xml,*/*'
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            xml_data = resp.read().decode('utf-8', errors='replace')
        
        root = ET.fromstring(xml_data)
        articles = []
        
        # RSS 2.0
        for item in root.iter('item'):
            title = item.findtext('title', '')
            link = item.findtext('link', '')
            pub_date = item.findtext('pubDate', '')
            desc = item.findtext('description', '')
            if title:
                articles.append({
                    'title': title.strip(),
                    'link': link.strip(),
                    'published': pub_date.strip(),
                    'summary': re.sub(r'<[^>]+>', '', desc).strip()[:400] if desc else '',
                })
        
        # Atom format
        if not articles:
            for entry in root.iter('{http://www.w3.org/2005/Atom}entry'):
                title = entry.findtext('{http://www.w3.org/2005/Atom}title', '')
                link_el = entry.find('{http://www.w3.org/2005/Atom}link')
                link = link_el.get('href', '') if link_el is not None else ''
                pub_date = entry.findtext('{http://www.w3.org/2005/Atom}published', '')
                summary = entry.findtext('{http://www.w3.org/2005/Atom}summary', '')
                if title:
                    articles.append({
                        'title': title.strip(),
                        'link': link.strip(),
                        'published': pub_date.strip(),
                        'summary': re.sub(r'<[^>]+>', '', summary).strip()[:400] if summary else '',
                    })
        
        return articles[:max_items]
    except Exception as e:
        return {'error': str(e)[:80]}


def fetch_leiphone(url, max_items=6):
    """雷锋网AI科技评论 - 网页抓取"""
    try:
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            html = resp.read().decode('utf-8', errors='replace')
        
        articles = []
        seen = set()
        pattern = re.findall(
            r'<h[2-5][^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html, re.DOTALL
        )
        for href, title in pattern:
            t = re.sub(r'<[^>]+>', '', title).strip()
            if t and len(t) > 8 and t not in seen:
                seen.add(t)
                if href.startswith('/'):
                    href = 'https://www.leiphone.com' + href
                articles.append({
                    'title': t,
                    'link': href,
                    'published': '',
                    'summary': '',
                })
        return articles[:max_items]
    except Exception as e:
        return {'error': str(e)[:80]}


def fetch_hn_ai(url, max_items=4):
    """Hacker News AI相关热门"""
    result = fetch_rss(url, 'HN·AI', max_items * 2, timeout=12)
    if isinstance(result, dict) and 'error' in result:
        return result
    
    ai_keywords = ['ai', 'artificial intelligence', 'llm', 'gpt', 'model', 'neural',
                   'deep learning', 'machine learning', 'transformer', 'openai',
                   'anthropic', 'deepmind', 'meta ai', 'robot', 'agent']
    filtered = []
    for a in result:
        title_lower = a['title'].lower()
        if any(kw in title_lower for kw in ai_keywords):
            filtered.append(a)
    return filtered[:max_items]


def filter_by_keywords(articles, keywords):
    """按关键词过滤（用于综合RSS源提取AI相关）"""
    if not keywords:
        return articles
    filtered = []
    for a in articles:
        text = (a.get('title', '') + ' ' + a.get('summary', '')).lower()
        if any(kw.lower() in text for kw in keywords):
            filtered.append(a)
    return filtered


def fetch_wechat_recent(url, max_items=2, timeout=15):
    """微信公众号近文（受限方案，2026-10-05实测）：
    微信文章页内链极少且多为平台页，搜狗搜索会混入同名无关号，均不可靠。
    诚实降级：返回该 url 指向文章本身作为候选（标题+发布时间），
    由日报筛选环节决定是否采用。宁缺勿滥，不猜、不混入无关号。
    维护提示：url 需人工更新为该号最新文章（维护者转发新文时替换）。"""
    UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
          'AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.49')
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        html = urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', errors='replace')
        try:
            html = html.encode('latin1').decode('utf-8')
        except Exception:
            pass
        tm = re.search(r'og:title" content="([^"]+)"', html)
        dm = re.search(r'create_time\s*=\s*["\']?(\d{10})', html) or \
             re.search(r'publish_time["\']?\s*[:=]\s*["\']?(\d{10})', html)
        title = tm.group(1).strip() if tm else ''
        published = ''
        if dm:
            published = datetime.fromtimestamp(int(dm.group(1)), BJT).strftime('%Y-%m-%d')
        if title:
            return [{'title': title, 'link': url, 'published': published, 'summary': ''}]
        return {'error': 'cannot parse article page'}
    except Exception as e:
        return {'error': str(e)[:80]}


def fetch_source(src):
    """抓取单个信源"""
    name = src['name']
    tier = src['tier']
    stype = src['type']
    max_items = src['max_items']
    # 带关键词过滤的源，多取一些再过滤，避免前N条都不相关
    fetch_count = max_items * 3 if 'filter_keywords' in src else max_items
    
    if stype == 'rss':
        result = fetch_rss(src['url'], name, fetch_count)
    elif stype == 'rss_html':
        result = fetch_rss_html(src['url'], name, fetch_count)
    elif stype == 'leiphone':
        result = fetch_leiphone(src['url'], fetch_count)
    elif stype == 'hn_ai':
        result = fetch_hn_ai(src['url'], fetch_count)
    elif stype == 'wechat_recent':
        result = fetch_wechat_recent(src['url'], fetch_count)
    else:
        return {'name': name, 'tier': tier, 'status': 'error', 'error': f'未知类型: {stype}', 'articles': []}
    
    if isinstance(result, dict) and 'error' in result:
        return {'name': name, 'tier': tier, 'status': 'error', 'error': result['error'], 'articles': []}
    
    # 关键词过滤
    if 'filter_keywords' in src:
        result = filter_by_keywords(result, src['filter_keywords'])
    
    # 截断到max_items
    result = result[:max_items]
    
    # 补全信源信息
    for a in result:
        a['source'] = name
        a['tier'] = tier
        a['lang'] = src['lang']
    
    return {'name': name, 'tier': tier, 'status': 'ok', 'count': len(result), 'articles': result}


def main():
    results = []
    all_articles = []
    failed = []
    
    for src in SOURCES:
        result = fetch_source(src)
        results.append(result)
        if result['status'] == 'ok':
            all_articles.extend(result['articles'])
        else:
            failed.append(result['name'])
    
    # 去重：按标题前40字
    seen = set()
    unique_articles = []
    for a in all_articles:
        key = re.sub(r'\s+', '', a['title'])[:40]
        if key not in seen:
            seen.add(key)
            unique_articles.append(a)
    
    today = datetime.now(BJT).strftime('%Y-%m-%d')
    weekday = datetime.now(BJT).strftime('%A')
    cn_weekday = {'Monday':'一','Tuesday':'二','Wednesday':'三','Thursday':'四',
                  'Friday':'五','Saturday':'六','Sunday':'日'}.get(weekday, weekday)
    
    # 构造输出
    output = {
        'date': today,
        'weekday': f'周{cn_weekday}',
        'total_articles': len(unique_articles),
        'sources_total': len(SOURCES),
        'sources_ok': len([r for r in results if r['status'] == 'ok']),
        'failed_sources': failed,
        'source_summary': [
            {'name': r['name'], 'tier': r['tier'], 'status': r['status'], 
             'count': r.get('count', 0), 'error': r.get('error', '')}
            for r in results
        ],
        'articles': unique_articles
    }
    
    # JSON输出（主格式，给LLM用）
    print(json.dumps(output, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
