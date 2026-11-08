# FREUD · 쿠로이트

> 카카오톡·인스타그램 DM·챗봇 대화를 읽고, 프로이트의 관점을 **해석의 렌즈**로 삼아 말버릇과 방어기제, 반복되는 패턴을 짚어 주고, 이를 **진로(학사·직업·삶의 가치관) 탐색**으로 잇는 Streamlit 웹 앱.

미래교육 AI 문제해결 챌린지 출품작 (팀 표범조).
**Live:** https://freudchatbot.streamlit.app

---

## Features

| 단계 | 내용 |
|---|---|
| 입력 | 직접 붙여넣기 · 카카오톡(txt/csv) · 인스타그램 DM(`message_N.html`/zip) · 챗쿠 대화(ndjson/zip) · 데모 체험 |
| 전처리 | 필수 동의 2건, 분석 대상자 선택(본인 발화만 분석), 전화번호·이메일·주민번호 자동 마스킹 |
| 통계 카드 | 대화 개요, 관계 속 내 모습, AI 앞의 나, 막힌 지점, 내면 욕망 단서, 반복 경향 |
| 분석 리포트 | 5개 항목(A–E) + 쿠로이트 한줄평 + 성찰 질문. 항목마다 관찰/가설 구분, 대화 인용 근거(채팅 말풍선), 근거 부족 시 판단 보류 |
| 진로 탐색 | 추가 질문에 답하면 학사·직업·삶의 가치관 영역별 후보·근거·작은 경험·다음 행동 카드 제시 |
| 후속 대화 | 새 정보를 주면 해석 수정, 리포트·진로 기록 내려받기(Markdown) |

### 리포트 항목

| 항목 | 내용 |
|---|---|
| A | 인간관계 속 내 모습 (방어기제 후보, 가면, 상황별 표현 차이) |
| B | AI 앞의 나 (챗봇 대화는 사람 대화가 없어도 분석) |
| C | 내면 욕망 (직접 표현·감정 반응·가치 선택·관계 반응 → 매슬로 5욕구로 분류) |
| D | 반복 경향과 성찰 포인트 |
| E | 잘하는 것·좋아하는 것·싫어하는 것·가치관·못하는 것 후보 |

### 설계 원칙

- 임상 진단·단정 금지. "~일 수 있다", "~경향이 보인다"로 표현한다.
- 프로이트 개념(원초아·자아·초자아)은 갈등을 해석하는 렌즈일 뿐, 대화로 측정할 수 있다고 주장하지 않는다.
- 상대방의 성격·심리는 추정하지 않는다. 분석 대상자의 발화만 읽는다.
- 한 번의 발화로 확정하지 않고 반복·상황 대비로 판단한다.
- 학사 영역의 제도·규정·일정은 연결된 에이전트(자유전공학부 안내봇, 학사봇)가 돌려준 답만 근거로 쓰며, 답이 없으면 추정하지 않는다.
- 자해·자살 암시가 보이면 분석을 멈추고 공감 문장과 상담 전화(109, 1577-0199)를 안내한다.

---

## Architecture

```
Browser ──► Streamlit app (streamlit_app_Freud.py, kstats.py)
                │  mask_pii → fit_length → prompt 구성
                ▼
        ChatKHU API Gateway (OpenAI 호환)
          1) 챗쿠 챗봇 (chatbots/…/chat/completions)  ── 안내봇·학사봇 연결
          2) 실패 시 gateway 모델 호출 (기본 gpt-5.6-luna)
                ▼
        JSON 응답 → parse_json → 통계 카드 / 리포트 / 진로 카드 렌더링
```

- **상태 관리:** `st.session_state.stage`로 `landing → input → pick → 분석 대기 → result → career → 후속 대화`를 전환한다.
- **LLM 호출 (`call_llm`):** 챗쿠 챗봇 엔드포인트를 먼저 호출하고, 실패하면 gateway의 모델 엔드포인트로 대체한다. 429/5xx와 연결 끊김은 1회 재시도한다. 어느 경로로 응답했는지는 UI에 표시한다.
- **분석 대기:** `run_with_freud_quotes`가 분석을 별도 스레드에서 실행하는 동안 일러스트와 프로이트 명언을 5초 간격으로 보여 준다.
- **프롬프트 모드:** `ANALYST_SYSTEM`(1차 자기이해 분석, JSON 출력) · `CAREER_MODE`(2차 진로 탐색) · `FOLLOWUP_MODE`(후속 대화, 6문장 이내) · `CHAT_SYSTEM`(공통 시스템 프롬프트).
- **통계 카드:** `kstats.compute`가 파싱된 메시지에서 시간대·분량·단어 빈도·질문 패턴 등을 계산해 카드(HTML)를 만든다. LLM 결과의 근거 인용은 `kstats.bubbles`로 채팅 말풍선으로 그린다.

### 입력 파서

| 형식 | 처리 |
|---|---|
| 카카오톡 | txt(`[이름] [시각] 내용`) / csv(`Date,User,Message`) → `이름: 내용` 줄로 정규화 |
| 인스타그램 DM | `message_N.html` 또는 zip. UTF-8 → latin-1 오해석으로 깨진 한글 복구 |
| 챗쿠 | ndjson/zip 내보내기에서 세션별 User/Assistant 발화 추출, 메모리 텍스트 선택 포함 |
| 붙여넣기 | 카카오톡 형식 등 `이름: 내용` 패턴을 인식해 발화자를 구분하고, 구분이 안 되면 글쓴이 본인의 글로 간주 |

입력 길이는 최소 300자, 최대 60,000자(`MIN_CHARS`, `MAX_CHARS`). 초과 시 최근 부분 또는 앞부분만 사용한다(`fit_length`).

---

## Privacy

- 입력한 대화와 분석 결과는 **이 서비스의 서버에 저장하지 않는다.** 분석 직후 원문(`raw`, `msgs`)은 세션 메모리에서 제거된다.
- 분석을 위해 대화 텍스트는 **ChatKHU API Gateway로 전송**된다. 전송 전 `mask_pii`가 전화번호·이메일·주민번호를 `[전화번호]`/`[이메일]`/`[주민번호]`로 치환한다.
- 이름·주소는 자동으로 가려지지 않는다. 이용 안내에서 가명으로 바꿔 올리도록 권장한다.
- 만 14세 미만은 이용할 수 없고, 결과는 심리 검사·진단·상담을 대신하지 않는다.

---

## Getting started

```bash
git clone https://github.com/eeekkdedk/freud_chatbot_v1.git
cd freud_chatbot_v1
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

API 키는 코드에 넣지 않고 Streamlit secrets 또는 환경 변수로 준다.

```toml
# .streamlit/secrets.toml  (git에 올리지 않음)
key = "YOUR_CHATKHU_API_KEY"
# model = "gpt-5.6-luna"   # 선택: gateway 대체 호출 모델 교체
```

| 설정 | secrets | 환경 변수 | 기본값 |
|---|---|---|---|
| API 키 | `key` | `BAZE_API_KEY` | (필수) |
| gateway 모델 | `model` | `BAZE_MODEL` | `gpt-5.6-luna` |

```bash
streamlit run streamlit_app_Freud.py
```

### Deploy (Streamlit Community Cloud)

1. 저장소를 연결하고 메인 파일을 `streamlit_app_Freud.py`로 지정한다.
2. **Settings → Secrets**에 `key = "..."`를 입력한다.
3. 테마·업로드 한도는 `.streamlit/config.toml`에서 관리한다 (`maxUploadSize = 10`MB, primary color `#A40F16`).

---

## Project structure

```
.
├── streamlit_app_Freud.py   # UI, 입력 파서, 프롬프트, LLM 호출, 결과 렌더링
├── kstats.py                # 대화 통계 카드 계산과 근거 말풍선 렌더링
├── requirements.txt         # streamlit, requests
├── .streamlit/config.toml   # 테마, 업로드 한도
├── assets/
│   ├── mascot.png|svg       # 쿠로이트 캐릭터
│   ├── loading/             # 분석 대기 일러스트
│   ├── guides/              # 카카오톡·인스타그램·챗쿠 내보내기 안내 이미지
│   ├── icons/               # 입력 탭 아이콘
│   └── demo/                # 체험용 가상 대화 (kakao.csv, instagram.zip, chatku.zip)
└── tools/
    └── vectorize_mascot.py  # 캐릭터 PNG → SVG 변환 스크립트
```

---

## Known limitations & roadmap

- 일부 대화만으로 개인의 전반적인 경험과 내면을 파악할 수 없고, AI 해석은 확정적 사실이 아니다.
- 학사 안내봇·학사봇 연결은 챗쿠 챗봇 경로에서만 동작한다. gateway 대체 호출 시에는 학사 제도 내용을 추정하지 않는다.
- 현재 구현은 대학 1학년(자유전공학부 안내 연계)을 기준으로 한다.
- 향후: 사용자 동의 기반 대화 자동 연동, 학사 정보 연결 범위 확대, 사용자 테스트·교사 인터뷰를 통한 검증, 고등학생·대학 저학년으로의 확대.
