#!/usr/bin/env python3
"""post.md → 미리보기 post.html + aside가 에디터에 입력할 aside_본문.txt

사용법: python 도구/build_html.py posts/<폴더>/post.md

post.md 표기 (기본만 지원해요)
  ## 소제목        ### 작은 소제목
  **굵게**         ==형광펜==
  [구분선]
  [이미지: 파일명 | 설명]
  [프롬프트 박스] ... [/프롬프트 박스]     (복사해서 쓰는 글, 코드 블록으로 들어가요)
  [표] | 칸 | 칸 | ... [/표]                (첫 줄이 머리글)
  [번호목록] ... [/번호목록]                (한 줄에 항목 하나)
  [링크카드: URL | 제목]
"""
import html
import re
import sys
from pathlib import Path


def inline(text):
    t = html.escape(text)
    t = re.sub(r"==(.+?)==", r'<b style="background:#FFF1A6">\1</b>', t)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)


def convert(body, img_dir=None):
    """본문을 미리보기 HTML로. 가운데 정렬 기본 (정렬 규칙은 rules/내규칙.md를 따르되 미리보기는 가운데)."""
    out, para = [], []
    mode, buf = None, []

    def flush():
        if para:
            out.append('<p style="text-align:center">' + "<br>".join(inline(x) for x in para) + "</p>")
            para.clear()

    for line in body.splitlines():
        s = line.rstrip()
        if mode == "code":
            if s == "[/프롬프트 박스]":
                out.append('<div style="background:#1E1E1E;color:#F2F2F2;text-align:left;border-radius:6px;padding:14px 16px;margin:16px 0;font-family:Menlo,Consolas,monospace;font-size:13px;line-height:1.7">'
                           + "".join(f"<div>{html.escape(x) or '&nbsp;'}</div>" for x in buf) + "</div>")
                mode = None
            else:
                buf.append(s)
            continue
        if mode == "table":
            if s == "[/표]":
                rows = [[c.strip() for c in r.strip().strip("|").split("|")] for r in buf if r.strip()]
                bd = "border:1px solid #1B1B1B;padding:8px 10px;text-align:center"
                trs = "".join("<tr>" + "".join(f'<{"th" if i == 0 else "td"} style="{bd}">{inline(c)}</{"th" if i == 0 else "td"}>' for c in r) + "</tr>" for i, r in enumerate(rows))
                out.append(f'<table style="border-collapse:collapse;margin:12px auto">{trs}</table>')
                mode = None
            else:
                buf.append(s)
            continue
        if mode == "list":
            if s == "[/번호목록]":
                out.append('<ol style="display:inline-block;text-align:left">' + "".join(f"<li>{inline(x)}</li>" for x in buf if x.strip()) + "</ol>")
                mode = None
            else:
                buf.append(s)
            continue
        if s in ("[프롬프트 박스]", "[표]", "[번호목록]"):
            flush()
            mode, buf = {"[프롬프트 박스]": "code", "[표]": "table", "[번호목록]": "list"}[s], []
            continue
        m = re.match(r"\[이미지: ([^|\]]+?)\s*(?:\|[^\]]*)?\]", s)
        if m:
            flush()
            name = m.group(1).strip()
            f = (img_dir / name) if img_dir else None
            if f and f.exists():
                out.append(f'<p style="text-align:center"><img src="{f.as_uri()}" style="max-width:100%"></p>')
            else:
                out.append(f'<p style="text-align:center;color:#999">[이미지 자리: {html.escape(name)}]</p>')
            continue
        m = re.match(r"\[링크카드: ([^|\]]+)\|\s*([^\]]+)\]", s)
        if m:
            flush()
            url, ttl = m.group(1).strip(), m.group(2).strip()
            out.append(f'<p style="text-align:center"><a href="{html.escape(url)}">{html.escape(ttl)}</a><br><span style="color:#999;font-size:13px">{html.escape(url)}</span></p>')
            continue
        if s.startswith("### "):
            flush(); out.append(f'<p style="text-align:center;margin-top:24px"><b>{inline(s[4:])}</b></p>'); continue
        if s.startswith("## "):
            flush(); out.append(f'<p style="text-align:center;margin-top:34px;font-size:22px;font-weight:800">{inline(s[3:])}</p>'); continue
        if s == "[구분선]":
            flush(); out.append("<hr>"); continue
        if not s:
            flush(); continue
        para.append(s)
    flush()
    return "\n".join(out)


def to_aside(meta, body):
    """aside가 읽을 입력용 본문. 서식은 [표시]로만 지정한다."""
    lines = [f"제목: {meta['제목']}", f"태그: {meta.get('태그', '')}", "", "----- 본문 시작 -----"]
    n, in_code = 0, False
    for line in body.splitlines():
        s = line.rstrip()
        if s == "[프롬프트 박스]":
            in_code = True; lines.append("[소스코드 시작]"); continue
        if s == "[/프롬프트 박스]":
            in_code = False; lines.append("[소스코드 끝]"); continue
        if in_code:
            lines.append(s); continue
        m = re.match(r"\[이미지: ([^|\]]+?)\s*(?:\|[^\]]*)?\]", s)
        if m:
            n += 1; lines.append(f"[사진 {n}: {m.group(1).strip()}]"); continue
        if s.startswith("### "):
            lines.append(f"[소항목] {s[4:]}")
        elif s.startswith("## "):
            lines.append(f"[소제목] {s[3:]}")
        elif s == "[표]":
            lines.append("[표 시작]")
        elif s == "[/표]":
            lines.append("[표 끝]")
        elif s == "[번호목록]":
            lines.append("[번호목록 시작]")
        elif s == "[/번호목록]":
            lines.append("[번호목록 끝]")
        elif re.match(r"\[링크카드: ", s):
            mm = re.match(r"\[링크카드: ([^|\]]+)\|", s)
            lines.append(f"[링크 카드] {mm.group(1).strip()}" if mm else s)
        else:
            lines.append(s)
    lines.append("----- 본문 끝 -----")
    return "\n".join(lines) + "\n"


def counts(text, tags):
    def c(p):
        return len(re.findall(p, text, flags=re.M))
    return (f"사진 {c(r'^\[사진')} / 소제목 {c(r'^\[소제목\]')} / 소항목 {c(r'^\[소항목\]')} / 표 {c(r'^\[표 시작\]')} / "
            f"소스코드 {c(r'^\[소스코드 시작\]')} / 형광펜 {len(re.findall(r'==[^=]+==', text))} / 굵게 {len(re.findall(r'\*\*[^*]+\*\*', text))} / "
            f"구분선 {c(r'^\[구분선\]')} / 링크카드 {c(r'^\[링크 카드\]')} / 번호목록 {c(r'^\[번호목록 시작\]')} / 태그 {tags}")


def main():
    if len(sys.argv) < 2:
        raise SystemExit("사용법: python 도구/build_html.py posts/<폴더>/post.md")
    src = Path(sys.argv[1])
    raw = src.read_text(encoding="utf-8")
    if "\n---\n" not in raw:
        raise SystemExit("❌ post.md 맨 위에 제목:, 태그: 줄을 쓰고 한 줄 '---'로 끝내야 해요.")
    head, body = raw.split("\n---\n", 1)
    meta = dict(l.split(": ", 1) for l in head.splitlines() if ": " in l)
    if "제목" not in meta:
        raise SystemExit("❌ post.md 맨 위에 '제목: ...' 줄이 없어요.")
    tag_n = len([t for t in meta.get("태그", "").split(",") if t.strip()])

    aside_text = to_aside(meta, body)
    (src.parent / "aside_본문.txt").write_text(aside_text, encoding="utf-8")
    page = ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            f'<title>미리보기 {html.escape(meta["제목"])}</title></head>'
            '<body style="margin:0;background:#f4f4f2;font-family:sans-serif"><div style="max-width:420px;margin:0 auto;background:#fff;padding:24px 18px;line-height:1.8">'
            f'{convert(body, src.parent / "images")}</div></body></html>')
    (src.parent / "post.html").write_text(page, encoding="utf-8")
    print(f"✅ {src.parent / 'post.html'} (미리보기)")
    print(f"✅ {src.parent / 'aside_본문.txt'} (aside 입력용)")
    print("확인 개수: " + counts(aside_text, tag_n))


if __name__ == "__main__":
    main()
