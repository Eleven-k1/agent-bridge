# 桌面端工作台视觉升级实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Electron renderer 工作台升级为深色专业控制台，同时保持现有任务、模型、事件和连接行为不变。

**Architecture:** 保留 `App.tsx` 中现有的 store 与 bridge 调用，仅重排展示结构并把视觉规则集中到 `App.css`。测试继续通过语义角色、ARIA label 和 `data-testid` 验证行为，不依赖颜色或像素快照。

**Tech Stack:** React 19、TypeScript、Electron Vite、Vitest、Testing Library、原生 CSS。

## Global Constraints

- 不新增运行时依赖或 UI 框架。
- 不修改 `bridge-api.ts`、`task-store.ts`、Python 后端、IPC 参数或公共协议。
- 保留 `Task request`、`Manual model`、`Available models` 的 ARIA label 和已有 `data-testid`。
- 空白任务、未选模型和命令提交中的禁用逻辑必须保持。
- 完成前运行桌面端全套 Vitest、TypeScript 类型检查和 Electron 构建。

---

### Task 1: 建立 renderer 视觉基础样式

**Files:**
- Create: `desktop/src/renderer/App.css`
- Modify: `desktop/src/renderer/App.tsx:1-140`

**Interfaces:**
- Consumes: `TaskState`、现有 bridge 回调和 `taskPrompt` 状态。
- Produces: `.app-shell`、`.topbar`、`.task-card`、`.result-grid`、`.result-card`、`.status-pill` 等只用于展示的 class 名。

- [ ] **Step 1: 写出样式目标对应的渲染断言**

在 `desktop/tests/app.test.tsx` 的首个渲染测试中补充以下断言，确保结构类名存在但不绑定具体颜色：

```tsx
expect(screen.getByTestId("app-shell")).toHaveClass("app-shell");
expect(screen.getByTestId("task-card")).toHaveClass("task-card");
expect(screen.getByTestId("stream-card")).toHaveClass("result-card");
expect(screen.getByTestId("recorded-card")).toHaveClass("result-card");
```

- [ ] **Step 2: 运行聚焦测试确认断言失败**

Run: `npm.cmd test -- tests/app.test.tsx`

Expected: 新增的 class 断言失败，因为当前 `App.tsx` 没有这些测试 ID 和 class。

- [ ] **Step 3: 创建 `App.css` 并实现完整视觉基础**

在 `desktop/src/renderer/App.css` 中定义页面背景、面板、排版、按钮、表单控件、状态徽标、结果卡片、空状态、错误提示和窄屏布局。使用 CSS 变量集中定义深海蓝灰背景、蓝绿色强调色、绿色成功色、琥珀色警告色和红色错误色；所有交互控件提供 `:focus-visible` 和 `:disabled` 样式。

- [ ] **Step 4: 将 `App.tsx` 的展示结构映射到 class**

给根节点、顶部栏、任务卡、演示操作组、状态栏和两个结果面板分别加上稳定 class 与必要 `data-testid`，将原有 inline style 替换为 className。保留所有现有事件回调、disabled 表达式、ARIA label、`streamed-text`、`recorded-text`、`terminal-label` 和连接状态文本。

- [ ] **Step 5: 运行聚焦测试确认通过**

Run: `npm.cmd test -- tests/app.test.tsx tests/real-provider.test.tsx`

Expected: 所有 renderer 行为测试通过，新增结构断言通过。

### Task 2: 调整真实任务信息层级并保持可访问性

**Files:**
- Modify: `desktop/src/renderer/App.tsx:70-190`
- Modify: `desktop/tests/app.test.tsx:1-120`
- Modify: `desktop/tests/real-provider.test.tsx:1-100`

**Interfaces:**
- Consumes: Task 1 的 class 和现有 `runRealTask`、`refreshModels`、`runTask`、`runTool` 回调。
- Produces: 真实任务主卡、演示操作区、连接状态徽标和结果卡片的可访问 DOM。

- [ ] **Step 1: 保留并验证关键可访问查询**

在真实 Provider 测试中继续使用以下查询，不改文本语义：

```tsx
screen.getByLabelText("Task request");
screen.getByLabelText("Manual model");
screen.getByLabelText("Available models");
screen.getByRole("button", { name: /run real task/i });
```

在 App 测试中保留连接状态、流式文本、正式文本和终态徽标断言。

- [ ] **Step 2: 运行测试确认当前行为基线**

Run: `npm.cmd test -- tests/app.test.tsx tests/real-provider.test.tsx`

Expected: Task 1 完成后所有断言通过。

- [ ] **Step 3: 完成信息层级结构**

将任务输入置于 `.task-card` 的第一视觉层，增加明确的标题和说明文案；把模型选择、手动模型输入和真实任务按钮放在同一控制行；把 Fake 操作放入 `.demo-actions`；将命令状态、终态和错误放入 `.feedback-row`，并继续渲染现有状态值。

- [ ] **Step 4: 运行完整 renderer 测试**

Run: `npm.cmd test -- tests/app.test.tsx tests/real-provider.test.tsx`

Expected: App 渲染和真实 Provider 控件测试全部通过。

### Task 3: 完整验证并保存检查点

**Files:**
- Modify: `README.md` only if the final visible workflow wording changes; otherwise leave it unchanged.
- Modify: `docs/current-status.md` only if the validation counts change.

- [ ] **Step 1: 检查差异与临时残留**

Run: `git diff --check; git status --short`

Expected: 只包含本轮 renderer、测试和必要样式文件；不包含 `.test-tmp-*`、调试脚本或临时打印。

- [ ] **Step 2: 运行桌面端全套测试**

Run from `desktop/` with `backend\\.venv\\Scripts` prepended to PATH:

```powershell
$env:Path = (Join-Path (Get-Location).Path '..\\backend\\.venv\\Scripts') + ';' + $env:Path
npm.cmd test
```

Expected: 5 个测试文件、至少 24 项测试全部通过。

- [ ] **Step 3: 运行类型检查和构建**

Run: `npm.cmd run typecheck` and `npm.cmd run build` from `desktop/`.

Expected: 两个命令退出码均为 0。

- [ ] **Step 4: 提交稳定检查点**

```powershell
git add desktop/src/renderer/App.tsx desktop/src/renderer/App.css desktop/tests/app.test.tsx desktop/tests/real-provider.test.tsx
git commit -m "feat(desktop): polish workbench interface"
```

Expected: 新提交只包含 UI 视觉升级相关文件，提交后 `git status --short --branch` 无未提交正式改动。
