import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

type Wire = Record<string, any>;
const commands = ["LIST_LOCAL_MODELS", "REAL_MODEL_RUN", "FAKE_MODEL_RUN", "FAKE_TOOL_RUN"] as const;

function App() {
  const socket = useRef<WebSocket | null>(null);
  const [connection, setConnection] = useState("连接中");
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [manual, setManual] = useState("");
  const [stream, setStream] = useState("");
  const [events, setEvents] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const selected = manual.trim() || model;

  useEffect(() => {
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    socket.current = ws;
    ws.onopen = () => setConnection("已连接本机服务");
    ws.onclose = () => { setConnection("本机服务不可用"); setStream(""); };
    ws.onerror = () => setError("无法连接本机 Web 服务");
    ws.onmessage = (event) => {
      let wire: Wire; try { wire = JSON.parse(event.data); } catch { return; }
      if (wire.type === "model_frame") setStream((v) => v + (wire.content_delta || wire.frame?.content_delta || ""));
      if (wire.type === "event") {
        const e = wire.event || {}; const p = e.payload || {};
        if (e.event_type === "LOCAL_MODELS_LISTED") { setModels(Array.isArray(p.models) ? p.models : []); setModel((v) => v || p.models?.[0] || ""); setBusy(false); }
        setEvents((v) => [...v, `${e.event_type || "EVENT"}${p.text ? `: ${p.text}` : ""}`].slice(-100));
      }
      if (wire.type === "command_result") { setBusy(false); if (wire.status === "ROUTER_ERROR" || wire.status === "INVALID_COMMAND") setError((wire.errors || []).join("; ")); }
    };
    return () => ws.close();
  }, []);

  const send = (command_type: typeof commands[number], payload: Wire = {}) => {
    if (!socket.current || socket.current.readyState !== WebSocket.OPEN) { setError("本机服务不可用"); return; }
    setError(""); setBusy(true); socket.current.send(JSON.stringify({ type: "command", command_type, payload }));
  };
  const runReal = () => { if (!selected) { setError("请先选择或输入模型名"); return; } setStream(""); send("REAL_MODEL_RUN", { model: selected, messages: [{ role: "user", content: "请简要介绍你自己。" }], stream: true }); };
  const status = useMemo(() => connection === "已连接本机服务" ? "online" : "offline", [connection]);
  return <main>
    <header><div><p className="eyebrow">LOCAL AGENT BRIDGE</p><h1>本机智能体工作台</h1><p className="sub">只连接本机服务，中转地址固定为 <code>http://127.0.0.1:8080</code></p></div><span className={`status ${status}`}>{connection}</span></header>
    <section className="toolbar">
      <button onClick={() => send("LIST_LOCAL_MODELS")} disabled={busy}>刷新模型</button>
      <select value={model} onChange={e => setModel(e.target.value)} aria-label="模型列表"><option value="">选择已发现模型</option>{models.map(m => <option key={m}>{m}</option>)}</select>
      <input value={manual} onChange={e => setManual(e.target.value)} placeholder="模型发现失败时手动输入模型名" aria-label="手动模型" />
      <button className="primary" onClick={runReal} disabled={busy}>运行真实任务</button>
      <button onClick={() => { setStream(""); send("FAKE_MODEL_RUN", { text_chunks: ["本地 Fake Provider 输出"] }); }} disabled={busy}>Fake 任务</button>
      <button onClick={() => send("FAKE_TOOL_RUN")} disabled={busy}>Fake Tool</button>
    </section>
    {error && <div className="error">{error}</div>}
    <section className="grid"><article><h2>临时流式输出</h2><pre>{stream || "等待模型输出…"}</pre></article><article><h2>正式事件</h2><pre>{events.join("\n") || "等待正式事件…"}</pre></article></section>
  </main>;
}
createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
