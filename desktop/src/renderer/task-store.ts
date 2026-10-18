/**
 * M7 renderer 的任务状态投影（纯逻辑，无 React 依赖，可单测）。
 *
 * 状态分两层（对应 M0 v0.5.1 §6 的帧/事件二分）：
 * - streamedText：来自临时 MODEL_DELTA 帧的 UI 即时显示层；
 *   帧不属于正式事件，断线重连不补发，因此重连即清空。
 * - recordedText / terminal：来自 M5 持久化正式事件的权威层；
 *   最终消息与任务状态只依据这一层恢复，绝不依赖帧。
 *
 * UI 的「完成」只能由正式终态事件驱动（MODEL_OPERATION_COMPLETED /
 * FAILED / CANCELLED），本模块不自行推导完成。
 */

export interface TaskState {
  connection:
    | "connecting"
    | "connected"
    | "reconnecting"
    | "disconnected"
    | "auth-failed";
  streamedText: string;
  recordedText: string;
  terminal: "COMPLETED" | "FAILED" | "CANCELLED" | null;
  lastEventType: string | null;
  lastEventError: string | null;
  receivedFrames: number;
  receivedEvents: number;
  pendingCommand: boolean;
  lastCommandStatus: string | null;
  availableModels: string[];
  modelDiscoveryStatus: string | null;
  manualModel: string;
  selectedModel: string;
  lastProviderError: string | null;
}

const TERMINAL_EVENT_MAP: Record<string, NonNullable<TaskState["terminal"]>> = {
  MODEL_OPERATION_COMPLETED: "COMPLETED",
  MODEL_OPERATION_FAILED: "FAILED",
  MODEL_OPERATION_CANCELLED: "CANCELLED",
};

const initialState: TaskState = {
  connection: "disconnected",
  streamedText: "",
  recordedText: "",
  terminal: null,
  lastEventType: null,
  lastEventError: null,
  receivedFrames: 0,
  receivedEvents: 0,
  pendingCommand: false,
  lastCommandStatus: null,
  availableModels: [],
  modelDiscoveryStatus: null,
  manualModel: "",
  selectedModel: "",
  lastProviderError: null,
};

export interface TaskStore {
  getState(): TaskState;
  subscribe(listener: () => void): () => void;
  beginTask(): void;
  markCommandPending(): void;
  handleCommandFailure(error?: string): void;
  handleConnectionState(state: TaskState["connection"]): void;
  handleMessage(message: unknown): void;
  setManualModel(model: string): void;
  setSelectedModel(model: string): void;
}

export function createTaskStore(): TaskStore {
  let state: TaskState = { ...initialState };
  const listeners = new Set<() => void>();

  function update(partial: Partial<TaskState>): void {
    state = { ...state, ...partial };
    for (const listener of listeners) listener();
  }

  return {
    getState: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    beginTask() {
      update({
        streamedText: "",
        recordedText: "",
        terminal: null,
        lastEventType: null,
        lastEventError: null,
        receivedFrames: 0,
        receivedEvents: 0,
        pendingCommand: true,
        lastCommandStatus: null,
        lastProviderError: null,
      });
    },
    markCommandPending() {
      update({ pendingCommand: true });
    },
    handleCommandFailure(error) {
      const message = error?.trim() || "命令提交失败";
      update({
        pendingCommand: false,
        lastCommandStatus: "FAILED",
        lastProviderError: message,
      });
    },
    setManualModel(model) { update({ manualModel: model }); },
    setSelectedModel(model) { update({ selectedModel: model }); },
    handleConnectionState(connection) {
      if (connection === "reconnecting") {
        // 帧不补发：重连即清空临时显示层；正式层保留，等待补发事件
        update({ connection, streamedText: "" });
        return;
      }
      update({ connection });
    },
    handleMessage(message) {
      if (typeof message !== "object" || message === null) return;
      const wire = message as Record<string, unknown>;
      const type = wire.type;

      if (type === "model_frame") {
        const frame = wire.frame as Record<string, unknown> | undefined;
        if (!frame) return;
        const partial: Partial<TaskState> = {
          receivedFrames: state.receivedFrames + 1,
        };
        if (frame.frame_type === "MODEL_DELTA_FRAME") {
          // 正式 M6 传输把内联文本放在消息顶层；保留旧 demo 的帧内字段兼容性。
          const delta =
            typeof wire.content_delta === "string"
              ? wire.content_delta
              : typeof frame.content_delta === "string"
                ? frame.content_delta
                : "";
          partial.streamedText = state.streamedText + delta;
        }
        update(partial);
        return;
      }

      if (type === "event") {
        const event = wire.event as Record<string, unknown> | undefined;
        if (!event) return;
        const eventType = event.event_type as string | undefined;
        if (!eventType) return;
        const payload = (event.payload as Record<string, unknown> | undefined) ?? {};

        const partial: Partial<TaskState> = {
          receivedEvents: state.receivedEvents + 1,
          lastEventType: eventType,
        };
        if (eventType === "LOCAL_MODELS_LISTED") {
          const models = Array.isArray(payload.models) ? payload.models.filter((model): model is string => typeof model === "string") : [];
          update({ availableModels: models, selectedModel: state.selectedModel || models[0] || "", modelDiscoveryStatus: typeof payload.status === "string" ? payload.status : "available", pendingCommand: false, lastCommandStatus: "ACCEPTED" });
          return;
        }
        if (eventType === "MODEL_CONTENT_CHUNK_RECORDED") {
          partial.recordedText = state.recordedText + (payload.text ?? "");
        }
        const terminal = TERMINAL_EVENT_MAP[eventType];
        if (terminal && state.terminal === null) {
          partial.terminal = terminal;
          if (eventType === "MODEL_OPERATION_FAILED") {
            const error = payload.error as Record<string, unknown> | undefined;
            partial.lastEventError = (error?.message as string) ?? "模型操作失败";
            partial.lastProviderError = partial.lastEventError;
          }
        }
        update(partial);
        return;
      }

      if (type === "command_result") {
        update({
          pendingCommand: false,
          lastCommandStatus: (wire.status as string) ?? null,
        });
        return;
      }
    },
  };
}
