import { useRef, useState } from "react";
import { api, runFlow } from "../api";
import { useStore } from "../store";
import type { FlowJson } from "../types";
import { ComponentUploadModal } from "./ComponentUploadModal";

/** 저장 안 됐으면 저장하고 flowId를 반환. */
export async function ensureSaved(): Promise<string> {
  const st = useStore.getState();
  const flow = st.serializeFlow();
  if (st.flowId) {
    await api.updateFlow(st.flowId, flow);
    return st.flowId;
  }
  const created = await api.createFlow(flow);
  st.setFlowId(created.id);
  return created.id;
}

export function Toolbar() {
  const flowName = useStore((s) => s.flowName);
  const setFlowName = useStore((s) => s.setFlowName);
  const flowId = useStore((s) => s.flowId);
  const running = useStore((s) => s.running);
  const setRunning = useStore((s) => s.setRunning);
  const resetStatus = useStore((s) => s.resetStatus);
  const applyEvent = useStore((s) => s.applyEvent);
  const loadFlow = useStore((s) => s.loadFlow);
  const log = useStore((s) => s.log);
  const importRef = useRef<HTMLInputElement>(null);
  const [flowList, setFlowList] = useState<{ id: string; name: string }[]>([]);
  const [bundling, setBundling] = useState(false);
  const [showCompUpload, setShowCompUpload] = useState(false);

  async function save() {
    try {
      const id = await ensureSaved();
      log(`flow 저장됨 (${id})`);
    } catch (ex) {
      alert(`저장 실패: ${(ex as Error).message}`);
    }
  }

  async function run() {
    setRunning(true);
    resetStatus();
    try {
      const id = await ensureSaved();
      await runFlow(id, {}, (ev) => {
        applyEvent(ev);
        if (ev.event === "run_finished") log(`실행 종료: ${ev.status}`);
      });
    } catch (ex) {
      log(`실행 실패: ${(ex as Error).message}`);
      alert(`실행 실패: ${(ex as Error).message}`);
    } finally {
      setRunning(false);
    }
  }

  async function makeBundle() {
    if (!confirm(
      "이 에이전트를 폐쇄망 이식용 도커 번들로 제조합니다.\n" +
      "이미지 저장 때문에 몇 분 걸리고, 제조 중 KB 컨테이너가 잠시 재시작됩니다. 진행할까요?",
    )) return;
    setBundling(true);
    log("번들 제조 중... (이미지 tar 저장, 수 분 소요)");
    try {
      const id = await ensureSaved();
      const res = await api.bundle(id);
      const totalMb = Math.round(
        Object.values(res.files).reduce((a, b) => a + b, 0) / 1024 / 1024,
      );
      log(`번들 완성: ${res.path} (${totalMb}MB)`);
      alert(
        `번들 제조 완료 (${totalMb}MB)\n\n${res.path}\n\n` +
        "이 폴더를 통째로 대상 PC에 복사한 뒤 install.ps1(윈도우) 또는 " +
        "install.sh(리눅스)를 실행하면 됩니다. 자세한 절차는 폴더 안 INSTALL.md 참고.",
      );
    } catch (ex) {
      alert(`번들 제조 실패: ${(ex as Error).message}`);
    } finally {
      setBundling(false);
    }
  }

  function exportFlow() {
    const flow = useStore.getState().serializeFlow();
    const blob = new Blob([JSON.stringify(flow, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${flow.name}.flow.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  async function importFlow(file: File | undefined) {
    if (!file) return;
    try {
      const flow = JSON.parse(await file.text()) as FlowJson;
      loadFlow(null, flow);
      log(`flow '${flow.name}' 불러옴 (저장 전)`);
    } catch (ex) {
      alert(`불러오기 실패: ${(ex as Error).message}`);
    }
    if (importRef.current) importRef.current.value = "";
  }

  async function openFlowList() {
    setFlowList(await api.flows());
  }

  async function openFlow(id: string) {
    const data = await api.getFlow(id);
    loadFlow(data.id, data.flow);
    setFlowList([]);
  }

  function newFlow() {
    loadFlow(null, { version: "1", name: "새 에이전트", nodes: [], edges: [], ui: {} });
  }

  return (
    <header className="toolbar">
      <strong className="brand">Unnest</strong>
      <input
        className="flow-name"
        value={flowName}
        onChange={(e) => setFlowName(e.target.value)}
      />
      <span className="muted">{flowId ?? "(저장 안 됨)"}</span>
      <div className="toolbar-actions">
        <button onClick={newFlow}>새 flow</button>
        <button
          onClick={() => setShowCompUpload(true)}
          title="컴포넌트 .py 업로드 — 검증 통과 시 사이드바에 즉시 등록"
        >
          ➕ 컴포넌트
        </button>
        <div className="dropdown">
          <button onClick={openFlowList}>열기</button>
          {flowList.length > 0 && (
            <div className="dropdown-list">
              {flowList.map((f) => (
                <div key={f.id} className="dropdown-item" onClick={() => openFlow(f.id)}>
                  {f.name} <span className="muted">{f.id}</span>
                </div>
              ))}
              <div className="dropdown-item" onClick={() => setFlowList([])}>
                닫기
              </div>
            </div>
          )}
        </div>
        <button onClick={save}>저장</button>
        <button onClick={exportFlow}>Export</button>
        <button onClick={() => importRef.current?.click()}>Import</button>
        <button onClick={makeBundle} disabled={bundling} title="폐쇄망 이식용 도커 번들 제조">
          {bundling ? "번들 제조 중..." : "📦 번들"}
        </button>
        <button className="run-btn" onClick={run} disabled={running}>
          {running ? "실행 중..." : "▶ 실행"}
        </button>
      </div>
      <input
        ref={importRef}
        type="file"
        accept=".json"
        style={{ display: "none" }}
        onChange={(e) => importFlow(e.target.files?.[0])}
      />
      {showCompUpload && <ComponentUploadModal onClose={() => setShowCompUpload(false)} />}
    </header>
  );
}
