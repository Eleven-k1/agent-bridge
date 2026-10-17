import { Socket } from "node:net";

/**
 * M7 main 进程持有的本地服务 TCP 客户端（NDJSON 行协议）。
 *
 * 职责：
 * - 连接 127.0.0.1 回环服务并完成临时凭据认证；
 * - 提交命令（CommandEnvelope）、接收临时模型流式帧与正式事件；
 * - 按 (scope_type, scope_id) 记录已收到的正式事件游标；
 * - 断线重连后对每个已知作用域自动发送 resume，补发正式事件（不补发帧）。
 *
 * 本模块不 import electron、不接触凭据存储、不访问项目文件，
 * 可在纯 Node 环境（vitest）中直接测试。
 */

export type WireMessage = Record<string, unknown>;

export type ConnectionState =
  | "connecting"
  | "connected"
  | "reconnecting"
  | "disconnected"
  | "auth-failed";

interface BridgeConfig {
  host: string;
  port: number;
  token: string;
}

const SCOPE_SEQUENCE_FIELDS = [
  "task_event_sequence",
  "workspace_event_sequence",
  "local_config_event_sequence",
] as const;

export class BridgeConnection {
  private config: BridgeConfig;
  private socket: Socket | null = null;
  private lineBuffer = "";
  private messageHandler: ((message: WireMessage) => void) | null = null;
  private stateHandler: ((state: ConnectionState) => void) | null = null;
  private authWaiter: ((ok: boolean) => void) | null = null;
  private hasConnectedOnce = false;

  /** 已确认收到的各作用域最后事件序号（重连 resume 的游标）。 */
  private readonly lastEventSeq = new Map<string, number>();

  constructor(config: Partial<BridgeConfig> & Pick<BridgeConfig, "host">) {
    this.config = { port: 0, token: "", ...config };
  }

  /** 后置注入端口与凭据（服务启动后才知道）。 */
  configure(partial: Partial<BridgeConfig>): void {
    this.config = { ...this.config, ...partial };
  }

  onMessage(handler: (message: WireMessage) => void): void {
    this.messageHandler = handler;
  }

  onStateChange(handler: (state: ConnectionState) => void): void {
    this.stateHandler = handler;
  }

  get connectionState(): ConnectionState {
    return this.socket?.readyState === "open" ? "connected" : "disconnected";
  }

  async connect(): Promise<void> {
    if (this.socket) {
      this.disconnect();
    }
    this.emitState(this.hasConnectedOnce ? "reconnecting" : "connecting");

    const socket = new Socket();
    this.socket = socket;
    this.lineBuffer = "";

    return await new Promise<void>((resolvePromise, rejectPromise) => {
      const failWaiter = (error: Error) => {
        socket.destroy();
        this.socket = null;
        rejectPromise(error);
      };

      socket.setTimeout(8_000);
      socket.once("error", (error) => {
        if (this.authWaiter) {
          const waiter = this.authWaiter;
          this.authWaiter = null;
          waiter(false);
        }
        failWaiter(new Error(`连接本地服务失败：${error.message}`));
      });
      socket.once("timeout", () => failWaiter(new Error("连接本地服务超时")));

      socket.connect(this.config.port, this.config.host, () => {
        this.authWaiter = (ok) => {
          this.authWaiter = null;
          if (!ok) {
            failWaiter(new Error("本地服务认证失败（auth_failed）"));
            return;
          }
          socket.setTimeout(0);
          socket.removeListener("timeout", () => failWaiter(new Error("连接本地服务超时")));
          this.hasConnectedOnce = true;
          this.emitState("connected");
          // 断线重连：对已知作用域补发正式事件（帧不补发）
          if (this.lastEventSeq.size > 0) {
            this.resumeAllScopes();
          }
          resolvePromise();
        };
        this.write({ type: "auth", token: this.config.token });
      });

      socket.on("data", (chunk: Buffer) => {
        this.lineBuffer += chunk.toString("utf-8");
        let newlineIndex = this.lineBuffer.indexOf("\n");
        while (newlineIndex !== -1) {
          const line = this.lineBuffer.slice(0, newlineIndex).trim();
          this.lineBuffer = this.lineBuffer.slice(newlineIndex + 1);
          if (line) {
            this.handleLine(line);
          }
          newlineIndex = this.lineBuffer.indexOf("\n");
        }
      });

      socket.once("close", () => {
        if (this.socket === socket) {
          this.socket = null;
          this.emitState("disconnected");
        }
      });
    });
  }

  async submitCommand(command: Record<string, unknown>): Promise<void> {
    this.write({ type: "command", command });
  }

  /** 手动请求补发某作用域的正式事件（模拟桌面端重启后的全量恢复）。 */
  async resume(scopeType: string, scopeId: string, lastEventSeq: number): Promise<void> {
    this.write({ type: "resume", scope_type: scopeType, scope_id: scopeId, last_event_seq: lastEventSeq });
  }

  disconnect(): void {
    if (this.socket) {
      this.socket.destroy();
      this.socket = null;
    }
    this.lineBuffer = "";
    this.emitState("disconnected");
  }

  /** 读取游标快照（调试与测试用）。 */
  cursorSnapshot(): Record<string, number> {
    return Object.fromEntries(this.lastEventSeq);
  }

  private resumeAllScopes(): void {
    for (const [scopeKey, lastSeq] of this.lastEventSeq) {
      const [scopeType, scopeId] = scopeKey.split(":", 2);
      this.write({ type: "resume", scope_type: scopeType, scope_id: scopeId, last_event_seq: lastSeq });
    }
  }

  private write(message: Record<string, unknown>): void {
    const socket = this.socket;
    if (!socket || socket.readyState !== "open") {
      throw new Error("与本地服务的连接未建立");
    }
    socket.write(`${JSON.stringify(message)}\n`, "utf-8");
  }

  private handleLine(line: string): void {
    let message: WireMessage;
    try {
      message = JSON.parse(line) as WireMessage;
    } catch {
      return; // 传输层容错：跳过无法解析的行
    }

    if (message.type === "auth_ok") {
      this.authWaiter?.(true);
      return;
    }
    if (message.type === "auth_failed") {
      this.authWaiter?.(false);
      return;
    }
    if (message.type === "connection_state") {
      // 服务端连接状态通知：转发给状态处理器（renderer 展示传输状态）
      return;
    }
    if (message.type === "event") {
      this.recordCursor(message.event as Record<string, unknown> | undefined);
    }
    this.messageHandler?.(message);
  }

  private recordCursor(event: Record<string, unknown> | undefined): void {
    if (!event) return;
    const scopeType = event.scope_type;
    const scopeId = event.scope_id;
    if (typeof scopeType !== "string" || typeof scopeId !== "string") return;
    for (const field of SCOPE_SEQUENCE_FIELDS) {
      const seq = event[field];
      if (typeof seq === "number") {
        const key = `${scopeType}:${scopeId}`;
        this.lastEventSeq.set(key, Math.max(this.lastEventSeq.get(key) ?? 0, seq));
        return;
      }
    }
  }

  private emitState(state: ConnectionState): void {
    this.stateHandler?.(state);
  }
}
