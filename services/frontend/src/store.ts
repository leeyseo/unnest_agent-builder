import { create } from "zustand";
import {
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
} from "@xyflow/react";
import type { ComponentSpec, FlowJson, KB, NodeStatus, RunEvent } from "./types";

export interface ChatMsg {
  role: "user" | "assistant" | "system";
  text: string;
}

export interface NodeData extends Record<string, unknown> {
  componentType: string;
  params: Record<string, unknown>;
}

export type RFNode = Node<NodeData>;

let nodeSeq = 1;

interface PlatformState {
  specs: ComponentSpec[];
  specMap: Record<string, ComponentSpec>;
  kbs: KB[];
  flowId: string | null;
  flowName: string;
  rfNodes: RFNode[];
  rfEdges: Edge[];
  nodeStatus: Record<string, NodeStatus>;
  running: boolean;
  chat: ChatMsg[];
  selectedNodeId: string | null;
  logLines: string[];

  setSpecs: (specs: ComponentSpec[]) => void;
  setKbs: (kbs: KB[]) => void;
  setFlowId: (id: string | null) => void;
  setFlowName: (name: string) => void;
  setRunning: (running: boolean) => void;
  setSelectedNode: (id: string | null) => void;
  onNodesChange: (changes: NodeChange<RFNode>[]) => void;
  onEdgesChange: (changes: EdgeChange[]) => void;
  onConnect: (conn: Connection) => void;
  addNode: (componentType: string, position: { x: number; y: number }, params?: Record<string, unknown>) => void;
  updateNodeParam: (nodeId: string, key: string, value: unknown) => void;
  loadFlow: (id: string | null, flow: FlowJson) => void;
  serializeFlow: () => FlowJson;
  resetStatus: () => void;
  applyEvent: (ev: RunEvent) => void;
  pushChat: (msg: ChatMsg) => void;
  clearChat: () => void;
  log: (line: string) => void;
}

export const useStore = create<PlatformState>((set, get) => ({
  specs: [],
  specMap: {},
  kbs: [],
  flowId: null,
  flowName: "새 에이전트",
  rfNodes: [],
  rfEdges: [],
  nodeStatus: {},
  running: false,
  chat: [],
  selectedNodeId: null,
  logLines: [],

  setSpecs: (specs) =>
    set({ specs, specMap: Object.fromEntries(specs.map((s) => [s.type, s])) }),
  setKbs: (kbs) => set({ kbs }),
  setFlowId: (flowId) => set({ flowId }),
  setFlowName: (flowName) => set({ flowName }),
  setRunning: (running) => set({ running }),
  setSelectedNode: (selectedNodeId) => set({ selectedNodeId }),

  onNodesChange: (changes) =>
    set((st) => ({ rfNodes: applyNodeChanges(changes, st.rfNodes) })),
  onEdgesChange: (changes) =>
    set((st) => ({ rfEdges: applyEdgeChanges(changes, st.rfEdges) })),
  onConnect: (conn) =>
    set((st) => {
      if (!conn.source || !conn.target || !conn.sourceHandle || !conn.targetHandle) return {};
      // 한 입력 포트에는 엣지 하나만 — 기존 연결 교체
      const rest = st.rfEdges.filter(
        (e) => !(e.target === conn.target && e.targetHandle === conn.targetHandle),
      );
      const edge: Edge = {
        id: `${conn.source}.${conn.sourceHandle}->${conn.target}.${conn.targetHandle}`,
        source: conn.source,
        sourceHandle: conn.sourceHandle,
        target: conn.target,
        targetHandle: conn.targetHandle,
      };
      return { rfEdges: [...rest, edge] };
    }),

  addNode: (componentType, position, params) =>
    set((st) => {
      const spec = st.specMap[componentType];
      if (!spec) return {};
      const defaults: Record<string, unknown> = {};
      for (const p of spec.params) defaults[p.name] = p.default ?? (p.kind === "secret" ? "" : p.default);
      const id = `n${Date.now().toString(36)}_${nodeSeq++}`;
      const node: RFNode = {
        id,
        type: "component",
        position,
        data: { componentType, params: { ...defaults, ...(params ?? {}) } },
      };
      return { rfNodes: [...st.rfNodes, node] };
    }),

  updateNodeParam: (nodeId, key, value) =>
    set((st) => ({
      rfNodes: st.rfNodes.map((n) =>
        n.id === nodeId
          ? { ...n, data: { ...n.data, params: { ...n.data.params, [key]: value } } }
          : n,
      ),
    })),

  loadFlow: (id, flow) => {
    const positions = flow.ui?.positions ?? {};
    const nodes: RFNode[] = flow.nodes.map((n, i) => ({
      id: n.id,
      type: "component",
      position: {
        x: positions[n.id]?.[0] ?? 80 + i * 240,
        y: positions[n.id]?.[1] ?? 200,
      },
      data: { componentType: n.type, params: n.params ?? {} },
    }));
    const edges: Edge[] = flow.edges.map((e) => ({
      id: `${e.from[0]}.${e.from[1]}->${e.to[0]}.${e.to[1]}`,
      source: e.from[0],
      sourceHandle: e.from[1],
      target: e.to[0],
      targetHandle: e.to[1],
    }));
    set({
      flowId: id,
      flowName: flow.name,
      rfNodes: nodes,
      rfEdges: edges,
      nodeStatus: {},
      chat: [],
      selectedNodeId: null,
    });
  },

  serializeFlow: () => {
    const st = get();
    const positions: Record<string, [number, number]> = {};
    for (const n of st.rfNodes) positions[n.id] = [Math.round(n.position.x), Math.round(n.position.y)];
    return {
      version: "1",
      name: st.flowName,
      nodes: st.rfNodes.map((n) => ({
        id: n.id,
        type: n.data.componentType,
        params: n.data.params,
      })),
      edges: st.rfEdges.map((e) => ({
        from: [e.source, e.sourceHandle ?? ""] as [string, string],
        to: [e.target, e.targetHandle ?? ""] as [string, string],
      })),
      ui: { positions },
    };
  },

  resetStatus: () => set({ nodeStatus: {} }),

  applyEvent: (ev) =>
    set((st) => {
      if (!ev.node_id) return {};
      const cur = { ...st.nodeStatus };
      if (ev.event === "node_started") cur[ev.node_id] = { state: "running" };
      else if (ev.event === "node_finished")
        cur[ev.node_id] = { state: "ok", durationMs: ev.duration_ms, outputPreview: ev.output_preview };
      else if (ev.event === "node_failed")
        cur[ev.node_id] = {
          state: "failed",
          error: ev.error,
          errorKind: ev.error_kind,
          traceback: ev.traceback,
          inputSnapshot: ev.input_snapshot,
        };
      else if (ev.event === "node_skipped") cur[ev.node_id] = { state: "skipped" };
      return { nodeStatus: cur };
    }),

  pushChat: (msg) => set((st) => ({ chat: [...st.chat, msg] })),
  clearChat: () => set({ chat: [] }),
  log: (line) => set((st) => ({ logLines: [...st.logLines.slice(-199), line] })),
}));

/** 포트 타입별 색 — 연결 가능 여부를 눈으로 판별하게 한다. */
const TYPE_COLORS: Record<string, string> = {
  RawFile: "#f59e0b",
  NormalizedDocument: "#84cc16",
  "list[Chunk]": "#22d3ee",
  Chunk: "#22d3ee",
  "list[RetrievalHit]": "#a78bfa",
  RetrievalHit: "#a78bfa",
  Message: "#60a5fa",
  IngestReport: "#f472b6",
  Any: "#9ca3af",
};

export function typeColor(t: string): string {
  return TYPE_COLORS[t] ?? "#9ca3af";
}

export function typesCompatible(a: string, b: string): boolean {
  return a === b || a === "Any" || b === "Any";
}
