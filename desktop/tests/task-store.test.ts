import { describe, expect, it } from "vitest";
import { createTaskStore } from "../src/renderer/task-store";

function deltaFrame(text: string, sequence: number) {
  return {
    type: "model_frame",
    frame: {
      frame_type: "MODEL_DELTA_FRAME",
      operation_id: "op-1",
      model_fragment_sequence: sequence,
      content_delta: text,
      content_delta_ref: `content:op-1:${sequence}`,
    },
  };
}

function formalDeltaFrame(text: string, sequence: number) {
  return {
    type: "model_frame",
    frame: {
      scope_type: "TASK",
      task_id: "task-1",
      run_id: "run-1",
      operation_id: "op-1",
      model_fragment_sequence: sequence,
      frame_type: "MODEL_DELTA_FRAME",
      content_delta_ref: `content:op-1:${sequence}`,
      received_at: "2026-08-30T00:00:00Z",
    },
    content_delta: text,
  };
}

function endFrame() {
  return {
    type: "model_frame",
    frame: { frame_type: "MODEL_STREAM_END_FRAME", operation_id: "op-1" },
  };
}

function taskEvent(eventType: string, sequence: number, payload: Record<string, unknown> = {}) {
  return {
    type: "event",
    event: {
      event_id: `evt-${sequence}`,
      event_type: eventType,
      task_id: "task-1",
      task_event_sequence: sequence,
      operation_id: "op-1",
      payload,
    },
  };
}

describe("TaskStore 状态投影", () => {
  it("临时帧增量只进入 streamedText（UI 即时显示层）", () => {
    const store = createTaskStore();
    store.handleMessage(deltaFrame("你", 1));
    store.handleMessage(deltaFrame("好", 2));

    expect(store.getState().streamedText).toBe("你好");
    expect(store.getState().recordedText).toBe("");
    expect(store.getState().receivedFrames).toBe(2);
  });

  it("正式 M6 帧从消息顶层读取即时文本，且不要求帧携带扩展字段", () => {
    const store = createTaskStore();
    store.handleMessage(formalDeltaFrame("正", 1));
    store.handleMessage(formalDeltaFrame("式", 2));

    expect(store.getState().streamedText).toBe("正式");
    expect(store.getState().receivedFrames).toBe(2);
  });

  it("正式内容块进入 recordedText（权威层）", () => {
    const store = createTaskStore();
    store.handleMessage(taskEvent("MODEL_CONTENT_CHUNK_RECORDED", 3, { text: "你" }));
    store.handleMessage(taskEvent("MODEL_CONTENT_CHUNK_RECORDED", 4, { text: "好" }));

    expect(store.getState().recordedText).toBe("你好");
    expect(store.getState().streamedText).toBe("");
    expect(store.getState().receivedEvents).toBe(2);
  });

  it("终态只由正式事件设置，不由 END 帧设置", () => {
    const store = createTaskStore();
    store.handleMessage(endFrame());
    expect(store.getState().terminal).toBeNull();

    store.handleMessage(
      taskEvent("MODEL_OPERATION_COMPLETED", 5, { finish_reason: "stop" }),
    );
    expect(store.getState().terminal).toBe("COMPLETED");
    expect(store.getState().lastEventType).toBe("MODEL_OPERATION_COMPLETED");
  });

  it("FAILED 与 CANCELLED 终态映射", () => {
    const store = createTaskStore();
    store.handleMessage(taskEvent("MODEL_OPERATION_FAILED", 3, { error: {} }));
    expect(store.getState().terminal).toBe("FAILED");

    const store2 = createTaskStore();
    store2.handleMessage(taskEvent("MODEL_OPERATION_CANCELLED", 3, {}));
    expect(store2.getState().terminal).toBe("CANCELLED");
  });

  it("command_result 清除命令提交中状态", () => {
    const store = createTaskStore();
    store.markCommandPending();
    expect(store.getState().pendingCommand).toBe(true);

    store.handleMessage({ type: "command_result", command_id: "cmd-1", status: "ACCEPTED" });
    expect(store.getState().pendingCommand).toBe(false);
  });

  it("重连：临时帧文本清空（帧不补发），正式层保留并增量追加", () => {
    const store = createTaskStore();
    store.handleConnectionState("connected");
    store.handleMessage(deltaFrame("临时", 1));
    store.handleMessage(taskEvent("MODEL_CONTENT_CHUNK_RECORDED", 3, { text: "正" }));

    store.handleConnectionState("reconnecting");
    expect(store.getState().connection).toBe("reconnecting");
    // 重连即清空临时显示层：帧不补发，UI 不依赖帧恢复
    expect(store.getState().streamedText).toBe("");
    // 正式层保留：resume 按游标增量补发（seq > 3）
    expect(store.getState().recordedText).toBe("正");

    store.handleConnectionState("connected");
    store.handleMessage(taskEvent("MODEL_CONTENT_CHUNK_RECORDED", 4, { text: "式" }));
    expect(store.getState().recordedText).toBe("正式");
    expect(store.getState().streamedText).toBe("");
  });

  it("全新 store 依据从头补发的正式事件重建文本与终态（模拟桌面端重启后恢复）", () => {
    const store = createTaskStore();
    store.handleConnectionState("connected");
    // 桌面端重启后 resume(0)：从第一条正式事件开始重放
    store.handleMessage(taskEvent("MODEL_OPERATION_PLANNED", 1, {}));
    store.handleMessage(taskEvent("MODEL_OPERATION_STARTED", 2, {}));
    store.handleMessage(taskEvent("MODEL_CONTENT_CHUNK_RECORDED", 3, { text: "重建文本" }));
    store.handleMessage(
      taskEvent("MODEL_OPERATION_COMPLETED", 4, { finish_reason: "stop" }),
    );

    const state = store.getState();
    expect(state.recordedText).toBe("重建文本");
    expect(state.terminal).toBe("COMPLETED");
    // 最终消息与任务状态来自正式事件，与临时帧无关
    expect(state.streamedText).toBe("");
    expect(state.receivedFrames).toBe(0);
  });

  it("终态后不回退：后续事件不覆盖已确认终态", () => {
    const store = createTaskStore();
    store.handleMessage(taskEvent("MODEL_OPERATION_COMPLETED", 5, {}));
    store.handleMessage(taskEvent("MODEL_OPERATION_CANCELLED", 6, {}));
    expect(store.getState().terminal).toBe("COMPLETED");
  });

  it("starts a new task with a clean task projection", () => {
    const store = createTaskStore();
    store.handleMessage(deltaFrame("old stream", 1));
    store.handleMessage(taskEvent("MODEL_CONTENT_CHUNK_RECORDED", 2, { text: "old recorded" }));
    store.handleMessage(taskEvent("MODEL_OPERATION_COMPLETED", 3, {}));

    store.beginTask();

    const state = store.getState();
    expect(state.streamedText).toBe("");
    expect(state.recordedText).toBe("");
    expect(state.terminal).toBeNull();
    expect(state.lastEventType).toBeNull();
    expect(state.lastEventError).toBeNull();
    expect(state.pendingCommand).toBe(true);
    expect(state.lastCommandStatus).toBeNull();
  });
});
