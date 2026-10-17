import { describe, expect, it } from "vitest";
import { connect } from "node:net";
import { resolve } from "node:path";
import { PythonServiceProcess } from "../src/main/service-process";

const BACKEND_SRC = resolve(__dirname, "../../backend/src");

async function isPortOpen(port: number): Promise<boolean> {
  return new Promise((resolvePromise) => {
    const socket = connect({ host: "127.0.0.1", port });
    socket.setTimeout(1000);
    socket.once("connect", () => {
      socket.destroy();
      resolvePromise(true);
    });
    socket.once("error", () => resolvePromise(false));
    socket.once("timeout", () => {
      socket.destroy();
      resolvePromise(false);
    });
  });
}

describe("PythonServiceProcess", () => {
  it("rejects a missing Python executable and allows a later start", async () => {
    const service = new PythonServiceProcess({
      backendSrcDir: BACKEND_SRC,
      pythonCommand: "missing-python-executable-for-agent-bridge-test",
    });

    await expect(service.start()).rejects.toThrow(/ENOENT/);
    expect(service.describe()).toEqual({ running: false, port: null, pid: null });
    await expect(service.start()).rejects.toThrow(/ENOENT/);
    await service.stop();
  });

  it("启动 demo 并从 stdout 解析监听通知（端口 + 临时凭据）", async () => {
    const service = new PythonServiceProcess({ backendSrcDir: BACKEND_SRC });
    try {
      const notice = await service.start();
      expect(notice.event).toBe("listening");
      expect(Number.isInteger(notice.port)).toBe(true);
      expect(notice.port).toBeGreaterThan(0);
      expect(typeof notice.token).toBe("string");
      expect(notice.token.length).toBeGreaterThanOrEqual(32);
      expect(service.isRunning()).toBe(true);
    } finally {
      await service.stop();
    }
  });

  it("stop 后进程退出、端口释放、可重复调用", async () => {
    const service = new PythonServiceProcess({ backendSrcDir: BACKEND_SRC });
    const notice = await service.start();
    expect(await isPortOpen(notice.port)).toBe(true);

    await service.stop();
    expect(service.isRunning()).toBe(false);

    // 端口在合理时间内释放
    let released = false;
    for (let i = 0; i < 20; i++) {
      released = !(await isPortOpen(notice.port));
      if (released) break;
      await new Promise((r) => setTimeout(r, 200));
    }
    expect(released).toBe(true);

    // 幂等：重复 stop 不抛错
    await service.stop();
  });

  it("凭据不经过 renderer 可见的接口（token 只存在于 main 进程内存）", async () => {
    const service = new PythonServiceProcess({ backendSrcDir: BACKEND_SRC });
    try {
      const notice = await service.start();
      // token 属于 main 进程私有；暴露给 renderer 的接口不应包含它
      expect(service.describe()).toEqual({
        running: true,
        port: notice.port,
        pid: expect.any(Number),
      });
    } finally {
      await service.stop();
    }
  });
});
