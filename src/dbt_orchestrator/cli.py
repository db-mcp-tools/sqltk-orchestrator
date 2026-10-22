"""sqltk —— 跨 MCP 编排引擎的命令行入口。

四种用法：

    sqltk tools                                        列出全部工具（按 MCP 分组）
    sqltk call sql-review.review_sql --args '{...}'     直接调一个工具
    sqltk run --seed sql-review.review_sql --args '{...}' [--follow-optional]
                                                       给一个入口，按 next_actions 自动追链
    sqltk run --recipe examples/safe_change.json       确定性流水线（不追 next_actions）

工具定位支持两种写法：`mcp.tool` 精确指定，或只写 `tool`（名字在多个 MCP 重名时会报歧义）。

返回值：默认渲染成人读报告（先结论后证据），加 `--json` 输出完整 trace。
退出码：0 全部成功 / 1 有步骤失败 / 2 用法错误。
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
from pathlib import Path


def _registry():
    from dbt_orchestrator.registry import registry
    return registry()


def _split_target(text: str) -> tuple[str | None, str]:
    """`sql-review.review_sql` → ("sql-review", "review_sql")；裸名 → (None, 名)。"""
    if "." in text:
        mcp, tool = text.split(".", 1)
        return mcp.strip(), tool.strip()
    return None, text.strip()


def _load_args(text: str) -> dict:
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--args 不是合法 JSON：{exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit("--args 必须是 JSON 对象")
    return value


def cmd_tools(args) -> int:
    """按 MCP 分组打印，比平铺一张长表更容易找到目标。"""
    reg = _registry()
    grouped: dict[str, list[tuple[str, str]]] = {}
    for (mcp, tool), entry in reg.by_mcp_tool.items():
        doc = (inspect.getdoc(entry.func) or "").splitlines()
        grouped.setdefault(mcp, []).append((tool, doc[0] if doc else ""))

    total = sum(len(v) for v in grouped.values())
    if args.json:
        print(json.dumps(
            {mcp: [{"tool": t, "doc": d} for t, d in sorted(items)]
             for mcp, items in sorted(grouped.items())},
            ensure_ascii=False, indent=2))
        return 0

    print(f"{len(grouped)} 个 MCP / {total} 个工具\n")
    for mcp in sorted(grouped):
        print(f"[{mcp}]  {len(grouped[mcp])} 个")
        for tool, doc in sorted(grouped[mcp]):
            print(f"  {tool:26s} {doc}")
        print()
    return 0


def cmd_call(args) -> int:
    reg = _registry()
    mcp, tool = _split_target(args.tool)
    entry = reg.get(mcp, tool)
    if entry is None:
        hint = f"{mcp}.{tool}" if mcp else tool
        print(f"没有工具 {hint}（`sqltk tools` 可查）", file=sys.stderr)
        return 2
    if args.help_args:
        print(f"{entry.mcp}.{entry.tool}{inspect.signature(entry.func)}")
        return 0
    kwargs = _load_args(args.args)
    try:
        raw = entry.func(**kwargs)
    except TypeError as exc:
        print(f"参数不匹配：{exc}\n提示：{entry.mcp}.{entry.tool}"
              f"{inspect.signature(entry.func)}", file=sys.stderr)
        return 2
    out = raw.to_dict() if hasattr(raw, "to_dict") else raw
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 1


def cmd_run(args) -> int:
    from dbt_orchestrator.engine import Orchestrator
    from dbt_orchestrator.report import render_trace

    orch = Orchestrator(max_steps=args.max_steps,
                        follow_optional=args.follow_optional)

    if args.recipe:
        path = Path(args.recipe)
        if not path.is_file():
            print(f"recipe 文件不存在：{path}", file=sys.stderr)
            return 2
        data = json.loads(path.read_text(encoding="utf-8"))
        steps = data.get("steps") if isinstance(data, dict) else data
        if not isinstance(steps, list) or not steps:
            print('recipe 必须是步骤数组，或形如 {"steps": [...]}', file=sys.stderr)
            return 2
        trace = orch.run_recipe(steps, instance_id=args.instance_id)
    elif args.seed:
        mcp, tool = _split_target(args.seed)
        seed_args = _load_args(args.args)
        # 便捷参数：--sql / --dialect 直接写进 args，省掉一层 JSON 转义
        if args.sql:
            seed_args["sql"] = args.sql
        if args.dialect:
            seed_args.setdefault("dialect", args.dialect)
        trace = orch.run(mcp, tool, seed_args,
                         instance_id=args.instance_id,
                         follow_optional=args.follow_optional)
    else:
        print("需要 --seed <mcp.tool> 或 --recipe <file>", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(trace.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(render_trace(trace))

    # 退出码要能直接当 CI 门禁用：
    #   ok=False  → 某步执行出错（连不上库、参数不对…）
    #   verdict=fail → 业务判定不通过（体检出高危问题、变更风险过高…）
    # 只看 ok 会漏掉第二种 —— 那恰恰是「要不要上线」的结论，
    # 用 `sqltk run` 卡 CI 的人要的就是它。
    bad = [s for s in trace.steps if not s.ok and not s.skipped]
    verdict = trace.final_verdict()
    if bad:
        print(f"\n{len(bad)} 步执行失败（共 {len(trace.steps)} 步）", file=sys.stderr)
        return 1
    if verdict == "fail":
        print("\n最终判定 fail —— 退出码 1（用于 CI 门禁）", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="sqltk",
        description="跨 MCP 编排引擎：按 next_actions 自动追链 + recipe 确定性流水线。"
                    "需要先装齐 6 个 MCP。")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("tools", help="列出全部工具（按 MCP 分组）")
    t.add_argument("--json", action="store_true")
    t.set_defaults(func=cmd_tools)

    c = sub.add_parser("call", help="直接调用一个工具")
    c.add_argument("tool", help="mcp.tool 或裸工具名")
    c.add_argument("--args", default="{}", help="JSON 参数对象")
    c.add_argument("--help-args", action="store_true", help="只打印参数签名")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_call)

    r = sub.add_parser("run", help="跑一条链（追 next_actions）或一份 recipe")
    r.add_argument("--seed", help="入口工具，如 sql-review.review_sql")
    r.add_argument("--recipe", help="recipe JSON 文件路径")
    r.add_argument("--args", default="{}", help="入口工具的 JSON 参数")
    r.add_argument("--sql", help="便捷参数：等价于 --args 里加 sql")
    r.add_argument("--dialect", help="便捷参数：等价于 --args 里加 dialect")
    r.add_argument("--instance-id", default="", help="目标实例标识，透传给每一步")
    r.add_argument("--follow-optional", action="store_true",
                   help="连 optional=true 的 next_actions 也追")
    r.add_argument("--max-steps", type=int, default=50, help="追链上限（默认 50）")
    r.add_argument("--json", action="store_true", help="输出完整 trace JSON")
    r.set_defaults(func=cmd_run)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
