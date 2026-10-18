// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, act } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BridgeApi } from "../src/renderer/bridge-api";

let bridge: BridgeApi;

beforeEach(() => {
  vi.resetModules();
  const messages = new Set<(message: unknown) => void>();
  const states = new Set<(state: "connected" | "connecting" | "reconnecting" | "disconnected" | "auth-failed") => void>();
  bridge = {
    runFakeTask: vi.fn().mockResolvedValue({ ok: true }),
    runFakeTool: vi.fn().mockResolvedValue({ ok: true }),
    listLocalModels: vi.fn().mockResolvedValue({ ok: true, models: [] }),
    runRealTask: vi.fn().mockResolvedValue({ ok: true }),
    getServiceInfo: vi.fn().mockResolvedValue({ running: true, port: 1, pid: 1 }),
    onMessage: (listener) => { messages.add(listener); return () => messages.delete(listener); },
    onStateChange: (listener) => { states.add(listener); return () => states.delete(listener); },
  };
  Object.defineProperty(window, "bridge", { value: bridge, configurable: true });
});

afterEach(() => {
  cleanup();
  delete window.bridge;
});

async function renderApp() {
  const { default: App } = await import("../src/renderer/App");
  render(React.createElement(App));
}

function expectCommandFailure(error: string, button: HTMLElement) {
  expect(screen.getByText(error)).toBeTruthy();
  expect(screen.getByText("FAILED")).toBeTruthy();
  expect(button.hasAttribute("disabled")).toBe(false);
}

describe("renderer IPC failure handling", () => {
  it("clears pending state and shows fake task submission errors", async () => {
    vi.mocked(bridge.runFakeTask).mockResolvedValue({ ok: false, error: "fake task unavailable" });
    await renderApp();

    const button = screen.getByRole("button", { name: /假任务/ });
    await act(async () => { button.click(); });
    expectCommandFailure("fake task unavailable", button);
  });

  it("clears pending state and shows fake tool submission errors", async () => {
    vi.mocked(bridge.runFakeTool).mockResolvedValue({ ok: false, error: "fake tool unavailable" });
    await renderApp();

    const button = screen.getByRole("button", { name: /假工具/ });
    await act(async () => { button.click(); });
    expectCommandFailure("fake tool unavailable", button);
  });

  it("clears pending state and shows local model discovery errors", async () => {
    vi.mocked(bridge.listLocalModels).mockResolvedValue({ ok: false, error: "local relay unavailable" });
    await renderApp();

    const button = screen.getByRole("button", { name: /refresh local models/i });
    await act(async () => { button.click(); });
    expectCommandFailure("local relay unavailable", button);
  });

  it("clears pending state and shows real task submission errors", async () => {
    vi.mocked(bridge.runRealTask).mockResolvedValue({ ok: false, error: "provider unavailable" });
    await renderApp();

    fireEvent.change(screen.getByLabelText("Manual model"), { target: { value: "local-model" } });
    fireEvent.change(screen.getByLabelText("Task request"), { target: { value: "run a task" } });
    const button = screen.getByRole("button", { name: /run real task/i });
    await act(async () => { button.click(); });
    expectCommandFailure("provider unavailable", button);
  });
});
