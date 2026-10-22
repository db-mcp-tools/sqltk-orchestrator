# sqltk-orchestrator

**跨 MCP 编排引擎** · 可选组件（不是 MCP）

> ⚠️ 这是**最后一步**的规划，不是起点。它要求 6 个 MCP 全部装好才能用。
> 单装任何一个 MCP 完全不需要它。

> 发行名 `sqltk-orchestrator`，import 包名 `dbt_orchestrator`（`pip install sqltk-orchestrator` → `import dbt_orchestrator`）—— 两者不同是刻意的，原因见顶层 README「命名」。

## 安装（可选，且会连带装齐 6 个 MCP）

```bash
pip install sqltk-orchestrator              # 从 PyPI（会连带装上 6 个 MCP + 3 个共享库）
pip install ./sqltk-orchestrator            # 从源码（在工具链根目录执行）
```

> 单装任何一个 MCP 都不需要它；反过来它**必须**有 6 个 MCP 才能跑。

## 它解决什么

DESIGN 把 `next_actions` 定义成「跨 MCP 的编排合同」：每个工具在结果里声明下一步该调谁、传什么参数。
编排引擎只是**照着合同跑**，因此不需要为每一对 MCP 写胶水代码。

两种驱动方式：

- **自动追链**：给一个入口工具，引擎沿 `next_actions` 逐层展开（默认不追 `optional`）
- **recipe 配方**：一份显式、确定性的流水线 JSON，适合 CI 与受控变更

```bash
sqltk tools                          # 6 个 MCP / 91 个工具，按 MCP 分组列出
sqltk call sql-review.review_sql --args '{"sql": "SELECT 1", "dialect": "mysql"}'

# 自动追链：给一个入口，沿着它声明的 next_actions 跑下去
sqltk run --seed sql-review.review_sql \
  --args '{"sql": "SELECT * FROM orders WHERE status = 1 ORDER BY RAND() LIMIT 1000", "dialect": "mysql"}'
sqltk run --seed ... --follow-optional     # 连 optional 的后续也追

# recipe：确定性流水线
sqltk run --recipe examples/safe_change.json     # 低风险变更 → warn，退出码 0
sqltk run --recipe examples/find_problems.json   # 故意写坏的 SQL → fail，退出码 1
```

**退出码可以当 CI 门禁用**：0 = 通过（或仅 warn），1 = 有步骤执行失败**或**最终判定为 `fail`，
2 = 用法错误。`find_problems.json` 那个例子就是用来演示"CI 该红的时候确实会红"。

## 关联三件套自动透传

`correlation_id` / `instance_id` / `sql_fingerprint` 沿整条链路传递，
所以跨 MCP 的每一步都能在 `sqltk-store` 里串起来查。

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

## 许可

MIT © 2026 罗文星 —— 见 [LICENSE](LICENSE)。
