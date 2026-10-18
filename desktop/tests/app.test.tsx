// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type AppType from "../src/renderer/App";
import type { BridgeApi } from "../src/renderer/bridge-api";
import type { TaskState } from "../src/renderer/task-store";

type MessageListener = (message: unknown) => void;
type ConnectionState = TaskState["connection"];
type StateListener = (state: ConnectionState) => void;

function installMockBridge(): {
  emitMessage: (message: unknown) => void;
  emitState: (state: ConnectionState) => void;
} {
  const messageListeners = new Set<MessageListener>();
  const stateListeners = new Set<StateListener>();
  const bridge: BridgeApi = {
    runFakeTask: vi.fn().mockResolvedValue({ ok: true }),
    runFakeTool: vi.fn().mockResolvedValue({ ok: true }),
    listLocalModels: vi.fn().mockResolvedValue({ ok: true, models: [] }),
    runRealTask: vi.fn().mockResolvedValue({ ok: true }),
    getServiceInfo: vi.fn().mockResolvedValue({ running: true, port: 1234, pid: 100 }),
    onMessage: (callback) => {
      messageListeners.add(callback);
      return () => messageListeners.delete(callback);
    },
    onStateChange: (callback) => {
      stateListeners.add(callback);
      return () => stateListeners.delete(callback);
    },
  };
  Object.defineProperty(window, "bridge", { value: bridge, configurable: true });
  return {
    emitMessage: (message) => {
      for (const listener of messageListeners) listener(message);
    },
    emitState: (state) => {
      for (const listener of stateListeners) listener(state);
    },
  };
}

beforeEach(() => {
  // App 内部的 store 是模块级单例：重置模块保证测试间状态隔离
  vi.resetModules();
});

afterEach(() => {
  cleanup();
  delete window.bridge;
});

/** 动态加载 App，保证拿到重置后的模块级 store。 */
async function loadApp(): Promise<typeof AppType> {
  const module = await import("../src/renderer/App");
  return module.default;
}

function deltaFrame(text: string) {
  return {
    type: "model_frame",
    frame: { frame_type: "MODEL_DELTA_FRAME", content_delta: text, operation_id: "op-1" },
  };
}

function taskEvent(eventType: string, sequence: number, payload: Record<string, unknown> = {}) {
  return {
    type: "event",
    event: {
      event_id: `evt-${sequence}`,
      event_type: eventType,
      scope_type: "TASK",
      scope_id: "task-1",
      task_event_sequence: sequence,
      payload,
    },
  };
}

describe("App 工作台渲染", () => {
  it("完整投影链路：连接 → 帧流 → 正式事件 → 终态徽标", async () => {
    const { emitMessage, emitState } = installMockBridge();
    const App = await loadApp();

    render(<App />);
    expect(screen.getByTestId("app-shell").classList.contains("app-shell")).toBe(true);
    expect(screen.getByTestId("task-card").classList.contains("task-card")).toBe(true);
    expect(screen.getByTestId("stream-card").classList.contains("result-card")).toBe(true);
    expect(screen.getByTestId("recorded-card").classList.contains("result-card")).toBe(true);
    expect(screen.getByTestId("connection-label").textContent).toContain("未连接本地服务");

    act(() => emitState("connected"));
    expect(screen.getByTestId("connection-label").textContent).toContain("已连接本地服务");

    act(() => emitMessage(deltaFrame("流式")));
    act(() => emitMessage(deltaFrame("内容")));
    expect(screen.getByTestId("streamed-text").textContent).toContain("流式内容");
    expect(screen.getByTestId("recorded-text").textContent).toContain("等待正式事件");

    act(() => emitMessage(taskEvent("MODEL_CONTENT_CHUNK_RECORDED", 3, { text: "正式内容" })));
    expect(screen.getByTestId("recorded-text").textContent).toContain("正式内容");

    act(() => emitMessage(taskEvent("MODEL_OPERATION_COMPLETED", 4, { finish_reason: "stop" })));
    expect(screen.getByTestId("terminal-label").textContent).toContain("模型操作完成");
  });

  it("submitting a second task clears the previous task projection", async () => {
    const { emitMessage } = installMockBridge();
    const App = await loadApp();

    render(<App />);
    fireEvent.change(screen.getByLabelText("Manual model"), { target: { value: "local-model" } });
    fireEvent.change(screen.getByLabelText("Task request"), { target: { value: "first task" } });
    act(() => emitMessage(deltaFrame("old stream")));
    act(() => emitMessage(taskEvent("MODEL_CONTENT_CHUNK_RECORDED", 1, { text: "old recorded" })));
    act(() => emitMessage(taskEvent("MODEL_OPERATION_COMPLETED", 2, {})));
    expect(screen.getByTestId("terminal-label")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Task request"), { target: { value: "second task" } });
    await act(async () => { screen.getByRole("button", { name: /run real task/i }).click(); });

    expect(screen.queryByTestId("terminal-label")).toBeNull();
    expect(screen.getByTestId("streamed-text").textContent).not.toContain("old stream");
    expect(screen.getByTestId("recorded-text").textContent).not.toContain("old recorded");
  });

  it("重连时清空临时流式层，保留正式层（帧不补发）", async () => {
    const { emitMessage, emitState } = installMockBridge();
    const App = await loadApp();

    render(<App />);
    act(() => emitState("connected"));
    act(() => emitMessage(deltaFrame("临时内容")));
    act(() => emitMessage(taskEvent("MODEL_CONTENT_CHUNK_RECORDED", 3, { text: "正式内容" })));

    act(() => emitState("reconnecting"));
    expect(screen.getByTestId("connection-label").textContent).toContain("正在重连");
    expect(screen.getByTestId("streamed-text").textContent).toContain("等待模型流式帧");
    expect(screen.getByTestId("recorded-text").textContent).toContain("正式内容");
  });

  it("运行按钮发起任务并进入命令提交中状态", async () => {
    const { emitMessage } = installMockBridge();
    const App = await loadApp();
    render(<App />);

    const button = screen.getByRole("button", { name: "运行假任务" });
    await act(async () => {
      button.click();
    });
    // bridge.runFakeTask 已被调用（回执未到前显示提交中由 store 驱动）
    expect(window.bridge?.runFakeTask).toHaveBeenCalled();

    act(() => emitMessage({ type: "command_result", command_id: "cmd-1", status: "ACCEPTED" }));
    expect(screen.getByText(/尚未提交命令|ACCEPTED/)).toBeTruthy();
  });

  it("假工具按钮只通过窄 IPC 发起受控工具流程", async () => {
    installMockBridge();
    const App = await loadApp();
    render(<App />);

    await act(async () => {
      screen.getByRole("button", { name: "运行假工具" }).click();
    });
    expect(window.bridge?.runFakeTool).toHaveBeenCalledTimes(1);
  });
});
