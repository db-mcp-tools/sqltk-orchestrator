"""`python -m dbt_orchestrator` 的入口 —— 与 console script `sqltk` 同一实现。

这里只做转接。历史上这里曾有一份独立实现的 argparse（参数风格是 `--sql/--arg`），
而 console script 用的是另一份（`--args` JSON），两边逻辑与退出码都不一致 ——
同一个工具有两套行为，文档无论怎么写都会有一半是错的。现在只有 `cli.py` 一处。
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
