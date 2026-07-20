import { useRef, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import type { ComponentUploadResult } from "../types";

const EXAMPLE = `from agentsdk import Component, Message, port, param

class MyFormatter(Component):
    """한 줄 설명 — 사이드바 툴팁에 표시된다."""
    display_name = "내 포맷터"
    category = "formatters"

    text: Message = port(input=True, display_name="입력")
    out: Message = port(output=True, display_name="출력")
    prefix: str = param(default=">> ", display_name="접두어")

    def run(self) -> Message:
        return Message(text=self.prefix + self.text.text)`;

/** 카테고리별 필수 포트 계약 (agentsdk.validate와 동일한 규칙). */
const CONTRACT_ROWS: [string, string, string][] = [
  ["parsers (파서)", "RawFile", "NormalizedDocument"],
  ["chunkers (청커)", "NormalizedDocument", "list[Chunk]"],
  ["embeddings (임베더)", "list[Chunk]", "list[Chunk]"],
  ["graphdb (검색/적재)", "Message 또는 list[Chunk]", "list[RetrievalHit] 또는 IngestReport"],
  ["llm", "(자유)", "Message"],
  ["io / formatters", "(자유)", "(자유)"],
];

export function ComponentUploadModal({ onClose }: { onClose: () => void }) {
  const setSpecs = useStore((s) => s.setSpecs);
  const log = useStore((s) => s.log);
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ComponentUploadResult | null>(null);

  async function upload(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    setResult(null);
    try {
      const res = await api.uploadComponent(file);
      setResult(res);
      if (res.ok) {
        setSpecs(await api.components());
        log(`컴포넌트 등록됨: ${(res.registered ?? []).join(", ")} — 사이드바에서 확인하세요`);
      }
    } catch (ex) {
      setResult({ ok: false, reports: [], load_error: (ex as Error).message });
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal comp-upload-modal" onClick={(e) => e.stopPropagation()}>
        <h3>컴포넌트 업로드</h3>
        <p className="muted">
          파이썬 파일 1개 = 컴포넌트 1개. 업로드하면 계약 검증기를 통과해야 등록되고,
          통과 즉시 사이드바에 나타납니다 (백엔드 재시작 불필요).
        </p>

        <details open>
          <summary>어떻게 만드나요?</summary>
          <ol className="guide-list">
            <li>
              <code>agentsdk.Component</code>를 상속한 클래스를 만들고{" "}
              <code>display_name</code>, <code>category</code>, docstring(한 줄 설명)을
              적습니다.
            </li>
            <li>
              입출력은 <code>port()</code>로 선언합니다 — 타입 어노테이션이 곧 포트
              타입이고, 캔버스에서 <b>같은 타입끼리만 연결</b>됩니다.
            </li>
            <li>
              설정값은 <code>param()</code>(폼 자동 렌더), API 키 같은 비밀값은{" "}
              <code>secret_param()</code>으로만 받습니다. <b>소스에 키를 하드코딩하면
              검증에서 거부됩니다.</b>
            </li>
            <li>
              <code>run()</code>은 입력 → 출력의 순수 함수로 작성합니다 (DB 쓰기는
              Writer류만). 에러는 사용자가 캔버스에서 읽으므로 한국어로 원인+조치를
              적으세요.
            </li>
            <li>
              외부 패키지를 새로 쓰면 <code>packages/components/pyproject.toml</code>에
              의존성을 추가해야 하고, 번들(폐쇄망)에서도 쓰려면 런타임 이미지
              재빌드가 필요합니다 — 검증기가 경고로 알려줍니다.
            </li>
          </ol>
        </details>

        <details>
          <summary>카테고리별 필수 인풋/아웃풋</summary>
          <table className="contract-table">
            <thead>
              <tr>
                <th>category</th>
                <th>필수 입력</th>
                <th>필수 출력</th>
              </tr>
            </thead>
            <tbody>
              {CONTRACT_ROWS.map(([c, i, o]) => (
                <tr key={c}>
                  <td>{c}</td>
                  <td>{i}</td>
                  <td>{o}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted">
            사용 가능한 포트 타입: RawFile, NormalizedDocument, Chunk, RetrievalHit,
            Message, IngestReport, Any, list[타입]
          </p>
        </details>

        <details>
          <summary>예제 코드</summary>
          <pre className="guide-code">{EXAMPLE}</pre>
          <p className="muted">
            업로드 전에 로컬에서 미리 검사하려면:{" "}
            <code>uv run python -m agentsdk.validate 내파일.py</code>
          </p>
        </details>

        <div className="modal-actions">
          <button onClick={onClose}>닫기</button>
          <button
            className="run-btn"
            disabled={busy}
            onClick={() => fileRef.current?.click()}
          >
            {busy ? "검증 중..." : ".py 파일 선택 → 업로드"}
          </button>
        </div>
        <input
          ref={fileRef}
          type="file"
          accept=".py"
          style={{ display: "none" }}
          onChange={(e) => upload(e.target.files?.[0])}
        />

        {result && (
          <div className={result.ok ? "output-box" : "error-box"}>
            <h4>{result.ok ? "✔ 검증 통과 — 등록 완료" : "✖ 검증 실패 — 등록되지 않음"}</h4>
            {result.load_error && <p>{result.load_error}</p>}
            {result.reports.map((r) => (
              <div key={r.component} className="report-item">
                <b>
                  {r.ok ? "✔" : "✖"} {r.component} ({r.category})
                </b>
                {r.errors.map((e, i) => (
                  <p key={i}>에러: {e}</p>
                ))}
                {r.warnings.map((w, i) => (
                  <p key={i} className="muted">
                    경고: {w}
                  </p>
                ))}
              </div>
            ))}
            {result.ok && (
              <p className="muted">
                사이드바에 새 컴포넌트가 나타났습니다. 드래그해서 바로 사용해보세요.
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
