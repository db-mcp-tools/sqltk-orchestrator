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
for _sib in ("dbt-core", "dbt-store", "dbt-know", "dbt-mcp-kit", "mcp-sql-review", "mcp-change-risk", "mcp-dbwatch", "mcp-migration-diff", "mcp-data-semantics", "mcp-profile-hub"):
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


# ==================================================================
print("\n" + "=" * 60)
print("通过 %d 项，失败 %d 项" % (len(PASSED), len(FAILED)))
if FAILED:
    print("\n失败明细：")
    for _f in FAILED:
        print("  ✗", _f)
    raise SystemExit(1)
print("全部通过")
