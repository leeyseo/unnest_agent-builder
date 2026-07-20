export interface PortSpec {
  name: string;
  type: string;
  display_name: string;
}

export interface ParamSpec {
  name: string;
  kind: "str" | "int" | "float" | "bool" | "enum" | "secret";
  default: unknown;
  display_name: string;
  choices: string[] | null;
  multiline: boolean;
  required: boolean;
  secret: boolean;
}

export interface ComponentSpec {
  type: string;
  category: string;
  display_name: string;
  icon: string;
  description: string;
  inputs: PortSpec[];
  outputs: PortSpec[];
  params: ParamSpec[];
}

export interface ComponentValidationReport {
  component: string;
  category: string;
  ok: boolean;
  errors: string[];
  warnings: string[];
  dynamic: "not_run" | "ok" | "skipped" | "failed";
}

export interface ComponentUploadResult {
  ok: boolean;
  reports: ComponentValidationReport[];
  registered?: string[];
  load_error?: string;
}

export interface KB {
  kb_id: string;
  name: string;
  bolt_uri: string;
  status: string;
  doc_count: number;
  embed_model: string;
  dim: number;
}

export interface FlowNodeJson {
  id: string;
  type: string;
  params: Record<string, unknown>;
}

export interface FlowEdgeJson {
  from: [string, string];
  to: [string, string];
}

export interface FlowJson {
  version: string;
  name: string;
  nodes: FlowNodeJson[];
  edges: FlowEdgeJson[];
  ui: { positions?: Record<string, [number, number]> };
}

export type NodeRunState = "idle" | "running" | "ok" | "failed" | "skipped";

export interface NodeStatus {
  state: NodeRunState;
  error?: string;
  errorKind?: string;
  traceback?: string;
  inputSnapshot?: Record<string, string>;
  durationMs?: number;
  outputPreview?: string;
}

export interface RunEvent {
  run_id?: string;
  node_id?: string;
  event: string;
  ts?: string;
  error?: string;
  error_kind?: string;
  traceback?: string;
  input_snapshot?: Record<string, string>;
  duration_ms?: number;
  output_preview?: string;
  status?: string;
  outputs?: Record<string, any>;
  [key: string]: unknown;
}
