import { useEffect } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { api } from "./api";
import { Canvas } from "./components/Canvas";
import { ChatPanel } from "./components/ChatPanel";
import { ParamPanel } from "./components/ParamPanel";
import { Sidebar } from "./components/Sidebar";
import { Toolbar } from "./components/Toolbar";
import { useStore } from "./store";

export default function App() {
  const setSpecs = useStore((s) => s.setSpecs);
  const setKbs = useStore((s) => s.setKbs);
  const logLines = useStore((s) => s.logLines);
  const log = useStore((s) => s.log);

  useEffect(() => {
    api
      .components()
      .then(setSpecs)
      .catch((ex) => log(`백엔드 연결 실패: ${ex.message} — :8000 기동 여부 확인`));
    api.kbs().then(setKbs).catch(() => {});
  }, [setSpecs, setKbs, log]);

  return (
    <div className="app">
      <Toolbar />
      <div className="main">
        <Sidebar />
        <ReactFlowProvider>
          <Canvas />
        </ReactFlowProvider>
        <div className="right-panel">
          <ParamPanel />
          <ChatPanel />
        </div>
      </div>
      <footer className="log-strip">
        {logLines.slice(-3).map((l, i) => (
          <div key={i}>{l}</div>
        ))}
      </footer>
    </div>
  );
}
