#!/usr/bin/env python3
"""리라이트한 post.md가 키워드 리포트 목표치를 맞췄는지 체크.

사용법: python 도구/check_post.py posts/<폴더>/post.md
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# 글자 수 셀 때 지우는 서식 표시
MARK = (r"^\[(?:단계|팁|참고|주석)\] |^\[/?(?:구분선|구분선 굵게|공감|따옴표|핵심정리|번호표|번호목록)\]$"
        r"|^\[링크카드: [^\]]*\]$|==|\*\*|\{\{|\}\}|__|^!!|\|")

# 자주 틀리는 띄어쓰기 (틀린 형태 → 맞는 형태)
SPACING = {"남겨주세요": "남겨 주세요", "알려드릴게요": "알려 드릴게요", "알려드린": "알려 드린", "할때": "할 때",
           "할일": "할 일", "꺼져있": "꺼져 있", "켜져있": "켜져 있", "예를들어": "예를 들어", "궁금한점": "궁금한 점",
           "등록시": "등록 시", "붙여넣어야": "붙여 넣어야"}


def flow_warnings(body):
    """rules 9장 흐름 점검 중 자동으로 볼 수 있는 것."""
    plain = re.sub(r"\[프롬프트 박스\].*?\[/프롬프트 박스\]", "", body, flags=re.S)
    plain = re.sub(r"\[/?표\].*?\[/표\]", "", plain, flags=re.S)  # 기호 설명 표는 # 그대로 둠
    warn = []
    for m in sorted(set(re.findall(r"(?<![\d.])0?\d번(?:은|이|으로|을|과|의|에)?", plain))):
        warn.append(f"번호로 대상 부름: '{m}' → 이름이나 A/B로")
    nums = [int(n) for n in re.findall(r"^## (\d+)\.", body, flags=re.M)]
    if nums and nums != list(range(1, len(nums) + 1)):
        warn.append(f"섹션 번호가 연속이 아님: {nums}")
    for bad, good in SPACING.items():
        if bad in plain:
            warn.append(f"띄어쓰기: '{bad}' → '{good}'")
    for m in sorted(set(re.findall(r"(?<![#\w])#[가-힣]+", plain))):
        warn.append(f"본문 '{m}' → 발행 창에 자동 태그가 붙음, 발행 직전 삭제 안내")
    for ph in ("발행 후 입력", "URL 입력", "TODO"):
        if ph in plain:
            warn.append(f"자리 표시 문구 남음: '{ph}'")
    return warn


def count(text, word):
    return re.sub(r"\s+", "", text).count(re.sub(r"\s+", "", word))


def main():
    path = Path(sys.argv[1])
    raw = path.read_text(encoding="utf-8")
    head, body = raw.split("\n---\n", 1)
    meta = dict(line.split(": ", 1) for line in head.splitlines() if ": " in line)
    keyword = meta["메인 키워드"]
    title = meta["제목"]

    images = re.findall(r"\[이미지: [^\]]+\]", body)
    todos = re.findall(r"\[(?:경험 추가 필요|선택 필요): [^\]]+\]", body)
    headings = re.findall(r"^## .+", body, flags=re.M)
    text = re.sub(r"\[(?:이미지|경험 추가 필요|선택 필요): [^\]]+\]|\[/?프롬프트 박스\]|\[/?표\]", "", body)
    text = re.sub(r"^## (?:예시\. )?|^### \S+ ", "", text, flags=re.M)
    text = re.sub(MARK, "", text, flags=re.M)
    chars = len(re.sub(r"\s+", "", text))
    tokens = keyword.split()
    intro = re.sub(r"\s+", " ", text.strip())[:200]

    # 같은 키워드의 최신 리포트에서 중앙값 가져오기
    slug = re.sub(r"[^\w가-힣]+", "_", keyword).strip("_")
    reports = sorted((ROOT / "reports").glob(f"*_{slug}.json"))
    target = json.loads(reports[-1].read_text(encoding="utf-8"))["summary"] if reports else {}

    def rng(lo, hi):
        return f"{lo}~{hi}"

    rows = [("글자 수(공백 제외)", chars, rng(int(target["chars"]["median"] * 0.9), int(target["chars"]["median"] * 1.2)) if target else "-")]
    kw_med = int(target["kw_count"]["median"]) if target else 0
    kw_lo = max(kw_med - 1, 3)
    kw_hi = max(kw_med + 1, kw_lo + 2)
    for i, t in enumerate(tokens):
        med = target.get("token_median", {}).get(t) if target else None
        if med is None:
            rows.append((f"'{t}' 반복", count(text, t), "-"))
            continue
        lo, hi = int(med * 0.8), int(med * 1.2) + 1
        # 붙은 형태를 kw_lo회 쓰면 그 안의 마지막 단어도 최소 그만큼 나온다. 범위가 모순되지 않게 맞춘다.
        if i == len(tokens) - 1 and len(tokens) > 1:
            hi = max(hi, kw_hi)
        rows.append((f"'{t}' 반복", count(text, t), rng(lo, hi)))
    rows.append((f"'{keyword}' 붙은 형태", count(text, keyword), rng(kw_lo, kw_hi) if target else "-"))
    plain = re.sub(r"\[프롬프트 박스\].*?\[/프롬프트 박스\]", "", body, flags=re.S)
    if plain != body:
        plain = re.sub(r"\[(?:이미지|경험 추가 필요|선택 필요): [^\]]+\]", "", plain)
        plain = re.sub(r"^## (?:예시\. )?|^### \S+ ", "", plain, flags=re.M)
        plain = re.sub(MARK, "", plain, flags=re.M)
        rows.append(("└ 프롬프트 제외 글자 수", len(re.sub(r"\s+", "", plain)), "참고"))
        rows.append((f"└ 프롬프트 제외 '{tokens[0]}'", count(plain, tokens[0]), "참고"))
    # 서브 키워드: 본문 횟수와 태그 포함 여부 (메인 키워드 이름 안에 든 서브는 메인 횟수로 갈음)
    norm = lambda s: re.sub(r"\s+", "", s)
    subs = [s.strip() for s in meta.get("서브 키워드", "").split(",") if s.strip()]
    tags = [norm(t).lstrip("#") for t in meta.get("태그", "").split(",") if t.strip()]
    kw_warn = []
    for s in subs:
        if norm(s) in norm(keyword):
            continue
        n = count(text, s)
        rows.append((f"서브 '{s}' 본문", n, "2~3"))
        if n < 2:
            kw_warn.append(f"서브 키워드 '{s}' 본문 {n}회 → 소제목이나 표에 넣어 2회 이상 (태그에만 두지 않기)")
    for k in [keyword] + subs:
        if not any(norm(k) in t for t in tags):
            kw_warn.append(f"태그에 '{norm(k)}' 없음 → 메인·서브 키워드를 띄어쓰기 없이 태그에 넣기")
    rows.append(("이미지", len(images), f"{max(int(target['images']['median']), 5)} 이상" if target else "5 이상"))
    rows.append(("소제목", len(headings), f"{max(int(target['heading_count']['median']), 3)} 이상" if target else "3 이상"))
    rows.append(("제목에 키워드", "O" if count(title, keyword) else "X", "O"))
    rows.append(("서론 200자에 키워드", "O" if count(intro, keyword) else "X", "O"))
    rows.append(("제목 길이", len(title), "25~40"))

    print(f"# 체크: {title}")
    print(f"기준 리포트: {reports[-1].name if reports else '없음'}\n")
    print("| 항목 | 실제 | 목표 |\n|---|---:|---|")
    for name, actual, goal in rows:
        print(f"| {name} | {actual} | {goal} |")
    if kw_warn:
        print("\n키워드 점검 경고:")
        for w in kw_warn:
            print(f"- {w}")
    warn = flow_warnings(body)
    if warn:
        print("\n흐름 점검 경고 (rules 9장):")
        for w in warn:
            print(f"- {w}")
    if todos:
        print("\n사용자 확인 필요:")
        for t in todos:
            print(f"- {t}")


if __name__ == "__main__":
    main()
