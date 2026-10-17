import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { createTaskStore, type TaskState } from "./task-store";
import "./App.css";

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

const TERMINAL_TONES: Record<NonNullable<TaskState["terminal"]>, string> = {
  COMPLETED: "success",
  FAILED: "danger",
  CANCELLED: "warning",
};

export default function App() {
  const state = useSyncExternalStore(store.subscribe, store.getState);
  const [taskPrompt, setTaskPrompt] = useState("");

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
    if (!model || !taskPrompt.trim()) return;
    store.markCommandPending();
    await window.bridge?.runRealTask(model, [{ role: "user", content: taskPrompt }], true);
  }, [state.manualModel, state.selectedModel, taskPrompt]);

  return (
    <main className="app-shell" data-testid="app-shell">
      <div className="ambient-glow ambient-glow-one" aria-hidden="true" />
      <div className="ambient-glow ambient-glow-two" aria-hidden="true" />

      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">AB</div>
          <div>
            <p className="eyebrow">LOCAL AGENT WORKBENCH</p>
            <h1>多智能体协作桥梁</h1>
            <p className="brand-subtitle">安全连接本地服务，观察每一次模型执行。</p>
          </div>
        </div>
        <div className={`status-pill status-${state.connection}`} data-testid="connection-label" role="status">
          <span className="status-dot" aria-hidden="true" />
          {CONNECTION_LABELS[state.connection]}
        </div>
      </header>

      <section className="workspace-intro" aria-labelledby="workspace-title">
        <div>
          <p className="eyebrow">M7 WORKSPACE</p>
          <h2 id="workspace-title">把任务交给本地模型</h2>
          <p className="intro-copy">输入目标，选择模型，实时观察临时流帧与正式事件的分层结果。</p>
        </div>
        <div className="intro-meta">
          <span className="meta-label">TRANSPORT</span>
          <strong>127.0.0.1 · NDJSON</strong>
        </div>
      </section>

      <section className="panel task-card" data-testid="task-card" aria-labelledby="task-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">PRIMARY WORKFLOW</p>
            <h2 id="task-title">真实任务</h2>
            <p className="section-copy">任务内容会作为用户消息原样发送给选定的模型。</p>
          </div>
          <span className="scope-chip">LOCAL ONLY</span>
        </div>

        <label className="field-label" htmlFor="task-request">
          <span>Task request</span>
          <span className="field-hint">必填</span>
        </label>
        <textarea
          id="task-request"
          className="task-input"
          aria-label="Task request"
          value={taskPrompt}
          onChange={(event) => setTaskPrompt(event.target.value)}
          rows={5}
          placeholder="描述你希望模型完成的任务，例如：总结当前项目架构并指出下一步风险。"
        />

        <div className="model-controls">
          <label className="field-group" htmlFor="available-models">
            <span className="field-label">Available model</span>
            <select id="available-models" className="control" aria-label="Available models" value={state.selectedModel} onChange={(event) => store.setSelectedModel(event.target.value)}>
              <option value="">Select model</option>
              {state.availableModels.map((model) => <option key={model} value={model}>{model}</option>)}
            </select>
          </label>
          <label className="field-group" htmlFor="manual-model">
            <span className="field-label">Manual model</span>
            <input id="manual-model" className="control" aria-label="Manual model" value={state.manualModel} onChange={(event) => store.setManualModel(event.target.value)} placeholder="可选回退名称" />
          </label>
          <button className="button button-secondary refresh-button" type="button" onClick={() => void refreshModels()} disabled={state.pendingCommand}>
            <span aria-hidden="true">↻</span>
            Refresh local models
          </button>
          <button className="button button-primary" type="button" onClick={() => void runRealTask()} disabled={state.pendingCommand || !(state.manualModel.trim() || state.selectedModel) || !taskPrompt.trim()}>
            Run real task
            <span className="button-arrow" aria-hidden="true">→</span>
          </button>
        </div>

        <div className="feedback-row" aria-live="polite">
          <span className="command-status">
            <span className={`status-dot ${state.pendingCommand ? "status-dot-pending" : ""}`} aria-hidden="true" />
            {state.pendingCommand ? "命令提交中…" : (state.lastCommandStatus ?? "尚未提交命令")}
          </span>
          {state.terminal && (
            <strong className={`feedback-item feedback-${TERMINAL_TONES[state.terminal]}`} data-testid="terminal-label">
              {TERMINAL_LABELS[state.terminal]}
            </strong>
          )}
          {state.lastEventError && <span className="feedback-item feedback-danger">{state.lastEventError}</span>}
          {state.modelDiscoveryStatus === "unavailable" && <span className="feedback-item feedback-danger">local relay unavailable</span>}
        </div>
      </section>

      <section className="demo-actions" aria-labelledby="demo-title">
        <div>
          <p className="eyebrow">DEVELOPER SANDBOX</p>
          <h2 id="demo-title">Fake 闭环</h2>
        </div>
        <div className="demo-buttons">
          <button className="button button-ghost" type="button" onClick={() => void runTask()} disabled={state.pendingCommand}>运行假任务</button>
          <button className="button button-ghost" type="button" onClick={() => void runTool()} disabled={state.pendingCommand}>运行假工具</button>
        </div>
      </section>

      <section className="results-section" aria-labelledby="results-title">
        <div className="results-heading">
          <div>
            <p className="eyebrow">OBSERVABILITY</p>
            <h2 id="results-title">执行结果</h2>
          </div>
          <span className="results-note">帧即时显示 · 事件可恢复</span>
        </div>
        <div className="result-grid">
          <div className="panel result-card" data-testid="stream-card">
            <div className="card-heading">
              <div>
                <span className="card-kicker">LIVE STREAM</span>
                <h3>临时流式层</h3>
              </div>
              <span className="count-badge">{state.receivedFrames} 帧</span>
            </div>
            <p className="card-description">仅用于即时显示，重连后不会补发。</p>
            <pre className="result-content" data-testid="streamed-text">{state.streamedText || "（等待模型流式帧…）"}</pre>
            <footer className="result-footer">MODEL_DELTA_FRAME</footer>
          </div>

          <div className="panel result-card" data-testid="recorded-card">
            <div className="card-heading">
              <div>
                <span className="card-kicker card-kicker-accent">PERSISTED EVENTS</span>
                <h3>正式事件层</h3>
              </div>
              <span className="count-badge count-badge-accent">{state.receivedEvents} 事件</span>
            </div>
            <p className="card-description">持久化事实，任务恢复与终态以此为准。</p>
            <pre className="result-content" data-testid="recorded-text">{state.recordedText || "（等待正式事件…）"}</pre>
            <footer className="result-footer">
              {state.lastEventType ? `最近事件 · ${state.lastEventType}` : "等待事件确认"}
            </footer>
          </div>
        </div>
      </section>
    </main>
  );
}
