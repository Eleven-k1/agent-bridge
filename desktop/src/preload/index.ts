import { contextBridge, ipcRenderer } from "electron";

/**
 * preload 暴露给 renderer 的窄 IPC 接口。
 *
 * renderer 只能：
 * - 发起一次假任务（命令的具体构造在 main 进程完成）；
 * - 发起一次无真实副作用的假工具流程；
 * - 订阅本地服务消息（帧/事件/回执）与连接状态。
 *
 * renderer 不能：访问 Python 子进程、临时凭据、TCP 连接、Provider、
 * 数据库、Shell 或项目文件。
 */

contextBridge.exposeInMainWorld("bridge", {
  runFakeTask: (chunks?: string[]): Promise<{ ok: boolean; error?: string }> =>
    ipcRenderer.invoke("bridge:run-fake-task", chunks),
  runFakeTool: (): Promise<{ ok: boolean; error?: string }> =>
    ipcRenderer.invoke("bridge:run-fake-tool"),
  listLocalModels: (): Promise<{ ok: boolean; models?: string[]; error?: string }> =>
    ipcRenderer.invoke("bridge:list-local-models"),
  runRealTask: (model: string, messages: Array<{ role: string; content: string }>, stream: boolean): Promise<{ ok: boolean; error?: string }> =>
    ipcRenderer.invoke("bridge:run-real-task", model, messages, stream),
  getServiceInfo: (): Promise<{ running: boolean; port: number | null; pid: number | null }> =>
    ipcRenderer.invoke("bridge:get-service-info"),
  onMessage: (callback: (message: unknown) => void): (() => void) => {
    const listener = (_event: unknown, message: unknown) => callback(message);
    ipcRenderer.on("bridge:message", listener as never);
    return () => ipcRenderer.removeListener("bridge:message", listener as never);
  },
  onStateChange: (callback: (state: string) => void): (() => void) => {
    const listener = (_event: unknown, state: string) => callback(state);
    ipcRenderer.on("bridge:state", listener as never);
    return () => ipcRenderer.removeListener("bridge:state", listener as never);
  },
});
