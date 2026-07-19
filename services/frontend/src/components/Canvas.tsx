import { useCallback } from "react";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  useReactFlow,
  type Connection,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { typesCompatible, useStore } from "../store";
import { FlowNode } from "./FlowNode";

const nodeTypes = { component: FlowNode };

export function Canvas() {
  const rfNodes = useStore((s) => s.rfNodes);
  const rfEdges = useStore((s) => s.rfEdges);
  const onNodesChange = useStore((s) => s.onNodesChange);
  const onEdgesChange = useStore((s) => s.onEdgesChange);
  const onConnect = useStore((s) => s.onConnect);
  const addNode = useStore((s) => s.addNode);
  const setSelectedNode = useStore((s) => s.setSelectedNode);
  const specMap = useStore((s) => s.specMap);
  const { screenToFlowPosition } = useReactFlow();

  const isValidConnection = useCallback(
    (conn: Connection | Edge) => {
      const { rfNodes } = useStore.getState();
      const src = rfNodes.find((n) => n.id === conn.source);
      const dst = rfNodes.find((n) => n.id === conn.target);
      if (!src || !dst || !conn.sourceHandle || !conn.targetHandle) return false;
      const srcSpec = specMap[src.data.componentType];
      const dstSpec = specMap[dst.data.componentType];
      const outType = srcSpec?.outputs.find((p) => p.name === conn.sourceHandle)?.type;
      const inType = dstSpec?.inputs.find((p) => p.name === conn.targetHandle)?.type;
      if (!outType || !inType) return false;
      return typesCompatible(outType, inType);
    },
    [specMap],
  );

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const raw = e.dataTransfer.getData("application/x-component");
      if (!raw) return;
      const { type, params } = JSON.parse(raw);
      const pos = screenToFlowPosition({ x: e.clientX, y: e.clientY });
      addNode(type, pos, params);
    },
    [addNode, screenToFlowPosition],
  );

  return (
    <div className="canvas" onDrop={onDrop} onDragOver={(e) => e.preventDefault()}>
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        isValidConnection={isValidConnection}
        onNodeClick={(_, node) => setSelectedNode(node.id)}
        onPaneClick={() => setSelectedNode(null)}
        fitView
        colorMode="dark"
        deleteKeyCode={["Delete", "Backspace"]}
      >
        <Background gap={20} />
        <Controls />
        <MiniMap pannable zoomable />
      </ReactFlow>
    </div>
  );
}
