# dbt-orchestrator

**跨 MCP 编排引擎** · 可选组件（不是 MCP）

> ⚠️ 这是**最后一步**的规划，不是起点。它要求 6 个 MCP 全部装好才能用。
> 单装任何一个 MCP 完全不需要它。

## 它解决什么

DESIGN 把 `next_actions` 定义成「跨 MCP 的编排合同」：每个工具在结果里声明下一步该调谁、传什么参数。
编排引擎只是**照着合同跑**，因此不需要为每一对 MCP 写胶水代码。

两种驱动方式：

- **自动追链**：给一个入口工具，引擎沿 `next_actions` 逐层展开（默认不追 `optional`）
- **recipe 配方**：一份显式、确定性的流水线 JSON，适合 CI 与受控变更

```bash
dbt run --seed sql-review.review_sql --sql "SELECT * FROM t WHERE 1=1" --dialect mysql
dbt run --seed sql-review.review_sql --sql "..." --follow-optional
dbt run --recipe examples/safe_change.json
```

## 关联三件套自动透传

`correlation_id` / `instance_id` / `sql_fingerprint` 沿整条链路传递，
所以跨 MCP 的每一步都能在 `dbt-store` 里串起来查。

## 设计约束

- 单步失败**隔离**（标 ❌ 继续跑），不因一个工具报错中断整条链
- 缺必填参数时给干净的 `E_ARGS`，而不是崩溃
- `max_steps` + 签名去重防环（同一 `(mcp, tool, args)` 只跑一次）
- 两个跨 MCP 重名工具（`list_metrics` / `record_observation`）强制显式带 `mcp`

## 何时该用

- 需要「体检 → 修法 → 验证 → 风险评估」一键跑完
- CI 里的固定流水线
- 做多 Agent 编排的上层入口

日常单点使用不需要它，直接用各 MCP 的 CLI 更快。

## 开发

```bash
python tests/test_dbt_orchestrator.py
```
