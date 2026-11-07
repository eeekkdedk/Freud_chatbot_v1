"""대화 통계 '카드' (Wrapped 스타일) — 대화 파싱, 통계 계산, HTML 카드 생성.

Streamlit에 의존하지 않는 순수 함수 모음. 대화 원문은 호출하는 쪽에서 분석 직후 폐기한다.
msgs 형식: [{"name": str, "dt": datetime | None, "text": str}, ...]
"""
from __future__ import annotations

import csv
import html as _html
import io
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
PALETTE = ["#F09AE0", "#F7CB45", "#98E0B8", "#9CC3F5", "#C9C9D6"]  # 그래프 색
NAME_COLORS = ["#8E3C7E", "#8A6A00", "#2F6B4B", "#2B5C9E", "#6B6B7B"]  # 말풍선 이름 색

_SKIP_TEXT = {"사진", "동영상", "이모티콘", "(이모티콘)", "삭제된 메시지입니다.", "파일", "음성메시지", "사진 2장"}


def _e(x) -> str:
    return _html.escape(str(x), quote=True)


# ───────────────────────── 파싱 ─────────────────────────
def _hm(ampm, h, m) -> tuple:
    h, m = int(h), int(m)
    if ampm == "오후" and h < 12:
        h += 12
    if ampm == "오전" and h == 12:
        h = 0
    return h, m


def _mk(y, mo, d, h=0, mi=0):
    try:
        return datetime(int(y), int(mo), int(d), h, mi)
    except Exception:
        return None


_DATE_SEP = re.compile(r"^-{3,}\s*(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일.*?-{3,}$")
_PC = re.compile(
    r"^(\d{4})[년.\-/]\s*(\d{1,2})[월.\-/]\s*(\d{1,2})[일.]?\s*(오전|오후)?\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*,\s*(.{1,30}?)\s*:\s(.*)$"
)
_MOBILE = re.compile(r"^\[([^\]]{1,30})\]\s*\[(오전|오후)?\s*(\d{1,2}):(\d{2})\]\s*(.*)$")
_PLAIN = re.compile(r"^([^\s:\[\]]{1,20}(?: [^\s:\[\]]{1,20})?)\s*:\s+(.+)$")


def parse_chat(text: str, is_person=None) -> list:
    """카카오톡 csv/txt(PC·모바일), '이름: 내용' 형식의 텍스트를 메시지 리스트로."""
    t = text.lstrip("\ufeff")
    if t.lstrip()[:200].lower().startswith("date,user,message"):
        out = []
        try:
            rows = list(csv.reader(io.StringIO(t)))[1:]
        except Exception:
            rows = []
        for r in rows:
            if len(r) >= 3 and r[2].strip():
                dt = None
                try:
                    dt = datetime.strptime(r[0].strip()[:19], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass
                out.append({"name": r[1].strip() or "?", "dt": dt, "text": r[2].strip()})
        return out

    ok = is_person or (lambda n: True)
    out, cur_date = [], None
    for raw in t.splitlines():
        ln = raw.rstrip()
        s = ln.strip()
        if not s:
            if out:
                out[-1]["text"] += "\n"
            continue
        m = _DATE_SEP.match(s)
        if m:
            cur_date = (m.group(1), m.group(2), m.group(3))
            continue
        m = _PC.match(s)
        if m and ok(m.group(7).strip()):
            h, mi = _hm(m.group(4), m.group(5), m.group(6))
            out.append({"name": m.group(7).strip(), "dt": _mk(m.group(1), m.group(2), m.group(3), h, mi), "text": m.group(8).strip()})
            continue
        m = _MOBILE.match(s)
        if m and ok(m.group(1).strip()):
            h, mi = _hm(m.group(2), m.group(3), m.group(4))
            dt = _mk(*cur_date, h, mi) if cur_date else None
            out.append({"name": m.group(1).strip(), "dt": dt, "text": m.group(5).strip()})
            continue
        m = _PLAIN.match(s)
        if m and ok(m.group(1).strip()):
            out.append({"name": m.group(1).strip(), "dt": None, "text": m.group(2).strip()})
            continue
        if out:  # 줄바꿈이 있는 긴 메시지의 이어지는 줄
            out[-1]["text"] += "\n" + s
    for m_ in out:
        m_["text"] = m_["text"].strip()
    return [m_ for m_ in out if m_["text"]]


def parse_ts(s: str):
    """인스타그램/기타 시각 문자열 → naive datetime(KST). 실패 시 None."""
    s = (s or "").strip()
    if not s:
        return None
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})\D+(오전|오후)\s*(\d{1,2}):(\d{2})", s)
    if m:
        h, mi = _hm(m.group(4), m.group(5), m.group(6))
        return _mk(m.group(1), m.group(2), m.group(3), h, mi)
    for f in ("%b %d, %Y, %I:%M %p", "%b %d, %Y %I:%M %p", "%B %d, %Y, %I:%M %p", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, f)
        except Exception:
            pass
    return parse_iso(s)


def parse_iso(s: str):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if d.tzinfo:
            d = d.astimezone(KST).replace(tzinfo=None)
        return d
    except Exception:
        return None


# ───────────────────────── 통계 ─────────────────────────
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B50\u2764]")
_PARTICLE = re.compile(r"(으로|에서|에게|이랑|까지|부터|한테|이다|하고|은|는|이|가|을|를|에|의|도|로|과|와|만)$")
_STOP = {
    "그리고", "그런데", "그래서", "근데", "그냥", "진짜", "이거", "저거", "그거", "우리", "나는", "내가", "너무", "오늘", "지금",
    "이제", "일단", "아니", "그럼", "정말", "하는", "있는", "없는", "같은", "하고", "해서", "하면", "하지", "있어", "없어", "해요",
    "있다", "없다", "한다", "합니다", "입니다", "있습니다", "the", "and", "that", "for", "you", "this", "with",
}


def _tokens(text: str):
    for w in re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}", text):
        if len(w) >= 3 and re.search(r"[가-힣]", w):
            w2 = _PARTICLE.sub("", w)
            w = w2 if len(w2) >= 2 else w
        if w.lower() not in _STOP:
            yield w


def _hour_label(h: int) -> str:
    if h < 6:
        return f"새벽 {h or 12}시"
    if h < 12:
        return f"오전 {h}시"
    if h == 12:
        return "낮 12시"
    if h < 18:
        return f"오후 {h - 12}시"
    if h < 22:
        return f"저녁 {h - 12}시"
    return f"밤 {h - 12}시"


def _kdate(d) -> str:
    return f"{d.year}년 {d.month}월 {d.day}일"



# ───────────────────────── 프로젝트 맞춤 지표 (사전 기반 · 참고용) ─────────────────────────
_POS = ("좋", "행복", "기쁘", "기뻐", "감사", "고마", "재밌", "재미있", "설레", "신나", "편하", "뿌듯", "다행", "최고", "사랑", "대박", "축하", "웃겨", "괜찮")
_NEG = ("힘들", "싫", "짜증", "우울", "슬프", "화나", "화가", "지치", "지쳐", "귀찮", "별로", "최악", "망했", "속상", "외롭", "답답", "서운", "허무", "자책")
_ANX = ("불안", "걱정", "두렵", "무섭", "초조", "긴장", "떨려", "막막", "부담", "조급", "눈치", "어떡", "망설")
_SIGNALS = [
    ("질문을 던짐", lambda t: "?" in t or re.search(r"(까요|나요|ㄹ까|을까|어때)\s*$", t)),
    ("사과·미안함", lambda t: re.search(r"미안|죄송|ㅈㅅ", t)),
    ("고마움 표현", lambda t: re.search(r"고마|감사|땡큐|ㄱㅅ", t)),
    ("완곡·망설임", lambda t: re.search(r"아마|그냥|좀|인 듯|같은데|것 같|모르겠|글쎄", t)),
    ("단호한 표현", lambda t: re.search(r"무조건|절대|반드시|확실|당연|꼭 ", t)),
]
_TOPICS = [
    ("진로·미래", ("진로", "전공", "취업", "직업", "커리어", "인턴", "대학원", "자격증", "목표", "꿈", "미래")),
    ("학업·과제", ("수업", "과제", "시험", "학점", "공부", "발표", "팀플", "강의", "제출", "리포트")),
    ("사람·관계", ("친구", "가족", "부모", "연애", "선배", "동기", "후배", "교수", "사람")),
    ("현실·돈", ("돈", "알바", "월급", "등록금", "비용", "생활비")),
    ("고민·망설임", ("고민", "모르겠", "어떡", "어떻게", "고르", "선택", "결정")),
]
_I = {"나", "난", "내가", "나는", "나도", "나를", "내", "나한테", "저", "저는", "제가", "전", "저도", "저를", "내가"}
_YOU = {"너", "넌", "네가", "니가", "너는", "너도", "너를", "너한테", "당신", "당신은"}


def _hits(t: str, words) -> int:
    return sum(t.count(w) for w in words)


def _mind_card(title, hint, inner, cls="lav"):
    return f'<div class="ks-card c-{cls}"><div class="ks-h">{_e(title)}</div>{inner}<div class="ks-sub">{_e(hint)}</div></div>'


def _stack(name, parts) -> str:
    tot = sum(v for _, v, _ in parts) or 1
    segs = "".join(
        f'<span style="width:{v / tot * 100:.1f}%;background:{c}" title="{_e(l)} {v}"></span>' for l, v, c in parts if v
    )
    return f'<div class="ks-row"><span class="ks-rn">{_e(name)}</span><span class="ks-track ks-stack">{segs}</span></div>'


def _legend(items) -> str:
    return '<div class="ks-key">' + "".join(f'<span><i style="background:{c}"></i>{_e(l)}</span>' for l, c in items) + "</div>"


def _mind_slides(msgs, people) -> list:
    out = []
    P3 = people[:3]
    by = {n: [m["text"] for m in msgs if m["name"] == n and m["text"] not in _SKIP_TEXT] for n in P3}
    by = {n: v for n, v in by.items() if len(v) >= 5}
    if not by:
        return out
    # 감정 온도
    rows, tot = "", 0
    laugh = ""
    for n, v in by.items():
        pos = sum(_hits(t, _POS) for t in v)
        neg = sum(_hits(t, _NEG) for t in v)
        anx = sum(_hits(t, _ANX) for t in v)
        tot += pos + neg + anx
        rows += _stack(n, [("긍정", pos, "#4FB286"), ("부정", neg, "#E0627A"), ("불안", anx, "#E9A23B")])
        happy = sum(len(re.findall(r"[ㅋㅎ]{2,}", t)) for t in v)
        sad = sum(len(re.findall(r"[ㅠㅜ]{2,}", t)) for t in v)
        laugh += f'<div class="ks-w">{_e(n)} <small>웃음 ㅋㅋ·ㅎㅎ {happy}회 · 울음 ㅠㅠ·ㅜㅜ {sad}회</small></div>'
    if tot >= 5:
        out.append((
            "감정 온도",
            _mind_card("감정 단어의 구성", "감정 단어를 사전으로 센 비율이에요. 얼마나 말하느냐보다 어떤 감정을 자주 꺼내는지가 단서가 돼요.", rows + _legend([("긍정", "#4FB286"), ("부정", "#E0627A"), ("불안", "#E9A23B")]), "mint")
            + _mind_card("웃음·울음 표현", "ㅋㅋ·ㅠㅠ 같은 표현으로 드러나는 분위기예요.", laugh, "yellow"),
        ))
    # 말투 신호
    head = "".join(f"<span>{_e(n)}</span>" for n in by)
    grid = ""
    for label, fn in _SIGNALS:
        cells = "".join(f"<span>{int(round(sum(1 for t in v if fn(t)) / len(v) * 100))}%</span>" for v in by.values())
        grid += f'<div class="ks-gr"><b>{_e(label)}</b>{cells}</div>'
    out.append((
        "말투에 담긴 신호",
        _mind_card("메시지 중 이런 말투가 든 비율", "관계에서 조심하는지, 확신하는지, 먼저 묻는지를 보여주는 습관이에요.", f'<div class="ks-gr ks-gh"><b></b>{head}</div>{grid}', "pink"),
    ))
    # 관심사
    allt = [t for v in by.values() for t in v]
    tp = [(lab, sum(1 for t in allt if any(w in t for w in ws))) for lab, ws in _TOPICS]
    tp = [(l, c) for l, c in tp if c]
    if tp:
        mxc = max(c for _, c in tp)
        bars = "".join(
            f'<div class="ks-row"><span class="ks-rn" style="width:5.6rem">{_e(l)}</span><span class="ks-track"><span class="ks-fill" style="width:{int(c / mxc * 100)}%;background:#E48B5B"></span></span><span class="ks-pc">{c}회</span></div>'
            for l, c in sorted(tp, key=lambda x: -x[1])
        )
        wc = Counter(w for t in allt for w in _tokens(t))
        tw = [w for w, c in wc.most_common(5) if c >= 2]
        words = "".join(f'<span class="ks-chip">{_e(w)}</span>' for w in tw)
        out.append((
            "머릿속 관심사",
            _mind_card("자주 꺼낸 주제 (해당 말이 들어간 메시지 수)", "진로 고민이 어느 주제와 맞닿아 있는지 보는 단서예요.", bars, "peach")
            + (_mind_card("가장 많이 쓴 단어", "", f'<div class="ks-chips">{words}</div>', "lav") if tw else ""),
        ))
    # 나·너·우리
    rows = ""
    ok = False
    for n, v in by.items():
        i = u = w = 0
        for t in v:
            for tok in re.split(r"[\s,.!?~]+", t):
                if tok in _I:
                    i += 1
                elif tok in _YOU:
                    u += 1
                elif tok.startswith("우리"):
                    w += 1
        if i + u + w >= 5:
            ok = True
        rows += _stack(n, [("나", i, "#7C6AE6"), ("너", u, "#F09AE0"), ("우리", w, "#F7CB45")])
    if ok:
        out.append((
            "나 · 너 · 우리",
            _mind_card("누구를 향해 말하는가", "‘나’가 많으면 자기 이야기를, ‘너’가 많으면 상대에게 초점을, ‘우리’가 많으면 함께하는 관점을 자주 쓴다는 뜻이에요.", rows + _legend([("나", "#7C6AE6"), ("너", "#F09AE0"), ("우리", "#F7CB45")]), "lav"),
        ))
    return out


def compute(msgs: list, mask=lambda s: s) -> dict:
    """통계를 계산해 {'people': [...], 'slides': [(제목, html), ...]} 로 돌려준다."""
    msgs = [m for m in msgs if m.get("text")]
    if len(msgs) < 5:
        return {"people": [], "slides": []}
    n_dt = sum(1 for m in msgs if m["dt"])
    has_dt = n_dt >= max(5, 0.7 * len(msgs))
    if has_dt:
        msgs = sorted([m for m in msgs if m["dt"]], key=lambda m: m["dt"])

    cnt = Counter(m["name"] for m in msgs)
    people = [n for n, _ in cnt.most_common(4)]
    pcol = {n: PALETTE[i] for i, n in enumerate(people)}
    ncol = {n: NAME_COLORS[i] for i, n in enumerate(people)}
    total = sum(cnt.values())
    slides = []

    # 1) 달력
    if has_dt:
        s = _cal_slide(msgs)
        if s:
            slides.append(("하루하루 쌓인 대화", s))

    # 2) 관계 방식: 지분 · 먼저 말 건 사람 · 칼답률
    if len(cnt) >= 2:
        body = _card("lav", "대화 지분", _donut(people, cnt, total, pcol, len(cnt)))
        if has_dt:
            starts = Counter()
            last = None
            for m in msgs:
                if last is None or (m["dt"] - last) >= timedelta(hours=6):
                    starts[m["name"]] += 1
                last = m["dt"]
            ts = sum(starts.values())
            if ts >= 3:
                bars = "".join(_bar(n, starts[n] / ts, pcol[n]) for n in people)
                body += _card("pink", "먼저 말 건 사람", bars)
            q = _quick_card(msgs, people, pcol)
            body += q
        slides.append(("대화 속 관계 방식", body))

    # 3) 마음 신호 (감정 · 말투 · 관심사 · 나너우리)
    slides += _mind_slides(msgs, people)

    # 3-1) 대화 시간대
    if has_dt:
        s = _hours_slide(msgs)
        if s:
            slides.append(("대화하는 시간", s))

    # 4) 가장 긴 메시지
    longest = []
    for n in people[:3]:
        mm = max((m for m in msgs if m["name"] == n and m["text"] not in _SKIP_TEXT), key=lambda m: len(m["text"]), default=None)
        if mm and len(mm["text"]) >= 30:
            longest.append((n, mm))
    if longest:
        body = ""
        for n, mm in longest:
            full = mask(mm["text"])
            head, rest = (full[:70].rstrip() + "…", full) if len(full) > 90 else (full, "")
            dt = f'<div class="ks-sub">{_e(_kdate(mm["dt"]))}</div>' if mm["dt"] else ""
            bub = (
                f'<div class="ks-b"><div class="ks-n" style="color:{ncol[n]}">{_e(n)}</div>'
                f'<span class="ks-t">{_e(head)}</span>'
                + (f'<details><summary>전체 보기</summary><span class="ks-t">{_e(rest)}</span></details>' if rest else "")
                + "</div>"
            )
            body += f'<div class="ks-card c-beige"><div class="ks-h">{_e(n)}</div><div class="ks-num">{len(mm["text"])}<small>자</small></div>{dt}{bub}</div>'
        slides.append(("가장 길게 털어놓은 말", body))

    # 5) 처음 / 지금
    def day_card(title, rows, d):
        h = f'<div class="ks-date">{_e(_kdate(d))}</div>' if d else ""
        return f'<div class="ks-card c-beige">{h}{bubbles([(m["name"], mask(m["text"][:140])) for m in rows], ncol)}</div>'

    first_rows, last_rows, d0, d1 = _edge_days(msgs, has_dt)
    if first_rows:
        slides.append(("모든 건 여기서 시작", day_card("", first_rows, d0)))
    if last_rows and last_rows != first_rows:
        slides.append(("그리고 지금, 여기까지", day_card("", last_rows, d1)))

    # 6) 자주 쓰는 말
    s = _words_slide(msgs, people, cnt, pcol)
    if s:
        slides.append(("자주 쓰는 말", s))
    return {"people": people, "slides": slides}


def _edge_days(msgs, has_dt):
    if not has_dt:
        return msgs[:6], msgs[-5:], None, None
    d0, d1 = msgs[0]["dt"].date(), msgs[-1]["dt"].date()
    first = [m for m in msgs if m["dt"].date() == d0][:6]
    last = [m for m in msgs if m["dt"].date() == d1][-5:]
    return first, last, d0, d1


def _card(cls: str, title: str, inner: str) -> str:
    return f'<div class="ks-card c-{cls}"><div class="ks-h">{_e(title)}</div>{inner}</div>'


def _bar(name, frac, color) -> str:
    p = int(round(frac * 100))
    return (
        f'<div class="ks-row"><span class="ks-rn">{_e(name)}</span>'
        f'<span class="ks-track"><span class="ks-fill" style="width:{max(p, 0)}%;background:{color}"></span></span>'
        f'<span class="ks-pc">{p}%</span></div>'
    )


def _donut(people, cnt, total, pcol, n_all) -> str:
    segs, off = "", 0.0
    shown = sum(cnt[n] for n in people)
    parts = [(n, cnt[n]) for n in people]
    if total - shown > 0:
        parts.append(("기타", total - shown))
    leg = ""
    for i, (n, c) in enumerate(parts):
        frac = c / total * 100
        col = pcol.get(n, PALETTE[4])
        segs += (
            f'<circle r="15.9155" cx="21" cy="21" fill="none" stroke="{col}" stroke-width="7" '
            f'stroke-dasharray="{frac:.2f} {100 - frac:.2f}" stroke-dashoffset="{25 - off:.2f}"/>'
        )
        off += frac
        leg += (
            f'<div class="ks-lg"><i style="background:{col}"></i><b>#{i + 1}</b><span>{_e(n)}</span>'
            f'<em>{int(round(frac))}%</em></div>'
        )
    return f'<div class="ks-dn"><svg viewBox="0 0 42 42" width="130" height="130">{segs}</svg><div class="ks-legend">{leg}</div></div>'


def _cal_slide(msgs) -> str:
    per = Counter(m["dt"].date() for m in msgs)
    d0, d1 = min(per), max(per)
    start = date(d0.year, 1, 1)
    if (d1.year - d0.year) * 12 + d1.month - d0.month > 11 or d0.year != d1.year and False:
        k = d1.year * 12 + d1.month - 1 - 11
        start = date(k // 12, k % 12 + 1, 1)
    mx = max(per.values())
    rows, y, mo = "", None, date(start.year, start.month, 1)
    end = date(d1.year, d1.month, 1)
    while mo <= end:
        if y != mo.year:
            rows += f'<div class="ks-yr">{mo.year}</div>'
            y = mo.year
        nxt = date(mo.year + (mo.month == 12), mo.month % 12 + 1, 1)
        nd = (nxt - mo).days
        cells = ""
        for d in range(1, 32):
            if d > nd:
                cells += '<i class="x"></i>'
                continue
            c = per.get(date(mo.year, mo.month, d), 0)
            if not c:
                cells += "<i></i>"
            else:
                k = 0.25 + 0.75 * (c / mx) ** 0.6
                r, g, b = (int(a + (z - a) * k) for a, z in zip((0x8E, 0x7C, 0x5A), (0xF8, 0xD7, 0x7E)))
                cells += f'<i style="background:rgb({r},{g},{b})" title="{mo.month}/{d} · {c}개"></i>'
        rows += f'<div class="ks-cr"><span>{mo.month}월</span>{cells}</div>'
        mo = nxt
    return (
        '<div class="ks-sub0">한 칸이 하루, 밝을수록 대화가 많았던 날이에요.</div>'
        f'<div class="ks-card c-dark"><div class="ks-h" style="color:#FFF1C2">달력</div><div class="ks-cal">{rows}</div></div>'
    )


def _quick_card(msgs, people, pcol) -> str:
    quick, repl = Counter(), Counter()
    for a, b in zip(msgs, msgs[1:]):
        if a["name"] != b["name"]:
            repl[b["name"]] += 1
            if (b["dt"] - a["dt"]).total_seconds() <= 60:
                quick[b["name"]] += 1
    rates = sorted(((n, quick[n] / repl[n]) for n in people if repl[n] >= 3), key=lambda x: -x[1])[:3]
    if len(rates) < 2:
        return ""
    order = [1, 0, 2] if len(rates) == 3 else [1, 0]
    medals = ["🥇", "🥈", "🥉"]
    boxes = ""
    for i in order:
        n, r = rates[i]
        pad = {0: 2.2, 1: 1.4, 2: 0.8}[i]
        boxes += (
            f'<div class="ks-pod" style="padding-top:{pad}rem;background:{pcol[n]}55"><div>{medals[i]}</div>'
            f'<div class="ks-pn">{_e(n)}</div><div class="ks-pv">{int(round(r * 100))}%</div></div>'
        )
    return f'<div class="ks-card c-lav"><div class="ks-h">칼답률</div><div class="ks-podium">{boxes}</div><div class="ks-sub">1분 안에 보낸 답장 비율 · 상대 말에 얼마나 바로 반응하는지 보여줘요.</div></div>'


def _hours_slide(msgs) -> str:
    out = ""
    per = Counter(m["dt"].date() for m in msgs)
    bd, bc = per.most_common(1)[0]
    out += f'<div class="ks-card c-peach"><div class="ks-h">메시지가 가장 많았던 날</div><div class="ks-num">{bc}<small>개 · {_e(_kdate(bd))}</small></div></div>'
    days = sorted(per)
    best = run = 1
    gap = 0
    for a, b in zip(days, days[1:]):
        dd = (b - a).days
        run = run + 1 if dd == 1 else 1
        best = max(best, run)
        gap = max(gap, dd - 1)
    sm = f'<div class="ks-card c-mint"><div class="ks-h">최장 연속 대화</div><div class="ks-num">{best}<small>일</small></div></div>'
    sm += f'<div class="ks-card c-pink"><div class="ks-h">가장 길었던 침묵</div><div class="ks-num">{gap}<small>일</small></div></div>'
    out += f'<div class="ks-two">{sm}</div>'
    hrs = Counter(m["dt"].hour for m in msgs)
    mxh = max(hrs.values())
    ph = hrs.most_common(1)[0][0]
    bars = "".join(
        f'<i style="height:{max(3, int(hrs.get(h, 0) / mxh * 100))}%;{"background:#5A4A10" if h == ph else ""}"></i>' for h in range(24)
    )
    night = sum(v for h, v in hrs.items() if h >= 22 or h < 5) / len(msgs)
    out += (
        f'<div class="ks-card c-yellow"><div class="ks-h">시간대별</div><div class="ks-hist">{bars}</div>'
        '<div class="ks-ax"><span>밤 12시</span><span>오전 6시</span><span>낮 12시</span><span>저녁 6시</span></div>'
        f'<div class="ks-sub">밤 10시~새벽 5시 {int(round(night * 100))}% · 가장 활발한 시간 {_hour_label(ph)}</div></div>'
    )
    return out


def _words_slide(msgs, people, cnt, pcol) -> str:
    out = ""
    short = Counter(m["text"].strip() for m in msgs if 1 <= len(m["text"].strip()) <= 6 and m["text"].strip() not in _SKIP_TEXT and "\n" not in m["text"])
    top = [(w, c) for w, c in short.most_common(3) if c >= 2]
    if top:
        li = "".join(f'<div class="ks-w">{_e(w)} <small>×{c}</small></div>' for w, c in top)
        out += _card("peach", "말버릇", li)
    wc = Counter(w for m in msgs if m["text"] not in _SKIP_TEXT for w in _tokens(m["text"]))
    tw = [w for w, c in wc.most_common(5) if c >= 2]
    if len(tw) >= 3:
        li = "".join(f'<div class="ks-rk"><b>#{i + 1}</b>{_e(w)}</div>' for i, w in enumerate(tw))
        out += _card("mint", "가장 많이 쓴 단어", li)
    ec = Counter(ch for m in msgs for ch in _EMOJI.findall(m["text"]))
    te = [c for c, _ in ec.most_common(3)]
    if te:
        out += _card("yellow", "가장 많이 쓴 이모지", f'<div class="ks-emo">{"".join(_e(c) for c in te)}</div>')
    icon = Counter(m["name"] for m in msgs if m["text"].strip() in ("이모티콘", "(이모티콘)"))
    ti = sum(icon.values())
    if ti >= 3 and len(cnt) >= 2:
        bars = "".join(_bar(n, icon[n] / ti, pcol[n]) for n in people)
        out += _card("lav", "이모티콘", f'<div class="ks-num">{ti}<small>개</small></div>{bars}')
    return out


# ───────────────────────── 채팅 말풍선 ─────────────────────────
def bubbles(rows: list, ncol: dict, hi: str = "") -> str:
    """rows: [(이름 또는 None, 내용)] → 카톡 스타일 말풍선 HTML. 같은 사람이 이어 말하면 이름은 처음만."""
    out, prev = "", object()
    for name, text in rows:
        lab = ""
        if name and name != prev:
            lab = f'<div class="ks-n" style="color:{ncol.get(name, NAME_COLORS[4])}">{_e(name)}</div>'
        prev = name
        cls = "ks-b hi" if (hi and name == hi) else "ks-b"
        out += f'<div class="{cls}">{lab}<span class="ks-t">{_e(text)}</span></div>'
    return f'<div class="ks-chat">{out}</div>'


def parse_evidence(s: str) -> list:
    """'이름: 내용 → 이름: 내용' 형태의 인용 문자열을 [(이름|None, 내용)]으로."""
    rows = []
    for part in re.split(r"\s*(?:→|->|⇒)\s*", str(s).strip()):
        part = part.strip().strip("“”\"'")
        if not part:
            continue
        m = re.match(r"^([^:：\n]{1,12})\s*[:：]\s*(.+)$", part, re.S)
        rows.append((m.group(1).strip(), m.group(2).strip().strip("“”\"'")) if m else (None, part))
    return rows


CSS = """
<style>
.ks-card{border-radius:26px;padding:1.3rem 1.4rem;margin:.8rem 0;color:#2B2B2B}
.ks-card .ks-h{font-size:1rem;font-weight:800;margin-bottom:.8rem}
.c-lav{background:#D7CAFB;color:#3B1F7A}.c-pink{background:#F6C4D0;color:#8A1B3D}.c-peach{background:#F6D0B9;color:#6B3512}
.c-mint{background:#C5F0E0;color:#17453A}.c-yellow{background:#F8ECB7;color:#5A4A10}.c-beige{background:#EDE6DB;color:#222}.c-dark{background:#151B33;color:#ddd}
.ks-num{font-size:2.8rem;font-weight:900;line-height:1.1}.ks-num small{font-size:.95rem;font-weight:700;opacity:.65;margin-left:.15rem}
.ks-sub{font-size:.82rem;opacity:.7;margin-top:.5rem}.ks-sub0{color:#717171;font-size:.95rem;margin:.2rem 0 .6rem}
.ks-two{display:grid;grid-template-columns:1fr 1fr;gap:.8rem}.ks-two .ks-card{margin:.8rem 0}
.ks-dn{display:flex;align-items:center;gap:1.2rem}.ks-dn svg{flex:0 0 auto;transform:none}
.ks-legend{flex:1}.ks-lg{display:flex;align-items:center;gap:.5rem;margin:.45rem 0;font-weight:800}
.ks-lg i{width:14px;height:14px;border-radius:50%;display:inline-block;border:2px solid rgba(0,0,0,.25)}
.ks-lg b{opacity:.5;font-size:.78rem;width:1.6rem}.ks-lg span{flex:1}.ks-lg em{font-style:normal}
.ks-row{display:flex;align-items:center;gap:.7rem;margin:.55rem 0;font-weight:700;font-size:.9rem}
.ks-rn{width:4.2rem;flex:0 0 auto}.ks-track{flex:1;height:14px;border-radius:99px;background:rgba(0,0,0,.12);overflow:hidden;display:block}
.ks-fill{display:block;height:100%;border-radius:99px}.ks-pc{width:2.6rem;text-align:right}
.ks-podium{display:flex;gap:.6rem;align-items:flex-end;margin-top:.3rem}
.ks-pod{flex:1;text-align:center;border-radius:16px;padding:.8rem .3rem;font-weight:800}
.ks-pn{font-size:.82rem;margin:.2rem 0}.ks-pv{font-size:1.5rem}
.ks-hist{display:flex;align-items:flex-end;gap:3px;height:110px;margin-top:.4rem}.ks-hist i{flex:1;background:#C9BA85;border-radius:3px 3px 0 0;display:block}
.ks-ax{display:flex;justify-content:space-between;font-size:.72rem;opacity:.75;margin-top:.35rem}
.ks-cal{overflow-x:auto}.ks-yr{color:#8A8AA0;font-weight:800;margin:.6rem 0 .3rem}
.ks-cr{display:grid;grid-template-columns:2.2rem repeat(31,minmax(7px,1fr));gap:3px;align-items:center;margin:3px 0}
.ks-cr span{color:#8A8AA0;font-size:.72rem;font-weight:800}.ks-cr i{aspect-ratio:1;border-radius:3px;background:#2A2F47;display:block}.ks-cr i.x{background:transparent}
.ks-stack{display:flex}.ks-stack span{display:block;height:100%}
.ks-key{display:flex;gap:.9rem;font-size:.78rem;font-weight:700;margin-top:.4rem;flex-wrap:wrap}.ks-key i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:.3rem}
.ks-gr{display:grid;grid-template-columns:1.6fr repeat(3,1fr);gap:.3rem;padding:.4rem 0;border-bottom:1px solid rgba(0,0,0,.08);font-size:.9rem}
.ks-gr b{font-weight:700}.ks-gr span{text-align:center;font-weight:800}.ks-gh span{font-size:.78rem;opacity:.7}
.ks-chips{display:flex;gap:.4rem;flex-wrap:wrap}.ks-chip{background:rgba(255,255,255,.6);border-radius:99px;padding:.25rem .8rem;font-weight:800;font-size:.9rem}
.ks-w{font-size:1.15rem;font-weight:800;margin:.3rem 0}.ks-w small{opacity:.6}.ks-rk{font-size:1.1rem;font-weight:700;margin:.4rem 0}.ks-rk b{opacity:.5;font-size:.78rem;margin-right:.9rem}
.ks-emo{font-size:2.4rem;letter-spacing:.5rem}
.ks-date{font-size:1.05rem;font-weight:800;margin-bottom:.7rem}
.ks-chat{display:flex;flex-direction:column;gap:.35rem;align-items:flex-start}
.ks-b{background:#fff;border-radius:16px;padding:.5rem .85rem;max-width:92%;font-size:.95rem;line-height:1.6;box-shadow:0 1px 2px rgba(0,0,0,.08);color:#222;word-break:keep-all}
.ks-b.hi{background:#E4F6D9}
.ks-b .ks-n{font-size:.72rem;font-weight:800;margin-bottom:.05rem}.ks-t{white-space:pre-wrap}
.ks-b details summary{cursor:pointer;font-size:.8rem;font-weight:700;color:#555;margin-top:.3rem}
.ks-card .ks-b{margin-top:.4rem}
.ev-wrap{background:#EDE6DB;border-radius:16px;padding:.7rem .8rem;margin:.45rem 0}
</style>
"""
