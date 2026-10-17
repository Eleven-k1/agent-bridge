import { useCallback, useEffect, useSyncExternalStore } from "react";
import { createTaskStore, type TaskState } from "./task-store";

/**
 * 最小工作台（Fake 闭环）：
 * - 顶部连接状态条（反映真实传输状态，绝不伪造「完成」）；
 * - 中部两层内容：临时帧流式层（重连即清空）与正式事件权威层
 *   （最终文本与终态只来自 M5 持久化事件）；
 * - 一个「运行假任务」入口（只发命令，状态一律等回执与事件）。
 */

const store = createTaskStore();

const CONNECTION_LABELS: Record<TaskState["connection"], string> = {
  connecting: "正在连接本地服务…",
  connected: "已连接本地服务",
  reconnecting: "连接中断，正在重连（事件将按序补发）",
  disconnected: "未连接本地服务",
  "auth-failed": "本地服务认证失败",
};

const TERMINAL_LABELS: Record<NonNullable<TaskState["terminal"]>, string> = {
  COMPLETED: "模型操作完成（正式事件确认）",
  FAILED: "模型操作失败（正式事件确认）",
  CANCELLED: "模型操作已取消（正式事件确认）",
};

export default function App() {
  const state = useSyncExternalStore(store.subscribe, store.getState);

  useEffect(() => {
    const bridge = window.bridge;
    if (!bridge) return;
    const offMessage = bridge.onMessage((message) => store.handleMessage(message));
    const offState = bridge.onStateChange((next) => store.handleConnectionState(next));
    return () => {
      offMessage();
      offState();
    };
  }, []);

  const runTask = useCallback(async () => {
    store.markCommandPending();
    await window.bridge?.runFakeTask(["正在", "通过", "Fake Provider", "流式输出…"]);
  }, []);

  const runTool = useCallback(async () => {
    store.markCommandPending();
    await window.bridge?.runFakeTool();
  }, []);

  const refreshModels = useCallback(async () => {
    store.markCommandPending();
    const result = await window.bridge?.listLocalModels();
    if (result && !result.ok) store.handleMessage({ type: "event", event: { event_type: "LOCAL_MODELS_LISTED", payload: { models: [], status: "unavailable", error: result.error } } });
  }, []);

  const runRealTask = useCallback(async () => {
    const model = state.manualModel.trim() || state.selectedModel;
    if (!model) return;
    store.markCommandPending();
    await window.bridge?.runRealTask(model, [{ role: "user", content: "请简要介绍你自己。" }], true);
  }, [state.manualModel, state.selectedModel]);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", margin: 0, padding: 16, display: "grid", gap: 12 }}>
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          borderBottom: "1px solid #d0d0d0",
          paddingBottom: 8,
        }}
      >
        <h1 style={{ fontSize: 16, margin: 0 }}>多智能体协作桥梁 · Fake 闭环</h1>
        <span
          style={{
            fontSize: 13,
            color: state.connection === "connected" ? "#0a7d32" : "#a05a00",
          }}
          data-testid="connection-label"
        >
          {CONNECTION_LABELS[state.connection]}
        </span>
      </header>

      <section style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button type="button" onClick={() => void runTask()} disabled={state.pendingCommand}>
          运行假任务
        </button>
        <button type="button" onClick={() => void runTool()} disabled={state.pendingCommand}>
          运行假工具
        </button>
        <button type="button" onClick={() => void refreshModels()} disabled={state.pendingCommand}>
          Refresh local models
        </button>
        <label>
          Manual model
          <input aria-label="Manual model" value={state.manualModel} onChange={(event) => store.setManualModel(event.target.value)} />
        </label>
        <select aria-label="Available models" value={state.selectedModel} onChange={(event) => store.setSelectedModel(event.target.value)}>
          <option value="">Select model</option>
          {state.availableModels.map((model) => <option key={model} value={model}>{model}</option>)}
        </select>
        <button type="button" onClick={() => void runRealTask()} disabled={state.pendingCommand || !(state.manualModel.trim() || state.selectedModel)}>
          Run real task
        </button>
        <span style={{ fontSize: 12, color: "#666" }}>
          {state.pendingCommand ? "命令提交中…" : (state.lastCommandStatus ?? "尚未提交命令")}
        </span>
        {state.terminal && (
          <strong style={{ fontSize: 13 }} data-testid="terminal-label">
            {TERMINAL_LABELS[state.terminal]}
          </strong>
        )}
        {state.lastEventError && (
          <span style={{ fontSize: 12, color: "#b00020" }}>{state.lastEventError}</span>
        )}
        {state.modelDiscoveryStatus === "unavailable" && <span style={{ fontSize: 12, color: "#b00020" }}>local relay unavailable</span>}
      </section>

      <section
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 12,
          minHeight: 240,
        }}
      >
        <div style={{ border: "1px solid #d0d0d0", borderRadius: 6, padding: 10 }}>
          <h2 style={{ fontSize: 13, margin: "0 0 8px", color: "#666" }}>
            临时流式层（仅 UI 即时显示 · 帧不补发）
          </h2>
          <pre
            data-testid="streamed-text"
            style={{ margin: 0, whiteSpace: "pre-wrap", fontFamily: "inherit", minHeight: 120 }}
          >
            {state.streamedText || "（等待模型流式帧…）"}
          </pre>
          <footer style={{ fontSize: 12, color: "#888" }}>已收帧：{state.receivedFrames}</footer>
        </div>

        <div style={{ border: "1px solid #d0d0d0", borderRadius: 6, padding: 10 }}>
          <h2 style={{ fontSize: 13, margin: "0 0 8px", color: "#666" }}>
            正式事件层（持久化事实 · 恢复依据）
          </h2>
          <pre
            data-testid="recorded-text"
            style={{ margin: 0, whiteSpace: "pre-wrap", fontFamily: "inherit", minHeight: 120 }}
          >
            {state.recordedText || "（等待正式事件…）"}
          </pre>
          <footer style={{ fontSize: 12, color: "#888" }}>
            已收事件：{state.receivedEvents}
            {state.lastEventType ? ` · 最近：${state.lastEventType}` : ""}
          </footer>
        </div>
      </section>
    </main>
  );
}
