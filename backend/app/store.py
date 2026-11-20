"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

from typing import Any

from app.seed import SEED_ROWS
from app.seed_pipeline import PIPELINE_ROWS

# 转换执行产生的过程表：互相之间要能按批次、运行、结果串联，属于采集转换管道的
# 内部明细，不单独算业务模块，“转换节点”和“血缘关系”本身仍然是业务对象。
PIPELINE_TABLES = {
    "transform",
    "transform_input",
    "transform_batch",
    "transform_run",
    "transform_node_run",
    "transform_result",
    "lineage",
}


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        # 转换执行相关表覆盖同名占位数据，并补充 seed.py 里没有的过程表。
        for name, rows in PIPELINE_ROWS.items():
            self._tables[name] = [dict(row) for row in rows]
        self._hidden = {
            name for name in PIPELINE_TABLES if name not in ("transform", "lineage")
        }

    def module_names(self) -> list[str]:
        return sorted(name for name in self._tables if name not in self._hidden)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
