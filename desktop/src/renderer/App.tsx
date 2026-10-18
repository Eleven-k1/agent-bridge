import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import {
  Activity,
  ArrowUpRight,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  Clock3,
  Command,
  Cpu,
  LayoutDashboard,
  Play,
  Radio,
  RefreshCw,
  Server,
  Settings2,
  Sparkles,
  Wrench,
  Zap,
} from "lucide-react";
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

  const activeModel = state.manualModel.trim() || state.selectedModel || "未选择模型";
  const terminalTone = state.terminal ? TERMINAL_TONES[state.terminal] : null;

  return (
    <main className="app-shell" data-testid="app-shell">
      <section className="main-column" aria-label="任务工作区">
        <header className="command-bar">
          <div className="breadcrumb">
            <span>Workspace</span>
            <ChevronRight size={14} aria-hidden="true" />
            <strong>New run</strong>
          </div>
          <div className={`status-pill status-${state.connection}`} data-testid="connection-label" role="status">
            <span className="status-dot" aria-hidden="true" />
            {CONNECTION_LABELS[state.connection]}
          </div>
        </header>

        <div className="main-scroll">
          <section className="run-heading" aria-labelledby="workspace-title">
            <div className="run-heading-copy">
              <span className="run-number">01</span>
              <div>
                <p className="eyebrow">M7 WORKSPACE / LIVE RUN</p>
                <h1 id="workspace-title">把任务交给本地模型</h1>
                <p className="intro-copy">输入目标，选择模型，实时观察每一层执行结果。</p>
              </div>
            </div>
            <div className="run-mode"><Radio size={14} aria-hidden="true" /> LOCAL ONLY</div>
          </section>

          <section className="task-card" data-testid="task-card" aria-labelledby="task-title">
            <div className="task-card-heading">
              <div>
                <div className="section-tag"><Zap size={13} aria-hidden="true" /> PRIMARY WORKFLOW</div>
                <h2 id="task-title">真实任务</h2>
              </div>
              <span className="task-card-hint">内容将原样发送</span>
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
              rows={6}
              placeholder="描述你希望模型完成的任务，例如：总结当前项目架构并指出下一步风险。"
            />
            <div className="composer-footer">
              <span className="composer-note"><Sparkles size={14} aria-hidden="true" /> Streaming enabled</span>
              <button className="button button-primary" type="button" onClick={() => void runRealTask()} disabled={state.pendingCommand || !(state.manualModel.trim() || state.selectedModel) || !taskPrompt.trim()}>
                Run real task
                <ArrowUpRight size={16} aria-hidden="true" />
              </button>
            </div>
            <div className="feedback-row" aria-live="polite">
              {state.terminal && (
                <strong className={`feedback-item feedback-${terminalTone}`} data-testid="terminal-label">
                  {state.terminal === "COMPLETED" ? <CheckCircle2 size={14} aria-hidden="true" /> : <CircleAlert size={14} aria-hidden="true" />}
                  {TERMINAL_LABELS[state.terminal]}
                </strong>
              )}
            </div>
          </section>

          <section className="results-section" aria-labelledby="results-title">
            <div className="results-heading">
              <div>
                <div className="section-tag"><Activity size={13} aria-hidden="true" /> OBSERVABILITY</div>
                <h2 id="results-title">执行结果</h2>
              </div>
              <span className="results-note">帧即时显示 · 事件可恢复</span>
            </div>
            <div className="result-grid">
              <div className="result-card" data-testid="stream-card">
                <div className="card-heading">
                  <div className="card-title-group"><span className="card-icon stream-icon"><Activity size={15} aria-hidden="true" /></span><div><span className="card-kicker">LIVE STREAM</span><h3>临时流式层</h3></div></div>
                  <span className="count-badge">{state.receivedFrames} 帧</span>
                </div>
                <p className="card-description">仅用于即时显示，重连后不会补发。</p>
                <pre className="result-content" data-testid="streamed-text">{state.streamedText || "（等待模型流式帧…）"}</pre>
                <footer className="result-footer">MODEL_DELTA_FRAME</footer>
              </div>
              <div className="result-card" data-testid="recorded-card">
                <div className="card-heading">
                  <div className="card-title-group"><span className="card-icon event-icon"><CheckCircle2 size={15} aria-hidden="true" /></span><div><span className="card-kicker card-kicker-accent">PERSISTED EVENTS</span><h3>正式事件层</h3></div></div>
                  <span className="count-badge count-badge-accent">{state.receivedEvents} 事件</span>
                </div>
                <p className="card-description">持久化事实，任务恢复与终态以此为准。</p>
                <pre className="result-content" data-testid="recorded-text">{state.recordedText || "（等待正式事件…）"}</pre>
                <footer className="result-footer">{state.lastEventType ? `最近事件 · ${state.lastEventType}` : "等待事件确认"}</footer>
              </div>
            </div>
          </section>
        </div>
      </section>

      <aside className="sidebar" aria-label="Workspace navigation">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true"><Command size={18} /></div>
          <div><h2>Agent Bridge</h2><p>LOCAL WORKBENCH</p></div>
        </div>
        <nav className="sidebar-nav" aria-label="Primary">
          <span className="nav-label">WORKSPACE</span>
          <div className="nav-item nav-item-active"><LayoutDashboard size={15} aria-hidden="true" /><span>工作台</span><span className="nav-shortcut">⌘1</span></div>
        </nav>
        <div className="sidebar-rule" />
        <div className="environment-block">
          <span className="nav-label">ENVIRONMENT</span>
          <div className="environment-row"><span className={`environment-dot status-${state.connection}`} aria-hidden="true" /><Server size={15} aria-hidden="true" /><div><strong>本地桥接</strong><small>127.0.0.1 · NDJSON</small></div></div>
        </div>
        <div className="sidebar-spacer" />
        <div className="sandbox-block">
          <span className="nav-label">DEVELOPER SANDBOX</span>
          <button className="sandbox-button" type="button" onClick={() => void runTask()} disabled={state.pendingCommand}><Play size={14} aria-hidden="true" /><span>运行假任务</span><ArrowUpRight className="sandbox-arrow" size={13} aria-hidden="true" /></button>
          <button className="sandbox-button" type="button" onClick={() => void runTool()} disabled={state.pendingCommand}><Wrench size={14} aria-hidden="true" /><span>运行假工具</span><ArrowUpRight className="sandbox-arrow" size={13} aria-hidden="true" /></button>
        </div>
        <div className="sidebar-footer"><span>AGENT BRIDGE</span><small>M0 · M7 workspace</small></div>
      </aside>

      <aside className="inspector" aria-labelledby="inspector-title">
        <div className="inspector-heading"><div><p className="eyebrow">RUN INSPECTOR</p><h2 id="inspector-title">检查栏</h2></div><Settings2 size={17} aria-hidden="true" /></div>
        <div className="inspector-section">
          <div className="inspector-label"><Cpu size={14} aria-hidden="true" /> ACTIVE MODEL</div>
          <div className="active-model">{activeModel}</div>
          <div className="model-controls">
            <label className="field-group" htmlFor="available-models"><span className="field-label">Available model</span><select id="available-models" className="control" aria-label="Available models" value={state.selectedModel} onChange={(event) => store.setSelectedModel(event.target.value)}><option value="">Select model</option>{state.availableModels.map((model) => <option key={model} value={model}>{model}</option>)}</select></label>
            <label className="field-group" htmlFor="manual-model"><span className="field-label">Manual model</span><input id="manual-model" className="control" aria-label="Manual model" value={state.manualModel} onChange={(event) => store.setManualModel(event.target.value)} placeholder="可选回退名称" /></label>
            <button className="button button-secondary refresh-button" type="button" onClick={() => void refreshModels()} disabled={state.pendingCommand}><RefreshCw size={14} aria-hidden="true" />Refresh local models</button>
          </div>
        </div>
        <div className="inspector-section status-section">
          <div className="inspector-label"><Radio size={14} aria-hidden="true" /> COMMAND STATUS</div>
          <div className="inspector-status-row"><span className={`status-led status-${state.pendingCommand ? "pending" : state.connection}`} aria-hidden="true" /><strong>{state.pendingCommand ? "命令提交中…" : (state.lastCommandStatus ?? "尚未提交命令")}</strong></div>
          {state.terminal && <div className={`inspector-terminal terminal-${terminalTone}`} data-testid="inspector-terminal">{TERMINAL_LABELS[state.terminal]}</div>}
        </div>
        <div className="inspector-section">
          <div className="inspector-label"><Activity size={14} aria-hidden="true" /> SIGNALS</div>
          <div className="signal-grid"><div className="signal-item"><span>Stream frames</span><strong>{state.receivedFrames}</strong></div><div className="signal-item"><span>Recorded events</span><strong>{state.receivedEvents}</strong></div></div>
        </div>
        <div className="inspector-section last-event-section">
          <div className="inspector-label"><Clock3 size={14} aria-hidden="true" /> LAST EVENT</div>
          <div className="last-event">{state.lastEventType ?? "等待事件确认"}</div>
          {state.lastEventError && <div className="inspector-error"><CircleAlert size={14} aria-hidden="true" />{state.lastEventError}</div>}
          {state.modelDiscoveryStatus === "unavailable" && <div className="inspector-error"><CircleAlert size={14} aria-hidden="true" />local relay unavailable</div>}
        </div>
      </aside>
    </main>
  );
}
