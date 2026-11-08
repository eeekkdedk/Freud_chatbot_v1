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
    "있다", "없다", "뭐야", "설명해", "설명", "쉽게", "알려줘", "해줘", "무슨", "어떻게", "어떤", "이건", "그건", "근데", "아직", "계속", "한번", "다시", "한다", "합니다", "입니다", "있습니다", "the", "and", "that", "for", "you", "this", "with",
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



_I = {"나", "난", "내가", "나는", "나도", "나를", "내", "나한테", "저", "저는", "제가", "전", "저도", "저를"}


def _card(cls: str, title: str, inner: str) -> str:
    return f'<div class="ks-card c-{cls}"><div class="ks-h">{_e(title)}</div>{inner}</div>'


def _bar(name, frac, color) -> str:
    p = int(round(frac * 100))
    return (
        f'<div class="ks-row"><span class="ks-rn">{_e(name)}</span>'
        f'<span class="ks-track"><span class="ks-fill" style="width:{max(p, 0)}%;background:{color}"></span></span>'
        f'<span class="ks-pc">{p}%</span></div>'
    )


def _cal_slide(msgs) -> str:
    per = Counter(m["dt"].date() for m in msgs)
    d0, d1 = min(per), max(per)
    start = date(d0.year, 1, 1) if (d1.year - d0.year) * 12 + d1.month - d0.month >= 3 else date(d0.year, d0.month, 1)
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




# ───────────────────────── 연결고리별 통계 (사전 기반 · 참고용) ─────────────────────────
# 프로이트 챗봇 논리구조의 1→2단계 연결고리 5개를 숫자로 보여준다. 해석은 리포트(A~E)가 맡는다.
_POS = ("좋", "행복", "기쁘", "기뻐", "감사", "고마", "재밌", "재미있", "설레", "신나", "편하", "뿌듯", "다행", "최고", "사랑", "대박", "축하", "웃겨", "괜찮")
_NEG = ("힘들", "싫", "짜증", "우울", "슬프", "화나", "화가", "지치", "지쳐", "귀찮", "별로", "최악", "망했", "속상", "외롭", "답답", "서운", "허무", "자책")
_ANX = ("불안", "걱정", "두렵", "무섭", "초조", "긴장", "떨려", "막막", "부담", "조급", "눈치", "어떡", "망설")

_REL = [  # ① 관계적 자아 7요인
    ("관계 회피", ("나중에", "바빠", "피곤", "됐어", "귀찮", "다음에", "그냥 괜찮", "혼자 있")),
    ("타인 의식", ("눈치", "남들", "다들", "사람들이", "어떻게 볼", "이상하게", "시선", "비현실적")),
    ("주도성", ("하자", "가자", "내가 할게", "정하자", "해보자", "같이 하", "내가 정리", "내가 알아")),
    ("도구적 관계", ("부탁", "빌려", "알려줘", "도와줘", "필요하면", "대신", "해줄 수")),
    ("공감·배려", ("고마", "감사", "괜찮아?", "걱정", "수고", "미안", "고생", "잘했", "이해해")),
    ("지지받기", ("힘들어", "위로", "어떡해", "응원", "괜찮을까", "들어줘", "도와")),
    ("관계 의존", ("보고싶", "보고 싶", "연락", "어디야", "뭐해", "같이 있", "외로", "기다")),
]
_WANT_DIRECT = re.compile(r"싶[어다은]|싶은|원해|바라|했으면|되고 싶|갖고 싶")
_WANT_EMO = ("부럽", "서운", "뿌듯", "속상", "설레", "질투", "아쉽", "억울", "섭섭", "짜증")
_WANT_VALUE = re.compile(r"보다|가장 중요|제일|중요한 건|중요하|우선|포기")
_MASLOW = [
    ("자아실현", ("성장", "의미", "도전", "꿈", "해보고 싶", "배우", "목표", "하고 싶")),
    ("존중", ("인정", "칭찬", "존중", "자신감", "무시", "자랑", "잘하")),
    ("애정·소속", ("친구", "같이", "외롭", "사랑", "함께", "동아리", "가족", "소속")),
    ("안전", ("취업", "안정", "돈", "걱정", "불안", "장학금", "현실", "생활비")),
    ("생리", ("잠", "피곤", "배고", "밥", "아파", "졸려")),
]
_CAUSAL = ("왜냐하면", "그래서", "때문", "그러니까", "결국", "생각해보니", "돌아보면", "내가 왜", "라서", "니까")
_UNSURE = ("아마", "것 같", "인 듯", "모르겠", "글쎄", "어쩌면", "할까")
_SURE = ("확실", "무조건", "당연", "절대", "반드시")
_CONFUSED = re.compile(r"이해가 안|이해 안|다시 설명|예시|모르겠|헷갈|무슨 말|어렵|잘 모르")
_RESOLVED = re.compile(r"아 그렇구나|아 알겠|알겠어|이해했|이해됐|아하|오 그렇|그렇구나|그건 알겠")
_AI_NAME = re.compile(r"chat\s*gpt|gpt|assistant|claude|gemini|copilot|챗쿠|챗봇|^ai$|bot$|어시스턴트", re.I)
_POLITE = re.compile(r"(요|니다|니까|세요|습니다)[.!?~ ]*$")


def is_ai(name: str) -> bool:
    return bool(_AI_NAME.search(name or ""))


def _hits(t: str, words) -> int:
    return sum(t.count(w) for w in words)


def _tag(code: str, report: str, hint: str = "") -> str:
    return (
        f'<div class="ks-tag"><b>{_e(code)}</b> 리포트 {_e(report)} 섹션과 이어져요'
        + (f"<span>{_e(hint)}</span>" if hint else "")
        + "</div>"
    )


_NOTE = "단어를 사전으로 센 단서예요. 진단이 아니라 리포트에서 해석할 재료예요."


def _pct(n: int, d: int) -> int:
    return int(round(n / d * 100)) if d else 0


def _hbars(rows, color="#7C6AE6", unit="%") -> str:
    mx = max((v for _, v in rows), default=0) or 1
    return "".join(
        f'<div class="ks-row"><span class="ks-rn" style="width:6rem">{_e(l)}</span>'
        f'<span class="ks-track"><span class="ks-fill" style="width:{int(v / mx * 100)}%;background:{color}"></span></span>'
        f'<span class="ks-pc">{v}{unit}</span></div>'
        for l, v in rows
    )


def _tiles(items) -> str:
    return '<div class="ks-two3">' + "".join(
        f'<div class="ks-tile"><div class="ks-tn">{_e(v)}</div><div class="ks-tl">{_e(l)}</div></div>' for l, v in items
    ) + "</div>"


def _feat(texts: list) -> dict:
    n = len(texts) or 1
    return {
        "n": len(texts),
        "len": round(sum(len(t) for t in texts) / n, 1),
        "polite": _pct(sum(1 for t in texts if _POLITE.search(t.strip())), n),
        "emo": _pct(sum(1 for t in texts if _hits(t, _POS + _NEG + _ANX)), n),
        "ask": _pct(sum(1 for t in texts if "?" in t or re.search(r"(까요|나요|ㄹ까|을까|어때)\s*$", t)), n),
        "laugh": _pct(sum(1 for t in texts if re.search(r"[ㅋㅎㅠㅜ]{2,}", t)), n),
    }


def compute(msgs: list, mask=lambda s: s, target: str = "", kind: str = "") -> dict:
    """연결고리별 통계 카드를 만들어 {'people': [...], 'slides': [(제목, html), ...]} 로 돌려준다.
    target: 분석 대상자 이름, kind: 'chatku'면 모든 발화를 AI 대화로 본다."""
    msgs = [m for m in msgs if m.get("text")]
    if len(msgs) < 5:
        return {"people": [], "slides": []}
    has_dt = sum(1 for m in msgs if m["dt"]) >= max(5, 0.7 * len(msgs))
    if has_dt:
        msgs = sorted([m for m in msgs if m["dt"]], key=lambda m: m["dt"])
    cnt = Counter(m["name"] for m in msgs)
    people = [n for n, _ in cnt.most_common(4)]
    humans = [n for n in cnt if not is_ai(n)]
    me = target if target in cnt else (max(humans, key=lambda n: cnt[n]) if humans else people[0])

    # 내 발화를 '사람 앞 / AI 앞'으로 나눈다 (바로 앞·뒤 발화자 기준)
    hu_ctx, ai_ctx = [], []
    for i, m in enumerate(msgs):
        if m["name"] != me or m["text"] in _SKIP_TEXT:
            continue
        nb = [msgs[j]["name"] for j in (i - 1, i + 1) if 0 <= j < len(msgs) and msgs[j]["name"] != me]
        if kind == "chatku" or (nb and any(is_ai(x) for x in nb)):
            ai_ctx.append(i)
        else:
            hu_ctx.append(i)
    T = lambda idx: [msgs[i]["text"] for i in idx]  # noqa: E731
    my_hu, my_ai = T(hu_ctx), T(ai_ctx)
    my_all = my_hu + my_ai
    slides = []

    # 0) 대화 개요
    ov = _tiles([("전체 메시지", f"{len(msgs)}개"), ("내 발화", f"{len(my_all)}개"), ("대화 상대", f"{max(len(cnt) - 1, 0)}명")])
    if has_dt:
        d0, d1 = msgs[0]["dt"].date(), msgs[-1]["dt"].date()
        ov += f'<div class="ks-sub0">{_e(_kdate(d0))} ~ {_e(_kdate(d1))} · 분석 대상: {_e(me)}</div>'
        ov += _cal_slide(msgs)
        hrs = Counter(m["dt"].hour for m in msgs)
        mxh, ph = max(hrs.values()), hrs.most_common(1)[0][0]
        bars = "".join(f'<i style="height:{max(3, int(hrs.get(h, 0) / mxh * 100))}%;{"background:#5A4A10" if h == ph else ""}"></i>' for h in range(24))
        ov += (
            f'<div class="ks-card c-yellow"><div class="ks-h">대화한 시간대</div><div class="ks-hist">{bars}</div>'
            '<div class="ks-ax"><span>밤 12시</span><span>오전 6시</span><span>낮 12시</span><span>저녁 6시</span></div>'
            f'<div class="ks-sub">가장 활발한 시간 {_hour_label(ph)}</div></div>'
        )
    else:
        ov += f'<div class="ks-sub0">분석 대상: {_e(me)} · 시각 정보가 없는 대화라 날짜·시간대 통계는 생략했어요.</div>'
    slides.append(("대화 개요", ov))

    # ① 관계 속 내 모습 → A
    others = [n for n in humans if n != me and cnt[n] >= 3]
    if len(my_hu) >= 8 and others:
        rows = sorted(((l, _pct(sum(1 for t in my_hu if _hits(t, ws)), len(my_hu))) for l, ws in _REL), key=lambda x: -x[1])
        body = _tag("연결고리 ①", "A", "인간관계 속 내 모습") + _card("lav", "관계적 자아 표현 (내 사람 대화 중 해당 말이 든 비율)", _hbars(rows) + f'<div class="ks-sub">{_NOTE}</div>')
        # 상대별 말투
        per = {}
        for n in others[:3]:
            tx = [msgs[i]["text"] for i in hu_ctx if i > 0 and msgs[i - 1]["name"] == n]
            if len(tx) >= 5:
                per[n] = _feat(tx)
        if len(per) >= 2:
            head = "".join(f"<span>{_e(n)}</span>" for n in per)
            grid = ""
            for lab, key, u in (("존댓말", "polite", "%"), ("ㅋㅋ·ㅠㅠ", "laugh", "%"), ("평균 글자수", "len", "자")):
                grid += f'<div class="ks-gr" style="grid-template-columns:1.6fr repeat({len(per)},1fr)"><b>{lab}</b>' + "".join(f"<span>{f[key]}{u}</span>" for f in per.values()) + "</div>"
            body += _card("pink", "상대에 따라 달라지는 내 말투", f'<div class="ks-gr ks-gh" style="grid-template-columns:1.6fr repeat({len(per)},1fr)"><b></b>{head}</div>{grid}<div class="ks-sub">상대별로 말투가 얼마나 다른지 보여줘요.</div>')
        if has_dt:
            starts, last = Counter(), None
            for m in msgs:
                if last is None or (m["dt"] - last) >= timedelta(hours=6):
                    starts[m["name"]] += 1
                last = m["dt"]
            ts = sum(starts.values())
            if ts >= 3:
                body += _card("mint", "먼저 말을 거는 편인가", _hbars([(me, _pct(starts[me], ts))]) + '<div class="ks-sub">6시간 이상 쉰 뒤 먼저 시작한 대화의 비율 (주도성 단서)</div>')
        slides.append(("관계 속 내 모습", body))

    # ② AI 앞의 나 → B
    if len(my_ai) >= 5:
        fa = _feat(my_ai)
        if len(my_hu) >= 5:
            fh = _feat(my_hu)
            grid = '<div class="ks-gr ks-gh"><b></b><span>사람 앞</span><span>AI 앞</span></div>'
            for lab, key, u in (("평균 글자수", "len", "자"), ("존댓말", "polite", "%"), ("감정 표현이 든 말", "emo", "%"), ("질문하는 말", "ask", "%"), ("ㅋㅋ·ㅠㅠ", "laugh", "%")):
                grid += f'<div class="ks-gr" style="grid-template-columns:1.6fr 1fr 1fr"><b>{lab}</b><span>{fh[key]}{u}</span><span>{fa[key]}{u}</span></div>'
            only = sorted(set(w for t in my_ai for w in _tokens(t)) - set(w for t in my_hu for w in _tokens(t)), key=lambda w: -sum(t.count(w) for t in my_ai))[:5]
            chips = f'<div class="ks-chips">{"".join(f"<span class=ks-chip>{_e(w)}</span>" for w in only)}</div>' if only else ""
            body = _tag("연결고리 ②", "B", "사람 앞과 AI 앞의 표현 차이") + _card("mint", "같은 사람, 다른 말투", grid + f'<div class="ks-sub">{_NOTE}</div>')
            if chips:
                body += _card("yellow", "AI 앞에서만 꺼낸 말", chips + '<div class="ks-sub">사람과의 대화에는 나오지 않고 AI와의 대화에만 나온 단어예요.</div>')
        else:
            body = _tag("연결고리 ②", "B", "AI 앞의 내 모습") + _card(
                "mint",
                "AI에게 말하는 방식",
                _tiles([("내 발화", f"{fa['n']}개"), ("평균 글자수", f"{fa['len']}자"), ("질문하는 말", f"{fa['ask']}%"), ("존댓말", f"{fa['polite']}%"), ("감정 표현", f"{fa['emo']}%")])
                + '<div class="ks-sub">사람과의 대화도 함께 올리면 사람 앞의 나와 나란히 비교해 드려요.</div>',
            )
        slides.append(("AI 앞의 나", body))

    # ⑤ 막힌 지점 → E  (AI 대화 위주, 없으면 전체)
    pool = hu_ctx + ai_ctx if not ai_ctx else ai_ctx
    pool = sorted(pool)
    conf = [i for i in pool if _CONFUSED.search(msgs[i]["text"])]
    if len(conf) >= 2:
        # 혼란 신호를 3발화 이내로 이어지는 묶음으로 만들고, 묶음 직후 이해 표현이 나오면 해소, 아니면 미해소로 본다.
        clusters, cur = [], []
        for pos, idx in enumerate(pool):
            if idx in conf:
                if cur and pos - cur[-1] <= 3:
                    cur.append(pos)
                else:
                    if cur:
                        clusters.append(cur)
                    cur = [pos]
        if cur:
            clusters.append(cur)
        resolved = unresolved = 0
        topics = Counter()
        for cl in clusters:
            nxt = pool[cl[-1] + 1] if cl[-1] + 1 < len(pool) else None
            ok = nxt is not None and bool(_RESOLVED.search(msgs[nxt]["text"]))
            if ok:
                resolved += 1
            elif len(cl) >= 2:
                unresolved += 1
                for q in range(max(cl[0] - 1, 0), cl[-1] + 1):
                    topics.update(w for w in _tokens(msgs[pool[q]]["text"]) if not _CONFUSED.search(w))
        tw = [w for w, c in topics.most_common(4) if c >= 2]
        tiles = _tiles([("혼란 신호", f"{len(conf)}회"), ("이해로 이어짐", f"{resolved}번"), ("계속 되물음", f"{unresolved}번")])
        chips = f'<div class="ks-chips">{"".join(f"<span class=ks-chip>{_e(w)}</span>" for w in tw)}</div><div class="ks-sub">되묻기가 이어진 대화에 자주 나온 단어 (주제 후보)</div>' if (tw and unresolved) else ""
        body = _tag("연결고리 ⑤", "E", "못하는 것 후보") + _card("peach", "질문하다 막힌 순간", tiles + '<div class="ks-sub">“이해가 안 돼”, “다시 설명해 줘” 같은 신호를 센 값이에요. 질문 횟수만으로는 판단하지 않고, 되묻기가 계속된 경우만 후보로 봐요.</div>')
        if chips:
            body += _card("lav", "되묻기가 이어진 주제 후보", chips)
        slides.append(("막힌 지점", body))

    # ③ 내면 욕망 단서 → C
    if len(my_all) >= 8:
        d = [t for t in my_all if _WANT_DIRECT.search(t)]
        e_ = [t for t in my_all if _hits(t, _WANT_EMO)]
        v = [t for t in my_all if _WANT_VALUE.search(t)]
        if d or e_ or v:
            body = _tag("연결고리 ③", "C", "내면 욕망") + _card("pink", "욕망이 드러나는 세 가지 말", _tiles([("직접 표현", f"{len(d)}회"), ("감정 반응", f"{len(e_)}회"), ("가치 강조·선택", f"{len(v)}회")]))
            ex = [("직접 표현", d), ("감정 반응", e_), ("가치 강조·선택", v)]
            rows = [(None, mask(lst_[0][:70])) for _, lst_ in ex if lst_]
            if rows:
                body += _card("beige", "예를 들면", bubbles([(me, t) for _, t in rows], {me: NAME_COLORS[0]}))
        else:
            body = ""
        mz = sorted(((l, sum(1 for t in my_all if _hits(t, ws))) for l, ws in _MASLOW), key=lambda x: -x[1])
        mz = [(l, c) for l, c in mz if c]
        if mz:
            body = (body or _tag("연결고리 ③", "C", "내면 욕망")) + _card("yellow", "어떤 욕구와 가까운 말이 많은가", _hbars(mz, "#E9A23B", "회") + f'<div class="ks-sub">매슬로 욕구 5단계의 단서 단어를 센 값이에요. {_NOTE}</div>')
        if body:
            slides.append(("내면 욕망 단서", body))

    # ④ 반복 경향 → D  (맥락이 다르므로 따로)
    cards = ""
    for lab, tx, cls in (("사람과의 대화", my_hu, "lav"), ("AI와의 대화", my_ai, "mint")):
        if len(tx) < 10:
            continue
        n = len(tx)
        toks = sum(len(t.split()) for t in tx) or 1
        first = sum(1 for t in tx for w in re.split(r"[\s,.!?~]+", t) if w in _I)
        top = [w for w, c in Counter(w for t in tx for w in _tokens(t)).most_common(5) if c >= 2]
        rows = [
            ("긍정 정서어", round(sum(_hits(t, _POS) for t in tx) / n * 100)),
            ("부정 정서어", round(sum(_hits(t, _NEG + _ANX) for t in tx) / n * 100)),
            ("인과·성찰어", round(sum(_hits(t, _CAUSAL) for t in tx) / n * 100)),
            ("불확실어", round(sum(_hits(t, _UNSURE) for t in tx) / n * 100)),
            ("확신어", round(sum(_hits(t, _SURE) for t in tx) / n * 100)),
        ]
        inner = f'<div class="ks-chips">{"".join(f"<span class=ks-chip>{_e(w)}</span>" for w in top)}</div>' if top else ""
        inner += f'<div class="ks-sub" style="margin:.6rem 0 .2rem">1인칭(나·내·저) 비율 {_pct(first, toks)}% · 발화 {n}개 기준, 메시지 100개당 횟수</div>' + _hbars(rows, "#4A7BD0", "회")
        cards += _card(cls, f"{lab} 속 반복 경향", inner)
    if cards:
        slides.append(("반복 경향", _tag("연결고리 ④", "D", "반복되는 경향·성찰 포인트") + cards + f'<div class="ks-sub0">{_NOTE} 사람과의 대화와 AI와의 대화는 맥락이 달라 따로 보여줘요.</div>'))

    return {"people": people, "slides": slides}


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
.ks-tag{background:#EAF0FB;color:#0D326F;border-radius:12px;padding:.5rem .8rem;font-size:.82rem;margin:.2rem 0 .4rem}.ks-tag b{color:#A40F16;margin-right:.4rem}.ks-tag span{display:block;opacity:.7;margin-top:.1rem}
.ks-two3{display:grid;grid-template-columns:repeat(auto-fit,minmax(90px,1fr));gap:.6rem;margin:.2rem 0 .6rem}.ks-tile{background:rgba(255,255,255,.55);border-radius:14px;padding:.7rem .5rem;text-align:center}.ks-tn{font-size:1.45rem;font-weight:900}.ks-tl{font-size:.76rem;opacity:.75;margin-top:.15rem}
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
