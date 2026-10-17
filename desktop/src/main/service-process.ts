import { spawn, type ChildProcess } from "node:child_process";
import { resolve } from "node:path";

/**
 * M7 main 进程持有的 Python 本地服务子进程管理。
 *
 * 职责：
 * - 启动指定的 Python 模块；桌面正式入口由 main 进程指定为
 *   `agent_bridge.app`，测试仍可显式使用 Fake demo；
 * - 从 stdout 首行解析监听通知（端口 + 临时会话凭据）；凭据只存在于
 *   main 进程内存，不进入 renderer、不落盘；
 * - 关闭时先经 stdin EOF 请求优雅退出，超时后用 Windows 进程树强杀
 *   （taskkill /T /F），保证不遗留失控子进程与监听端口。
 *
 * 本模块不 import electron，可在纯 Node 环境（vitest）中直接测试。
 */

export interface ServiceNotice {
  event: string;
  port: number;
  token: string;
  transport?: string;
}

export interface ServiceDescription {
  running: boolean;
  port: number | null;
  pid: number | null;
}

const START_TIMEOUT_MS = 15_000;
const GRACEFUL_EXIT_TIMEOUT_MS = 3_000;

export class PythonServiceProcess {
  private child: ChildProcess | null = null;
  private notice: ServiceNotice | null = null;
  private stopping = false;

  constructor(
    private readonly options: {
      backendSrcDir: string;
      pythonCommand?: string;
      module?: string;
      env?: NodeJS.ProcessEnv;
    },
  ) {}

  async start(): Promise<ServiceNotice> {
    if (this.child) {
      throw new Error("服务进程已在运行");
    }
    const command = this.options.pythonCommand ?? "python";
    const module = this.options.module ?? "agent_bridge.transport.demo";
    this.stopping = false;

    const child = spawn(command, ["-m", module], {
      cwd: resolve(this.options.backendSrcDir),
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, ...this.options.env, PYTHONIOENCODING: "utf-8" },
      windowsHide: true,
      detached: false,
    });
    this.child = child;

    let stderrText = "";
    child.stderr?.setEncoding("utf-8");
    child.stderr?.on("data", (chunk: string) => {
      stderrText += chunk;
      if (stderrText.length > 4000) stderrText = stderrText.slice(-4000);
    });
    return await new Promise<ServiceNotice>((resolvePromise, rejectPromise) => {
      let settled = false;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        rejectPromise(
          new Error(`等待服务监听通知超时（${START_TIMEOUT_MS}ms）；stderr=${stderrText}`),
        );
        void this.stop();
      }, START_TIMEOUT_MS);

      child.once("error", (error: NodeJS.ErrnoException) => {
        if (settled || this.notice || this.stopping) return;
        settled = true;
        clearTimeout(timer);
        this.child = null;
        const code = error.code ?? "unknown";
        rejectPromise(
          new Error(
            `Python service failed to start (executable=${JSON.stringify(command)}; code=${code}; stderr=${stderrText || "(empty)"})`,
            { cause: error },
          ),
        );
      });

      let buffer = "";
      child.stdout?.setEncoding("utf-8");
      child.stdout?.on("data", (chunk: string) => {
        buffer += chunk;
        const newlineIndex = buffer.indexOf("\n");
        if (newlineIndex === -1) return;
        const line = buffer.slice(0, newlineIndex).trim();
        buffer = buffer.slice(newlineIndex + 1);
        if (!line) return;
        try {
          const notice = JSON.parse(line) as ServiceNotice;
          if (notice.event === "listening" && Number.isInteger(notice.port)) {
            settled = true;
            clearTimeout(timer);
            this.notice = notice;
            resolvePromise(notice);
          }
        } catch {
          // 非 JSON 行（理论上不应出现）：忽略，继续等待
        }
      });

      child.once("exit", (code) => {
        if (!settled && !this.notice && !this.stopping) {
          settled = true;
          clearTimeout(timer);
          rejectPromise(new Error(`服务进程提前退出（code=${code}）；stderr=${stderrText}`));
        }
      });
    });
  }

  isRunning(): boolean {
    return this.child !== null && this.child.exitCode === null;
  }

  /** 暴露给 renderer 的安全描述：不含凭据。 */
  describe(): ServiceDescription {
    return {
      running: this.isRunning(),
      port: this.notice?.port ?? null,
      pid: this.child?.pid ?? null,
    };
  }

  get noticeValue(): ServiceNotice | null {
    return this.notice;
  }

  async stop(): Promise<void> {
    const child = this.child;
    if (!child) return;
    this.stopping = true;

    // 第一层：请求优雅退出（stdin EOF -> demo 服务安全关闭）
    if (child.stdin && !child.stdin.destroyed) {
      child.stdin.end();
    }

    const exited = await waitForExit(child, GRACEFUL_EXIT_TIMEOUT_MS);
    if (!exited && child.pid) {
      // 第二层：Windows 进程树强杀，避免任何子进程残留
      killProcessTree(child.pid);
      await waitForExit(child, 5_000);
    }

    this.child = null;
    this.notice = null;
    this.stopping = false;
  }
}

function waitForExit(child: ChildProcess, timeoutMs: number): Promise<boolean> {
  if (child.exitCode !== null) return Promise.resolve(true);
  return new Promise((resolvePromise) => {
    const timer = setTimeout(() => resolvePromise(false), timeoutMs);
    child.once("exit", () => {
      clearTimeout(timer);
      resolvePromise(true);
    });
  });
}

function killProcessTree(pid: number): void {
  try {
    // Windows 下 /T 终止整棵进程树；其他平台退化为 SIGKILL
    if (process.platform === "win32") {
      spawn("taskkill", ["/PID", String(pid), "/T", "/F"], { windowsHide: true });
    } else {
      process.kill(pid, "SIGKILL");
    }
  } catch {
    // 进程可能已退出；忽略
  }
}
