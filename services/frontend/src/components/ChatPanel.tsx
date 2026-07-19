import { useState } from "react";
import { runFlow } from "../api";
import { useStore } from "../store";
import { ensureSaved } from "./Toolbar";

export function ChatPanel() {
  const chat = useStore((s) => s.chat);
  const pushChat = useStore((s) => s.pushChat);
  const clearChat = useStore((s) => s.clearChat);
  const running = useStore((s) => s.running);
  const setRunning = useStore((s) => s.setRunning);
  const resetStatus = useStore((s) => s.resetStatus);
  const applyEvent = useStore((s) => s.applyEvent);
  const hasChatIO = useStore(
    (s) =>
      s.rfNodes.some((n) => n.data.componentType === "ChatInput") &&
      s.rfNodes.some((n) => n.data.componentType === "ChatOutput"),
  );
  const [text, setText] = useState("");

  if (!hasChatIO) return null;

  async function send() {
    const q = text.trim();
    if (!q || running) return;
    setText("");
    pushChat({ role: "user", text: q });
    setRunning(true);
    resetStatus();

    // 멀티턴: 이력은 호출자(프론트)가 실어 보낸다 (원칙 3)
    const history = useStore
      .getState()
      .chat.filter((m) => m.role !== "system")
      .map((m) => ({ role: m.role === "user" ? "user" : "assistant", content: m.text }));

    try {
      const id = await ensureSaved();
      let answered = false;
      await runFlow(id, { text: q, history }, (ev) => {
        applyEvent(ev);
        if (ev.event === "run_finished") {
          const st = useStore.getState();
          const outNode = st.rfNodes.find((n) => n.data.componentType === "ChatOutput");
          const out = outNode ? ev.outputs?.[outNode.id] : undefined;
          if (out?.text) {
            pushChat({ role: "assistant", text: out.text });
            answered = true;
          } else if (ev.status !== "ok") {
            pushChat({
              role: "system",
              text: "실행 실패 — 캔버스에서 빨간 노드를 클릭해 원인을 확인하세요.",
            });
            answered = true;
          }
        }
      });
      if (!answered)
        pushChat({ role: "system", text: "답변이 생성되지 않았습니다 (ChatOutput 미도달)." });
    } catch (ex) {
      pushChat({ role: "system", text: `오류: ${(ex as Error).message}` });
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="chat-panel">
      <div className="chat-header">
        <h3>플레이그라운드</h3>
        <button onClick={clearChat}>비우기</button>
      </div>
      <div className="chat-body">
        {chat.length === 0 && <p className="muted">질문을 입력하면 flow가 실행됩니다.</p>}
        {chat.map((m, i) => (
          <div key={i} className={`chat-msg chat-${m.role}`}>
            {m.text}
          </div>
        ))}
        {running && <div className="chat-msg chat-system">실행 중...</div>}
      </div>
      <div className="chat-input">
        <input
          value={text}
          placeholder="질문 입력..."
          disabled={running}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
        />
        <button onClick={send} disabled={running}>
          전송
        </button>
      </div>
    </div>
  );
}
