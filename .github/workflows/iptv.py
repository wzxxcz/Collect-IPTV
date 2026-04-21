import os
import aiohttp
import asyncio
import time
import json
from collections import Counter, defaultdict
import re
from typing import Dict, Iterable, List, Optional, Set, Tuple, Any

def contains_date(text):
    date_pattern = r"\d{4}-\d{2}-\d{2}"
    return re.search(date_pattern, text) is not None

def normalize_text_for_match(text: str) -> str:
    normalized = text.strip().upper().replace("＋", "+")
    normalized = re.sub(r"[ \t\r\n\-_|·•:：,，.。/\\()\[\]【】「」'\"`]+", "", normalized)
    return normalized

CONFIG = {
    "timeout": 10,
    "max_parallel": 30,
    "output_file": "best_sorted.m3u",
}

PROVINCE_ALIASES = {
    "北京": {"北京台"},
    "上海": {"上海台", "东方明珠"},
    "天津": {"天津台"},
    "重庆": {"重庆台"},
    "河北": {"河北台"},
    "山西": {"山西台"},
    "辽宁": {"辽宁台"},
    "吉林": {"吉林台"},
    "内蒙": {"内蒙古"},
    "黑龙江": {"黑龙江台"},
    "江苏": {"江苏台"},
    "浙江": {"浙江台"},
    "安徽": {"安徽台"},
    "福建": {"福建台"},
    "江西": {"江西台"},
    "山东": {"山东台"},
    "河南": {"河南台"},
    "湖北": {"湖北台"},
    "湖南": {"湖南台"},
    "广东": {"广东台"},
    "广西": {"广西台"},
    "海南": {"海南台"},
    "四川": {"四川台"},
    "贵州": {"贵州台"},
    "云南": {"云南台"},
    "陕西": {"陕西台"},
    "甘肃": {"甘肃台"},
    "青海": {"青海台"},
    "宁夏": {"宁夏台"},
    "新疆": {"新疆台"},
}

COMMON_CHANNEL_SUFFIXES = (
    "新闻综合频道","新闻综合","新闻频道","影视娱乐","经济生活","文体旅游",
    "文旅频道","旅游频道","体育频道","教育频道","少儿频道","科教频道","都市频道",
    "民生频道","资讯频道","公共频道","综合频道","娱乐频道","影视","导视频道","生活频道"
)

NON_GEO_TOKENS = {
    "新闻","综合","公共","生活","民生","都市","经济","科教","教育","少儿","影视","娱乐",
    "体育","文旅","旅游","文化","资讯","导视","频道","电视","法治","军事","党建","购物",
    "健康","戏曲","综艺","台","TV","HD","4K"
}

SMART_CATEGORY_KEYWORDS = {
    "港澳台频道": ("翡翠台", "明珠台", "凤凰", "纬来", "东森", "中天", "台视", "华视", "民视", "三立", "TVBS"),
    "文旅频道": ("古城", "古镇", "景区", "风景", "风光", "全景", "雪山", "公园", "湖景"),
    "新闻频道": ("新闻", "时政", "资讯", "焦点"),
    "体育频道": ("体育", "足球", "篮球", "赛事"),
    "影视频道": ("电影", "影院", "剧场", "电视剧"),
    "少儿动漫": ("少儿", "卡通", "动漫", "动画"),
    "纪录人文": ("纪录", "纪实", "人文", "自然", "地理"),
    "音乐频道": ("音乐", "MV"),
    "戏曲综艺": ("戏曲", "戏剧", "曲艺", "综艺"),
    "法治军事": ("法治", "军事"),
}

BLOCKED_M3U_KEYWORDS = (
    "更新时间", "维护时间", "公告", "说明", "支持作者", "免费订阅", "温馨提示", "请勿贩卖"
)
BLOCKED_M3U_KEYWORDS_NORMALIZED = tuple(normalize_text_for_match(k) for k in BLOCKED_M3U_KEYWORDS)

def load_cctv_channels(file_path=".github/workflows/IPTV/CCTV.txt"):
    cctv_channels = set()
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    cctv_channels.add(line)
    except:
        pass
    return cctv_channels

def normalize_cctv_name(channel_name):
    return re.sub(r'(?i)CCTV[\s-]?(\d+\+?)', r'CCTV\1', channel_name).replace("＋", "+")

def is_cctv_channel(channel_name: str, normalized_channel: str, normalized_cctv_channels: Set[str]) -> bool:
    if re.search(r'(?i)CCTV[\s-]?(\d+\+?)', channel_name):
        return True
    if normalized_channel in normalized_cctv_channels:
        return True
    return False

def resolve_province_aliases(province_name: str) -> Set[str]:
    aliases = {province_name}
    aliases.update(PROVINCE_ALIASES.get(province_name, set()))
    return aliases

def simplify_channel_name(channel_name: str) -> str:
    channel_name = re.sub(r"[（(【\[][^\])）】]{0,24}[)）】\]]", "", channel_name)
    channel_name = re.sub(r"\b(IPV6|HEVC|H265|H264|HDR|UHD|FHD|HD|SD|4K|8K)\b", "", channel_name, flags=re.I)
    return channel_name.strip()

def strip_common_channel_suffixes(token: str) -> str:
    value = token
    value = re.sub(r"(?:TV|BTV|NBTV|CETV)\d+$", "", value)
    value = re.sub(r"[0-9一二三四五六七八九十]+套?$", "", value)
    for suffix in sorted(COMMON_CHANNEL_SUFFIXES, key=len, reverse=True):
        if value.endswith(suffix) and len(value) > len(suffix)+1:
            value = value[:-len(suffix)]
    return value

def extract_geo_tokens(channel_name: str, normalized_aliases: Set[str]) -> Set[str]:
    tokens = set()
    simplified = simplify_channel_name(channel_name)
    for part in re.split(r"[|｜/\\\-_·•\s]+", simplified):
        nt = normalize_text_for_match(part)
        nt = strip_common_channel_suffixes(nt)
        if 2 <= len(nt) <=8 and nt not in NON_GEO_TOKENS:
            tokens.add(nt)
    return tokens

def build_province_matchers(province_channels: Dict[str, Set[str]]) -> Dict[str, List[str]]:
    matchers = {}
    for province, chs in province_channels.items():
        pats = set()
        base = province.replace("频道","")
        aliases = {normalize_text_for_match(a) for a in resolve_province_aliases(base)}
        for ch in chs:
            pats.update(extract_geo_tokens(ch, aliases))
        pats.update(aliases)
        matchers[province] = sorted(pats, key=len, reverse=True)
    return matchers

def match_province(normalized_channel: str, province_matchers: Dict[str, List[str]]) -> Optional[str]:
    for province, pats in province_matchers.items():
        for p in pats:
            if p in normalized_channel:
                return province
    return None

def match_smart_category(normalized_channel: str) -> Optional[str]:
    for cat, kws in SMART_CATEGORY_KEYWORDS.items():
        for kw in kws:
            if normalize_text_for_match(kw) in normalized_channel:
                return cat
    return None

def natural_sort_key(text: str) -> list:
    return [int(s) if s.isdigit() else s.lower() for s in re.split(r'(\d+)', text)]

def cctv_sort_key(channel_name: str) -> tuple:
    m = re.search(r'(?i)CCTV[\s-]?(\d+)(\+?)', channel_name)
    if m:
        return (0, int(m.group(1)), 1 if m.group(2) else 0)
    return (1, natural_sort_key(channel_name))

def channel_identity_key(channel: str) -> str:
    return normalize_text_for_match(normalize_cctv_name(channel))

def looks_like_notice_entry(channel: str, source_group_title=None) -> bool:
    s = normalize_text_for_match(f"{channel} {source_group_title or ''}")
    return any(bk in s for bk in BLOCKED_M3U_KEYWORDS_NORMALIZED)

def sanitize_channel_name(channel: str, extinf_line=None) -> str:
    channel = re.sub(r'tvg-[\w-]+="[^"]*"', '', channel)
    channel = re.sub(r'group-title="[^"]*"', '', channel)
    channel = channel.strip('", ').strip()
    return channel or "Unknown"

def parse_group_title_from_extinf(line: str) -> Optional[str]:
    m = re.search(r'group-title\s*=\s*"([^"]+)"', line, re.I)
    return m.group(1).strip() if m else None

def infer_group_from_upstream_title(g: str, pm) -> Optional[str]:
    if not g: return None
    n = normalize_text_for_match(g)
    if "cctv" in n or "央视" in g: return "央视频道"
    if "卫视" in g: return "卫视频道"
    p = match_province(n, pm)
    if p: return p
    return match_smart_category(n)

def deduplicate_candidate_entries(entries):
    seen = set()
    out = []
    for e in entries:
        c = sanitize_channel_name(e.get("channel",""))
        u = e.get("url","").strip()
        if not c or not u.startswith(("http://","https://")): continue
        if looks_like_notice_entry(c, e.get("source_group_title")): continue
        k = (channel_identity_key(c), u)
        if k not in seen:
            seen.add(k)
            out.append({"channel":c, "url":u, "source_group_title":e.get("source_group_title")})
    return out

def select_best_streams(entries):
    best = {}
    for e in entries:
        c = e["channel"]
        k = channel_identity_key(c)
        if k not in best or len(e["url"]) < len(best[k]["url"]):
            best[k] = e
    return sorted(best.values(), key=lambda x: natural_sort_key(x["channel"]))

def extract_urls_from_txt(content):
    out = []
    for line in content.splitlines():
        line = line.strip()
        if "," in line:
            c, u = line.split(",",1)
            c = sanitize_channel_name(c)
            out.append({"channel":c, "url":u.strip(), "source_group_title":None})
    return out

def extract_urls_from_m3u(content):
    out = []
    ch = "Unknown"
    gt = None
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF:"):
            gt = parse_group_title_from_extinf(line)
            if "," in line:
                ch = sanitize_channel_name(line.split(",",1)[1], line)
        elif line.startswith(("http://","https://")):
            out.append({"channel":ch, "url":line, "source_group_title":gt})
    return out

async def test_stream(session, sem, url):
    async with sem:
        try:
            async with session.get(url, timeout=5) as r:
                return r.status == 200
        except:
            return False

async def test_entries(session, sem, entries):
    tasks = [test_stream(session, sem, e["url"]) for e in entries]
    return await asyncio.gather(*tasks)

async def read_file(session, url, is_m3u):
    try:
        async with session.get(url, timeout=10) as r:
            t = await r.text("utf-8", "ignore")
            return extract_urls_from_m3u(t) if is_m3u else extract_urls_from_txt(t)
    except:
        return []

# ====================== 核心：TXT 分组已修复 ======================
def generate_sorted_m3u(valid_entries, cctv_channels, province_channels, filename):
    cctv = []
    satellite = []
    province = defaultdict(list)
    smart = defaultdict(list)
    other = []

    ncctv = {normalize_text_for_match(normalize_cctv_name(n)) for n in cctv_channels}
    pm = build_province_matchers(province_channels)

    for e in valid_entries:
        ch = e["channel"]
        url = e["url"]
        gt = e.get("source_group_title")
        if contains_date(ch) or contains_date(url): continue
        nch = normalize_text_for_match(normalize_cctv_name(ch))
        upg = infer_group_from_upstream_title(gt, pm)

        if is_cctv_channel(ch, nch, ncctv) or upg == "央视频道":
            cctv.append({"channel":ch,"url":url})
        elif "卫视" in ch or upg == "卫视频道":
            satellite.append({"channel":ch,"url":url})
        else:
            p = match_province(nch, pm)
            if p:
                province[p].append({"channel":ch,"url":url})
            else:
                sc = upg if upg in SMART_CATEGORY_KEYWORDS else match_smart_category(nch)
                if sc:
                    smart[sc].append({"channel":ch,"url":url})
                else:
                    other.append({"channel":ch,"url":url})

    cctv.sort(key=cctv_sort_key)
    satellite.sort(key=lambda x: natural_sort_key(x["channel"]))
    for p in province: province[p].sort(key=lambda x: natural_sort_key(x["channel"]))
    for c in smart: smart[c].sort(key=lambda x: natural_sort_key(x["channel"]))
    other.sort(key=lambda x: natural_sort_key(x["channel"]))

    m3u8 = filename.replace(".m3u",".m3u8")
    txt = filename.replace(".m3u",".txt")
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    # 写 M3U
    for fn in [filename, m3u8]:
        with open(fn,"w",encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            f.write(f"# Update: {now}\n")
            for item in cctv:
                f.write(f'#EXTINF:-1 group-title="央视频道",{item["channel"]}\n{item["url"]}\n')
            for item in satellite:
                f.write(f'#EXTINF:-1 group-title="卫视频道",{item["channel"]}\n{item["url"]}\n')
            for p in sorted(province):
                for item in province[p]:
                    f.write(f'#EXTINF:-1 group-title="{p}",{item["channel"]}\n{item["url"]}\n')
            for c in SMART_CATEGORY_KEYWORDS:
                for item in smart.get(c,[]):
                    f.write(f'#EXTINF:-1 group-title="{c}",{item["channel"]}\n{item["url"]}\n')
            for item in other:
                f.write(f'#EXTINF:-1 group-title="其他频道",{item["channel"]}\n{item["url"]}\n')

    # ====================== TXT 分组结构已修复 ======================
    with open(txt,"w",encoding="utf-8") as f:
        if cctv:
            f.write("[央视频道]\n")
            for item in cctv: f.write(f"{item['channel']},{item['url']}\n")
            f.write("\n")
        if satellite:
            f.write("[卫视频道]\n")
            for item in satellite: f.write(f"{item['channel']},{item['url']}\n")
            f.write("\n")
        for p in sorted(province):
            lst = province[p]
            if lst:
                f.write(f"[{p}]\n")
                for item in lst: f.write(f"{item['channel']},{item['url']}\n")
                f.write("\n")
        for c in SMART_CATEGORY_KEYWORDS:
            lst = smart.get(c,[])
            if lst:
                f.write(f"[{c}]\n")
                for item in lst: f.write(f"{item['channel']},{item['url']}\n")
                f.write("\n")
        if other:
            f.write("[其他频道]\n")
            for item in other: f.write(f"{item['channel']},{item['url']}\n")

def load_province_channels(files):
    pc = defaultdict(set)
    for fp in files:
        name = os.path.basename(fp).replace(".txt","")
        try:
            with open(fp,"r",encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line: pc[name].add(line)
        except:
            pass
    return pc

async def main(urls, cctv_file, province_files):
    cctv_ch = load_cctv_channels(cctv_file)
    province_ch = load_province_channels(province_files)
    sem = asyncio.Semaphore(30)
    entries = []

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(10)) as s:
        for u in urls:
            e = await read_file(s, u, u.endswith((".m3u",".m3u8")))
            entries.extend(e)
        dedup = deduplicate_candidate_entries(entries)
        ok = await test_entries(s, sem, dedup)
        valid = [d for d, o in zip(dedup, ok) if o]
        best = select_best_streams(valid)

    generate_sorted_m3u(best, cctv_ch, province_ch, CONFIG["output_file"])

if __name__ == "__main__":
    FILES = [
        "https://gitee.com/yimi321/tv/raw/master/tv.png",
        "https://ds65.tv1288.xyz",
        "https://gh-proxy.org/https://raw.githubusercontent.com/Jsnzkpg/Jsnzkpg/Jsnzkpg/Jsnzkpg1.m3u",
        "http://wangziduoqing.com/yuan/zb.txt",
        "https://tzdr.com/iptv.txt",
        "https://live.kilvn.com/iptv.m3u",
        "https://m3u.ibert.me/txt/fmml_itv.txt",
        "https://gh-proxy.com/raw.githubusercontent.com/vbskycn/iptv/master/tv/iptv4.m3u",
        "http://175.178.251.183:6689/live.m3u",
        "https://raw.githubusercontent.com/suxuang/myIPTV/main/ipv4.m3u",
        "https://m3u.ibert.me/ycl_iptv.m3u",
        "https://tv.iill.top/m3u/Gather",
        "https://live.zbds.org/tv/iptv4.m3u",
        "https://raw.githubusercontent.com/YueChan/Live/main/IPTV.m3u",
        "https://raw.githubusercontent.com/Kimentanm/aptv/master/m3u/iptv.m3u",
        "https://raw.githubusercontent.com/BurningC4/Chinese-IPTV/master/TV-IPV4.m3u",
        "https://raw.githubusercontent.com/zwc456baby/iptv_alive/master/live.m3u",
        "https://raw.githubusercontent.com/hujingguang/ChinaIPTV/main/cnTV_AutoUpdate.m3u8"
    ]

    PROVINCE_FILES = [
        ".github/workflows/IPTV/北京频道.txt",
        ".github/workflows/IPTV/上海频道.txt",
        ".github/workflows/IPTV/天津频道.txt",
        ".github/workflows/IPTV/重庆频道.txt",
        ".github/workflows/IPTV/河北频道.txt",
        ".github/workflows/IPTV/山西频道.txt",
        ".github/workflows/IPTV/辽宁频道.txt",
        ".github/workflows/IPTV/吉林频道.txt",
        ".github/workflows/IPTV/内蒙频道.txt",
        ".github/workflows/IPTV/黑龙江频道.txt",
        ".github/workflows/IPTV/江苏频道.txt",
        ".github/workflows/IPTV/浙江频道.txt",
        ".github/workflows/IPTV/安徽频道.txt",
        ".github/workflows/IPTV/福建频道.txt",
        ".github/workflows/IPTV/江西频道.txt",
        ".github/workflows/IPTV/山东频道.txt",
        ".github/workflows/IPTV/河南频道.txt",
        ".github/workflows/IPTV/湖北频道.txt",
        ".github/workflows/IPTV/湖南频道.txt",
        ".github/workflows/IPTV/广东频道.txt",
        ".github/workflows/IPTV/广西频道.txt",
        ".github/workflows/IPTV/海南频道.txt",
        ".github/workflows/IPTV/四川频道.txt",
        ".github/workflows/IPTV/贵州频道.txt",
        ".github/workflows/IPTV/云南频道.txt",
        ".github/workflows/IPTV/陕西频道.txt",
        ".github/workflows/IPTV/甘肃频道.txt",
        ".github/workflows/IPTV/青海频道.txt",
        ".github/workflows/IPTV/宁夏频道.txt",
        ".github/workflows/IPTV/新疆频道.txt",
        ".github/workflows/IPTV/卫视频道.txt",
    ]

    asyncio.run(main(FILES, ".github/workflows/IPTV/CCTV.txt", PROVINCE_FILES))
