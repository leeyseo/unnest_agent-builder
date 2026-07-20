# CLAUDE.md — Unnest

온프레미스 정부 SI를 위한 에이전트 빌더 플랫폼 (제품명: **Unnest**). 캔버스에서 컴포넌트를 드래그앤드롭으로
이어붙여 GraphRAG 에이전트를 만들고, 완성된 에이전트를 `런타임 컨테이너 + DB 컨테이너`
번들로 추출해 폐쇄망에 이식한다. Langflow를 벤치마킹하되 전부 자체 개발한다.

```
에이전트 = 런타임 컨테이너(flow JSON + 실행 엔진)
         + KB 컨테이너(Neo4j: 그래프 + 벡터)
         + .env(LLM API 엔드포인트/키)
```

---

## 0. 절대 원칙 (모든 코드가 지켜야 함)

1. **폐쇄망 우선.** 런타임 컨테이너는 기동 후 네트워크로 아무것도 내려받지 않는다.
   의존성·임베딩 모델은 전부 빌드 타임에 이미지에 굽는다.
2. **비밀값은 flow JSON에 저장 금지 — 캔버스 입력은 허용.** 사용자가 노드에서 API 키를
   입력하는 UX는 지원한다(자격증명 선택 드롭다운 + "새 키 등록" 모달). 단, 값은 그
   자리에서 백엔드 자격증명 저장소(암호화)로 가고 flow params에는 참조 이름만 남는다.
   실행 시점에 엔진이 이름을 해석해 환경변수로 주입하고, 컴포넌트는 `os.environ`에서만
   읽는다. DB 접속정보는 사용자 입력 자체가 없다 — 프로비저너가 생성해 저장소에 보관.
   관측 이벤트(입력 스냅샷·로그·runs 저장)에도 비밀값이 찍히면 안 된다 — secret
   파라미터는 스냅샷에서 `***` 마스킹. flow 저장 시 params에 비밀값 패턴이 감지되면
   저장을 거부한다.
3. **런타임은 stateless.** 대화 이력·상태는 런타임이 갖지 않는다. 멀티턴은 호출자가
   이력을 실어 보낸다.
4. **모든 KB 데이터에 `kb_id`.** 노드·관계·쿼리 전부. KB마다 컨테이너가 분리되어 있어도
   반드시 태운다 (향후 공유 DB 전환 대비).
5. **임베딩 모델은 KB에 기록하고 기동 시 대조.** `(:KBMeta {kb_id, embed_model, dim})`
   노드와 런타임의 `EMBED_MODEL`이 다르면 즉시 실패한다. 조용히 망가지는 검색 금지.
6. **관측 가능해야 한다.** 모든 flow 실행은 노드 단위 이벤트(시작/성공/실패+원인+트레이스)를
   남기고, 캔버스에서 어느 노드가 왜 막혔는지 볼 수 있어야 한다.
7. **컴포넌트는 계속 추가된다.** 새 컴포넌트 추가가 "파일 하나 + 의존성 등록"으로
   끝나야 한다. 프론트엔드 수정 없이 사이드바에 자동 등록된다.

---

## 1. 시스템 구성 (4개 서비스 + 프로비저닝되는 KB들)

```
[frontend]  React + @xyflow/react 캔버스           :3000
     │ REST/SSE
[backend]   FastAPI 게이트웨이                      :8000
     │        ├─ 컴포넌트 레지스트리 introspection
     │        ├─ flow CRUD/실행중계, 실행이력(runs)
     │        ├─ 문서 등록(ingest), KB 카탈로그
     │        ├─ KB 프로비저너 (Docker SDK로 Neo4j 컨테이너 생성)
     │        └─ 번들 export
     │
[engine]    실행 엔진 — 패키지 (서버 아님)
     │        backend(플레이그라운드/적재)와 runtime(납품)이 공유
     │
[runtime]   에이전트 런타임 — engine + 얇은 FastAPI 셸  :8100
     │        flow JSON 1개를 로드해 /run 으로 서빙, x-api-key 인증
     │
[kb-*]      KB 컨테이너들 (Neo4j 5) — 프로비저너가 동적 생성
             kb-{kb_id} 이름, 내부 네트워크 전용, 볼륨에 데이터
```

**왜 engine이 패키지인가:** 같은 실행 코드를 세 군데서 쓴다 — ① 빌더 플레이그라운드
(backend가 실행), ② 문서 적재(backend가 ingest flow 실행), ③ 납품 에이전트(runtime이
실행). 엔진을 서버가 아닌 패키지로 두면 세 곳이 동일한 실행 의미론을 공유하고,
"빌더에서 되던 게 납품하면 안 됨" 클래스의 버그가 구조적으로 줄어든다.

---

## 2. 모노레포 구조

```
agent-platform/
├── CLAUDE.md                      # 이 문서
├── docker-compose.dev.yml         # frontend + backend (+ 프로비저닝된 kb-*)
├── packages/
│   ├── sdk/                       # 컴포넌트 SDK: BaseComponent, 포트/타입, 데코레이터
│   │   └── agentsdk/
│   ├── engine/                    # DAG 실행 엔진: 파서, toposort, 실행기, 이벤트
│   │   └── agentengine/
│   └── components/                # 컴포넌트 라이브러리 (카테고리별 폴더)
│       └── agentcomponents/
│           ├── io/                # ChatInput, ChatOutput, FileInput
│           ├── parsers/           # PDFParser (…HWPX, DOCX, OCR 추가 예정)
│           ├── chunkers/          # SimpleChunker (…Semantic, Article 추가 예정)
│           ├── embeddings/        # LocalEmbedder
│           ├── graphdb/           # Neo4jWriter, Neo4jRetriever
│           └── llm/               # PromptTemplate, OpenAICompatLLM
├── services/
│   ├── backend/                   # FastAPI 앱 (api/, provisioner/, ingest/, bundler/)
│   ├── runtime/                   # 런타임 셸 (Dockerfile 포함 — 표준 런타임 이미지)
│   └── frontend/                  # React + Vite + @xyflow/react
├── flows/                         # export된 flow JSON
├── bundles/                       # make бundle 산출물
└── tests/                         # e2e 시나리오 테스트
```

파이썬 패키지 3개(sdk/engine/components)는 uv workspace로 관리하고, backend와
runtime이 로컬 의존성으로 설치한다.

---

## 3. 데이터 타입 계약 (packages/sdk — Pydantic)

엣지 위로 흐르는 모든 것은 아래 타입 중 하나다. **포트 타입이 캔버스 연결 가능
여부를 결정한다** (출력 타입 == 입력 타입, 또는 어느 한쪽이 `Any`).

```python
class RawFile(BaseModel):          # 업로드된 원본
    path: str; mime: str; filename: str

class Block(BaseModel):            # 파서가 뽑은 단위 (문단/표/조문 등)
    type: str                      # "text" | "table" | "article" | ...
    content: str
    meta: dict = {}                # page, article_no 등

class NormalizedDocument(BaseModel):  # 그림2의 '공통 입력 JSON'
    doc_type: str; source: str
    blocks: list[Block]
    meta: dict = {}

class Chunk(BaseModel):
    text: str
    meta: dict = {}                # 출처 블록/조문 참조 유지 (provenance)
    embedding: list[float] | None = None

class RetrievalHit(BaseModel):
    text: str; score: float
    provenance: dict = {}          # article_no, doc title 등 — 답변 인용에 사용

class Message(BaseModel):
    text: str
    history: list[dict] = []       # 멀티턴은 호출자가 채워 보냄 (원칙 3)

class IngestReport(BaseModel):
    kb_id: str; chunks_written: int; nodes_created: int
```

리스트는 `list[Chunk]`, `list[RetrievalHit]`처럼 그대로 흐른다. 새 타입이 필요하면
여기(sdk)에 추가하고 이 문서의 이 절을 갱신한다.

---

## 4. 컴포넌트 SDK (packages/sdk)

Langflow의 introspection 패턴을 차용한다: 컴포넌트는 파이썬 클래스, 프론트는
스펙 JSON만 보고 노드를 그린다.

```python
from agentsdk import Component, port, param

class PDFParser(Component):
    """PDF에서 텍스트 블록을 추출한다."""
    display_name = "PDF 파서"
    category = "parsers"
    icon = "file-text"

    file: RawFile = port(input=True, display_name="PDF 파일")
    chunkable: NormalizedDocument = port(output=True, display_name="문서")

    max_pages: int = param(default=0, display_name="최대 페이지 (0=전체)")

    def run(self) -> NormalizedDocument:
        ...
```

- `port()` = 엣지가 꽂히는 핸들 (타입 어노테이션이 곧 포트 타입)
- `param()` = 캔버스 노드의 파라미터 폼 필드 (str/int/bool/enum → 자동 렌더)
- `secret_param()` = 자격증명 참조. 캔버스에는 이름 드롭다운만 뜨고 값은
  백엔드 자격증명 저장소 → 실행 시 환경변수로 주입 (원칙 2)
- 레지스트리가 `agentcomponents/` 하위를 임포트 스캔해서 스펙 JSON 생성:

```json
{ "type": "PDFParser", "category": "parsers", "display_name": "PDF 파서",
  "inputs":  [{"name": "file", "type": "RawFile"}],
  "outputs": [{"name": "chunkable", "type": "NormalizedDocument"}],
  "params":  [{"name": "max_pages", "kind": "int", "default": 0}] }
```

**새 컴포넌트 추가 절차 (항상 이 순서):**
1. `packages/components/agentcomponents/{카테고리}/xxx.py` 작성
2. 의존성이 늘면 `services/runtime/requirements.txt`에 추가 (표준 이미지 재빌드)
3. 계약 검증: `uv run python -m agentsdk.validate xxx.py` — 카테고리별 포트 계약
   (파서 RawFile→NormalizedDocument 등), 비밀값 하드코딩, 의존성 누락을 검사한다.
   CI(tests/test_component_contract.py)가 등록된 전체 컴포넌트에 같은 검증을 돌린다.
4. 재기동하면 사이드바에 자동 등록 — 프론트 수정 금지

**GUI 업로드 경로**: 툴바 "➕ 컴포넌트" → .py 업로드 → 백엔드가 서브프로세스에서
같은 검증기를 실행 → 통과 시 `agentcomponents/contrib/`(gitignore)에 배치하고
레지스트리 리로드 → 재시작 없이 사이드바 등록. 내장 컴포넌트와 이름이 겹치면 거부.

---

## 5. Flow JSON 스키마

캔버스 저장물 = 배포 아티팩트 = 런타임 입력. 전부 이 하나의 포맷이다.
캔버스에서 언제든 export(다운로드) 가능해야 한다.

```json
{
  "version": "1",
  "name": "parking-agent",
  "nodes": [
    {"id": "n1", "type": "ChatInput",       "params": {}},
    {"id": "n2", "type": "Neo4jRetriever",  "params": {"kb_id": "parking_law", "top_k": 5}},
    {"id": "n3", "type": "PromptTemplate",  "params": {"template": "...{context}...{question}..."}},
    {"id": "n4", "type": "OpenAICompatLLM", "params": {"temperature": 0.2}},
    {"id": "n5", "type": "ChatOutput",      "params": {}}
  ],
  "edges": [
    {"from": ["n1", "message"],  "to": ["n2", "query"]},
    {"from": ["n2", "hits"],     "to": ["n3", "context"]},
    {"from": ["n1", "message"],  "to": ["n3", "question"]},
    {"from": ["n3", "prompt"],   "to": ["n4", "prompt"]},
    {"from": ["n4", "answer"],   "to": ["n5", "message"]}
  ],
  "ui": { "positions": {"n1": [80, 200]} }
}
```

- `ui`는 실행에 관여하지 않는다 (런타임은 무시)
- 비밀값 필드가 params에 들어오면 저장 시 검증 에러 (원칙 2의 방어선)

---

## 6. 실행 엔진 (packages/engine)

1. flow JSON → 그래프 로드, 포트 타입 검증 (연결 불일치는 실행 전에 실패)
2. 위상 정렬 → 순서대로 각 노드: 컴포넌트 인스턴스화 → params/입력 주입 → `run()`
3. 출력 값을 엣지 따라 다음 노드 입력 버퍼에 저장
4. **모든 단계에서 이벤트 발행** (관측성 — 원칙 6):

```json
{"run_id": "r-01J...", "node_id": "n2", "event": "node_started", "ts": "..."}
{"run_id": "r-01J...", "node_id": "n2", "event": "node_finished", "duration_ms": 812,
 "output_preview": "[{\"text\": \"제32조...\", \"score\": 0.91}, ...]"}
{"run_id": "r-01J...", "node_id": "n4", "event": "node_failed",
 "error": "LLM_BASE_URL 응답 없음 (connect timeout 10s)",
 "error_kind": "upstream_unreachable", "traceback": "..."}
```

- `error_kind` 분류: `bad_input`(타입/값), `component_bug`(예외),
  `upstream_unreachable`(LLM/DB 연결), `auth_failed`(키), `timeout`
- 이벤트 소비자: backend는 SSE로 프론트에 중계 + `runs` 테이블(SQLite)에 저장,
  runtime은 SSE 스트림으로만 노출 (stateless — 저장 안 함)
- 노드 실패 시 하류 노드는 `skipped` 이벤트를 받고 run은 `failed`로 종료

캔버스는 이 이벤트로 노드를 실시간 색칠한다: 실행 중=파랑 펄스, 성공=초록,
실패=빨강(클릭 시 error/traceback/입력 스냅샷 패널), 스킵=회색.

---

## 7. 백엔드 API (services/backend)

```
GET  /api/components                     # 컴포넌트 스펙 목록 (사이드바 소스)
POST /api/components/upload {file:.py}   # 컴포넌트 업로드 → 계약 검증 통과 시 contrib/ 등록
GET  /api/kb                             # KB 카탈로그 (사이드바 'KB' 섹션 소스)
POST /api/kb {name}                      # KB 생성 → 프로비저너가 컨테이너 기동
DELETE /api/kb/{kb_id}

POST /api/flows | GET/PUT/DELETE /api/flows/{id}
GET  /api/flows/{id}/export              # flow JSON 다운로드
POST /api/flows/{id}/run                 # 플레이그라운드 실행 → SSE 이벤트 스트림
GET  /api/runs/{run_id}                  # 저장된 실행 이력 (디버깅)

POST /api/documents {file, kb_id, ingest_flow_id}   # 문서 등록 → ingest flow 실행(SSE)
GET  /api/documents?kb_id=

POST /api/credentials {name, value}      # 자격증명 저장 (암호화, 값은 재조회 불가)
GET  /api/credentials                    # 이름 목록만

POST /api/agents/{flow_id}/bundle        # 번들 제조 → bundles/에 산출
```

MVP는 인증 없이 로컬 단일 사용자. (게이트웨이 인증·HITL은 본 플랫폼 단계)

---

## 8. KB 프로비저너 (backend 내부 모듈)

"DB가 자동으로 구축되면 그건 다 컨테이너" — KB 생성이 곧 컨테이너 프로비저닝이다.

- `POST /api/kb {name: "parking_law"}` →
  1. Docker SDK로 `kb-parking-law` 컨테이너 기동 (neo4j:5, 볼륨 `kbdata_parking_law`,
     비밀번호 랜덤 생성 → 자격증명 저장소에 보관, **호스트 포트 미개방** — 내부
     네트워크 `agent-net`으로만)
  2. healthcheck 대기 → 벡터 인덱스 생성(`chunk_embedding`, dim은 EMBED_MODEL 기준)
     → `(:KBMeta {kb_id, embed_model, dim, created_at})` 기록
  3. 카탈로그(SQLite)에 등록: `{kb_id, name, bolt_uri, status, doc_count}`
- 캔버스 사이드바의 "지식 베이스" 섹션은 `GET /api/kb`로 채워진다. **KB를 캔버스로
  드래그하면 `kb_id`가 미리 채워진 검색 노드가 생성된다** (기본 드래그=하이브리드,
  칩으로 벡터/키워드/Writer 선택) — KB는 별도 노드 타입이 아니라 "파라미터가
  프리셋된 컴포넌트 인스턴스"다. 문서 업로드 시에는 적재 flow(파서·청커·임베딩
  조합)를 선택할 수 있고, 캔버스에서 만든 적재 flow(FileInput+Neo4jWriter 포함)가
  자동으로 선택지에 나타난다.
- 개발 환경 백엔드 컨테이너에 `/var/run/docker.sock` 마운트 필요. 이 권한은
  개발/제조 환경 한정이며 납품 번들에는 프로비저너가 없다 (번들 KB는 정적).
- **호스트 개발 모드**: 백엔드를 호스트에서 직접 돌릴 때(`KB_BIND_HOST_PORTS=true`,
  기본값)는 KB bolt 포트를 127.0.0.1 임의 포트로 개방한다. 컨테이너 배치에서는
  `KB_BIND_HOST_PORTS=false`로 agent-net 내부 통신만 허용한다.

---

## 9. 문서 등록 시스템 (ingest)

적재도 flow다. 기본 제공 ingest flow:

```
FileInput → PDFParser → SimpleChunker → LocalEmbedder → Neo4jWriter(kb_id)
```

- `POST /api/documents`가 파일을 저장하고 지정된 ingest flow를 engine으로 실행,
  진행 상황을 SSE로 스트림 (파서 몇 페이지, 청크 몇 개, 적재 몇 건 — 이벤트로 관측)
- MVP는 동기 실행. 대용량·동시 등록이 필요해지면 MQ + 워커로 빼되, 워커도 같은
  engine 패키지로 같은 flow를 실행한다 (아키텍처 불변)
- 그래프 스키마 (MVP):

```
(:Document {kb_id, title, source})
  -[:HAS_CHUNK]-> (:Chunk {kb_id, text, embedding, seq})
(:KBMeta {kb_id, embed_model, dim})
```

  구조 파서(조문 추출)가 들어오면 Document와 Chunk 사이에
  `(:Article {no, title})` 계층과 `[:REFERS_TO]` 조문 참조가 추가된다.
  LLM 기반 엔티티 추출(GraphExtractor)은 백로그 — 적재 flow에 노드 하나 추가로
  들어올 수 있게 스키마에 `(:Entity)` 자리를 비워둔다.

---

## 10. 에이전트 런타임 & 번들 (services/runtime)

- engine 패키지 + 얇은 FastAPI: `POST /run {input}` (x-api-key), `GET /health`,
  응답은 SSE(노드 이벤트 + 최종 답변) 또는 단일 JSON
- 표준 런타임 이미지 1종: 전체 컴포넌트 의존성 합집합 + 임베딩 모델을 빌드 타임에
  포함. 에이전트별 이미지 없음 — `표준 이미지 + flow JSON`이 에이전트다.
- 환경변수 계약:

```
LLM_BASE_URL / LLM_API_KEY / LLM_MODEL    # OpenAI 호환 — 키만 넣으면 동작
EMBED_MODEL                                # 이미지에 구운 모델명 (KBMeta와 대조)
NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD
RUNTIME_API_KEY                            # /run 인증
FLOW_PATH=/flows/agent.json
```

- **번들 제조** (`POST /api/agents/{flow_id}/bundle` 또는 `scripts/make_bundle.sh`):
  1. flow JSON 검증 (비밀값 오염 검사 포함)
  2. 해당 kb_id 컨테이너에서 `neo4j-admin database dump`
  3. `docker save` — 표준 런타임 이미지 + neo4j:5
  4. compose 템플릿 + `.env.example` + INSTALL.md + SHA256SUMS 로 폴더 조립

```
bundles/{name}-bundle/
├── images/agent-runtime.tar, neo4j.tar
├── flows/agent.json          # :ro 마운트
├── data/neo4j.dump
├── docker-compose.yml        # healthcheck·기동순서·메모리상한·init 적재 포함
├── .env.example              # LLM 3줄 + RUNTIME_API_KEY + NEO4J_*
├── INSTALL.md
└── SHA256SUMS
```

  설치 = `docker load` ×2 → `.env` 작성 → `compose up -d`. 이식 리허설:
  번들을 빈 폴더에 복사해 INSTALL.md 절차만으로 기동·질의되는지 e2e 테스트로 검증.

---

## 11. 프론트엔드 캔버스 (services/frontend)

React + Vite + TypeScript + `@xyflow/react` + zustand.

- **사이드바**: `/api/components` 스펙으로 카테고리별 자동 생성 + "지식 베이스"
  섹션(`/api/kb`). 드래그 → 캔버스에 노드 생성.
- **노드**: 스펙의 inputs/outputs로 핸들 렌더, 포트 타입별 색. params로 폼 자동
  렌더(더블클릭 패널). **타입이 맞는 핸들끼리만 연결 허용** (연결 시도 시 타입 표시).
- **실행**: Run 버튼 → `/api/flows/{id}/run` SSE 구독 → 노드 상태 색칠(6절),
  실패 노드 클릭 → 에러 패널(error_kind, 메시지, traceback, 입력 스냅샷).
- **채팅 패널**: ChatInput/ChatOutput이 있는 flow는 우측에 플레이그라운드 채팅.
- **export**: 툴바에서 flow JSON 다운로드 / 업로드(import).
- **자격증명**: 설정 화면에서 이름+값 등록 → LLM 노드 등에서 이름만 선택.

---

## 12. MVP 컴포넌트 목록 (이것만으로 엔드투엔드)

| 컴포넌트 | 카테고리 | 입력 | 출력 | 파라미터/비고 |
|---|---|---|---|---|
| FileInput | io | — | RawFile | 파일 업로드 슬롯 |
| ChatInput | io | — | Message | 채팅 진입점 |
| ChatOutput | io | Message | — | 채팅 종점 |
| PDFParser | parsers | RawFile | NormalizedDocument | pypdf, max_pages, ocr(auto/off/force — 텍스트 레이어 없으면 Windows OCR 폴백, 리눅스 런타임용 OCR은 백로그) |
| DOCXParser | parsers | RawFile | NormalizedDocument | python-docx, 문단+표 |
| HWPXParser | parsers | RawFile | NormalizedDocument | zip+XML 표준 라이브러리, 구형 .hwp 미지원 |
| TextParser | parsers | RawFile | NormalizedDocument | txt/md, utf-8/cp949 자동 판별 |
| SimpleChunker | chunkers | NormalizedDocument | list[Chunk] | chunk_size, overlap (문자 단위) |
| SentenceChunker | chunkers | NormalizedDocument | list[Chunk] | 문장 경계 보존, max_chars, 겹침 문장 수 |
| ArticleChunker | chunkers | NormalizedDocument | list[Chunk] | "제N조" 경계 분리, article_no가 provenance로 남음 |
| LocalEmbedder | embeddings | list[Chunk] | list[Chunk] | fastembed(ONNX), EMBED_MODEL 기본: paraphrase-multilingual-MiniLM-L12-v2 (dim 384) |
| Neo4jWriter | graphdb | list[Chunk] | IngestReport | kb_id, 접속은 env/카탈로그 |
| Neo4jRetriever | graphdb | Message | list[RetrievalHit] | 벡터(cosine) 검색. kb_id, top_k, expand(이웃 청크 ±n 병합) |
| KeywordRetriever | graphdb | Message | list[RetrievalHit] | 풀텍스트(Lucene) 검색 — 고유명사·조문번호에 강함 |
| HybridRetriever | graphdb | Message | list[RetrievalHit] | 벡터+키워드 RRF 융합, rrf_k. 대부분의 기본값 |
| PromptTemplate | llm | Message + list[RetrievalHit] | Message | template ({question},{context}) |
| RetrievalPreview | formatters | list[RetrievalHit] | Message | 검색 결과를 텍스트로 정리 — LLM 없이 검색 flow를 채팅으로 테스트 |
| OpenAICompatLLM | llm | Message | Message | temperature; LLM_* env |

**확장 백로그** (구조는 위와 동일, 파일 추가만으로 들어옴):
ImageOCRParser(리눅스 런타임용), SemanticChunker, GraphExtractor(LLM 엔티티→:Entity),
VectorRetriever(외부 벡터DB), Reranker, ConversationSummarizer, HTTPTool,
정부24/국가법령 조회 Tool.

**ingest flow 자동 선택**: `POST /api/documents`에 ingest_flow_id를 안 주면 파일
확장자로 기본 flow를 고른다 (pdf/docx/hwpx/txt·md). 법령 문서용
`ingest-law-pdf`(조문 청커)는 명시적으로 지정해 사용한다.

---

## 13. 마일스톤 (각 단계가 실행 가능한 검증점)

- **M0 — SDK + 엔진**: 컴포넌트 2개(더미) flow JSON을 CLI로 실행, 이벤트 stdout 출력.
  `pytest tests/test_engine.py` 그린.
- **M1 — 백엔드 골격**: /api/components 스펙, /api/flows CRUD, /run SSE. curl로 검증.
- **M2 — 캔버스**: 팔레트→드래그→타입 매칭 연결→파라미터 폼→저장/export→실행 시
  노드 상태 색칠까지.
- **M3 — 문서 등록 + KB**: POST /api/kb로 Neo4j 컨테이너 자동 기동, PDF 업로드 →
  ingest flow → Neo4j에 청크+벡터 적재 (브라우저 콘솔로 육안 확인).
- **M4 — 질의 E2E**: 캔버스에서 KB 드래그 → 질의 flow 조립 → 채팅 패널에서
  GraphRAG 답변 (LLM 키 넣으면 동작). ★ 데모 시나리오: 도로교통법 1건 적재 후
  "어린이보호구역 주차 과태료?" 질의.
- **M5 — 번들 + 이식**: bundle API → 산출 폴더를 빈 디렉토리에서 INSTALL.md 절차로
  기동 → 질의 성공. (이 리허설이 통과해야 "이식 문제 없음"이 증명된 것)

---

## 14. 개발 규칙 (Claude Code 작업 시)

- 커밋 단위 = 마일스톤 내 검증 가능한 최소 조각. 각 조각에 테스트 동반.
- 파이썬 3.12, uv workspace. 타입힌트 필수, `ruff` + `pyright` 통과.
- 프론트: TypeScript strict. API 타입은 backend의 OpenAPI에서 생성.
- **비밀값·URI 하드코딩 발견 즉시 리팩터** (원칙 2). 테스트 픽스처도 env 주입.
- 컴포넌트의 `run()`은 순수하게: 입력 → 출력. 사이드이펙트(DB 쓰기)는 Writer류만.
- 에러 메시지는 사용자가 캔버스에서 읽는다는 전제로 작성 (한국어, 원인+조치).
- Neo4j 쿼리에 `kb_id` 필터 누락 = 리뷰 리젝 사유 (원칙 4).
- 이 문서와 코드가 어긋나면 이 문서를 먼저 고치고 코드를 맞춘다.
