#!/usr/bin/env python3
"""orchestrator 回归测试：跨 MCP 追链 / recipe / 去重防环

不依赖 pytest / 真实数据库。直接运行：

    python tests/test_dbt_orchestrator.py
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "src"))
# 开发期：兄弟仓库用源码目录；正式使用请 pip install（见 README）
for _sib in ("sqltk-core", "sqltk-store", "sqltk-know", "mcp-sql-review", "mcp-change-risk", "mcp-dbwatch", "mcp-migration-diff", "mcp-data-semantics", "mcp-profile-hub"):
    _p = _HERE.parent.parent / _sib / "src"
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

os.environ.setdefault("DBT_LOG_LEVEL", "ERROR")
_TMPDIR = tempfile.mkdtemp(prefix="dbt-dbt_orchestrator-")
os.environ.setdefault("DBT_STORE", os.path.join(_TMPDIR, "store.db"))

from dbcore.protocol import Result, ToolError  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(name)
    else:
        FAILED.append(f"{name}  << {detail[:300] if detail else ''}")


def section(title: str) -> None:
    print(f"\n── {title}")


section("11. 编排层 orchestrator（跨 MCP 追链）")
# ==================================================================
from sql_review import server as _SR  # noqa: E402
from change_risk import server as _CR  # noqa: E402
from dbwatch import server as _DW  # noqa: E402
from migration_diff import server as _MG  # noqa: E402
from data_semantics import server as _DS  # noqa: E402
from profile_hub import server as _PH  # noqa: E402
_total_tools = sum(len(m.TOOLS) for m in (_SR, _CR, _DW, _MG, _DS, _PH))
from dbt_orchestrator.engine import Orchestrator  # noqa: E402
from dbt_orchestrator.registry import build_registry  # noqa: E402

_reg = build_registry()
check("注册表覆盖全部 (mcp,tool)", len(_reg.by_mcp_tool) == _total_tools, str(len(_reg.by_mcp_tool)))
check("按 (mcp,tool) 解析 review_sql", _reg.resolve("sql-review", "review_sql") is not None)
check("按纯工具名解析 review_sql", _reg.resolve(None, "review_sql") is not None)
# 全局唯一名若跨 MCP 重名，必须被标记为歧义（强制调用方显式指定 mcp）
check("歧义工具名已标记", len(_reg.ambiguous) == _total_tools - len(_reg.names()),
      f"{_reg.ambiguous}")
try:
    _reg.resolve(None, "不存在的工具_xyz")
    check("未知工具应抛错", False)
except KeyError:
    check("未知工具应抛错", True)

# 自动追链：体检 ->（自动）suggest_fixes
_och = Orchestrator(registry=_reg, max_steps=30)
_tr = _och.run("sql-review", "review_sql",
               {"sql": "SELECT * FROM t WHERE 1=1 ORDER BY RAND() LIMIT 1000",
                "dialect": "mysql"})
check("追链至少跑两步（review_sql + suggest_fixes）", len(_tr.steps) >= 2, str(len(_tr.steps)))
check("correlation_id 被产出并透传", bool(_tr.correlation_id))
# 每一步的 correlation_id 参数应等于链路级 ID
_chain_cid = all(
    (s.result and s.result.correlation_id == _tr.correlation_id)
    for s in _tr.steps if s.result
)
check("correlation_id 沿链路透传到底", _chain_cid)
check("review_sql 与 suggest_fixes 都被执行",
      {s.tool for s in _tr.steps} >= {"review_sql", "suggest_fixes"})

# 防环：开 follow_optional 也不应超过 max_steps
_och2 = Orchestrator(registry=_reg, max_steps=40, follow_optional=True)
_tr2 = _och2.run("sql-review", "review_sql",
                 {"sql": "SELECT * FROM orders ORDER BY id LIMIT 10 OFFSET 50000",
                  "dialect": "mysql"})
check("follow_optional 不失控（步数受 max_steps 约束）",
      len(_tr2.steps) <= 40, str(len(_tr2.steps)))

# 跨 MCP：review_sql 的后续里出现 change-risk.assess_change
_cross = {f"{s.mcp}.{s.tool}" for s in _tr2.steps}
check("跨 MCP 链路出现 change-risk.assess_change",
      "change-risk.assess_change" in _cross, str(sorted(_cross)))

# 优雅失败：缺必填参数的后续不应抛异常，而是标 E_ARGS
_precheck = [s for s in _tr2.steps if s.tool == "precheck_change_window"]
check("precheck_change_window 因缺 url 被标为 E_ARGS 而非崩溃",
      any(s.error_code == "E_ARGS" for s in _precheck), str([s.error_code for s in _precheck]))

# recipe 模式：确定性流水线，按给定顺序执行，不自动追 next_actions
_recipe = Orchestrator(registry=_reg, max_steps=10)
_tr3 = _recipe.run_recipe([
    {"mcp": "sql-review", "tool": "review_sql",
     "args": {"sql": "SELECT * FROM t", "dialect": "mysql"}},
    {"mcp": "sql-review", "tool": "suggest_fixes",
     "args": {"sql": "SELECT * FROM t", "dialect": "mysql"}},
])
check("recipe 按声明顺序执行 2 步", len(_tr3.steps) == 2, str(len(_tr3.steps)))
check("recipe 不自动追 next_actions（步数不膨胀）", len(_tr3.steps) == 2)

# ==================================================================
# 12. CLI 回归（2026-10 实测发现：console script 是坏的）
#
# cli.py 曾是照抄 MCP 模板的产物：tools 去 import 一个不存在的 TOOLS 字典，
# 而文档里的 `run` 子命令根本没实现。更糟的是 __main__.py 里有第二份能用的
# 实现（参数风格还不同），于是"同一个工具有两套行为"。
# 这几条用例锁住：一个实现、四种用法都通、退出码能当 CI 门禁。
# ==================================================================
print("\n-- 12. CLI --")

import contextlib  # noqa: E402
import io  # noqa: E402
import json as _json  # noqa: E402

from dbt_orchestrator import cli as CLI  # noqa: E402


def run_cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = CLI.main(list(argv))
        except SystemExit as exc:            # argparse 的用法错误
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


_code, _out, _err = run_cli("tools")
check("cli tools 不崩溃且返回 0", _code == 0, _err[:200])
check("cli tools 列出 6 个 MCP", "6 个 MCP" in _out, _out[:80])
check("cli tools 带工具描述", "review_sql" in _out and "体检" in _out)

_code, _out, _ = run_cli("tools", "--json")
check("cli tools --json 可解析", _code == 0 and len(_json.loads(_out)) == 6)

_code, _, _err = run_cli("call", "no-such-mcp.no_such_tool")
check("cli call 未知工具 → 2", _code == 2, _err[:80])

_code, _out, _ = run_cli("call", "sql-review.review_sql", "--args",
                         '{"sql": "SELECT id FROM t WHERE id = 1", "dialect": "mysql"}')
check("cli call 成功返回信封", _code == 0 and '"schema": "dbt.result/v1"' in _out)

_code, _out, _ = run_cli("call", "sql-review.review_sql", "--help-args")
check("cli call --help-args 打印签名", _code == 0 and "review_sql(" in _out)

# 追链：给入口后应按 next_actions 展开（>1 步）
_code, _out, _ = run_cli("run", "--seed", "sql-review.review_sql",
                         "--sql", "SELECT * FROM orders ORDER BY RAND() LIMIT 10",
                         "--dialect", "mysql")
check("cli run --seed 追到了下一步", "链路步数：2" in _out or "suggest_fixes" in _out, _out[:200])
check("cli run --seed 用 --sql/--dialect 便捷参数", "review_sql" in _out)

_EX = _HERE.parent / "examples"
_code, _out, _ = run_cli("run", "--recipe", str(_EX / "safe_change.json"))
check("cli run --recipe 安全变更 → 退出 0", _code == 0, _out[:150])

_code, _out, _err = run_cli("run", "--recipe", str(_EX / "find_problems.json"))
check("cli run --recipe 检出问题 → 退出 1（可当 CI 门禁）", _code == 1, _out[:150])
check("  且报告里结论是 fail", "fail" in _out)

_code, _, _err = run_cli("run")
check("cli run 缺 --seed/--recipe → 2", _code == 2, _err[:80])

_code, _out, _err = run_cli("run", "--recipe", "not-exists.json")
check("cli run 不存在的 recipe → 2", _code == 2, _err[:80])

# python -m 与 console script 必须是同一实现（历史上是两份）
import subprocess  # noqa: E402

# 子进程不继承本测试手工拼的 sys.path，用 PYTHONPATH 显式传过去
_env = {**os.environ, "PYTHONPATH": os.pathsep.join(str(x) for x in sys.path if x)}
_p = subprocess.run([sys.executable, "-m", "dbt_orchestrator", "--help"],
                    capture_output=True, text=True, env=_env)
check("python -m dbt_orchestrator 与 sqltk 同源",
      _p.returncode == 0 and "{tools,call,run}" in _p.stdout, _p.stderr[-200:])

# ==================================================================
print("\n" + "=" * 60)
print("通过 %d 项，失败 %d 项" % (len(PASSED), len(FAILED)))
if FAILED:
    print("\n失败明细：")
    for _f in FAILED:
        print("  ✗", _f)
    raise SystemExit(1)
print("全部通过")
