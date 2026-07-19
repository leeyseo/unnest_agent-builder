import { useRef, useState } from "react";
import { api, uploadDocument } from "../api";
import { useStore } from "../store";
import type { ComponentSpec } from "../types";

const CATEGORY_LABELS: Record<string, string> = {
  io: "입출력",
  parsers: "파서",
  chunkers: "청커",
  embeddings: "임베딩",
  graphdb: "그래프 DB",
  llm: "LLM",
  formatters: "포맷터",
};

// KB 드래그 시 선택 가능한 검색 전략 (기본 드래그 = 하이브리드)
const KB_STRATEGIES: { type: string; label: string; title: string }[] = [
  { type: "HybridRetriever", label: "하이브리드", title: "벡터+키워드 RRF 융합 (권장 기본값)" },
  { type: "Neo4jRetriever", label: "벡터", title: "의미 유사도 검색" },
  { type: "KeywordRetriever", label: "키워드", title: "풀텍스트 검색 — 고유명사·조문번호에 강함" },
  { type: "Neo4jWriter", label: "적재", title: "이 KB에 기록하는 Writer 노드" },
];

function DraggableComponent({ spec }: { spec: ComponentSpec }) {
  return (
    <div
      className="palette-item"
      draggable
      title={spec.description}
      onDragStart={(e) => {
        e.dataTransfer.setData(
          "application/x-component",
          JSON.stringify({ type: spec.type }),
        );
        e.dataTransfer.effectAllowed = "move";
      }}
    >
      <span>{spec.display_name}</span>
      <span className="palette-type">{spec.type}</span>
    </div>
  );
}

interface PendingUpload {
  kbId: string;
  file: File;
}

export function Sidebar() {
  const specs = useStore((s) => s.specs);
  const kbs = useStore((s) => s.kbs);
  const setKbs = useStore((s) => s.setKbs);
  const log = useStore((s) => s.log);
  const applyEvent = useStore((s) => s.applyEvent);
  const [creating, setCreating] = useState(false);
  const [newKbName, setNewKbName] = useState("");
  const [busyKb, setBusyKb] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const uploadTarget = useRef<string | null>(null);
  // 업로드 확정 전 상태: 적재 flow 선택 모달
  const [pending, setPending] = useState<PendingUpload | null>(null);
  const [ingestFlows, setIngestFlows] = useState<{ id: string; name: string }[]>([]);
  const [selectedIngest, setSelectedIngest] = useState("");

  const categories = [...new Set(specs.map((s) => s.category))];

  async function refreshKbs() {
    setKbs(await api.kbs());
  }

  async function createKb() {
    if (!newKbName.trim()) return;
    setCreating(true);
    log(`KB '${newKbName}' 생성 중... (Neo4j 컨테이너 기동, 1~2분 소요)`);
    try {
      const kb = await api.createKb(newKbName.trim());
      log(`KB '${kb.kb_id}' 준비 완료`);
      setNewKbName("");
      await refreshKbs();
    } catch (ex) {
      log(`KB 생성 실패: ${(ex as Error).message}`);
      alert(`KB 생성 실패: ${(ex as Error).message}`);
    } finally {
      setCreating(false);
    }
  }

  async function deleteKb(kbId: string) {
    if (!confirm(
      `KB '${kbId}'를 삭제할까요?\n컨테이너가 제거되고 카탈로그에서 사라집니다. (데이터 볼륨은 남습니다)`,
    )) return;
    try {
      await api.deleteKb(kbId);
      log(`KB '${kbId}' 삭제됨`);
      await refreshKbs();
    } catch (ex) {
      alert(`삭제 실패: ${(ex as Error).message}`);
    }
  }

  function pickFileFor(kbId: string) {
    uploadTarget.current = kbId;
    fileRef.current?.click();
  }

  /** 파일 선택 → 적재 flow 선택 모달을 띄운다. */
  async function onFilePicked(file: File | undefined) {
    const kbId = uploadTarget.current;
    if (!file || !kbId) return;
    const flows = (await api.flows()).filter((f) => f.is_ingest);
    setIngestFlows(flows);
    setSelectedIngest(""); // "" = 확장자 자동 선택
    setPending({ kbId, file });
    if (fileRef.current) fileRef.current.value = "";
  }

  async function startUpload() {
    if (!pending) return;
    const { kbId, file } = pending;
    setPending(null);
    setBusyKb(kbId);
    const flowLabel = selectedIngest
      ? ingestFlows.find((f) => f.id === selectedIngest)?.name
      : "자동 (확장자 기준)";
    log(`'${file.name}' → KB '${kbId}' 적재 시작 [${flowLabel}]`);
    try {
      await uploadDocument(kbId, file, (ev) => {
        applyEvent(ev);
        if (ev.event === "node_failed") log(`  [적재] 실패: ${ev.error}`);
        if (ev.event === "document_done")
          log(`적재 ${ev.status === "done" ? "완료" : "실패"} — 청크 ${ev.chunks_written}개`);
      }, selectedIngest || undefined);
      await refreshKbs();
    } catch (ex) {
      log(`적재 실패: ${(ex as Error).message}`);
      alert(`적재 실패: ${(ex as Error).message}`);
    } finally {
      setBusyKb(null);
    }
  }

  return (
    <aside className="sidebar">
      <h2>컴포넌트</h2>
      {categories.map((cat) => (
        <div key={cat} className="palette-group">
          <h3>{CATEGORY_LABELS[cat] ?? cat}</h3>
          {specs
            .filter((s) => s.category === cat)
            .map((s) => (
              <DraggableComponent key={s.type} spec={s} />
            ))}
        </div>
      ))}

      <h2>지식 베이스</h2>
      <div className="kb-create">
        <input
          value={newKbName}
          placeholder="새 KB 이름 (영문)"
          onChange={(e) => setNewKbName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && createKb()}
          disabled={creating}
        />
        <button onClick={createKb} disabled={creating}>
          {creating ? "생성 중..." : "+"}
        </button>
      </div>
      {kbs.map((kb) => (
        <div key={kb.kb_id} className="kb-item">
          <div
            className="kb-main"
            draggable
            title="드래그 = 하이브리드 검색 노드 생성 (아래 칩으로 다른 전략 선택)"
            onDragStart={(e) =>
              e.dataTransfer.setData(
                "application/x-component",
                JSON.stringify({ type: "HybridRetriever", params: { kb_id: kb.kb_id } }),
              )
            }
          >
            <span className={`kb-dot kb-${kb.status}`} />
            <span className="kb-name">{kb.kb_id}</span>
            <span className="kb-docs">{kb.doc_count}건</span>
            <button
              className="kb-delete"
              title="KB 삭제"
              onClick={(e) => {
                e.stopPropagation();
                deleteKb(kb.kb_id);
              }}
            >
              ×
            </button>
          </div>
          <div className="kb-actions">
            {KB_STRATEGIES.map((s) => (
              <span
                key={s.type}
                className="kb-chip"
                draggable
                title={s.title}
                onDragStart={(e) =>
                  e.dataTransfer.setData(
                    "application/x-component",
                    JSON.stringify({ type: s.type, params: { kb_id: kb.kb_id } }),
                  )
                }
              >
                {s.label}
              </span>
            ))}
            <button
              className="kb-chip kb-upload"
              disabled={busyKb === kb.kb_id}
              onClick={() => pickFileFor(kb.kb_id)}
            >
              {busyKb === kb.kb_id ? "적재중..." : "문서 업로드"}
            </button>
          </div>
        </div>
      ))}
      <input
        ref={fileRef}
        type="file"
        accept=".pdf,.docx,.hwpx,.txt,.md"
        style={{ display: "none" }}
        onChange={(e) => onFilePicked(e.target.files?.[0])}
      />

      {pending && (
        <div className="modal-backdrop" onClick={() => setPending(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>문서 적재 — 파이프라인 선택</h3>
            <p className="muted">
              '{pending.file.name}' → KB '{pending.kbId}'
            </p>
            <select
              value={selectedIngest}
              onChange={(e) => setSelectedIngest(e.target.value)}
            >
              <option value="">자동 (확장자 기준 기본 파이프라인)</option>
              {ingestFlows.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name}
                </option>
              ))}
            </select>
            <p className="muted">
              파서·청커·임베딩 조합을 직접 정하려면 캔버스에서
              FileInput → 파서 → 청커 → 임베더 → Neo4j 적재 flow를 만들어 저장하세요.
              저장된 적재 flow가 이 목록에 나타납니다.
            </p>
            <div className="modal-actions">
              <button onClick={() => setPending(null)}>취소</button>
              <button className="run-btn" onClick={startUpload}>
                적재 시작
              </button>
            </div>
          </div>
        </div>
      )}
    </aside>
  );
}
