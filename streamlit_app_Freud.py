import streamlit as st
from openai import OpenAI

st.set_page_config(page_title="프로이트 챗봇", page_icon="🛋️", layout="centered")

SYSTEM_PROMPT = """너는 '프로이트 챗봇'이다. 사용자가 붙여넣은 SNS 대화나 챗봇 대화 기록을 읽고
다음 순서로 한국어로 답한다.

1. 반복되는 경향: 자주 쓰는 단어·표현, 대화 특징을 근거 문장과 함께 짚는다.
2. 나의 특성: 잘하는 것/좋아하는 것/싫어하는 것/가치관을 '가설' 형태로 제시한다.
3. 아직 어려워 보이는 것: 반복해서 질문하거나 이해하지 못한 주제를 '아직 숙달되지 않았을 가능성'으로 표현한다.
4. 진로 솔루션: 위 분석을 바탕으로 어울릴 만한 진로 방향과 다음 행동 2~3가지를 제안한다.
5. 성찰 질문 1~2개로 마무리한다.

규칙: 진단·단정 표현을 쓰지 말고 '~일 수 있어요', '~로 보여요'처럼 가설로 말한다.
분량이 너무 적으면 추론을 보류하고 더 많은 대화를 요청한다. 의학적·임상적 진단은 하지 않는다."""

st.title("🛋️ 프로이트 챗봇")
st.caption("대화 기록을 바탕으로 나의 경향과 특성을 돌아보고 진로 방향을 찾아보세요. (참고용 가설이며 진단이 아닙니다)")

api_key = st.secrets.get("OPENAI_API_KEY", None)
if not api_key:
    st.error("OPENAI_API_KEY가 설정되지 않았어요. Streamlit 앱 Settings → Secrets에 추가해 주세요.")
    st.stop()

client = OpenAI(api_key=api_key)

if "messages" not in st.session_state:
    st.session_state.messages = [
        {
            "role": "assistant",
            "content": "안녕하세요! 분석하고 싶은 SNS 대화나 챗봇 대화 기록을 붙여넣어 주세요. "
                       "분량이 충분할수록 더 정확해져요.",
        }
    ]

with st.sidebar:
    st.subheader("설정")
    model = st.selectbox("모델", ["gpt-4o-mini", "gpt-4o"], index=0)
    if st.button("대화 초기화"):
        del st.session_state["messages"]
        st.rerun()
    st.caption("입력한 내용은 분석을 위해 OpenAI API로 전송됩니다. 개인정보는 지우고 입력해 주세요.")

for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

if prompt := st.chat_input("대화 기록이나 질문을 입력하세요"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            stream = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}]
                + [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages],
                stream=True,
            )
            reply = st.write_stream(stream)
        except Exception as e:
            reply = f"오류가 발생했어요: {e}"
            st.error(reply)
    st.session_state.messages.append({"role": "assistant", "content": reply})
