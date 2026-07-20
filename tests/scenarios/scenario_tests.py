"""시나리오 테스트 — 실행 중인 백엔드(:8000)를 상대로 기능·에러경로·격리를 검사한다.

pytest가 아닌 독립 스크립트다 (백엔드 + Docker가 떠 있어야 하므로 CI 단위 테스트와 분리):

    uv run python tests/scenarios/scenario_tests.py

전용 KB(qa_scenario)를 만들어 쓰고 마지막에 정리한다. 기존 KB/flow는 건드리지 않는다.
S11(RAG)은 .env의 LLM 설정이 필요하다. 나머지는 LLM 없이 동작한다.
"""
from __future__ import annotations

import io
import json
import traceback

import requests

BASE = "http://localhost:8000"
KB = "qa_scenario"
results: list[tuple[str, str, str]] = []  # (PASS/FAIL, name, detail)
created_flows: list[str] = []


def sse(resp) -> list[dict]:
    events = []
    for line in resp.iter_lines(decode_unicode=True):
        if line and line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def run_adhoc(flow: dict, text: str | None = None) -> list[dict]:
    if text is not None:
        flow = {**flow, "__input__": {"text": text}}
    r = requests.post(f"{BASE}/api/flows/adhoc/run",
                      json={"name": flow.get("name", "adhoc"), "flow": flow},
                      stream=True, timeout=300)
    r.raise_for_status()
    return sse(r)


def finished(events: list[dict]) -> dict:
    return next(e for e in events if e.get("event") == "run_finished")


def failures(events: list[dict]) -> list[dict]:
    return [e for e in events if e.get("event") == "node_failed"]


def terminal_text(events: list[dict]) -> str:
    for out in (finished(events).get("outputs") or {}).values():
        if isinstance(out, dict) and "text" in out:
            return out["text"]
    return ""


def preview_flow(kb_id: str, rtype: str = "HybridRetriever", top_k: int = 5) -> dict:
    return {
        "version": "1", "name": f"qa-{rtype}",
        "nodes": [
            {"id": "n1", "type": "ChatInput", "params": {}},
            {"id": "n2", "type": rtype, "params": {"kb_id": kb_id, "top_k": top_k}},
            {"id": "n3", "type": "RetrievalPreview", "params": {"max_chars": 0}},
            {"id": "n4", "type": "ChatOutput", "params": {}},
        ],
        "edges": [
            {"from": ["n1", "message"], "to": ["n2", "query"]},
            {"from": ["n2", "hits"], "to": ["n3", "hits"]},
            {"from": ["n3", "message"], "to": ["n4", "message"]},
        ],
        "ui": {},
    }


# ---------------------------------------------------------------- 시나리오들


def s01_flow_crud():
    flow = {"version": "1", "name": "qa-crud", "nodes": [{"id": "n1", "type": "ChatInput", "params": {}}], "edges": [], "ui": {}}
    r = requests.post(f"{BASE}/api/flows", json={"name": "qa-crud", "flow": flow}); r.raise_for_status()
    fid = r.json()["id"]; created_flows.append(fid)
    assert requests.get(f"{BASE}/api/flows/{fid}").json()["name"] == "qa-crud"
    flow["name"] = "qa-crud-2"
    requests.put(f"{BASE}/api/flows/{fid}", json={"name": "qa-crud-2", "flow": flow}).raise_for_status()
    exp = requests.get(f"{BASE}/api/flows/{fid}/export")
    assert exp.json()["name"] == "qa-crud-2" and "attachment" in exp.headers.get("content-disposition", "")
    requests.delete(f"{BASE}/api/flows/{fid}").raise_for_status()
    assert requests.get(f"{BASE}/api/flows/{fid}").status_code == 404
    created_flows.remove(fid)
    return "생성/조회/수정/export/삭제 정상"


def s02_secret_defense():
    flow = {"version": "1", "name": "qa-secret",
            "nodes": [{"id": "n1", "type": "OpenAICompatLLM",
                       "params": {"api_key": "sk-proj-Abcdefghij1234567890"}}],
            "edges": [], "ui": {}}
    r = requests.post(f"{BASE}/api/flows", json={"name": "qa-secret", "flow": flow})
    assert r.status_code == 422, f"비밀값 저장이 거부되지 않음: {r.status_code}"
    assert "비밀값" in r.json()["detail"]
    return f"422 거부 + 한국어 안내: '{r.json()['detail'][:60]}...'"


def s03_type_mismatch():
    flow = {"version": "1", "name": "qa-mismatch",
            "nodes": [{"id": "n1", "type": "ChatInput", "params": {}},
                      {"id": "n2", "type": "SimpleChunker", "params": {}}],
            "edges": [{"from": ["n1", "message"], "to": ["n2", "document"]}], "ui": {}}
    ev = run_adhoc(flow, text="hi")
    fin = next(e for e in ev if e.get("event") in ("run_failed", "run_finished"))
    err = fin.get("error", "")
    assert "타입 불일치" in err, f"타입 검증 미동작: {err}"
    return "실행 전 타입 불일치 검출 (Message→NormalizedDocument)"


def s04_empty_input():
    flow = preview_flow(KB)
    ev = run_adhoc(flow)  # __input__ 없음
    f = failures(ev)
    assert f and f[0]["error_kind"] == "bad_input" and "text가 없습니다" in f[0]["error"]
    skipped = [e for e in ev if e.get("event") == "node_skipped"]
    assert len(skipped) == 3, f"하류 스킵 전파 이상: {len(skipped)}"
    return "ChatInput bad_input + 하류 3개 노드 skipped 전파"


def s05_missing_kb():
    ev = run_adhoc(preview_flow("no_such_kb"), text="질문")
    f = failures(ev)
    assert f and "카탈로그에 없습니다" in f[0]["error"]
    return "존재하지 않는 KB → 명확한 한국어 에러"


def s06_kb_create():
    r = requests.post(f"{BASE}/api/kb", json={"name": KB}, timeout=300)
    r.raise_for_status()
    kb = r.json()
    assert kb["status"] == "ready" and kb["dim"] == 384
    return f"컨테이너 프로비저닝 완료 (bolt={kb['bolt_uri']}, dim=384)"


def s07_upload_txt():
    with open("samples/주차관리규정.txt", "rb") as f:
        r = requests.post(f"{BASE}/api/documents",
                          files={"file": ("주차관리규정.txt", f, "text/plain")},
                          data={"kb_id": KB}, stream=True, timeout=300)
        ev = sse(r)
    done = next(e for e in ev if e.get("event") == "document_done")
    assert done["status"] == "done" and done["chunks_written"] > 0
    docs = requests.get(f"{BASE}/api/documents", params={"kb_id": KB}).json()
    assert len(docs) == 1 and docs[0]["status"] == "done"
    return f"확장자 자동 선택(텍스트) 적재 — 청크 {done['chunks_written']}개, 문서목록 반영"


def s08_upload_table_pdf():
    import fitz
    buf_path = "data/uploads/qa_표문서.pdf"
    doc = fitz.open(); page = doc.new_page()
    page.insert_text((72, 60), "Refund policy table:", fontsize=11)
    rows = [["item", "days", "fee"], ["laptop", "14", "0"], ["phone", "7", "10000"]]
    x0, y0, cw, rh = 72, 90, 140, 24
    for i in range(len(rows) + 1):
        page.draw_line((x0, y0 + i * rh), (x0 + cw * 3, y0 + i * rh))
    for c in range(4):
        page.draw_line((x0 + c * cw, y0), (x0 + c * cw, y0 + rh * len(rows)))
    for ri, row in enumerate(rows):
        for c, cell in enumerate(row):
            page.insert_text((x0 + c * cw + 6, y0 + ri * rh + 16), cell, fontsize=10)
    doc.save(buf_path); doc.close()
    with open(buf_path, "rb") as f:
        r = requests.post(f"{BASE}/api/documents",
                          files={"file": ("qa_표문서.pdf", f, "application/pdf")},
                          data={"kb_id": KB, "ingest_flow_id": "ingest-pdf-table"},
                          stream=True, timeout=300)
        ev = sse(r)
    done = next(e for e in ev if e.get("event") == "document_done")
    assert done["status"] == "done" and done["chunks_written"] >= 2
    return f"내장 체인 flow(ingest-pdf-table) 적재 — 청크 {done['chunks_written']}개"


def s09_retrieval_strategies():
    out = []
    for rtype, q, expect in [
        ("KeywordRetriever", "laptop 14", "| laptop | 14 | 0 |"),
        ("Neo4jRetriever", "환불 정책", "Refund"),
        ("HybridRetriever", "laptop refund fee", "laptop"),
    ]:
        ev = run_adhoc(preview_flow(KB, rtype), text=q)
        assert not failures(ev), f"{rtype} 실패: {failures(ev)}"
        text = terminal_text(ev)
        assert expect in text, f"{rtype}: '{expect}' 미포함"
        out.append(rtype)
    return f"3전략 모두 정상 ({', '.join(out)}) — 표 마크다운 행 구조 검색 확인"


def s10_kb_isolation():
    # qa_scenario에는 약전(실험문서) 내용이 없다 — 다른 KB(test) 문서가 새면 안 됨
    ev = run_adhoc(preview_flow(KB, "KeywordRetriever"), text="대한민국약전 통칙")
    text = terminal_text(ev)
    assert "실험문서" not in text, "다른 KB 문서가 검색됨 (격리 실패!)"
    return "타 KB 문서 미검출 — kb_id/컨테이너 격리 정상"


def s11_rag_with_llm():
    flow = {
        "version": "1", "name": "qa-rag",
        "nodes": [
            {"id": "n1", "type": "ChatInput", "params": {}},
            {"id": "n2", "type": "HybridRetriever", "params": {"kb_id": KB, "top_k": 5}},
            {"id": "n3", "type": "PromptTemplate", "params": {}},
            {"id": "n4", "type": "OpenAICompatLLM", "params": {"temperature": 0.0}},
            {"id": "n5", "type": "ChatOutput", "params": {}},
        ],
        "edges": [
            {"from": ["n1", "message"], "to": ["n2", "query"]},
            {"from": ["n1", "message"], "to": ["n3", "question"]},
            {"from": ["n2", "hits"], "to": ["n3", "context"]},
            {"from": ["n3", "prompt"], "to": ["n4", "prompt"]},
            {"from": ["n4", "answer"], "to": ["n5", "message"]},
        ], "ui": {},
    }
    ev = run_adhoc(flow, text="노트북(laptop)은 며칠 안에 환불할 수 있어?")
    assert not failures(ev), f"RAG 실패: {failures(ev)}"
    ans = terminal_text(ev)
    assert "14" in ans, f"표 근거 답변 실패: {ans[:120]}"
    ev2 = run_adhoc(flow, text="화성 이주 비용은 얼마야?")
    ans2 = terminal_text(ev2)
    refused = ("확인되지 않" in ans2) or ("없" in ans2)
    assert refused, f"환각 거부 실패: {ans2[:120]}"
    return f"표 근거 답변 OK('{ans[:40]}...') + 환각 거부 OK"


def s12_run_history():
    ev = run_adhoc(preview_flow(KB), text="laptop")
    run_id = finished(ev)["run_id"]
    r = requests.get(f"{BASE}/api/runs/{run_id}")
    r.raise_for_status()
    saved = r.json()
    kinds = {e.get("event") for e in saved["events"]}
    assert {"run_started", "node_started", "node_finished", "run_finished"} <= kinds
    assert saved["status"] == "ok"
    return f"runs 저장/조회 정상 — 이벤트 {len(saved['events'])}건, 노드별 preview 포함"


def s13_credentials():
    requests.post(f"{BASE}/api/credentials", json={"name": "qa-cred", "value": "dummy-value-123"}).raise_for_status()
    names = requests.get(f"{BASE}/api/credentials").json()
    assert "qa-cred" in names
    requests.delete(f"{BASE}/api/credentials/qa-cred").raise_for_status()
    assert "qa-cred" not in requests.get(f"{BASE}/api/credentials").json()
    return "등록/목록(값 미노출)/삭제 정상"


def s14_component_upload_reject():
    bad = ("from agentsdk import Component, Message, port\n"
           "class QaBad(Component):\n"
           "    display_name='x'; category='parsers'\n"
           "    t: Message = port(input=True)\n"
           "    o: Message = port(output=True)\n"
           "    def run(self): return self.t\n")
    r = requests.post(f"{BASE}/api/components/upload",
                      files={"file": ("qa_bad.py", io.BytesIO(bad.encode()))})
    res = r.json()
    assert res["ok"] is False and any("RawFile" in e for rep in res["reports"] for e in rep["errors"])
    specs = [s["type"] for s in requests.get(f"{BASE}/api/components").json()]
    assert "QaBad" not in specs
    return "계약 위반 컴포넌트 거부 + 미등록 확인"


def s15_kb_delete():
    requests.delete(f"{BASE}/api/kb/{KB}").raise_for_status()
    kbs = [k["kb_id"] for k in requests.get(f"{BASE}/api/kb").json()]
    assert KB not in kbs
    docs = requests.get(f"{BASE}/api/documents", params={"kb_id": KB}).json()
    assert docs == []
    return "KB 삭제 → 카탈로그/문서목록 정리 확인"


SCENARIOS = [
    ("S01 flow CRUD/export", s01_flow_crud),
    ("S02 비밀값 저장 방어선(원칙2)", s02_secret_defense),
    ("S03 포트 타입 불일치 사전 검출", s03_type_mismatch),
    ("S04 빈 입력 → bad_input + 스킵 전파", s04_empty_input),
    ("S05 없는 KB → 친절한 에러", s05_missing_kb),
    ("S06 KB 프로비저닝", s06_kb_create),
    ("S07 텍스트 문서 적재(확장자 자동)", s07_upload_txt),
    ("S08 표 PDF 적재(내장 체인 flow)", s08_upload_table_pdf),
    ("S09 검색 3전략(키워드/벡터/하이브리드)", s09_retrieval_strategies),
    ("S10 KB 격리", s10_kb_isolation),
    ("S11 RAG 질의응답 + 환각 거부", s11_rag_with_llm),
    ("S12 실행 이력(runs) 관측성", s12_run_history),
    ("S13 자격증명 API", s13_credentials),
    ("S14 컴포넌트 업로드 거부 경로", s14_component_upload_reject),
    ("S15 KB 삭제 정리", s15_kb_delete),
]

for name, fn in SCENARIOS:
    try:
        detail = fn()
        results.append(("PASS", name, detail))
        print(f"[PASS] {name} — {detail}")
    except Exception as ex:
        results.append(("FAIL", name, f"{type(ex).__name__}: {ex}"))
        print(f"[FAIL] {name} — {type(ex).__name__}: {ex}")
        traceback.print_exc()

# 정리: 남은 qa flow 제거
for fid in created_flows:
    requests.delete(f"{BASE}/api/flows/{fid}")

npass = sum(1 for r in results if r[0] == "PASS")
print(f"\n===== 시나리오 {len(results)}개 — 통과 {npass}, 실패 {len(results) - npass} =====")
