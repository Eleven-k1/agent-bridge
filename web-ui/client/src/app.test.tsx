import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

class MockWebSocket {
  static OPEN = 1;
  static instances: MockWebSocket[] = [];
  readyState = MockWebSocket.OPEN;
  onopen?: () => void;
  onclose?: () => void;
  onerror?: () => void;
  onmessage?: (event: { data: string }) => void;
  sent: string[] = [];

  constructor(readonly url: string) {
    MockWebSocket.instances.push(this);
  }

  send(data: string) { this.sent.push(data); }
  close() { this.readyState = 3; this.onclose?.(); }
  receive(message: unknown) { this.onmessage?.({ data: JSON.stringify(message) }); }
  connect() { this.onopen?.(); }
  disconnect() { this.readyState = 3; this.onclose?.(); }
}

async function renderWorkbench() {
  vi.resetModules();
  MockWebSocket.instances = [];
  vi.stubGlobal("WebSocket", MockWebSocket as unknown as typeof WebSocket);
  document.body.innerHTML = '<div id="root"></div>';
  const { default: App } = await import("./App");
  const view = render(React.createElement(App));
  const socket = MockWebSocket.instances[0];
  await act(async () => socket.connect());
  return { ...view, socket };
}

beforeEach(() => vi.useRealTimers());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("browser workbench", () => {
  it("sends the user's task text and selected model over the compact command protocol", async () => {
    const { socket } = await renderWorkbench();
    await act(async () => socket.receive({
      type: "event",
      event: { event_type: "LOCAL_MODELS_LISTED", payload: { models: ["local-model"], status: "available" } },
    }));
    fireEvent.change(screen.getByLabelText("Task request"), { target: { value: "Summarize this repository." } });

    await act(async () => fireEvent.click(screen.getByRole("button", { name: /run real task/i })));

    expect(JSON.parse(socket.sent.at(-1)!)).toEqual({
      type: "command",
      command_type: "REAL_MODEL_RUN",
      payload: { model: "local-model", messages: [{ role: "user", content: "Summarize this repository." }], stream: true },
    });
  });

  it("keeps stream frames separate from recorded output and only uses formal terminal events", async () => {
    const { socket } = await renderWorkbench();
    await act(async () => {
      socket.receive({ type: "model_frame", frame: { frame_type: "MODEL_DELTA_FRAME" }, content_delta: "partial" });
      socket.receive({ type: "event", event: { event_type: "MODEL_CONTENT_CHUNK_RECORDED", payload: { text: "saved" }, task_event_sequence: 1, scope_type: "TASK", scope_id: "task-1" } });
      socket.receive({ type: "command_result", status: "ACCEPTED" });
    });

    expect(screen.getByTestId("streamed-text").textContent).toContain("partial");
    expect(screen.getByTestId("recorded-text").textContent).toContain("saved");
    expect(screen.queryByTestId("terminal-label")).toBeNull();

    await act(async () => socket.receive({
      type: "event",
      event: { event_type: "MODEL_OPERATION_COMPLETED", payload: {}, task_event_sequence: 2, scope_type: "TASK", scope_id: "task-1" },
    }));

    expect(screen.getByTestId("terminal-label").textContent).toContain("已完成");
  });

  it("shows command errors and releases the pending state", async () => {
    const { socket } = await renderWorkbench();
    fireEvent.change(screen.getByLabelText("Manual model"), { target: { value: "local-model" } });
    fireEvent.change(screen.getByLabelText("Task request"), { target: { value: "Run this task." } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /run real task/i })));
    await act(async () => socket.receive({ type: "command_result", status: "ROUTER_ERROR", errors: ["provider unavailable"] }));

    expect(screen.getByText("provider unavailable")).toBeTruthy();
    expect(screen.getByText("ROUTER_ERROR")).toBeTruthy();
    expect(screen.getByRole("button", { name: /run real task/i }).hasAttribute("disabled")).toBe(false);
  });

  it("keeps task controls disabled after acceptance until a formal terminal event", async () => {
    const { socket } = await renderWorkbench();
    const runFakeTask = screen.getByRole("button", { name: /运行假任务/ });
    await act(async () => fireEvent.click(runFakeTask));
    await act(async () => socket.receive({ type: "command_result", status: "ACCEPTED" }));

    expect(runFakeTask.hasAttribute("disabled")).toBe(true);

    await act(async () => socket.receive({
      type: "event",
      event: { event_type: "MODEL_OPERATION_COMPLETED", payload: {}, task_event_sequence: 1, scope_type: "TASK", scope_id: "task-1" },
    }));

    expect(runFakeTask.hasAttribute("disabled")).toBe(false);
  });

  it("resumes both event scopes after reconnect and clears only the transient stream", async () => {
    vi.useFakeTimers();
    const { socket } = await renderWorkbench();
    await act(async () => {
      socket.receive({ type: "event", event: { event_type: "MODEL_CONTENT_CHUNK_RECORDED", payload: { text: "saved" }, task_event_sequence: 1, scope_type: "TASK", scope_id: "task-1" } });
      socket.receive({ type: "model_frame", frame: { frame_type: "MODEL_DELTA_FRAME" }, content_delta: "partial" });
      socket.disconnect();
      await vi.advanceTimersByTimeAsync(1000);
    });
    const reconnect = MockWebSocket.instances.at(-1)!;
    await act(async () => reconnect.connect());

    const resumed = reconnect.sent.map((message) => JSON.parse(message));
    expect(resumed).toContainEqual({ type: "resume", scope_type: "TASK", scope_id: "task-1", last_event_seq: 1 });
    expect(resumed).toContainEqual({ type: "resume", scope_type: "LOCAL_CONFIG", scope_id: "local-config", last_event_seq: 0 });
    expect(screen.getByTestId("streamed-text").textContent).not.toContain("partial");
    expect(screen.getByTestId("recorded-text").textContent).toContain("saved");
  });
});
