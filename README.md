# Unnest Agent Builder

**온프레미스(폐쇄망) 환경을 위한 GraphRAG 에이전트 빌더 플랫폼.**

브라우저 캔버스에서 컴포넌트를 드래그앤드롭으로 이어붙여 RAG 에이전트를 만들고,
완성된 에이전트를 `런타임 컨테이너 + Neo4j KB 컨테이너` 도커 번들로 추출해
인터넷이 없는 서버에 그대로 이식한다. Langflow를 벤치마킹하되 전부 자체 구현했다.

```
에이전트 = 표준 런타임 이미지(실행 엔진 + 임베딩 모델 내장)
         + flow JSON(캔버스 저장물 = 배포 아티팩트)
         + KB 컨테이너(Neo4j 5: 문서 그래프 + 벡터 인덱스)
         + .env(LLM 접속정보만 현장에서 주입)
```

---

## 무엇이 되나 (전부 실제 동작 검증됨)

- **비주얼 빌더**: 사이드바의 컴포넌트를 캔버스로 드래그 → 포트 타입이 맞는 것끼리만
  연결 → 파라미터 폼 자동 렌더 → 저장/Export/Import
- **KB 자동 프로비저닝**: GUI에서 "KB 생성" 버튼 = Neo4j 컨테이너 자동 기동 +
  벡터 인덱스 + 메타데이터 세팅. DB 설치·설정 지식 불필요
- **문서 → 벡터 DB**: PDF 업로드 → 파싱(텍스트 레이어 없으면 Windows OCR 자동 폴백)
  → 청킹 → 임베딩(로컬 ONNX 모델) → Neo4j 적재. 업로드 즉시 검색 대상이 됨
- **실행 관측성**: 실행하면 캔버스 노드가 실시간 색칠(파랑=실행 중, 초록=성공,
  빨강=실패, 회색=스킵). 실패 노드 클릭 → 원인 분류·에러 메시지·traceback·입력 스냅샷
- **플레이그라운드 채팅**: ChatInput/ChatOutput이 있는 flow는 우측 채팅 패널에서 바로 대화
- **도커 번들 추출**: 툴바 `📦 번들` 버튼 → `bundles/` 아래에 이식 폴더 생성 →
  대상 PC에서 install 스크립트 실행만으로 기동 (이식 리허설 E2E 검증 완료)

### 검증된 RAG 시나리오

| 질문 | 결과 |
|---|---|
| 문서에 있는 내용 질문 | 발췌 인용([1])과 함께 정확 답변 |
| 문서 세부 사실 질문 | 문서 표현 그대로 근거 인용 |
| 문서에 없는 내용 질문 | "문서에서 확인되지 않습니다" — 환각 거부 |

---

## 아키텍처

```
[frontend]  React + @xyflow/react 캔버스            :3000
     │ REST / SSE
[backend]   FastAPI 게이트웨이                       :8000
     │        ├─ 컴포넌트 레지스트리 (파이썬 클래스 introspection → 스펙 JSON)
     │        ├─ flow CRUD / 실행 중계(SSE) / 실행 이력(runs)
     │        ├─ 문서 등록(ingest) — 적재도 flow로 실행
     │        ├─ KB 프로비저너 (Docker SDK로 Neo4j 컨테이너 생성)
     │        ├─ 자격증명 저장소 (Fernet 암호화, 값 재조회 불가)
     │        └─ 번들 제조기 (KB 덤프 + docker save + compose 조립)
     │
[engine]    실행 엔진 — 서버가 아닌 파이썬 패키지
     │        backend(빌더/적재)와 runtime(납품)이 동일 코드 공유
     │        → "빌더에선 됐는데 납품하면 안 됨" 버그가 구조적으로 차단
     │
[runtime]   에이전트 런타임 — engine + 얇은 FastAPI    :8100
     │        flow JSON 1개 로드, POST /run (x-api-key), stateless
     │
[kb-*]      KB 컨테이너들 (Neo4j 5) — KB마다 1개, 프로비저너가 동적 생성
```

### 모노레포 구조

```
├── packages/
│   ├── sdk/          # agentsdk: Component 베이스, port()/param()/secret_param(), 타입 계약
│   ├── engine/       # agentengine: flow 파서, 위상정렬 실행기, 노드 이벤트, 비밀값 방어선
│   └── components/   # agentcomponents: 컴포넌트 라이브러리 (카테고리별 폴더)
│       └── agentcomponents/{io,parsers,chunkers,embeddings,graphdb,llm,formatters}/
├── services/
│   ├── backend/      # FastAPI 앱 (app/)
│   ├── runtime/      # 런타임 셸 + 표준 이미지 Dockerfile
│   └── frontend/     # React + Vite + TypeScript + @xyflow/react + zustand
├── tests/            # 엔진 단위 테스트
├── CLAUDE.md         # 설계 문서 (원칙·계약·마일스톤)
└── bundles/          # 번들 산출물 (gitignore)
```

### 컴포넌트 목록 (18종) — RAG 전략을 조립식으로 선택

| 분류 | 컴포넌트 | 비고 |
|---|---|---|
| io | ChatInput / ChatOutput / FileInput | 채팅 진입·종점, 파일 슬롯 |
| **파서** | PDFParser | pypdf + **OCR 자동 폴백**(텍스트 레이어 없는 PDF) |
| | DOCXParser | 워드 문단+표 (python-docx) |
| | HWPXParser | 한글 hwpx (zip+XML, 표준 라이브러리만) |
| | TextParser | txt/md, utf-8/cp949 자동 판별 |
| **청커** | SimpleChunker | 문자 크기/겹침 (기본값) |
| | SentenceChunker | 문장 경계 보존 — 문장이 잘리지 않음 |
| | ArticleChunker | 법령 "제N조" 단위 분리, 조문번호가 인용에 남음 |
| 임베딩 | LocalEmbedder | fastembed(ONNX), 다국어 MiniLM 384차원 |
| **검색** | Neo4jRetriever (벡터) | cosine 유사도 — 의미 기반. `expand`로 이웃 청크 ±n 병합 |
| | KeywordRetriever | 풀텍스트(Lucene) — 고유명사·조문번호·코드에 강함 |
| | HybridRetriever | 벡터+키워드 **RRF 융합** — 대부분의 경우 최선 기본값 |
| graphdb | Neo4jWriter | (:Document)-[:HAS_CHUNK]->(:Chunk) 적재 |
| llm | PromptTemplate | {question}/{context}, 출처(조문·페이지) 인용 강제 |
| | OpenAICompatLLM | OpenAI 호환이면 전부 (GPT/vLLM/Ollama) |
| 포맷터 | RetrievalPreview | **LLM 없이** 검색 결과를 채팅으로 확인 |

문서 업로드 시 확장자에 맞는 파서 flow가 자동 선택되고(pdf/docx/hwpx/txt/md),
법령 문서는 조문 청커 flow(`ingest-law-pdf`)를 지정해 적재할 수 있다.
검색 전략은 캔버스에서 노드만 갈아끼우면 되므로, 같은 KB로
벡터/키워드/하이브리드를 나란히 비교할 수 있다.

**새 컴포넌트 추가 = 파이썬 파일 1개.** `packages/components/agentcomponents/{카테고리}/`에
클래스를 만들고 백엔드를 재시작하면 사이드바에 자동 등록된다 (프론트 수정 불필요).

```python
from agentsdk import Component, Message, port, param

class MyComponent(Component):
    """한 줄 설명 — 사이드바 툴팁에 표시된다."""
    display_name = "내 컴포넌트"
    category = "formatters"

    text: Message = port(input=True, display_name="입력")
    out: Message = port(output=True, display_name="출력")
    prefix: str = param(default=">> ", display_name="접두어")

    def run(self) -> Message:
        return Message(text=self.prefix + self.text.text)
```

---

## 개발 환경 실행

### 요구사항

- Python 3.12+ / [uv](https://docs.astral.sh/uv/)
- Node.js 20+
- Docker Desktop (KB 프로비저닝·번들 제조에 필요)
- Windows: 텍스트 레이어 없는 PDF의 OCR에 Windows 한국어 언어팩 사용 (기본 설치됨)

### 1. 설치

```bash
git clone https://github.com/leeyseo/unnest_agent-builder.git
cd unnest_agent-builder
uv sync --all-packages                 # 파이썬 워크스페이스 전체 설치
cd services/frontend && npm install    # 프론트엔드
```

### 2. LLM 설정 (선택 — RAG 답변 생성에 필요)

```bash
cp .env.example .env
# .env 편집: LLM_BASE_URL / LLM_MODEL / LLM_API_KEY
# OpenAI 호환이면 뭐든 됨: OpenAI, vLLM, Ollama(http://localhost:11434/v1) 등
```

LLM 없이도 검색 flow(RetrievalPreview)는 완전히 동작한다.

### 3. 기동

```bash
# 터미널 1 — 백엔드 (모노레포 루트에서)
uv run uvicorn app.main:app --port 8000

# 터미널 2 — 프론트엔드
cd services/frontend && npm run dev    # http://localhost:3000
```

### 4. 첫 에이전트 (5분 코스)

1. 사이드바 "지식 베이스"에 이름 입력 → **+** (Neo4j 컨테이너 자동 기동, 1~2분)
2. 생성된 KB의 **문서 업로드** → PDF 선택 (하단 로그로 적재 진행 확인)
3. KB를 캔버스로 **드래그** → kb_id가 채워진 Neo4j 검색 노드 생성
4. `채팅 입력`, `프롬프트 템플릿`, `LLM`, `채팅 출력`을 드래그하고 포트 연결
   (같은 색 포트끼리만 연결됨):
   - 채팅 입력 `message` → 검색 `query` **그리고** 템플릿 `question` (2곳)
   - 검색 `hits` → 템플릿 `context`
   - 템플릿 `prompt` → LLM `prompt` → LLM `answer` → 채팅 출력 `message`
5. 우측 플레이그라운드에서 질문 → 노드가 실시간 색칠되며 답변 생성

### 테스트

```bash
uv run pytest tests/          # 엔진: 타입검증, 실패 전파, 비밀값 저장 거부 등
```

---

## 도커 번들 — 폐쇄망 이식

### 만들기

- **GUI**: 에이전트를 열고 툴바 **📦 번들** 클릭 (수 분 소요)
- **API**: `POST /api/agents/{flow_id}/bundle`
- 사전 준비 (최초 1회): 표준 런타임 이미지 빌드
  ```bash
  docker build -f services/runtime/Dockerfile -t agent-runtime .
  ```

산출물 (`bundles/{이름}-bundle/`, 약 1.2GB):

```
images/agent-runtime.tar   # 런타임 이미지 — 엔진+컴포넌트+임베딩 모델 내장, 오프라인 강제
images/neo4j.tar           # Neo4j 5 이미지
flows/agent.json           # 에이전트 정의 (비밀값 0개 보장 — 저장 시 검증)
data/neo4j.dump            # KB 스냅샷 (문서 그래프 + 벡터)
docker-compose.yml         # healthcheck·기동순서 포함
.env.example               # LLM 3줄 + RUNTIME_API_KEY(랜덤 생성) + NEO4J_PASSWORD
INSTALL.md                 # 설치 안내
install.ps1 / install.sh   # 자동 설치 스크립트
SHA256SUMS                 # 무결성 검증
```

### 대상 PC에서 설치

요구사항: **Docker 뿐**. 인터넷·파이썬 등 일절 불필요.

```bash
# 번들 폴더를 복사해 온 뒤
bash install.sh            # Windows: install.ps1
# → 첫 실행 시 .env 생성 후 멈춤 — LLM 키/주소 채우고 재실행
# → 이미지 load → KB 덤프 복원 → compose up

curl http://localhost:8100/health
curl -X POST http://localhost:8100/run \
  -H "x-api-key: <.env의 RUNTIME_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"input": {"text": "질문"}, "stream": false}'
```

### 키 정책

| 키 | 번들 포함 여부 |
|---|---|
| `RUNTIME_API_KEY` (에이전트 호출 인증) | ✅ 랜덤 생성돼 포함 (현장에서 변경 가능) |
| `LLM_API_KEY` 등 비밀값 | ❌ **절대 미포함** — 현장 `.env`에서 주입 |

LLM 노드가 없는 flow(검색 미리보기 등)는 외부 키 0개로 완전 자립 동작한다.

---

## 설계 원칙 (요약 — 상세는 CLAUDE.md)

1. **폐쇄망 우선** — 런타임은 기동 후 아무것도 다운로드하지 않는다 (`HF_HUB_OFFLINE=1` 강제)
2. **비밀값은 flow JSON 저장 금지** — 자격증명은 암호화 저장소에, flow에는 참조 이름만.
   저장 시 비밀값 패턴 감지되면 거부. 관측 이벤트에서도 `***` 마스킹
3. **런타임 stateless** — 대화 이력은 호출자가 실어 보낸다
4. **모든 KB 데이터에 `kb_id`** — 쿼리 필터 누락은 리뷰 리젝 사유
5. **임베딩 모델은 KB에 기록·기동 시 대조** — 불일치면 즉시 실패 (조용히 망가지는 검색 금지)
6. **관측 가능** — 모든 노드 실행이 이벤트를 남기고 캔버스에서 원인을 볼 수 있다
7. **컴포넌트 추가 = 파일 1개** — 프론트 수정 없이 자동 등록

## 로드맵

- 파서 확장: HWPX, DOCX, 이미지 OCR(리눅스 런타임용), 법령 조문(Article) 청커
- GraphExtractor: LLM 엔티티 추출 → `(:Entity)` 그래프 확장 (진짜 GraphRAG)
- Reranker, HybridRetriever, ConversationSummarizer
- 대용량 적재 워커 큐, 게이트웨이 인증, HITL
