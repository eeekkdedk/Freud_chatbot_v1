"""FREUD — 대화 속 자기 성찰 보조 챗봇 (Streamlit)

- 카톡/SNS/챗봇 대화 기록 → 자기 성찰·진로 상담 준비용 비진단적 분석
- 분석은 BAZE API Gateway의 chatbot 55975(자체 프롬프트 내장)가 수행합니다.
  chatbot 호출이 실패하면 같은 프롬프트(chatbot_prompt.txt)를 system 으로 넣어 일반 게이트웨이로 대체 호출합니다.
- API 키는 st.secrets["key"]에서만 읽습니다.
"""
import html
import os
import re
from collections import Counter
from pathlib import Path

import requests
import streamlit as st

# ───────────────────────── 설정 ─────────────────────────
CHATBOT_URL = "https://factchat-cloud.mindlogic.ai/v1/gateway/chatbots/55975/chat/completions"
GATEWAY_URL = "https://factchat-cloud.mindlogic.ai/v1/gateway/chat/completions"  # chatbot 실패 시 대체
GATEWAY_MODEL = "gpt-5.6-luna"
REQUEST_TIMEOUT = 240

MIN_CHARS = 300
MAX_CHARS = 60_000
MIN_UTTERANCES = 8  # chatbot 프롬프트의 대화 자료 최소 기준

st.set_page_config(page_title="FREUD · 대화 속 자기 성찰", page_icon="🛋️", layout="centered")

# chatbot 55975 워크플로(1~3단계)의 고정 문구 — 앱 화면에서 동의를 받은 뒤 같은 순서로 전달합니다.
INTRO_TEXT = (
    "안녕하세요! 본 서비스는 입력해주신 대화 기록을 바탕으로 자기 성찰 및 상담 준비를 돕는 AI 보조 서비스입니다. "
    "진행을 시작하시겠습니까? ('네' 또는 '아니요'로 답변해 주세요.)"
)
CONSENT_TEXT = """대화 분석 및 서비스 제공을 위해 개인정보 수집·이용 동의가 필요합니다.
• 수집·이용 목적: 대화 서비스 제공, 맞춤형 분석 및 자기 성찰 보조자료 작성
• 수집하는 항목: 업로드하는 대화 내용 및 세션 식별 정보
• 보유 및 이용 기간: 분석 완료 및 서비스 제공 후 지체 없이 파기
• 동의 거부 권리: 귀하는 동의를 거부할 권리가 있으나, 거부 시 대화 분석 서비스 이용이 제한됩니다.
위 내용에 동의하십니까? ('네' 또는 '아니요'로 답변해 주세요.)"""
UPLOAD_TEXT = "동의해 주셔서 감사합니다. 분석을 원하시는 대화 기록(텍스트 파일 또는 복사한 대화 내용)을 업로드하거나 입력해 주세요."
TOO_SHORT_TEXT = (
    "제공해주신 대화 자료의 발화 수가 부족합니다(최소 8회 이상의 대화 필요). "
    "보다 정확한 성찰 및 분석을 위해 조금 더 긴 대화 기록을 추가로 입력해 주세요."
)


def load_chatbot_prompt() -> str:
    try:
        return (Path(__file__).parent / "chatbot_prompt.txt").read_text(encoding="utf-8")
    except Exception:
        return ""


# ───────────────────────── 스타일 ─────────────────────────
st.markdown(
    """
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css');
html, body, [class*="css"], .stApp { font-family: 'Pretendard', -apple-system, sans-serif; }
.block-container { max-width: 760px; padding-top: 2.2rem; }
.hero { text-align:center; padding: 2.2rem 0 1.2rem; }
.hero .brand { letter-spacing: .4em; font-size: 2.6rem; color:#a78bfa; font-weight:800; margin-bottom:.4rem; }
.hero h1 { font-size: 2.3rem; line-height:1.25; margin:.6rem 0 .5rem; font-weight:800; }
.hero .sub { color:#9ca3af; font-size:1rem; }
.hero .desc { color:#cbd5e1; font-size:.95rem; margin-top:1rem; line-height:1.7; }
.sec { font-size:.75rem; letter-spacing:.3em; color:#a78bfa; font-weight:700; margin:1.8rem 0 .4rem; }
.src { text-align:right; color:#6b7280; font-size:.72rem; margin-top:.4rem; }
.disc { color:#6b7280; font-size:.78rem; line-height:1.6; margin-top:2rem; border-top:1px solid #262b3a; padding-top:1rem; }
@media (max-width:640px){ .hero h1{font-size:1.8rem;} .hero .brand{font-size:2rem;} }
</style>
""",
    unsafe_allow_html=True,
)


def e(x) -> str:
    return html.escape(str(x if x is not None else ""))


# ───────────────────────── LLM 호출 ─────────────────────────
def get_key() -> str:
    try:
        k = st.secrets["key"]
    except Exception:
        k = os.environ.get("BAZE_API_KEY", "")
    return str(k).strip()


def _extract_text(data: dict) -> str:
    try:
        return data["choices"][0]["message"]["content"] or ""
    except Exception:
        for k in ("answer", "content", "text", "output_text"):
            if isinstance(data.get(k), str):
                return data[k]
    return ""


def call_llm(messages: list):
    """(응답 텍스트, 호출 출처 라벨) 반환.
    chatbot 55975 는 자체 프롬프트를 쓰므로 system 메시지 없이 호출하고,
    대체 호출(일반 게이트웨이)에서는 같은 프롬프트를 system 으로 붙입니다."""
    key = get_key()
    if not key:
        raise RuntimeError("API 키가 없습니다. Streamlit Secrets에 `key`를 등록해 주세요.")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    fallback_msgs = [{"role": "system", "content": load_chatbot_prompt()}] + messages
    errors = []
    for url, body, label in (
        (CHATBOT_URL, {"messages": messages}, "chatbot 55975"),
        (GATEWAY_URL, {"messages": fallback_msgs, "model": GATEWAY_MODEL}, f"gateway 대체 · {GATEWAY_MODEL}"),
    ):
        try:
            r = requests.post(url, headers=headers, json={**body, "stream": False}, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                text = _extract_text(r.json())
                if text.strip():
                    return text, label
                errors.append(f"[{label}] 빈 응답")
            else:
                errors.append(f"[{label}] HTTP {r.status_code}: {r.text[:200]}")
        except Exception as ex:  # 네트워크/타임아웃/JSON 오류
            errors.append(f"[{label}] {type(ex).__name__}: {str(ex)[:150]}")
    raise RuntimeError(" | ".join(errors))


# ───────────────────────── 텍스트 처리 ─────────────────────────
def mask_pii(text: str) -> str:
    text = re.sub(r"\b01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}\b", "[전화번호]", text)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[이메일]", text)
    text = re.sub(r"\b\d{6}[-\s]?[1-4]\d{6}\b", "[주민번호]", text)
    return text


_PATTERNS = [
    # 카톡 모바일:  [홍길동] [오후 3:00] 내용
    re.compile(r"^\[(?P<n>[^\]]{1,30})\]\s*\[(?:오전|오후)?\s*\d{1,2}:\d{2}\]\s*(?P<m>.*)$"),
    # 카톡 PC:  2024년 1월 1일 오후 3:00, 홍길동 : 내용  /  2024. 1. 1. 오후 3:00, 홍길동 : 내용
    re.compile(
        r"^\d{4}[년.\-/]\s*\d{1,2}[월.\-/]\s*\d{1,2}[일.]?\s*(?:오전|오후)?\s*\d{1,2}:\d{2}(?::\d{2})?\s*,\s*(?P<n>[^:]{1,30}?)\s*:\s*(?P<m>.*)$"
    ),
    # 일반:  이름: 내용   (챗봇 로그의 User: / 나: 등)
    re.compile(r"^(?P<n>[^\s:\[\]]{1,20}(?: [^\s:\[\]]{1,20})?)\s*:\s+(?P<m>.+)$"),
]


def parse_speakers(text: str):
    """대화자별 발화 수 Counter. 구분 불가 시 빈 Counter."""
    lines = text.splitlines()
    best_cnt = Counter()
    for pat in _PATTERNS:
        cnt = Counter()
        for ln in lines:
            m = pat.match(ln.strip())
            if m:
                cnt[m.group("n").strip()] += 1
        if sum(cnt.values()) > sum(best_cnt.values()):
            best_cnt = cnt
    if sum(best_cnt.values()) < 5:
        return Counter()
    return Counter({k: v for k, v in best_cnt.items() if v >= 3})


def count_utterances(text: str, speakers: Counter) -> int:
    """말한 줄이 있으면 발화로 셉니다. 화자 표기가 잡히면 그 줄 수, 아니면 내용이 있는 줄 수."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    matched = max(sum(1 for ln in lines if pat.match(ln)) for pat in _PATTERNS)
    return matched if matched > 0 else len(lines)


def read_upload(f) -> str:
    raw = f.getvalue()
    for enc in ("utf-8-sig", "cp949", "utf-16"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", errors="ignore")


def fit_length(text: str, keep: str) -> str:
    if len(text) <= MAX_CHARS:
        return text
    return text[-MAX_CHARS:] if keep == "recent" else text[:MAX_CHARS]


# ───────────────────────── 분석 실행 ─────────────────────────
def run_analysis(text: str, target: str, keep: str):
    """chatbot 55975 의 1~3단계(소개→동의→업로드 요청)를 앱에서 마친 상태로 재현해 4단계(분석)를 요청합니다."""
    body = fit_length(mask_pii(text), keep)
    who = f"분석 대상자(사용자 본인): {target}" if target else "분석 대상자: 사용자 본인 (대화 전체가 사용자 본인의 글입니다)"
    messages = [
        {"role": "user", "content": "안녕하세요"},
        {"role": "assistant", "content": INTRO_TEXT},
        {"role": "user", "content": "네"},
        {"role": "assistant", "content": CONSENT_TEXT},
        {"role": "user", "content": "네"},
        {"role": "assistant", "content": UPLOAD_TEXT},
        {"role": "user", "content": f"{who}\n\n[대화 기록 시작]\n{body}\n[대화 기록 끝]"},
    ]
    return call_llm(messages)  # (text, src)


# ───────────────────────── 화면 조각 ─────────────────────────
def hero():
    st.markdown(
        """
<div class="hero">
<div class="brand">F R E U D</div>
<h1>당신의 말은<br>당신보다 먼저 압니다</h1>
<div class="sub">대화 속 나를 돌아보고, 진로 상담을 준비합니다</div>
<div class="desc">카카오톡·SNS·챗봇 대화를 붙여넣으면<br>프로이트 등 심리학 관점으로 말과 관계의 패턴을 살펴보고<br>스스로 확인해 볼 질문과 작은 행동을 제안해요. (진단이 아닌 자기 성찰 보조자료)</div>
</div>
""",
        unsafe_allow_html=True,
    )


def terms_box():
    with st.expander("개인정보 수집·이용 동의서 및 이용 안내 (필독)"):
        st.markdown("**개인정보 수집·이용 동의서**")
        st.text(CONSENT_TEXT.rsplit("\n", 1)[0])
        st.markdown(
            """
**이용 안내**
- 입력한 대화와 분석 결과는 이 앱 서버에 저장하지 않으며, 브라우저를 닫으면 사라집니다.
- 분석을 위해 대화 텍스트가 AI API(BAZE Gateway)로 전송됩니다.
- 전화번호·이메일·주민번호는 전송 전에 자동으로 가려집니다. 이름은 가명(예: "홍길동" → "길동")으로 바꿔 올리는 것을 권장합니다.
- 본인이 참여한 대화만 올려 주세요. 상대방의 성격·정신건강 상태는 분석하지 않습니다.
- 결과는 **참고용 AI 해석**이며 심리 검사·진단·상담을 대신하지 않습니다.
"""
        )


def disclaimer():
    st.markdown(
        '<div class="disc">본 결과는 AI가 대화 텍스트만으로 생성한 비진단적 참고자료이며, 심리 검사·진단·의료·법률 자문을 대신하지 않습니다. '
        "힘든 마음이 계속된다면 전문가의 도움을 받아 보세요. (자살예방상담전화 109 · 정신건강위기상담 1577-0199)</div>",
        unsafe_allow_html=True,
    )


# ───────────────────────── 상태 관리 ─────────────────────────
for k, v in (("stage", "input"), ("raw", ""), ("result", ""), ("target", ""), ("src", ""), ("busy", False)):
    st.session_state.setdefault(k, v)


def reset():
    for k in ("stage", "raw", "result", "target", "src", "busy"):
        st.session_state.pop(k, None)


if not get_key():
    st.error("API 키가 설정되지 않았습니다. 배포 환경의 Secrets에 `key = \"...\"` 를 추가해 주세요.")

# ───────────────────────── 1단계: 입력 ─────────────────────────
if st.session_state.stage == "input":
    hero()
    st.markdown('<div class="sec">INPUT</div>', unsafe_allow_html=True)
    tab1, tab2 = st.tabs(["✍️ 텍스트 붙여넣기", "📄 파일 업로드 (.txt)"])
    with tab1:
        pasted = st.text_area(
            "대화 내용",
            height=240,
            max_chars=200_000,
            placeholder="카카오톡 '대화 내보내기' 텍스트, SNS 댓글/DM, ChatGPT 등 챗봇과 나눈 대화를 붙여넣으세요.\n말풍선(발화)이 최소 8개 이상, 화자 구분이 되는 대화여야 해요.",
            label_visibility="collapsed",
        )
        st.caption(f"{len(pasted):,} / 200,000자 · 최소 {MIN_CHARS}자")
    with tab2:
        up = st.file_uploader("텍스트 파일", type=["txt", "csv", "md"], label_visibility="collapsed")
        st.caption("카카오톡 → 채팅방 메뉴 → 대화 내보내기(텍스트)로 받은 .txt 파일을 올려 주세요.")

    terms_box()
    ok1 = st.checkbox("개인정보 수집·이용에 동의합니다. (필수)")
    ok2 = st.checkbox("본인이 참여한 대화이며, 타인의 정보는 가명 처리했습니다. (필수)")

    if st.button("🛋️ 분석 시작하기", type="primary", use_container_width=True):
        text = read_upload(up) if up is not None else pasted
        if len(text.strip()) < MIN_CHARS:
            st.warning(f"대화가 너무 짧아요. 최소 {MIN_CHARS}자 이상 입력해 주세요. (현재 {len(text.strip()):,}자)")
        elif not (ok1 and ok2):
            st.warning("두 가지 동의 항목에 모두 체크해 주세요.")
        elif not get_key():
            st.warning("API 키가 없어 분석할 수 없어요.")
        else:
            st.session_state.raw = text
            st.session_state.stage = "pick"
            st.rerun()

# ───────────────────────── 2단계: 대상 선택 ─────────────────────────
elif st.session_state.stage == "pick":
    text = st.session_state.raw
    speakers = parse_speakers(text)
    busy = st.session_state.get("busy", False)
    n_utt = count_utterances(text, speakers)
    too_short = n_utt < MIN_UTTERANCES

    st.markdown("## 누구를 분석할까요?")
    st.caption(
        f"총 {len(text):,}자 · 발화 {n_utt}개"
        + (" · 대화자를 자동으로 찾았어요." if speakers else " · 대화자를 구분하지 못했어요.")
    )
    st.caption("분석은 사용자 본인의 발언을 기준으로 하며, 상대방의 성격·마음 상태는 분석하지 않아요.")

    target = ""
    if speakers:
        names = [f"{n}  ({c}줄)" for n, c in speakers.most_common(8)]
        raw_names = [n for n, _ in speakers.most_common(8)]
        choice = st.radio("분석 대상 (나)", names + ["직접 입력", "전체를 내 글로 보기"], index=0, disabled=busy)
        if choice == "직접 입력":
            target = st.text_input("대화에 나온 이름 그대로 입력", disabled=busy)
        elif choice in names:
            target = raw_names[names.index(choice)]
    else:
        st.info("전체 텍스트를 글쓴이 본인의 글로 보고 분석합니다.")
        target = st.text_input("분석할 사람(나) 이름 (비워 두면 전체)", disabled=busy)

    keep = "recent"
    if len(text) > MAX_CHARS:
        st.warning(f"대화가 {MAX_CHARS:,}자를 넘어요. 일부만 분석할 수 있어요.")
        keep = (
            "recent"
            if st.radio("어느 부분을 쓸까요?", ["최근 대화 위주 (앞부분 제거)", "초반 대화 위주 (뒷부분 제거)"], disabled=busy).startswith("최근")
            else "early"
        )

    if too_short:
        st.warning(TOO_SHORT_TEXT)

    b1, b2 = st.columns([1, 2])
    if b1.button("← 뒤로", use_container_width=True, disabled=busy):
        st.session_state.stage = "input"
        st.rerun()
    b2.button(
        "분석 중…" if busy else "분석 시작",
        type="primary",
        use_container_width=True,
        disabled=busy or too_short,
        on_click=lambda: st.session_state.update(busy=True),
    )

    if busy:  # 버튼이 잠긴 상태로 분석 실행
        with st.spinner("대화를 살펴보는 중… 보통 1~3분 걸려요"):
            try:
                answer, src = run_analysis(text, target.strip(), keep)
            except Exception as ex:
                st.session_state.busy = False
                st.error(f"분석 중 오류가 발생했어요: {ex}")
                if st.button("다시 시도"):
                    st.rerun()
                st.stop()
        st.session_state.busy = False
        st.session_state.result = answer
        st.session_state.src = src
        st.session_state.target = target.strip()
        st.session_state.raw = ""  # 원문은 분석 직후 메모리에서 제거
        st.session_state.stage = "result"
        st.rerun()

# ───────────────────────── 3단계: 결과 ─────────────────────────
else:
    st.markdown(f"## 대화 분석 결과{' — ' + e(st.session_state.target) if st.session_state.target else ''}")
    with st.container(border=True):
        st.markdown(st.session_state.result)
    st.markdown(f'<div class="src">호출: {e(st.session_state.src or "-")}</div>', unsafe_allow_html=True)

    st.download_button(
        "📥 결과 저장 (.md)",
        st.session_state.result + "\n\n※ AI 참고용 비진단적 해석이며 진단·상담을 대신하지 않습니다.",
        file_name="freud_report.md",
        use_container_width=True,
    )
    if st.button("🔄 다시 분석하기", use_container_width=True):
        reset()
        st.rerun()
    disclaimer()
