#!/usr/bin/env python3
"""네이버 블로그 키워드 · 상위노출 분석기

사용법:
    python 도구/seo_analyze.py "키워드1" "키워드2" ...
    python 도구/seo_analyze.py --demo            # API 키 없이 예시 데이터로 실행

결과: reports/날짜_키워드.md (+ .json)
표준 라이브러리만 사용 (설치 필요 없음).
"""
import argparse
import base64
import hashlib
import hmac
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


# ---------- 설정 ----------

def load_env():
    """.env 파일을 읽어 dict로 반환 (python-dotenv 없이)."""
    env = {}
    path = ROOT / ".env"
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def http_get(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


# ---------- 검색광고 API: 월간 검색수 ----------

def to_int(value):
    """검색광고 API는 10 미만이면 '< 10' 문자열을 준다."""
    if isinstance(value, (int, float)):
        return int(value)
    return 5 if "<" in str(value) else int(re.sub(r"\D", "", str(value)) or 0)


def fetch_search_volume(keyword, env):
    """메인 키워드 월간 검색수 + 연관 키워드 상위 목록."""
    customer = env.get("SEARCHAD_CUSTOMER_ID")
    license_key = env.get("SEARCHAD_ACCESS_LICENSE")
    secret = env.get("SEARCHAD_SECRET_KEY")
    if not (customer and license_key and secret):
        return None

    uri = "/keywordstool"
    ts = str(int(time.time() * 1000))
    sign = base64.b64encode(
        hmac.new(secret.encode(), f"{ts}.GET.{uri}".encode(), hashlib.sha256).digest()
    ).decode()
    hint = keyword.replace(" ", "")  # 힌트 키워드는 공백 불가
    url = "https://api.searchad.naver.com" + uri + "?" + urllib.parse.urlencode(
        {"hintKeywords": hint, "showDetail": "1"}
    )
    headers = {
        "X-Timestamp": ts,
        "X-API-KEY": license_key,
        "X-Customer": customer,
        "X-Signature": sign,
    }
    data = json.loads(http_get(url, headers))
    rows = []
    for item in data.get("keywordList", []):
        pc = to_int(item.get("monthlyPcQcCnt", 0))
        mo = to_int(item.get("monthlyMobileQcCnt", 0))
        rows.append({
            "keyword": item.get("relKeyword"),
            "pc": pc,
            "mobile": mo,
            "total": pc + mo,
            "comp": item.get("compIdx"),
        })
    main = next((r for r in rows if r["keyword"] == hint), None)
    related = sorted((r for r in rows if r["keyword"] != hint), key=lambda r: -r["total"])
    return {"main": main, "related": related[:20]}


# ---------- 검색 API: 발행량 + 상위 글 ----------

def fetch_blog_search(keyword, env, top):
    """NAVER API HUB(ncloud) 키 우선, 없으면 구 개발자센터 키."""
    params = urllib.parse.urlencode({"query": keyword, "display": top, "sort": "sim"})
    if env.get("NCP_HUB_CLIENT_ID") and env.get("NCP_HUB_CLIENT_SECRET"):
        url = "https://naverapihub.apigw.ntruss.com/search/v1/blog?" + params
        headers = {
            "X-NCP-APIGW-API-KEY-ID": env["NCP_HUB_CLIENT_ID"],
            "X-NCP-APIGW-API-KEY": env["NCP_HUB_CLIENT_SECRET"],
        }
    elif env.get("NAVER_CLIENT_ID") and env.get("NAVER_CLIENT_SECRET"):
        url = "https://openapi.naver.com/v1/search/blog.json?" + params
        headers = {
            "X-Naver-Client-Id": env["NAVER_CLIENT_ID"],
            "X-Naver-Client-Secret": env["NAVER_CLIENT_SECRET"],
        }
    else:
        raise SystemExit("❌ .env에 NCP_HUB_CLIENT_ID / NCP_HUB_CLIENT_SECRET 이 없습니다.")
    try:
        return json.loads(http_get(url, headers))
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        raise SystemExit(f"❌ 블로그 검색 API 오류 {e.code}: {body}\n   → API 허브 Application에 '블로그 검색'이 추가됐는지, 키가 맞는지 확인")


class PostParser(HTMLParser):
    """스마트에디터 ONE 본문(se-main-container)에서 텍스트·이미지·소제목 집계."""

    def __init__(self):
        super().__init__()
        self.depth = 0          # se-main-container 안쪽 div 깊이
        self.in_heading = 0
        self.texts = []
        self.images = 0
        self.headings = []
        self.videos = 0
        self._heading_buf = []

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class", "") or ""
        if self.depth == 0:
            if tag == "div" and "se-main-container" in cls:
                self.depth = 1
            return
        if tag == "div":
            self.depth += 1
            # 소제목 서식 또는 인용구(블로거들이 소제목으로 주로 사용) 컴포넌트
            if "se-component" in cls and ("se-sectionTitle" in cls or "se-quotation" in cls):
                self.in_heading = self.depth
                self._heading_buf = []
            if "se-video" in cls:
                self.videos += 1
        if tag == "img" and "se-image-resource" in cls:
            self.images += 1

    def handle_endtag(self, tag):
        if self.depth == 0 or tag != "div":
            return
        if self.in_heading and self.depth == self.in_heading:
            text = " ".join("".join(self._heading_buf).split())
            if text:
                self.headings.append(text)
            self.in_heading = 0
        self.depth -= 1

    def handle_data(self, data):
        if self.depth > 0:
            self.texts.append(data)
            if self.in_heading:
                self._heading_buf.append(data)


def parse_blog_link(link):
    m = re.search(r"blog\.naver\.com/([^/?#]+)/(\d+)", link)
    if m:
        return m.group(1), m.group(2)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
    if "blogId" in q and "logNo" in q:
        return q["blogId"][0], q["logNo"][0]
    return None


def count_keyword(text, keyword):
    """띄어쓰기 무시하고 키워드 등장 횟수 센다."""
    compact = re.sub(r"\s+", "", text)
    kw = re.sub(r"\s+", "", keyword)
    return compact.count(kw) if kw else 0


def analyze_post(item, keyword):
    title = re.sub(r"<[^>]+>", "", item.get("title", ""))
    result = {
        "title": title,
        "link": item.get("link"),
        "blogger": item.get("bloggername"),
        "postdate": item.get("postdate"),
        "title_has_kw": count_keyword(title, keyword) > 0,
    }
    ids = parse_blog_link(item.get("link", ""))
    if not ids:
        result["error"] = "네이버 블로그 아님"
        return result
    url = f"https://m.blog.naver.com/PostView.naver?blogId={ids[0]}&logNo={ids[1]}"
    try:
        html = http_get(url, {"User-Agent": MOBILE_UA})
    except (urllib.error.URLError, TimeoutError) as e:
        result["error"] = f"본문 불러오기 실패: {e}"
        return result
    parser = PostParser()
    parser.feed(html)
    text = " ".join("".join(parser.texts).split())
    if not text:
        result["error"] = "본문 구조 인식 실패(구버전 에디터일 수 있음)"
        return result
    tokens = keyword.split()
    result.update({
        "token_counts": {t: count_keyword(text, t) for t in tokens},
        "title_has_all_tokens": all(count_keyword(title, t) for t in tokens),
        "chars": len(re.sub(r"\s+", "", text)),
        "images": parser.images,
        "headings": parser.headings,
        "heading_count": len(parser.headings),
        "videos": parser.videos,
        "kw_count": count_keyword(text, keyword),
        "kw_in_intro": count_keyword(text[:200], keyword) > 0,
    })
    return result


# ---------- 상위 글 체급 점검 (블로그 지수 추정) ----------
# 네이버는 블로그 지수를 공개하지 않는다. 공개 RSS(최근 글 최대 50개)로 규모를 추정한다.
#  - RSS 글 수 < 50 이면 그 블로그의 공개 글 수가 곧 그 숫자 (50이면 '50개 이상')
#  - 이웃 수는 공개 수단이 없어 수집하지 않는다.
SMALL_BLOG_POSTS = 30   # 공개 글 수가 이 미만이면 '소규모 블로그'
FRESH_DAYS = 60         # 상위 글 발행이 이 일수 이내면 '최근 글' (참고 표시용)
STRONG_LAST30 = 20      # 공개 글 50개 이상 + 최근 30일 발행이 이 개수 이상이면 '활발한 대형'
_RSS_CACHE = {}


def fetch_blog_activity(blog_id):
    """RSS로 공개 글 수와 최근 30일 발행 수를 추정. 실패 시 None."""
    if blog_id in _RSS_CACHE:
        return _RSS_CACHE[blog_id]
    info = None
    try:
        xml = http_get(f"https://rss.blog.naver.com/{blog_id}.xml", {"User-Agent": "Mozilla/5.0"})
        items = re.findall(r"<item>(.*?)</item>", xml, re.S)
        dates = []
        for it in items:
            m = re.search(r"<pubDate>(.*?)</pubDate>", it)
            if m:
                try:
                    dates.append(parsedate_to_datetime(m.group(1)).replace(tzinfo=None))
                except (TypeError, ValueError):
                    pass
        now = datetime.now()
        info = {
            "posts": len(items),
            "capped": len(items) >= 50,
            "last30": sum(1 for d in dates if (now - d).days <= 30),
        }
    except (urllib.error.URLError, TimeoutError):
        info = None
    _RSS_CACHE[blog_id] = info
    time.sleep(0.3)
    return info


def post_age_days(postdate):
    try:
        return (datetime.now() - datetime.strptime(str(postdate), "%Y%m%d")).days
    except ValueError:
        return None


def strength_check(items):
    """검색 API 상위 items → 체급 표 + 판정(도전 가능/보통/회피)."""
    rows = []
    for rank, item in enumerate(items, 1):
        ids = parse_blog_link(item.get("link", ""))
        title = re.sub(r"<[^>]+>", "", item.get("title", ""))
        row = {"rank": rank, "blogger": item.get("bloggername"), "title": title,
               "age": post_age_days(item.get("postdate")), "blog_id": ids[0] if ids else None,
               "activity": fetch_blog_activity(ids[0]) if ids else None}
        act = row["activity"]
        row["small"] = bool(act) and not act["capped"] and act["posts"] < SMALL_BLOG_POSTS
        row["fresh"] = row["age"] is not None and row["age"] <= FRESH_DAYS
        row["strong"] = bool(act) and (act["capped"] and act["last30"] >= STRONG_LAST30)
        rows.append(row)
    small = sum(r["small"] for r in rows)
    fresh = sum(r["fresh"] for r in rows)
    strong = sum(1 for r in rows if r["strong"])
    # '최근 글'은 약한 신호가 아님 (대형 블로그가 신선한 글로 상위를 채우는 경우가 많음)
    if small >= 3:
        verdict = "도전 가능"
    elif small >= 1 and strong <= 6:
        verdict = "보통"
    else:
        verdict = "회피 (대형·활발한 블로그가 장악)"
    return {"rows": rows, "small": small, "fresh": fresh, "strong": strong, "verdict": verdict}


def strength_lines(st):
    lines = ["", "## 3. 상위 글 체급 (블로그 지수 추정)", "",
             "| 순위 | 블로그 | 글 나이 | 공개 글 수 | 최근 30일 발행 | 표시 |", "|---:|---|---:|---:|---:|---|"]
    for r in st["rows"]:
        a = r["activity"]
        posts = "-" if not a else (f"{a['posts']}+" if a["capped"] else str(a["posts"]))
        last30 = "-" if not a else str(a["last30"])
        age = "-" if r["age"] is None else f"{r['age']}일"
        flag = ", ".join(x for x, ok in (("소규모", r["small"]), ("활발한 대형", r["strong"]), ("최근 글", r["fresh"])) if ok) or ""
        lines.append(f"| {r['rank']} | {r['blogger']} | {age} | {posts} | {last30} | {flag} |")
    lines += ["",
              f"- 소규모 블로그(공개 글 {SMALL_BLOG_POSTS}개 미만) {st['small']}개 · 활발한 대형(최근 30일 {STRONG_LAST30}개 이상 발행) {st['strong']}개 · 최근 {FRESH_DAYS}일 이내 글 {st['fresh']}개",
              f"- **판정: {st['verdict']}** (소규모 3개 이상 = 도전 가능, 소규모 1~2개이고 활발한 대형 6개 이하 = 보통, 그 외 회피)",
              "- 한계: 블로그 지수·이웃 수는 비공개라 RSS 규모와 글 나이로 추정한 값 (참고용). '회피'여도 검색수가 작고 상위 글이 구체적이지 않으면 도전할 만하니 실제 상위 글을 직접 열어 보고 정할 것"]
    return lines


# ---------- 집계 · 리포트 ----------

def summarize(posts):
    ok = [p for p in posts if "chars" in p]
    if not ok:
        return {}

    def stat(key):
        vals = [p[key] for p in ok]
        return {
            "median": statistics.median(vals),
            "min": min(vals),
            "max": max(vals),
        }

    tokens = list(ok[0].get("token_counts", {}))
    return {
        "analyzed": len(ok),
        "chars": stat("chars"),
        "images": stat("images"),
        "kw_count": stat("kw_count"),
        "heading_count": stat("heading_count"),
        "token_median": {t: statistics.median(p["token_counts"][t] for p in ok) for t in tokens},
        "title_has_kw_ratio": sum(p["title_has_kw"] for p in ok) / len(ok),
        "title_all_tokens_ratio": sum(p.get("title_has_all_tokens", p["title_has_kw"]) for p in ok) / len(ok),
        "kw_in_intro_ratio": sum(p["kw_in_intro"] for p in ok) / len(ok),
    }


def build_report(keyword, volume, total_posts, posts, summary, demo=False, strength=None):
    lines = [f"# 키워드 분석: {keyword}" + ("  (DEMO 예시 데이터)" if demo else ""), ""]
    lines.append(f"분석 시각: {datetime.now():%Y-%m-%d %H:%M}")
    lines.append("")
    lines.append("## 1. 수요 vs 공급")
    main = volume and volume.get("main")
    if main:
        ratio = total_posts / main["total"] if main["total"] else float("inf")
        lines += [
            f"- 월간 검색수: **{main['total']:,}** (PC {main['pc']:,} / 모바일 {main['mobile']:,}) · 광고경쟁 {main['comp']}",
            f"- 블로그 발행량: **{total_posts:,}**",
            f"- 경쟁도(발행량÷검색수): **{ratio:.1f}**  → 낮을수록 유리 (참고: 10 이하 양호, 50 이상 레드오션 — 통설)",
        ]
    else:
        lines += [
            f"- 블로그 발행량: **{total_posts:,}**",
            "- 월간 검색수: (검색광고 API 키 없음 → 미수집)",
        ]
    if volume and volume.get("related"):
        lines += ["", "### 연관 키워드 (검색수 순)", "| 키워드 | 월검색 | 모바일 | 광고경쟁 |", "|---|---:|---:|---|"]
        for r in volume["related"][:15]:
            lines.append(f"| {r['keyword']} | {r['total']:,} | {r['mobile']:,} | {r['comp']} |")

    lines += ["", "## 2. 상위 노출 글 기준치 (검색 API 정확도순 상위)"]
    if summary:
        s = summary
        lines += [
            f"- 분석 성공: {s['analyzed']}개",
            f"- 글자 수(공백 제외): 중앙값 **{s['chars']['median']:,.0f}** (범위 {s['chars']['min']:,}~{s['chars']['max']:,})",
            f"- 이미지 수: 중앙값 **{s['images']['median']:.0f}** (범위 {s['images']['min']}~{s['images']['max']})",
            f"- 본문 키워드(붙은 형태) 반복: 중앙값 **{s['kw_count']['median']:.0f}회** (범위 {s['kw_count']['min']}~{s['kw_count']['max']})",
        ]
        if len(s.get("token_median", {})) > 1:
            lines.append("- 본문 단어별 반복 중앙값: " + ", ".join(f"**{t} {n:.0f}회**" for t, n in s["token_median"].items()))
        lines += [
            f"- 소제목(소제목 서식+인용구) 수: 중앙값 **{s['heading_count']['median']:.0f}**",
            f"- 제목에 키워드 붙은 형태 포함: {s['title_has_kw_ratio']:.0%} · 단어 모두 포함: {s['title_all_tokens_ratio']:.0%}",
            f"- 서론(첫 200자)에 키워드 포함 비율: {s['kw_in_intro_ratio']:.0%}",
        ]
    else:
        lines.append("- 분석 가능한 글 없음")

    lines += ["", "### 개별 글", "| # | 제목 | 글자 | 이미지 | 키워드 | 소제목 | 작성일 |", "|---|---|---:|---:|---:|---:|---|"]
    for i, p in enumerate(posts, 1):
        title = p["title"].replace("|", "/")[:40]
        if "chars" in p:
            lines.append(
                f"| {i} | [{title}]({p['link']}) | {p['chars']:,} | {p['images']} | {p['kw_count']} | {p['heading_count']} | {p['postdate']} |"
            )
        else:
            lines.append(f"| {i} | [{title}]({p['link']}) | - | - | - | - | {p.get('error')} |")

    lines += [
        "",
        "## 3. 리라이트 목표치 (자동 산출)",
    ]
    if summary:
        s = summary
        kw_med = int(s["kw_count"]["median"])
        kw_lo = max(kw_med - 1, 3)
        kw_hi = max(kw_med + 1, kw_lo + 2)
        lines += [
            f"- 글자 수: {int(s['chars']['median'] * 0.9):,} ~ {int(s['chars']['median'] * 1.2):,}자",
        ]
        if len(s.get("token_median", {})) > 1:
            lines.append("- 단어별 반복: " + ", ".join(f"{t} {int(n * 0.8)}~{int(n * 1.2) + 1}회" for t, n in s["token_median"].items()))
        lines += [
            f"- 이미지: {max(int(s['images']['median']), 5)}장 이상",
            f"- 메인 키워드(붙은 형태) 반복: {kw_lo} ~ {kw_hi}회 (과다 반복 금지)",
            f"- 소제목: {max(int(s['heading_count']['median']), 3)}개 이상",
            "- 제목 앞쪽 + 서론 첫 2문장 안에 메인 키워드",
        ]
    lines += [
        "",
        "> 주의: 검색 API 정확도순은 실제 검색결과(스마트블록·AI브리핑) 순서와 다를 수 있음. 실제 노출 화면은 Claude in Chrome으로 교차 확인.",
    ]
    if strength:
        lines += strength_lines(strength)
    return "\n".join(lines) + "\n"


def demo_data():
    keyword = "챗GPT 사용법"
    volume = {
        "main": {"keyword": "챗GPT사용법", "pc": 8200, "mobile": 21500, "total": 29700, "comp": "높음"},
        "related": [
            {"keyword": "챗GPT무료", "pc": 12000, "mobile": 33000, "total": 45000, "comp": "중간"},
            {"keyword": "챗GPT프롬프트", "pc": 6100, "mobile": 9800, "total": 15900, "comp": "중간"},
            {"keyword": "챗GPT유료차이", "pc": 1500, "mobile": 4200, "total": 5700, "comp": "낮음"},
        ],
    }
    posts = []
    samples = [(2350, 12, 7, 5), (1890, 9, 6, 4), (3120, 18, 9, 6), (2010, 11, 5, 4), (2600, 14, 8, 5)]
    for i, (c, img, kw, h) in enumerate(samples, 1):
        posts.append({
            "title": f"챗GPT 사용법 예시 글 {i}", "link": f"https://blog.naver.com/demo/{100000 + i}",
            "blogger": f"demo{i}", "postdate": "20261001", "title_has_kw": True,
            "chars": c, "images": img, "kw_count": kw, "heading_count": h,
            "headings": [], "videos": 0, "kw_in_intro": i != 4,
        })
    return keyword, volume, 184000, posts


def save(keyword, report, payload):
    REPORTS.mkdir(exist_ok=True)
    slug = re.sub(r"[^\w가-힣]+", "_", keyword).strip("_")
    base = REPORTS / f"{datetime.now():%Y%m%d}_{slug}"
    base.with_suffix(".md").write_text(report, encoding="utf-8")
    base.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return base.with_suffix(".md")


def quick_screen(keywords, env, with_strength=False):
    """여러 후보 키워드를 검색수·발행량·경쟁도로 한 표에 비교."""
    rows = []
    has_ad = all(env.get(k) for k in ("SEARCHAD_CUSTOMER_ID", "SEARCHAD_ACCESS_LICENSE", "SEARCHAD_SECRET_KEY"))
    if not has_ad:
        print("ℹ️ 검색광고 키가 없어서 월검색수와 경쟁도는 비웁니다. 발행량이 적은 순으로 보여 드려요.")
        print("   월검색수는 네이버 검색창 자동완성과 관련 검색어를 aside로 확인하세요 (README 참고).\n")
    for kw in keywords:
        try:
            vol = fetch_search_volume(kw, env)
        except urllib.error.HTTPError:
            vol = None
        main_row = vol and vol.get("main")
        res = fetch_blog_search(kw, env, 10 if with_strength else 1)
        total = res.get("total", 0)
        searches = main_row["total"] if main_row else 0
        ratio = total / searches if searches else float("inf")
        verdict = strength_check(res.get("items", []))["verdict"].split(" (")[0] if with_strength else None
        rows.append((kw, searches, total, ratio, main_row["comp"] if main_row else "-", verdict))
        time.sleep(0.3)
    rows.sort(key=(lambda r: r[3]) if has_ad else (lambda r: r[2]))
    if with_strength:
        lines = ["| 키워드 | 월검색 | 발행량 | 경쟁도 | 광고경쟁 | 상위 체급 판정 |", "|---|---:|---:|---:|---|---|"]
        lines += [(f"| {k} | {s:,} | {t:,} | {r:.1f} | {c} | {v} |" if has_ad else f"| {k} | - | {t:,} | - | - | {v} |") for k, s, t, r, c, v in rows]
    else:
        lines = ["| 키워드 | 월검색 | 발행량 | 경쟁도 | 광고경쟁 |", "|---|---:|---:|---:|---|"]
        lines += [(f"| {k} | {s:,} | {t:,} | {r:.1f} | {c} |" if has_ad else f"| {k} | - | {t:,} | - | - |") for k, s, t, r, c, v in rows]
    table = "\n".join(lines)
    print(table)
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"{datetime.now():%Y%m%d_%H%M}_후보비교.md"
    path.write_text("# 키워드 후보 비교 (" + ("경쟁도 낮은 순" if has_ad else "발행량 적은 순, 월검색수 없음") + ")\n\n" + table + "\n", encoding="utf-8")
    print(f"✅ 저장: {path}")


def main():
    ap = argparse.ArgumentParser(description="네이버 블로그 키워드·상위노출 분석")
    ap.add_argument("keywords", nargs="*", help="분석할 키워드")
    ap.add_argument("--top", type=int, default=10, help="분석할 상위 글 수 (기본 10)")
    ap.add_argument("--strength", action="store_true", help="--quick과 함께: 상위 10개 글의 체급(소규모 블로그·최근 글 비율) 판정 추가")
    ap.add_argument("--demo", action="store_true", help="API 없이 예시 데이터로 실행")
    ap.add_argument("--quick", action="store_true", help="검색수·발행량·경쟁도만 빠르게 비교 (본문 분석 생략)")
    args = ap.parse_args()

    if args.demo:
        keyword, volume, total, posts = demo_data()
        summary = summarize(posts)
        report = build_report(keyword, volume, total, posts, summary, demo=True)
        path = save(keyword + "_DEMO", report, {"keyword": keyword, "volume": volume, "total": total, "posts": posts, "summary": summary})
        print(report)
        print(f"✅ 저장: {path}")
        return

    if not args.keywords:
        ap.error("키워드를 입력하세요. 예: python 도구/seo_analyze.py \"챗GPT 사용법\"")

    env = load_env()
    if args.quick:
        quick_screen(args.keywords, env, args.strength)
        return
    for keyword in args.keywords:
        print(f"🔎 {keyword} 분석 중...")
        try:
            volume = fetch_search_volume(keyword, env)
        except urllib.error.HTTPError as e:
            print(f"  ⚠️ 검색광고 API 오류 {e.code}: {e.read().decode(errors='replace')[:200]}")
            volume = None
        if volume is None:
            print("  ⚠️ 월간 검색수 미수집 (검색광고 API 키 확인)")
        search = fetch_blog_search(keyword, env, args.top)
        posts = []
        for item in search.get("items", []):
            posts.append(analyze_post(item, keyword))
            time.sleep(0.7)  # 과도한 요청 방지
        summary = summarize(posts)
        strength = strength_check(search.get("items", []))
        report = build_report(keyword, volume, search.get("total", 0), posts, summary, strength=strength)
        path = save(keyword, report, {"keyword": keyword, "volume": volume, "total": search.get("total"), "posts": posts, "summary": summary, "strength": strength})
        print(f"  ✅ 저장: {path}")


if __name__ == "__main__":
    sys.exit(main())
