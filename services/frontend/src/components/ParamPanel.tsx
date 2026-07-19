import { useEffect, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import type { ParamSpec } from "../types";

function ParamField({
  nodeId,
  spec,
  value,
}: {
  nodeId: string;
  spec: ParamSpec;
  value: unknown;
}) {
  const updateNodeParam = useStore((s) => s.updateNodeParam);
  const kbs = useStore((s) => s.kbs);
  const [credNames, setCredNames] = useState<string[]>([]);

  useEffect(() => {
    if (spec.secret) api.credentials().then(setCredNames).catch(() => {});
  }, [spec.secret]);

  const set = (v: unknown) => updateNodeParam(nodeId, spec.name, v);

  // kb_id 파라미터는 카탈로그 드롭다운으로
  if (spec.name === "kb_id") {
    return (
      <select value={String(value ?? "")} onChange={(e) => set(e.target.value)}>
        <option value="">KB 선택...</option>
        {kbs.map((kb) => (
          <option key={kb.kb_id} value={kb.kb_id}>
            {kb.kb_id} ({kb.doc_count}건)
          </option>
        ))}
      </select>
    );
  }

  if (spec.secret) {
    return (
      <div className="secret-field">
        <select value={String(value ?? "")} onChange={(e) => set(e.target.value)}>
          <option value="">(자격증명 없음 — env 사용)</option>
          {credNames.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        <button
          title="새 키 등록"
          onClick={async () => {
            const name = prompt("자격증명 이름 (예: my-openai-key)");
            if (!name) return;
            const val = prompt("값 (저장 후 재조회 불가)");
            if (!val) return;
            await api.createCredential(name, val);
            setCredNames(await api.credentials());
            set(name);
          }}
        >
          +키
        </button>
      </div>
    );
  }

  if (spec.kind === "bool") {
    return (
      <input type="checkbox" checked={Boolean(value)} onChange={(e) => set(e.target.checked)} />
    );
  }
  if (spec.kind === "enum" && spec.choices) {
    return (
      <select value={String(value ?? "")} onChange={(e) => set(e.target.value)}>
        {spec.choices.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    );
  }
  if (spec.multiline) {
    return (
      <textarea
        rows={8}
        value={String(value ?? "")}
        onChange={(e) => set(e.target.value)}
      />
    );
  }
  return (
    <input
      type={spec.kind === "int" || spec.kind === "float" ? "number" : "text"}
      step={spec.kind === "float" ? "0.1" : undefined}
      value={String(value ?? "")}
      onChange={(e) => {
        const v = e.target.value;
        if (spec.kind === "int") set(v === "" ? 0 : parseInt(v, 10));
        else if (spec.kind === "float") set(v === "" ? 0 : parseFloat(v));
        else set(v);
      }}
    />
  );
}

export function ParamPanel() {
  const selectedNodeId = useStore((s) => s.selectedNodeId);
  const node = useStore((s) => s.rfNodes.find((n) => n.id === selectedNodeId));
  const spec = useStore((s) => (node ? s.specMap[node.data.componentType] : undefined));
  const status = useStore((s) => (selectedNodeId ? s.nodeStatus[selectedNodeId] : undefined));

  if (!node || !spec) return null;

  return (
    <div className="param-panel">
      <h3>
        {spec.display_name} <span className="muted">({node.id})</span>
      </h3>
      <p className="muted">{spec.description}</p>
      {spec.params.map((p) => (
        <label key={p.name} className="param-field">
          <span>
            {p.display_name}
            {p.required && <em> *</em>}
          </span>
          <ParamField nodeId={node.id} spec={p} value={node.data.params[p.name]} />
        </label>
      ))}
      {status?.state === "failed" && (
        <div className="error-box">
          <h4>실행 실패 — {status.errorKind}</h4>
          <p>{status.error}</p>
          {status.inputSnapshot && Object.keys(status.inputSnapshot).length > 0 && (
            <>
              <h5>입력 스냅샷</h5>
              <pre>{JSON.stringify(status.inputSnapshot, null, 2)}</pre>
            </>
          )}
          {status.traceback && (
            <details>
              <summary>traceback</summary>
              <pre>{status.traceback}</pre>
            </details>
          )}
        </div>
      )}
      {status?.state === "ok" && status.outputPreview && (
        <div className="output-box">
          <h4>출력 미리보기 ({status.durationMs}ms)</h4>
          <pre>{status.outputPreview}</pre>
        </div>
      )}
    </div>
  );
}
