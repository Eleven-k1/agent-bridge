import { app, BrowserWindow, ipcMain } from "electron";
import { randomUUID } from "node:crypto";
import { resolve } from "node:path";
import { PythonServiceProcess } from "./service-process";
import { BridgeConnection, type ConnectionState } from "./bridge-connection";

/**
 * Electron main 进程：
 * - 持有 Python 本地服务子进程与临时会话凭据（renderer 永远接触不到凭据）；
 * - 持有到本地服务的 TCP 连接，把帧/事件/回执原样转发给 renderer；
 * - 只提供窄 IPC：运行假任务、订阅消息与连接状态；
 * - 窗口全部关闭时清理子进程与连接，不遗留监听端口。
 */

const DEV_BACKEND_SRC = resolve(app.getAppPath(), "..", "backend", "src");

let mainWindow: BrowserWindow | null = null;
let service: PythonServiceProcess | null = null;
let connection: BridgeConnection | null = null;
let shuttingDown = false;

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1024,
    height: 720,
    title: "多智能体协作桥梁（Fake 闭环）",
    webPreferences: {
      preload: resolve(__dirname, "../preload/index.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
  if (process.env.ELECTRON_RENDERER_URL) {
    void mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL);
  } else {
    void mainWindow.loadFile(resolve(__dirname, "../renderer/index.html"));
  }
}

async function bootstrapBackend(): Promise<void> {
  service = new PythonServiceProcess({
    backendSrcDir: DEV_BACKEND_SRC,
    module: "agent_bridge.app",
  });
  const notice = await service.start();

  connection = new BridgeConnection({
    host: "127.0.0.1",
    port: notice.port,
    token: notice.token,
  });
  connection.onMessage((message) => {
    mainWindow?.webContents.send("bridge:message", message);
  });
  connection.onStateChange((state) => {
    mainWindow?.webContents.send("bridge:state", state);
  });
  await connection.connect();
}

async function shutdown(): Promise<void> {
  if (shuttingDown) return;
  shuttingDown = true;
  connection?.disconnect();
  connection = null;
  await service?.stop();
  service = null;
}

function fakeRunCommand(chunks: string[]): Record<string, unknown> {
  const commandId = randomUUID();
  return {
    protocol_version: "0.5.1",
    scope_type: "TASK",
    scope_id: "task-1",
    task_id: "task-1",
    run_id: "run-1",
    command_id: `cmd-${commandId}`,
    command_idempotency_key: `idem-${commandId}`,
    command_request_digest: `digest-${commandId}`,
    issuer: "local-user",
    command_type: "FAKE_MODEL_RUN",
    payload: { text_chunks: chunks, chunk_delay_s: 0.05 },
    issued_at: new Date().toISOString(),
  };
}

function fakeToolCommand(): Record<string, unknown> {
  const commandId = randomUUID();
  return {
    protocol_version: "0.5.1",
    scope_type: "TASK",
    scope_id: "task-1",
    task_id: "task-1",
    run_id: "run-1",
    command_id: `cmd-${commandId}`,
    command_idempotency_key: `idem-${commandId}`,
    command_request_digest: `digest-${commandId}`,
    issuer: "local-user",
    command_type: "FAKE_TOOL_RUN",
    payload: { target_path: "fake-output.txt", content: "fake content" },
    issued_at: new Date().toISOString(),
  };
}

function localModelsCommand(): Record<string, unknown> {
  const id = randomUUID();
  return { protocol_version: "0.5.1", scope_type: "LOCAL_CONFIG", scope_id: "local-config", command_id: `cmd-${id}`, command_idempotency_key: `idem-${id}`, command_request_digest: `digest-${id}`, issuer: "local-user", command_type: "LIST_LOCAL_MODELS", payload: {}, issued_at: new Date().toISOString() };
}

function realRunCommand(model: string, messages: Array<{ role: string; content: string }>, stream: boolean): Record<string, unknown> {
  const id = randomUUID();
  return { protocol_version: "0.5.1", scope_type: "TASK", scope_id: "task-1", task_id: "task-1", run_id: `run-${id}`, command_id: `cmd-${id}`, command_idempotency_key: `idem-${id}`, command_request_digest: `digest-${id}`, issuer: "local-user", command_type: "REAL_MODEL_RUN", payload: { model, messages, stream }, issued_at: new Date().toISOString() };
}

ipcMain.handle("bridge:run-fake-task", async (_event, chunks: unknown) => {
  if (!connection) {
    return { ok: false, error: "本地服务连接未建立" };
  }
  const safeChunks = Array.isArray(chunks)
    ? (chunks as unknown[]).filter((c): c is string => typeof c === "string")
    : ["这是一段默认的假模型输出。"];
  try {
    await connection.submitCommand(fakeRunCommand(safeChunks));
    return { ok: true };
  } catch (error) {
    return { ok: false, error: String(error) };
  }
});

ipcMain.handle("bridge:run-fake-tool", async () => {
  if (!connection) {
    return { ok: false, error: "本地服务连接未建立" };
  }
  try {
    await connection.submitCommand(fakeToolCommand());
    return { ok: true };
  } catch (error) {
    return { ok: false, error: String(error) };
  }
});

ipcMain.handle("bridge:list-local-models", async () => {
  if (!connection) return { ok: false, error: "本地服务连接未建立" };
  try { await connection.submitCommand(localModelsCommand()); return { ok: true }; } catch (error) { return { ok: false, error: String(error) }; }
});

ipcMain.handle("bridge:run-real-task", async (_event, model: unknown, messages: unknown, stream: unknown) => {
  if (!connection) return { ok: false, error: "本地服务连接未建立" };
  if (typeof model !== "string" || !model.trim()) return { ok: false, error: "请选择模型" };
  const safeMessages = Array.isArray(messages) ? messages.filter((message): message is { role: string; content: string } => typeof message === "object" && message !== null && typeof (message as Record<string, unknown>).role === "string" && typeof (message as Record<string, unknown>).content === "string") : [];
  try { await connection.submitCommand(realRunCommand(model.trim(), safeMessages, stream !== false)); return { ok: true }; } catch (error) { return { ok: false, error: String(error) }; }
});

ipcMain.handle("bridge:get-service-info", () => service?.describe() ?? { running: false, port: null, pid: null });

app.whenReady().then(() => {
  createWindow();
  bootstrapBackend().catch((error) => {
    mainWindow?.webContents.send("bridge:state", "disconnected" satisfies ConnectionState);
    console.error("本地服务启动失败：", error);
  });
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  void shutdown().finally(() => app.quit());
});

app.on("before-quit", (event) => {
  if (!shuttingDown && (service || connection)) {
    event.preventDefault();
    void shutdown().finally(() => app.quit());
  }
});
