import { describe, expect, it } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { BridgeConnection } from "../src/main/bridge-connection";
import { PythonServiceProcess } from "../src/main/service-process";

const BACKEND_SRC = resolve(__dirname, "../../backend/src");

function integratedService(): { service: PythonServiceProcess; dataDir: string } {
  const dataDir = mkdtempSync(join(tmpdir(), "agent-bridge-desktop-test-"));
  return {
    service: new PythonServiceProcess({
      backendSrcDir: BACKEND_SRC,
      module: "agent_bridge.app",
      env: { AGENT_BRIDGE_DB_PATH: join(dataDir, "audit.sqlite3") },
    }),
    dataDir,
  };
}

interface WireMessage {
  type: string;
  [key: string]: unknown;
}

function fakeRunCommand(chunks: string[], idempotencyKey: string): Record<string, unknown> {
  return {
    protocol_version: "0.5.1",
    scope_type: "TASK",
    scope_id: "task-1",
    task_id: "task-1",
    run_id: "run-1",
    command_id: `cmd-${idempotencyKey}`,
    command_idempotency_key: idempotencyKey,
    command_request_digest: `digest-${idempotencyKey}`,
    issuer: "local-user",
    command_type: "FAKE_MODEL_RUN",
    payload: { text_chunks: chunks, chunk_delay_s: 0.01 },
    issued_at: new Date().toISOString(),
  };
}

function waitFor(predicate: () => boolean, timeoutMs = 8000, stepMs = 50): Promise<void> {
  return new Promise((resolvePromise, reject) => {
    const started = Date.now();
    const timer = setInterval(() => {
      if (predicate()) {
        clearInterval(timer);
        resolvePromise();
      } else if (Date.now() - started > timeoutMs) {
        clearInterval(timer);
        reject(new Error("waitFor 超时"));
      }
    }, stepMs);
  });
}

describe("BridgeConnection", () => {
  it("认证成功并完成一次 Fake 闭环（帧 + 事件 + 回执）", async () => {
    const { service, dataDir } = integratedService();
    const connection = new BridgeConnection({ host: "127.0.0.1", port: 0, token: "" });
    try {
      const notice = await service.start();
      const messages: WireMessage[] = [];
      connection.onMessage((m) => messages.push(m as WireMessage));
      connection.configure({ port: notice.port, token: notice.token });
      await connection.connect();

      await connection.submitCommand(fakeRunCommand(["连接", "测试"], "cli-1"));
      await waitFor(
        () =>
          messages.some(
            (m) =>
              m.type === "event" &&
              (m.event as Record<string, string>).event_type === "MODEL_OPERATION_COMPLETED",
          ),
      );

      const frames = messages
        .filter((m) => m.type === "model_frame")
        .map((m) => m as WireMessage & { frame: Record<string, unknown> });
      const deltas = frames.filter((f) => f.frame.frame_type === "MODEL_DELTA_FRAME");
      expect(deltas.map((d) => d.content_delta)).toEqual(["连接", "测试"]);
      expect(deltas.every((d) => typeof d.frame.content_delta_ref === "string")).toBe(true);
      expect(deltas.every((d) => !("content_delta" in d.frame))).toBe(true);

      const receipts = messages.filter((m) => m.type === "command_result");
      expect(receipts[0]).toMatchObject({ command_id: "cmd-cli-1", status: "ACCEPTED" });
    } finally {
      connection.disconnect();
      await service.stop();
      rmSync(dataDir, { recursive: true, force: true });
    }
  });

  it("错误凭据连接被拒绝", async () => {
    const { service, dataDir } = integratedService();
    try {
      const notice = await service.start();
      const connection = new BridgeConnection({
        host: "127.0.0.1",
        port: notice.port,
        token: "definitely-wrong-token",
      });
      await expect(connection.connect()).rejects.toThrow(/auth/i);
      connection.disconnect();
    } finally {
      await service.stop();
      rmSync(dataDir, { recursive: true, force: true });
    }
  });

  it("断开后全新连接从 0 游标 resume：依据正式事件恢复，帧不补发", async () => {
    const { service, dataDir } = integratedService();
    const connection = new BridgeConnection({ host: "127.0.0.1", port: 0, token: "" });
    try {
      const notice = await service.start();
      const firstMessages: WireMessage[] = [];
      connection.onMessage((m) => firstMessages.push(m as WireMessage));
      connection.configure({ port: notice.port, token: notice.token });

      await connection.connect();
      await connection.submitCommand(fakeRunCommand(["恢复", "依据", "正式事件"], "cli-2"));
      await waitFor(
        () =>
          firstMessages.some(
            (m) =>
              m.type === "event" &&
              (m.event as Record<string, string>).event_type === "MODEL_OPERATION_COMPLETED",
          ),
      );
      const eventsBefore = firstMessages.filter((m) => m.type === "event").length;
      expect(eventsBefore).toBeGreaterThan(0);
      connection.disconnect();

      // 模拟桌面端重启：全新连接（无游标），从 0 号游标请求补发
      const recovery = new BridgeConnection({
        host: "127.0.0.1",
        port: notice.port,
        token: notice.token,
      });
      const recoveryMessages: WireMessage[] = [];
      recovery.onMessage((m) => recoveryMessages.push(m as WireMessage));
      await recovery.connect();
      await recovery.resume("TASK", "task-1", 0);
      await waitFor(
        () => recoveryMessages.some((m) => m.type === "resume_done"),
      );

      // 补发的全部正式事件与第一次连接收到的一致
      const recoveryEvents = recoveryMessages.filter((m) => m.type === "event");
      expect(recoveryEvents.length).toBe(eventsBefore);
      const firstEvents = firstMessages.filter((m) => m.type === "event");
      expect(
        recoveryEvents.map((m) => (m.event as Record<string, unknown>).event_id),
      ).toEqual(firstEvents.map((m) => (m.event as Record<string, unknown>).event_id));

      // 依据正式事件重建最终文本与终态
      const chunkTexts = recoveryEvents
        .map((m) => (m as { event?: Record<string, unknown> }).event)
        .filter((e) => e?.event_type === "MODEL_CONTENT_CHUNK_RECORDED")
        .map((e) => (e!.payload as Record<string, string>).text);
      expect(chunkTexts).toEqual(["恢复", "依据", "正式事件"]);
      expect(
        recoveryEvents.some(
          (m) =>
            (m.event as Record<string, string>).event_type === "MODEL_OPERATION_COMPLETED",
        ),
      ).toBe(true);

      // 断线重连只补发正式事件：临时帧一个都不补发
      expect(recoveryMessages.filter((m) => m.type === "model_frame")).toEqual([]);
      recovery.disconnect();
    } finally {
      connection.disconnect();
      await service.stop();
      rmSync(dataDir, { recursive: true, force: true });
    }
  });

  it("同一连接断开后重连：自动按游标 resume 并收到确认", async () => {
    const { service, dataDir } = integratedService();
    const connection = new BridgeConnection({ host: "127.0.0.1", port: 0, token: "" });
    try {
      const notice = await service.start();
      const messages: WireMessage[] = [];
      connection.onMessage((m) => messages.push(m as WireMessage));
      connection.configure({ port: notice.port, token: notice.token });

      await connection.connect();
      await connection.submitCommand(fakeRunCommand(["游标"], "cli-3"));
      await waitFor(
        () =>
          messages.some(
            (m) =>
              m.type === "event" &&
              (m.event as Record<string, string>).event_type === "MODEL_OPERATION_COMPLETED",
          ),
      );

      // 断开重连：已记录游标的作用域会自动 resume
      connection.disconnect();
      await connection.connect();
      await waitFor(() =>
        messages.some(
          (m) => m.type === "resume_done" && (m.replayed as number) === 0,
        ),
      );
      // 游标之后没有新事件：重连不产生任何补发事件或帧
      // （单 chunk 任务共 4 个正式事件：PLANNED / STARTED / CHUNK / COMPLETED）
      expect(messages.filter((m) => m.type === "event").length).toBe(4);
    } finally {
      connection.disconnect();
      await service.stop();
      rmSync(dataDir, { recursive: true, force: true });
    }
  });
});
