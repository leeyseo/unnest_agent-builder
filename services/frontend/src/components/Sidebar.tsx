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
      log(`KB '${kb.kb_id}' 준비 완료 (${kb.bolt_uri})`);
      setNewKbName("");
      await refreshKbs();
    } catch (ex) {
      log(`KB 생성 실패: ${(ex as Error).message}`);
      alert(`KB 생성 실패: ${(ex as Error).message}`);
    } finally {
      setCreating(false);
    }
  }

  function pickFileFor(kbId: string) {
    uploadTarget.current = kbId;
    fileRef.current?.click();
  }

  async function onFilePicked(file: File | undefined) {
    const kbId = uploadTarget.current;
    if (!file || !kbId) return;
    setBusyKb(kbId);
    log(`'${file.name}' → KB '${kbId}' 적재 시작`);
    try {
      await uploadDocument(kbId, file, (ev) => {
        applyEvent(ev);
        if (ev.event === "node_started") log(`  [적재] ${ev.node_id} 실행...`);
        if (ev.event === "node_failed") log(`  [적재] 실패: ${ev.error}`);
        if (ev.event === "document_done")
          log(`적재 ${ev.status === "done" ? "완료" : "실패"} — 청크 ${ev.chunks_written}개`);
      });
      await refreshKbs();
    } catch (ex) {
      log(`적재 실패: ${(ex as Error).message}`);
    } finally {
      setBusyKb(null);
      if (fileRef.current) fileRef.current.value = "";
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
            title="캔버스로 드래그하면 이 KB로 프리셋된 Neo4j 검색 노드가 생성됩니다"
            onDragStart={(e) =>
              e.dataTransfer.setData(
                "application/x-component",
                JSON.stringify({ type: "Neo4jRetriever", params: { kb_id: kb.kb_id } }),
              )
            }
          >
            <span className={`kb-dot kb-${kb.status}`} />
            <span className="kb-name">{kb.kb_id}</span>
            <span className="kb-docs">{kb.doc_count}건</span>
          </div>
          <div className="kb-actions">
            <span
              className="kb-chip"
              draggable
              title="적재(Writer) 노드로 드래그"
              onDragStart={(e) =>
                e.dataTransfer.setData(
                  "application/x-component",
                  JSON.stringify({ type: "Neo4jWriter", params: { kb_id: kb.kb_id } }),
                )
              }
            >
              적재노드
            </span>
            <button
              className="kb-chip"
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
        accept=".pdf"
        style={{ display: "none" }}
        onChange={(e) => onFilePicked(e.target.files?.[0])}
      />
    </aside>
  );
}
