import type { ComponentSpec, FlowJson, KB, RunEvent } from "./types";

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  components: () => fetch("/api/components").then((r) => j<ComponentSpec[]>(r)),
  kbs: () => fetch("/api/kb").then((r) => j<KB[]>(r)),
  createKb: (name: string) =>
    fetch("/api/kb", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }).then((r) => j<KB>(r)),
  deleteKb: (kbId: string) =>
    fetch(`/api/kb/${kbId}`, { method: "DELETE" }).then((r) => j(r)),
  flows: () => fetch("/api/flows").then((r) => j<{ id: string; name: string }[]>(r)),
  getFlow: (id: string) =>
    fetch(`/api/flows/${id}`).then((r) => j<{ id: string; name: string; flow: FlowJson }>(r)),
  createFlow: (flow: FlowJson) =>
    fetch("/api/flows", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: flow.name, flow }),
    }).then((r) => j<{ id: string; name: string }>(r)),
  updateFlow: (id: string, flow: FlowJson) =>
    fetch(`/api/flows/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: flow.name, flow }),
    }).then((r) => j<{ id: string; name: string }>(r)),
  bundle: (flowId: string) =>
    fetch(`/api/agents/${flowId}/bundle`, { method: "POST" }).then((r) =>
      j<{ path: string; kb_id: string; files: Record<string, number> }>(r),
    ),
  credentials: () => fetch("/api/credentials").then((r) => j<string[]>(r)),
  createCredential: (name: string, value: string) =>
    fetch("/api/credentials", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, value }),
    }).then((r) => j(r)),
};

/** SSE(POST) 스트림을 읽어 이벤트 콜백으로 넘긴다. */
export async function streamRun(
  url: string,
  body: BodyInit,
  headers: Record<string, string>,
  onEvent: (ev: RunEvent) => void,
): Promise<void> {
  const res = await fetch(url, { method: "POST", headers, body });
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop() ?? "";
    for (const part of parts) {
      const line = part.trim();
      if (line.startsWith("data: ")) {
        try {
          onEvent(JSON.parse(line.slice(6)));
        } catch {
          /* 파싱 불가 이벤트 무시 */
        }
      }
    }
  }
}

export function runFlow(flowId: string, input: Record<string, unknown>, onEvent: (ev: RunEvent) => void) {
  return streamRun(
    `/api/flows/${flowId}/run`,
    JSON.stringify({ input }),
    { "Content-Type": "application/json" },
    onEvent,
  );
}

export function uploadDocument(kbId: string, file: File, onEvent: (ev: RunEvent) => void) {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("kb_id", kbId);
  return streamRun("/api/documents", fd, {}, onEvent);
}
