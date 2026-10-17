// @vitest-environment jsdom
import { cleanup, render, screen, act } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BridgeApi } from "../src/renderer/bridge-api";

let bridge: BridgeApi;
let emitMessage: (message: unknown) => void;
let emitState: (state: "connected" | "connecting" | "reconnecting" | "disconnected" | "auth-failed") => void;

beforeEach(() => {
  vi.resetModules();
  const messages = new Set<(message: unknown) => void>();
  const states = new Set<(state: "connected" | "connecting" | "reconnecting" | "disconnected" | "auth-failed") => void>();
  emitMessage = (message) => messages.forEach((listener) => listener(message));
  emitState = (state) => states.forEach((listener) => listener(state));
  bridge = {
    runFakeTask: vi.fn().mockResolvedValue({ ok: true }),
    runFakeTool: vi.fn().mockResolvedValue({ ok: true }),
    listLocalModels: vi.fn().mockResolvedValue({ ok: true, models: ["local-model"] }),
    runRealTask: vi.fn().mockResolvedValue({ ok: true }),
    getServiceInfo: vi.fn().mockResolvedValue({ running: true, port: 1, pid: 1 }),
    onMessage: (listener) => { messages.add(listener); return () => messages.delete(listener); },
    onStateChange: (listener) => { states.add(listener); return () => states.delete(listener); },
  };
  Object.defineProperty(window, "bridge", { value: bridge, configurable: true });
});

afterEach(() => { cleanup(); delete window.bridge; });

describe("real provider controls", () => {
  it("discovers models and submits a real task with the selected model", async () => {
    const { default: App } = await import("../src/renderer/App");
    render(<App />);
    await act(async () => { screen.getByRole("button", { name: /refresh local models/i }).click(); });
    act(() => emitMessage({ type: "event", event: { event_type: "LOCAL_MODELS_LISTED", payload: { models: ["local-model"], status: "available" } } }));
    expect(screen.getByRole("option", { name: "local-model" })).toBeTruthy();
    await act(async () => { screen.getByRole("button", { name: /run real task/i }).click(); });
    expect(bridge.runRealTask).toHaveBeenCalledWith("local-model", expect.any(Array), true);
  });

  it("allows manual model fallback and displays provider errors", async () => {
    vi.mocked(bridge.listLocalModels).mockResolvedValue({ ok: false, error: "local relay unavailable" });
    const { default: App } = await import("../src/renderer/App");
    render(<App />);
    await act(async () => { screen.getByRole("button", { name: /refresh local models/i }).click(); });
    const input = screen.getByLabelText(/manual model/i);
    await act(async () => { input.dispatchEvent(new Event("input", { bubbles: true })); Object.defineProperty(input, "value", { value: "manual-model" }); input.dispatchEvent(new Event("change", { bubbles: true })); });
    expect(screen.getByText(/local relay unavailable/i)).toBeTruthy();
    act(() => emitMessage({ type: "event", event: { event_type: "MODEL_OPERATION_FAILED", payload: { error: { message: "provider failed" } } } }));
    expect(screen.getByText("provider failed")).toBeTruthy();
  });
});
