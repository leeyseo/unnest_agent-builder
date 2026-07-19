import { Handle, Position, type NodeProps } from "@xyflow/react";
import { typeColor, useStore, type RFNode } from "../store";

const STATE_STYLE: Record<string, { border: string; shadow?: string; anim?: boolean }> = {
  idle: { border: "#3f3f46" },
  running: { border: "#3b82f6", shadow: "0 0 12px #3b82f6aa", anim: true },
  ok: { border: "#22c55e" },
  failed: { border: "#ef4444", shadow: "0 0 10px #ef444488" },
  skipped: { border: "#6b7280" },
};

export function FlowNode({ id, data, selected }: NodeProps<RFNode>) {
  const spec = useStore((s) => s.specMap[data.componentType]);
  const status = useStore((s) => s.nodeStatus[id]);
  const state = status?.state ?? "idle";
  const st = STATE_STYLE[state] ?? STATE_STYLE.idle;

  if (!spec) {
    return <div className="node node-unknown">알 수 없는 컴포넌트: {data.componentType}</div>;
  }

  const kbId = data.params["kb_id"] as string | undefined;

  return (
    <div
      className={`node ${st.anim ? "node-pulse" : ""}`}
      style={{
        borderColor: selected ? "#eab308" : st.border,
        boxShadow: st.shadow,
      }}
    >
      <div className="node-header">
        <span className="node-title">{spec.display_name}</span>
        <span className="node-cat">{spec.category}</span>
      </div>
      {kbId !== undefined && (
        <div className="node-kb">{kbId ? `KB: ${kbId}` : "KB 미지정"}</div>
      )}
      {state === "failed" && <div className="node-error">✕ {status?.errorKind}</div>}
      {state === "ok" && status?.durationMs !== undefined && (
        <div className="node-ok">✓ {status.durationMs}ms</div>
      )}
      <div className="node-ports">
        <div className="node-inputs">
          {spec.inputs.map((p, i) => (
            <div key={p.name} className="port-row">
              <Handle
                type="target"
                position={Position.Left}
                id={p.name}
                style={{ background: typeColor(p.type), top: "auto", position: "relative", transform: "none" }}
              />
              <span className="port-label" title={p.type}>{p.display_name}</span>
            </div>
          ))}
        </div>
        <div className="node-outputs">
          {spec.outputs.map((p) => (
            <div key={p.name} className="port-row port-row-out">
              <span className="port-label" title={p.type}>{p.display_name}</span>
              <Handle
                type="source"
                position={Position.Right}
                id={p.name}
                style={{ background: typeColor(p.type), top: "auto", position: "relative", transform: "none" }}
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
