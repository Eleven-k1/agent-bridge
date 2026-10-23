import { useCallback, useEffect, useRef, useState } from "react";
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
import "./style.css";

type Wire = Record<string, unknown>;
type Connection = "connecting" | "connected" | "reconnecting" | "disconnected" | "auth-failed";
type Terminal = "COMPLETED" | "FAILED" | "CANCELLED";
type EventRow = { id: string; type: string; detail: string };
type LocalAgent = { provider_id: string; display_name: string; installed: boolean; adapted: boolean; supported_roles: string[]; diagnostic?: string };
type CollaborationFact = { type: string; payload: Wire; runId?: string };
type AgentReportStatus = "completed" | "failed" | "cancelled";
type AgentReport = {
  status: AgentReportStatus;
  title: string;
  analyses: Array<{ providerId: string; summary: string }>;
  plan?: string;
  planApproved: boolean;
  writer?: { providerId?: string; branch?: string; worktree?: string; diff?: string; exitCode?: number };
  reviewer?: { providerId?: string; summary?: string };
  error?: string;
  failedProviderIds?: string[];
  failedProviderErrors: Array<{ providerId: string; error: string }>;
};

const CONNECTION_LABELS: Record<Connection, string> = {
  connecting: "正在连接本地服务…",
  connected: "已连接本地服务",
  reconnecting: "连接中断，正在重连并恢复事件…",
  disconnected: "未连接本地服务",
  "auth-failed": "本地服务认证失败",
};

const TERMINAL_LABELS: Record<Terminal, string> = {
  COMPLETED: "模型操作已完成",
  FAILED: "模型操作失败",
  CANCELLED: "模型操作已取消",
};

const TERMINAL_TONES: Record<Terminal, string> = {
  COMPLETED: "success",
  FAILED: "danger",
  CANCELLED: "warning",
};

const TERMINAL_EVENTS: Record<string, Terminal> = {
  MODEL_OPERATION_COMPLETED: "COMPLETED",
  MODEL_OPERATION_FAILED: "FAILED",
  MODEL_OPERATION_CANCELLED: "CANCELLED",
};

const RESUME_SCOPES = [
  { scope_type: "TASK", scope_id: "task-1", cursorField: "task_event_sequence" },
  { scope_type: "LOCAL_CONFIG", scope_id: "local-config", cursorField: "local_config_event_sequence" },
] as const;

function errorMessage(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "object" && value !== null) {
    const message = (value as Record<string, unknown>).message;
    if (typeof message === "string") return message;
    try { return JSON.stringify(value); } catch { return "未知错误"; }
  }
  return "未知错误";
}

function eventSummary(payload: Wire): string {
  if (typeof payload.text === "string") return payload.text;
  if (payload.error !== undefined) return errorMessage(payload.error);
  if (typeof payload.status === "string") return payload.status;
  if (typeof payload.category === "string") return payload.category;
  return "";
}

function buildAgentReport(facts: CollaborationFact[]): AgentReport | null {
  const latestRun = [...facts].reverse().find((fact) => fact.type === "AGENT_COLLABORATION_STARTED");
  if (!latestRun) return null;
  const terminal = [...facts].reverse().find((fact) => fact.runId === latestRun.runId && [
    "AGENT_COLLABORATION_COMPLETED",
    "AGENT_COLLABORATION_FAILED",
    "AGENT_COLLABORATION_CANCELLED",
  ].includes(fact.type));
  if (!terminal) return null;

  const reportFacts = facts.filter((fact) => fact.runId === latestRun.runId);
  const terminalPayload = terminal.payload;
  const analyses = reportFacts
    .filter((fact) => fact.type === "AGENT_ANALYSIS_COMPLETED")
    .map((fact) => ({
      providerId: typeof fact.payload.provider_id === "string" ? fact.payload.provider_id : "unknown",
      summary: typeof fact.payload.summary === "string" ? fact.payload.summary : "",
    }))
    .filter((analysis) => analysis.summary);
  const planFact = [...reportFacts].reverse().find((fact) => fact.type === "AGENT_PLAN_READY" && typeof fact.payload.plan === "string");
  const writerFact = [...reportFacts].reverse().find((fact) => fact.type === "AGENT_WRITER_COMPLETED");
  const reviewFact = [...reportFacts].reverse().find((fact) => fact.type === "AGENT_REVIEW_COMPLETED");
  const failedProviderErrors = reportFacts
    .filter((fact) => fact.type === "AGENT_PARTICIPANT_STATUS" && fact.payload.status === "failed" && typeof fact.payload.provider_id === "string")
    .map((fact) => ({
      providerId: fact.payload.provider_id as string,
      error: fact.payload.error ? errorMessage(fact.payload.error) : typeof fact.payload.exit_code === "number" ? `退出码 ${fact.payload.exit_code}` : "未提供诊断",
    }));
  const writerPayload = writerFact?.payload ?? terminalPayload;
  const writer = writerFact || typeof writerPayload.diff === "string" || typeof writerPayload.branch === "string"
    ? {
      providerId: typeof writerPayload.provider_id === "string" ? writerPayload.provider_id : undefined,
      branch: typeof writerPayload.branch === "string" ? writerPayload.branch : undefined,
      worktree: typeof writerPayload.worktree_path === "string" ? writerPayload.worktree_path : undefined,
      diff: typeof writerPayload.diff === "string" ? writerPayload.diff : undefined,
      exitCode: typeof writerPayload.exit_code === "number" ? writerPayload.exit_code : undefined,
    }
    : undefined;
  const status: AgentReportStatus = terminal.type === "AGENT_COLLABORATION_COMPLETED"
    ? "completed"
    : terminal.type === "AGENT_COLLABORATION_CANCELLED" ? "cancelled" : "failed";

  return {
    status,
    title: status === "completed" ? "协作任务已完成" : status === "cancelled" ? "协作任务已取消" : "协作任务失败",
    analyses,
    plan: planFact && typeof planFact.payload.plan === "string" ? planFact.payload.plan : undefined,
    planApproved: reportFacts.some((fact) => fact.type === "AGENT_PLAN_APPROVED"),
    writer,
    reviewer: reviewFact ? {
      providerId: typeof reviewFact.payload.provider_id === "string" ? reviewFact.payload.provider_id : undefined,
      summary: typeof reviewFact.payload.summary === "string" ? reviewFact.payload.summary : undefined,
    } : undefined,
    error: terminalPayload.error ? errorMessage(terminalPayload.error) : undefined,
    failedProviderIds: Array.isArray(terminalPayload.failed_provider_ids)
      ? terminalPayload.failed_provider_ids.filter((provider): provider is string => typeof provider === "string")
      : [...new Set(reportFacts
        .filter((fact) => fact.type === "AGENT_PARTICIPANT_STATUS" && fact.payload.status === "failed" && typeof fact.payload.provider_id === "string")
        .map((fact) => fact.payload.provider_id as string))],
    failedProviderErrors,
  };
}

export default function App() {
  const socketRef = useRef<WebSocket | null>(null);
  const agentRunRef = useRef<{ taskId: string; runId: string } | null>(null);
  const cursorsRef = useRef<Record<string, number>>({ TASK: 0, LOCAL_CONFIG: 0 });
  const pendingEventsRef = useRef<Record<string, Map<number, Wire>>>({ TASK: new Map(), LOCAL_CONFIG: new Map() });
  const readyEventsRef = useRef(new WeakSet<Wire>());
  const handleWireMessageRef = useRef<(wire: Wire) => void>(() => undefined);
  const [connection, setConnection] = useState<Connection>("connecting");
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [manualModel, setManualModel] = useState("");
  const [taskPrompt, setTaskPrompt] = useState("");
  const [streamedText, setStreamedText] = useState("");
  const [recordedText, setRecordedText] = useState("");
  const [terminal, setTerminal] = useState<Terminal | null>(null);
  const [lastEventType, setLastEventType] = useState<string | null>(null);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [receivedFrames, setReceivedFrames] = useState(0);
  const [receivedEvents, setReceivedEvents] = useState(0);
  const [pending, setPending] = useState(false);
  const [commandStatus, setCommandStatus] = useState<string | null>(null);
  const [modelDiscoveryStatus, setModelDiscoveryStatus] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [agents, setAgents] = useState<LocalAgent[]>([]);
  const [agentPrompt, setAgentPrompt] = useState("");
  const [analysisProviders, setAnalysisProviders] = useState<string[]>(["codex", "claude-code"]);
  const [coordinatorProvider, setCoordinatorProvider] = useState("codex");
  const [reviewerProvider, setReviewerProvider] = useState("claude-code");
  const [workspaceConsent, setWorkspaceConsent] = useState(false);
  const [agentRun, setAgentRun] = useState<{ taskId: string; runId: string } | null>(null);
  const [agentFacts, setAgentFacts] = useState<CollaborationFact[]>([]);
  const [agentPlan, setAgentPlan] = useState<{ text: string; digest: string } | null>(null);
  const [agentOutputs, setAgentOutputs] = useState<Record<string, { providerId: string; role: string; text: string }>>({});
  const [agentDiff, setAgentDiff] = useState<{ text: string; branch?: string; worktree?: string; truncated?: boolean } | null>(null);
  const [agentBusy, setAgentBusy] = useState(false);
  const selectedModel = manualModel.trim() || model;
  const terminalTone = terminal ? TERMINAL_TONES[terminal] : null;

  const resetRun = useCallback(() => {
    setStreamedText("");
    setRecordedText("");
    setTerminal(null);
    setLastEventType(null);
    setEvents([]);
    setReceivedFrames(0);
    setReceivedEvents(0);
    setError("");
    setCommandStatus(null);
  }, []);

  const sendCommand = useCallback((commandType: string, payload: Wire = {}) => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setPending(false);
      setCommandStatus("FAILED");
      setError("本地服务当前不可用");
      return;
    }
    setError("");
    setPending(true);
    setCommandStatus("SUBMITTING");
    try {
      socket.send(JSON.stringify({ type: "command", command_type: commandType, payload }));
    } catch (sendError) {
      setPending(false);
      setCommandStatus("FAILED");
      setError(errorMessage(sendError));
    }
  }, []);

  const sendResume = useCallback((socket: WebSocket) => {
    for (const scope of RESUME_SCOPES) {
      socket.send(JSON.stringify({
        type: "resume",
        scope_type: scope.scope_type,
        scope_id: scope.scope_id,
        last_event_seq: cursorsRef.current[scope.scope_type],
      }));
    }
  }, []);

  const handleWireMessage = useCallback((wire: Wire) => {
    if (wire.type === "connection_state") {
      const next = wire.state;
      if (next === "connected" || next === "connecting" || next === "reconnecting" || next === "disconnected" || next === "auth-failed") {
        setConnection(next);
      }
      return;
    }

    if (wire.type === "model_frame") {
      const frame = typeof wire.frame === "object" && wire.frame !== null ? wire.frame as Wire : {};
      if (frame.frame_type !== "MODEL_DELTA_FRAME") return;
      const delta = typeof wire.content_delta === "string"
        ? wire.content_delta
        : typeof frame.content_delta === "string" ? frame.content_delta : "";
      setReceivedFrames((count) => count + 1);
      setStreamedText((text) => text + delta);
      return;
    }

    if (wire.type === "agent_frame") {
      const frame = typeof wire.frame === "object" && wire.frame !== null ? wire.frame as Wire : {};
      const payload = typeof wire.payload === "object" && wire.payload !== null ? wire.payload as Wire : {};
      if (frame.frame_type !== "AGENT_OUTPUT_FRAME" || typeof frame.provider_id !== "string" || typeof payload.delta !== "string") return;
      const activeRun = agentRunRef.current;
      if (!activeRun || frame.run_id !== activeRun.runId || frame.task_id !== activeRun.taskId) return;
      const role = typeof frame.role === "string" ? frame.role : "agent";
      const outputId = `${frame.provider_id}:${role}`;
      setAgentOutputs((current) => ({ ...current, [outputId]: { providerId: frame.provider_id as string, role, text: `${current[outputId]?.text ?? ""}${payload.delta as string}`.slice(-12000) } }));
      return;
    }

    if (wire.type === "event") {
      const event = typeof wire.event === "object" && wire.event !== null ? wire.event as Wire : {};
      const type = typeof event.event_type === "string" ? event.event_type : "";
      if (!type) return;
      const payload = typeof event.payload === "object" && event.payload !== null ? event.payload as Wire : {};
      const scopeType = typeof event.scope_type === "string" ? event.scope_type : "";
      const scopeId = typeof event.scope_id === "string" ? event.scope_id : "";
      const scope = RESUME_SCOPES.find((candidate) => candidate.scope_type === scopeType && candidate.scope_id === scopeId);
      if (scope) {
        const sequence = event[scope.cursorField];
        if (typeof sequence === "number") {
          const isReady = readyEventsRef.current.has(event);
          if (!isReady) {
            if (sequence <= cursorsRef.current[scope.scope_type]) return;
            const pendingEvents = pendingEventsRef.current[scope.scope_type];
            pendingEvents.set(sequence, wire);
            let nextSequence = cursorsRef.current[scope.scope_type] + 1;
            while (pendingEvents.has(nextSequence)) {
              const nextWire = pendingEvents.get(nextSequence)!;
              pendingEvents.delete(nextSequence);
              const nextEvent = nextWire.event as Wire;
              readyEventsRef.current.add(nextEvent);
              handleWireMessageRef.current(nextWire);
              nextSequence = cursorsRef.current[scope.scope_type] + 1;
            }
            return;
          }
          readyEventsRef.current.delete(event);
          cursorsRef.current[scope.scope_type] = sequence;
        }
      }

      setReceivedEvents((count) => count + 1);
      setLastEventType(type);
      const detail = eventSummary(payload);
      setEvents((current) => [...current, { id: `${scopeType}:${scopeId}:${String(event[scope?.cursorField ?? ""] ?? current.length)}`, type, detail }].slice(-100));

      if (type === "LOCAL_AGENTS_LISTED") {
        const listed = Array.isArray(payload.agents) ? payload.agents.filter((item): item is LocalAgent => typeof item === "object" && item !== null && typeof (item as Wire).provider_id === "string") : [];
        setAgents(listed);
        return;
      }

      if (type.startsWith("AGENT_") && scopeType === "TASK") {
        setPending(false);
        const runId = typeof event.run_id === "string" ? event.run_id : "";
        const taskId = typeof event.task_id === "string" ? event.task_id : scopeId;
        if (runId) {
          const activeRun = { taskId, runId };
          agentRunRef.current = activeRun;
          setAgentRun(activeRun);
        }
        if (type === "AGENT_COLLABORATION_STARTED") {
          setAgentFacts([{ type, payload, runId: runId || undefined }]);
          setAgentPlan(null);
          setAgentDiff(null);
          setAgentOutputs({});
        } else {
          setAgentFacts((current) => [...current, { type, payload, runId: runId || undefined }]);
        }
        if (type === "AGENT_COLLABORATION_STARTED" || type === "AGENT_PLAN_APPROVED" || type === "AGENT_WRITER_STARTED" || type === "AGENT_REVIEW_COMPLETED") setAgentBusy(type !== "AGENT_REVIEW_COMPLETED");
        if (["AGENT_COLLABORATION_COMPLETED", "AGENT_COLLABORATION_FAILED", "AGENT_COLLABORATION_CANCELLED"].includes(type)) setAgentBusy(false);
        if (type === "AGENT_PLAN_READY" && typeof payload.plan === "string" && typeof payload.approval_digest === "string") setAgentPlan({ text: payload.plan, digest: payload.approval_digest });
        if (type === "AGENT_WRITER_COMPLETED" && typeof payload.diff === "string") {
          setAgentDiff({
            text: payload.diff,
            branch: typeof payload.branch === "string" ? payload.branch : undefined,
            worktree: typeof payload.worktree_path === "string" ? payload.worktree_path : undefined,
            truncated: payload.diff_truncated === true,
          });
        }
        if (["AGENT_COLLABORATION_COMPLETED", "AGENT_COLLABORATION_FAILED", "AGENT_COLLABORATION_CANCELLED"].includes(type) && typeof payload.diff === "string") {
          setAgentDiff({
            text: payload.diff,
            branch: typeof payload.branch === "string" ? payload.branch : undefined,
            worktree: typeof payload.worktree_path === "string" ? payload.worktree_path : undefined,
            truncated: payload.diff_truncated === true,
          });
        }
        return;
      }

      if (type === "LOCAL_MODELS_LISTED") {
        const listed = Array.isArray(payload.models) ? payload.models.filter((item): item is string => typeof item === "string") : [];
        setModels(listed);
        setModel((current) => listed.includes(current) ? current : listed[0] ?? "");
        setModelDiscoveryStatus(typeof payload.status === "string" ? payload.status : "available");
        setPending(false);
        if (payload.status === "unavailable") setError(payload.error ? errorMessage(payload.error) : typeof payload.category === "string" ? payload.category : "本地模型列表不可用");
      }

      if (type === "MODEL_CONTENT_CHUNK_RECORDED" && typeof payload.text === "string") {
        setRecordedText((text) => text + payload.text);
      }

      const terminalState = TERMINAL_EVENTS[type];
      if (terminalState) {
        setPending(false);
        setTerminal(terminalState);
        if (terminalState === "FAILED") setError(payload.error ? errorMessage(payload.error) : "模型操作失败");
      }
      if (type === "TOOL_EXECUTION_COMPLETED") setPending(false);
      if (type === "TOOL_EXECUTION_FAILED") {
        setPending(false);
        setError(payload.error_message ? errorMessage(payload.error_message) : "工具操作失败");
      }
      return;
    }

    if (wire.type === "command_result") {
      const status = typeof wire.status === "string" ? wire.status : "UNKNOWN";
      setCommandStatus(status);
      if (status === "ACCEPTED") return;
      setPending(false);
      if (status === "ROUTER_ERROR" || status === "INVALID_COMMAND" || status === "IDEMPOTENCY_CONFLICT") {
        const errors = Array.isArray(wire.errors) ? wire.errors.filter((item): item is string => typeof item === "string") : [];
        setError(errors.join("; ") || status);
        setAgentBusy(false);
      }
      return;
    }

    if (wire.type === "input_error" || wire.type === "protocol_error") {
      setPending(false);
      setCommandStatus("FAILED");
      setError(typeof wire.error === "string" ? wire.error : "本地服务协议错误");
    }
  }, []);

  handleWireMessageRef.current = handleWireMessage;

  useEffect(() => {
    let disposed = false;
    let retryDelay = 1000;
    let reconnectTimer: number | undefined;

    const connect = () => {
      if (disposed) return;
      setConnection((current) => current === "connecting" ? current : "reconnecting");
      const protocol = location.protocol === "https:" ? "wss" : "ws";
      const socket = new WebSocket(`${protocol}://${location.host}/ws`);
      socketRef.current = socket;

      socket.onopen = () => {
        if (disposed) return;
        retryDelay = 1000;
        setConnection("connecting");
        try { sendResume(socket); }
        catch (sendError) { setError(errorMessage(sendError)); }
        try { socket.send(JSON.stringify({ type: "command", command_type: "LIST_LOCAL_AGENTS", payload: {} })); }
        catch (sendError) { setError(errorMessage(sendError)); }
      };
      socket.onmessage = (message) => {
        if (disposed) return;
        try {
          const wire: unknown = JSON.parse(message.data);
          if (typeof wire === "object" && wire !== null) handleWireMessage(wire as Wire);
        } catch {
          setError("收到无法解析的本地服务消息");
        }
      };
      socket.onerror = () => {
        if (!disposed) setError("无法连接本地桥接服务");
      };
      socket.onclose = () => {
        if (disposed) return;
        setConnection("reconnecting");
        setStreamedText("");
        setAgentOutputs({});
        setPending(false);
        reconnectTimer = window.setTimeout(connect, retryDelay);
        retryDelay = Math.min(retryDelay * 2, 10000);
      };
    };

    connect();
    return () => {
      disposed = true;
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [handleWireMessage, sendResume]);

  const runRealTask = () => {
    if (!selectedModel) {
      setError("请先选择或输入模型名");
      return;
    }
    if (!taskPrompt.trim()) {
      setError("请输入任务内容");
      return;
    }
    resetRun();
    sendCommand("REAL_MODEL_RUN", { model: selectedModel, messages: [{ role: "user", content: taskPrompt }], stream: true });
  };

  const runFakeTask = () => {
    resetRun();
    sendCommand("FAKE_MODEL_RUN", { text_chunks: ["本地 Fake Provider 输出", "用于验证 Web 工作台状态"] });
  };

  const runFakeTool = () => {
    resetRun();
    sendCommand("FAKE_TOOL_RUN");
  };

  const refreshModels = () => {
    sendCommand("LIST_LOCAL_MODELS");
  };

  const refreshAgents = () => sendCommand("LIST_LOCAL_AGENTS");
  const availableFor = (role: string) => agents.filter((agent) => agent.installed && agent.adapted && agent.supported_roles.includes(role));
  const startCollaboration = () => {
    if (!agentPrompt.trim() || !workspaceConsent || analysisProviders.length < 2 || !coordinatorProvider || !reviewerProvider) return;
    setAgentFacts([]);
    setAgentPlan(null);
    setAgentOutputs({});
    setAgentDiff(null);
    setAgentRun(null);
    agentRunRef.current = null;
    setAgentBusy(true);
    sendCommand("START_AGENT_COLLABORATION", {
      prompt: agentPrompt.trim(), analysis_provider_ids: analysisProviders,
      coordinator_provider_id: coordinatorProvider, writer_provider_id: "codex",
      reviewer_provider_id: reviewerProvider, acknowledge_workspace_access: true,
    });
  };
  const approveAgentPlan = () => agentRun && agentPlan && sendCommand("APPROVE_AGENT_PLAN", { run_id: agentRun.runId, approval_digest: agentPlan.digest });
  const cancelCollaboration = () => agentRun && sendCommand("CANCEL_AGENT_COLLABORATION", { run_id: agentRun.runId });
  const planApproved = agentFacts.some((fact) => fact.type === "AGENT_PLAN_APPROVED");
  const agentReport = buildAgentReport(agentFacts);
  const toggleAnalysisProvider = (providerId: string) => setAnalysisProviders((current) => current.includes(providerId) ? current.filter((id) => id !== providerId) : [...current, providerId]);

  const connectionStatus = connection === "connected" ? "online" : connection === "auth-failed" ? "offline" : "waiting";
  const activeModel = selectedModel || "未选择模型";

  return (
    <main className="app-shell" data-testid="app-shell">
      <aside className="sidebar" aria-label="工作区导航">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true"><Command size={18} /></div>
          <div><h2>Agent Bridge</h2><p>LOCAL WORKBENCH</p></div>
        </div>
        <nav className="sidebar-nav" aria-label="主导航">
          <span className="nav-label">WORKSPACE</span>
          <div className="nav-item nav-item-active"><LayoutDashboard size={15} aria-hidden="true" /><span>工作台</span><span className="nav-shortcut">01</span></div>
        </nav>
        <div className="sidebar-rule" />
        <div className="environment-block">
          <span className="nav-label">ENVIRONMENT</span>
          <div className="environment-row"><span className={`environment-dot status-${connection}`} aria-hidden="true" /><Server size={15} aria-hidden="true" /><div><strong>本地桥接</strong><small>127.0.0.1 · NDJSON</small></div></div>
        </div>
        <div className="sidebar-spacer" />
        <div className="sandbox-block">
          <span className="nav-label">DEVELOPER SANDBOX</span>
          <button className="sandbox-button" type="button" onClick={runFakeTask} disabled={pending}><Play size={14} aria-hidden="true" /><span>运行假任务</span><ArrowUpRight className="sandbox-arrow" size={13} aria-hidden="true" /></button>
          <button className="sandbox-button" type="button" onClick={runFakeTool} disabled={pending}><Wrench size={14} aria-hidden="true" /><span>运行假工具</span><ArrowUpRight className="sandbox-arrow" size={13} aria-hidden="true" /></button>
        </div>
        <div className="sidebar-footer"><span>AGENT BRIDGE</span><small>M0 · M7 workspace</small></div>
      </aside>

      <section className="main-column" aria-label="任务工作区">
        <header className="command-bar">
          <div className="breadcrumb"><span>Workspace</span><ChevronRight size={14} aria-hidden="true" /><strong>New run</strong></div>
          <div className={`status-pill status-${connection}`} data-testid="connection-label" role="status"><span className="status-dot" aria-hidden="true" />{CONNECTION_LABELS[connection]}</div>
        </header>

        <div className="main-scroll">
          <section className="run-heading" aria-labelledby="workspace-title">
            <div className="run-heading-copy"><span className="run-number">01</span><div><p className="eyebrow">WEB WORKSPACE / LIVE RUN</p><h1 id="workspace-title">把任务交给本地模型</h1><p className="intro-copy">输入目标，选择模型，实时观察流式输出与正式事件。</p></div></div>
            <div className="run-mode"><Radio size={14} aria-hidden="true" /> LOCAL ONLY</div>
          </section>

          <section className="task-card" aria-labelledby="task-title">
            <div className="task-card-heading"><div><div className="section-tag"><Zap size={13} aria-hidden="true" /> PRIMARY WORKFLOW</div><h2 id="task-title">真实任务</h2></div><span className="task-card-hint">内容将原样发送</span></div>
            <label className="field-label" htmlFor="task-request"><span>Task request</span><span className="field-hint">必填</span></label>
            <textarea id="task-request" className="task-input" aria-label="Task request" value={taskPrompt} onChange={(event) => setTaskPrompt(event.target.value)} rows={5} placeholder="描述你希望模型完成的任务，例如：总结当前项目架构并指出下一步风险。" />
            <div className="composer-footer"><span className="composer-note"><Sparkles size={14} aria-hidden="true" /> Streaming enabled</span><button className="button button-primary" type="button" onClick={runRealTask} disabled={pending || !selectedModel || !taskPrompt.trim()}>Run real task<ArrowUpRight size={16} aria-hidden="true" /></button></div>
            <div className="feedback-row" aria-live="polite">{terminal && <strong className={`feedback-item feedback-${terminalTone}`} data-testid="terminal-label">{terminal === "COMPLETED" ? <CheckCircle2 size={14} aria-hidden="true" /> : <CircleAlert size={14} aria-hidden="true" />}{TERMINAL_LABELS[terminal]}</strong>}</div>
          </section>

          <section className="collaboration-workspace" aria-labelledby="collaboration-title">
            <div className="task-card-heading"><div><div className="section-tag"><Activity size={13} aria-hidden="true" /> LOCAL CLI AGENTS</div><h2 id="collaboration-title">多 Agent 协作</h2></div><button className="button button-secondary" type="button" onClick={refreshAgents}><RefreshCw size={14} aria-hidden="true" />刷新库存</button></div>
            <p className="collab-intro">Agent 由本机已安装并适配的 CLI 启动。先并行只读分析，再由协调 Agent 汇总方案；只有你批准后，Codex 才会在独立 Git worktree 写入，最后交给不同的只读 Agent 审查。</p>
            <div className="provider-list" aria-label="本机 Agent 库存">
              {agents.length === 0 && <p className="empty-agents">正在读取本机 CLI 库存…</p>}
              {agents.map((agent) => {
                const available = agent.installed && agent.adapted;
                return <div className={`provider-row ${available ? "provider-ready" : "provider-unavailable"}`} key={agent.provider_id}>
                  <span className="provider-indicator" aria-hidden="true" /><strong>{agent.display_name}</strong><span className="provider-status">{!agent.installed ? "未安装" : !agent.adapted ? "未适配" : "可用"}</span><small>{agent.diagnostic ?? ""}</small>
                </div>;
              })}
            </div>
            <label className="field-group" htmlFor="collaboration-task"><span className="field-label">协作任务</span><textarea aria-label="Collaboration task" id="collaboration-task" className="task-input" value={agentPrompt} onChange={(event) => setAgentPrompt(event.target.value)} rows={3} placeholder="描述需要多个本地编程 Agent 协作完成的任务" /></label>
            <div className="collab-role-grid">
              <fieldset className="role-fieldset"><legend>并行只读分析（至少选择两个）</legend>{availableFor("analysis").map((agent) => <label className="role-option" key={agent.provider_id}><input type="checkbox" checked={analysisProviders.includes(agent.provider_id)} onChange={() => toggleAnalysisProvider(agent.provider_id)} />{agent.display_name}</label>)}{availableFor("analysis").length < 2 && <small>需要至少两个已安装且已适配的分析 Agent</small>}</fieldset>
              <label className="field-group"><span className="field-label">方案协调 Agent</span><select className="control" aria-label="方案协调 Agent" value={coordinatorProvider} onChange={(event) => setCoordinatorProvider(event.target.value)}>{availableFor("coordinator").filter((agent) => analysisProviders.includes(agent.provider_id)).map((agent) => <option value={agent.provider_id} key={agent.provider_id}>{agent.display_name}</option>)}</select></label>
              <div className="writer-lock"><span className="field-label">唯一写入 Agent</span><strong>Codex CLI</strong><small>批准后在独立 Git worktree 执行</small></div>
              <label className="field-group"><span className="field-label">独立审查 Agent</span><select className="control" aria-label="独立审查 Agent" value={reviewerProvider} onChange={(event) => setReviewerProvider(event.target.value)}>{availableFor("reviewer").filter((agent) => agent.provider_id !== "codex").map((agent) => <option value={agent.provider_id} key={agent.provider_id}>{agent.display_name}</option>)}</select></label>
            </div>
            <label className="consent-row"><input aria-label="Allow selected agents to read project files" type="checkbox" checked={workspaceConsent} onChange={(event) => setWorkspaceConsent(event.target.checked)} /><span><strong>允许所选 Agent 读取项目文件</strong><small>分析和审查阶段使用只读权限。不要把密钥、凭据或不应交给模型的文件放入工作区。</small></span></label>
            <div className="composer-footer"><span className="composer-note">{agentBusy ? "协作正在进行" : agentRun ? "协作阶段已记录，可断线恢复" : "等待启动"}</span><div className="collab-actions">{agentBusy && <button className="button button-secondary" type="button" onClick={cancelCollaboration} disabled={!agentRun}><CircleAlert size={14} />取消协作</button>}<button className="button button-primary" type="button" onClick={startCollaboration} disabled={agentBusy || !agentPrompt.trim() || !workspaceConsent || analysisProviders.length < 2 || availableFor("analysis").filter((agent) => analysisProviders.includes(agent.provider_id)).length < 2 || !availableFor("coordinator").some((agent) => agent.provider_id === coordinatorProvider) || !availableFor("reviewer").some((agent) => agent.provider_id === reviewerProvider)}>启动协作<ArrowUpRight size={16} /></button></div></div>
            {agentPlan && !agentReport && <section className="plan-panel" aria-label="已保存的协作方案"><div className="card-heading"><div><span className="card-kicker">COORDINATOR PLAN</span><h3>{planApproved ? "已批准，Codex 正在执行" : "等待你的批准"}</h3></div><span className="count-badge">Codex 写入者</span></div><pre className="result-content">{agentPlan.text}</pre>{!planApproved && <div className="collab-actions"><button className="button button-secondary" type="button" onClick={cancelCollaboration} disabled={!agentRun}>拒绝方案</button><button className="button button-primary" type="button" onClick={approveAgentPlan} disabled={!agentRun}>批准方案并启动 Codex</button></div>}</section>}
            {agentReport && <section className={`agent-report agent-report-${agentReport.status}`} aria-label="任务完成汇报">
              <div className="agent-report-heading"><div><span className="card-kicker">PERSISTED TASK REPORT</span><h3>{agentReport.title}</h3></div><span className={`report-status report-status-${agentReport.status}`}>{agentReport.status === "completed" ? "已完成" : agentReport.status === "failed" ? "失败" : "已取消"}</span></div>
              {agentReport.error && <p className="report-error"><CircleAlert size={14} aria-hidden="true" />{agentReport.error}</p>}
              {!!agentReport.failedProviderIds?.length && <p className="report-failed-providers">未完成的 Agent：{agentReport.failedProviderIds.join("、")}</p>}
              {!!agentReport.failedProviderErrors.length && <div className="report-provider-errors" aria-label="Agent 失败诊断">{agentReport.failedProviderErrors.map((failure, index) => <p key={`${failure.providerId}-${index}`}><strong>{failure.providerId}</strong><span>{failure.error}</span></p>)}</div>}
              <div className="report-grid">
                <section className="report-section" aria-label="Agent 分析结论"><h4>分析结论 <span>{agentReport.analyses.length} 位 Agent</span></h4>{agentReport.analyses.length ? agentReport.analyses.map((analysis, index) => <article className="report-agent-note" key={`${analysis.providerId}-${index}`}><strong>{agents.find((agent) => agent.provider_id === analysis.providerId)?.display_name ?? analysis.providerId}</strong><p>{analysis.summary}</p></article>) : <p className="report-empty">尚未收到完整分析结论</p>}</section>
                <section className="report-section" aria-label="协调方案"><h4>协调方案 <span>{agentReport.plan ? agentReport.planApproved ? "已批准" : "已生成" : "未生成"}</span></h4>{agentReport.plan ? <pre className="report-plan">{agentReport.plan}</pre> : <p className="report-empty">任务未进入方案阶段</p>}</section>
                <section className="report-section" aria-label="执行结果"><h4>执行结果</h4>{agentReport.writer ? <><p className="report-meta">{agentReport.writer.providerId ?? "Codex"}{typeof agentReport.writer.exitCode === "number" && ` · 退出码 ${agentReport.writer.exitCode}`}</p>{agentReport.writer.branch && <p className="report-meta">分支：{agentReport.writer.branch}</p>}{agentReport.writer.worktree && <p className="report-meta">Worktree：{agentReport.writer.worktree}</p>}</> : <p className="report-empty">写入 Agent 尚未完成执行</p>}</section>
                <section className="report-section" aria-label="独立审查"><h4>独立审查</h4>{agentReport.reviewer?.summary ? <><p className="report-meta">{agentReport.reviewer.providerId ?? "审查 Agent"}</p><p className="report-review-summary">{agentReport.reviewer.summary}</p></> : <p className="report-empty">任务未完成审查</p>}</section>
              </div>
              {agentReport.writer?.diff !== undefined && <details className="agent-diff report-diff" open><summary>查看最终 Git diff{agentDiff?.truncated ? "（已截断）" : ""}</summary><div className="diff-meta">{agentReport.writer.branch && <span>分支：{agentReport.writer.branch}</span>}{agentReport.writer.worktree && <span>Worktree：{agentReport.writer.worktree}</span>}</div><pre>{agentReport.writer.diff || "（没有文件差异）"}</pre></details>}
            </section>}
            <div className="agent-output-grid" aria-label="Agent 临时输出">{Object.entries(agentOutputs).map(([outputId, output]) => <div className="agent-output" key={outputId}><strong>{agents.find((agent) => agent.provider_id === output.providerId)?.display_name ?? output.providerId}<span className="output-role"> · {output.role}</span></strong><pre data-testid={`agent-output-${output.providerId}-${output.role}`}>{output.text}</pre></div>)}</div>
            <div className="agent-facts" aria-label="协作阶段记录">{agentFacts.map((fact, index) => <div className="agent-fact" key={`${fact.type}-${index}`}><span className="event-mark" /><strong>{fact.type}</strong>{typeof fact.payload.provider_id === "string" && <span>{fact.payload.provider_id}</span>}{fact.type === "AGENT_REVIEW_COMPLETED" && typeof fact.payload.summary === "string" && <p>{fact.payload.summary}</p>}{fact.type === "AGENT_WRITER_COMPLETED" && typeof fact.payload.branch === "string" && <p>分支：{fact.payload.branch}</p>}</div>)}</div>
            {agentDiff && !agentReport && <details className="agent-diff" open><summary>查看 Codex Git diff{agentDiff.truncated ? "（已截断）" : ""}</summary><div className="diff-meta">{agentDiff.branch && <span>分支：{agentDiff.branch}</span>}{agentDiff.worktree && <span>Worktree：{agentDiff.worktree}</span>}</div><pre>{agentDiff.text || "（没有文件差异）"}</pre></details>}
          </section>

          <section className="results-section" aria-labelledby="results-title">
            <div className="results-heading"><div><div className="section-tag"><Activity size={13} aria-hidden="true" /> OBSERVABILITY</div><h2 id="results-title">执行结果</h2></div><span className="results-note">帧即时显示 · 正式事件可恢复</span></div>
            <div className="result-grid">
              <div className="result-card" data-testid="stream-card"><div className="card-heading"><div className="card-title-group"><span className="card-icon stream-icon"><Activity size={15} aria-hidden="true" /></span><div><span className="card-kicker">LIVE STREAM</span><h3>临时流式层</h3></div></div><span className="count-badge">{receivedFrames} 帧</span></div><p className="card-description">仅用于即时显示，断线后会清空并重新连接。</p><pre className="result-content" data-testid="streamed-text">{streamedText || "（等待模型流式帧…）"}</pre><footer className="result-footer">MODEL_DELTA_FRAME</footer></div>
              <div className="result-card" data-testid="recorded-card"><div className="card-heading"><div className="card-title-group"><span className="card-icon event-icon"><CheckCircle2 size={15} aria-hidden="true" /></span><div><span className="card-kicker card-kicker-accent">PERSISTED EVENTS</span><h3>正式事件层</h3></div></div><span className="count-badge count-badge-accent">{receivedEvents} 事件</span></div><p className="card-description">持久化事实，重连恢复与任务终态以此为准。</p><pre className="result-content" data-testid="recorded-text">{recordedText || "（等待正式事件…）"}</pre><div className="event-feed" aria-label="最近正式事件">{events.slice(-5).reverse().map((event) => <div className="event-row" key={event.id}><span className="event-mark" aria-hidden="true" /><div><strong>{event.type}</strong>{event.detail && <small>{event.detail}</small>}</div></div>)}</div><footer className="result-footer">{lastEventType ? `最近事件 · ${lastEventType}` : "等待事件确认"}</footer></div>
            </div>
          </section>
        </div>
      </section>

      <aside className="inspector" aria-labelledby="inspector-title">
        <div className="inspector-heading"><div><p className="eyebrow">RUN INSPECTOR</p><h2 id="inspector-title">检查栏</h2></div><Settings2 size={17} aria-hidden="true" /></div>
        <div className="inspector-section"><div className="inspector-label"><Cpu size={14} aria-hidden="true" /> ACTIVE MODEL</div><div className="active-model">{activeModel}</div><div className="model-controls"><label className="field-group" htmlFor="available-models"><span className="field-label">Available model</span><select id="available-models" className="control" aria-label="Available models" value={model} onChange={(event) => setModel(event.target.value)}><option value="">Select model</option>{models.map((item) => <option key={item} value={item}>{item}</option>)}</select></label><label className="field-group" htmlFor="manual-model"><span className="field-label">Manual model</span><input id="manual-model" className="control" aria-label="Manual model" value={manualModel} onChange={(event) => setManualModel(event.target.value)} placeholder="可选回退名称" /></label><button className="button button-secondary refresh-button" type="button" onClick={refreshModels} disabled={pending}><RefreshCw size={14} aria-hidden="true" />Refresh local models</button>{modelDiscoveryStatus === "unavailable" && <span className="field-hint">模型发现不可用，可手动填写模型名。</span>}</div></div>
        <div className="inspector-section status-section"><div className="inspector-label"><Radio size={14} aria-hidden="true" /> COMMAND STATUS</div><div className="inspector-status-row"><span className={`status-led status-${pending ? "pending" : connection}`} aria-hidden="true" /><strong>{pending ? "命令提交中…" : commandStatus ?? "尚未提交命令"}</strong></div>{terminal && <div className={`inspector-terminal terminal-${terminalTone}`} data-testid="inspector-terminal">{TERMINAL_LABELS[terminal]}</div>}</div>
        <div className="inspector-section"><div className="inspector-label"><Activity size={14} aria-hidden="true" /> SIGNALS</div><div className="signal-grid"><div className="signal-item"><span>Stream frames</span><strong>{receivedFrames}</strong></div><div className="signal-item"><span>Recorded events</span><strong>{receivedEvents}</strong></div></div></div>
        <div className="inspector-section last-event-section"><div className="inspector-label"><Clock3 size={14} aria-hidden="true" /> LAST EVENT</div><div className="last-event">{lastEventType ?? "等待事件确认"}</div>{error && <div className="inspector-error" role="alert"><CircleAlert size={14} aria-hidden="true" />{error}</div>}{modelDiscoveryStatus === "unavailable" && !error && <div className="inspector-error"><CircleAlert size={14} aria-hidden="true" />本地模型列表不可用</div>}</div>
        <span className={`connection-caption ${connectionStatus}`}>{connectionStatus === "online" ? "LOCAL BRIDGE ONLINE" : connectionStatus === "offline" ? "LOCAL BRIDGE OFFLINE" : "LOCAL BRIDGE CONNECTING"}</span>
      </aside>
    </main>
  );
}
