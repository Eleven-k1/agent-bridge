import type { TaskState } from "./task-store";

/** preload 暴露的窄 IPC 类型。 */
export interface BridgeApi {
  runFakeTask: (chunks?: string[]) => Promise<{ ok: boolean; error?: string }>;
  runFakeTool: () => Promise<{ ok: boolean; error?: string }>;
  listLocalModels: () => Promise<{ ok: boolean; models?: string[]; error?: string }>;
  runRealTask: (model: string, messages: Array<{ role: string; content: string }>, stream: boolean) => Promise<{ ok: boolean; error?: string }>;
  getServiceInfo: () => Promise<{ running: boolean; port: number | null; pid: number | null }>;
  onMessage: (callback: (message: unknown) => void) => () => void;
  onStateChange: (callback: (state: TaskState["connection"]) => void) => () => void;
}

declare global {
  interface Window {
    bridge?: BridgeApi;
  }
}

export {};
