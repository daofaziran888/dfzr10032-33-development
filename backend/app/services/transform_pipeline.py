"""采集转换管道执行服务：输入版本、批次、运行、结果与血缘的读写都在这里。

设计口径：

- 输入明细不可变：更正字段时新增一条输入版本，只给真正变化的字段换字段版本，
  老批次仍能按自己的数据版本读到原来的明细；
- 一次运行在开始时固定输入快照，结果行记录用到的输入版本与每个字段版本；
- 结果行同样不可变，失败行写明失败节点；未确认结果不会进入后继节点；
- 重试时复用上一次运行中“所读字段版本完全没变”的节点结果，复用结果保留
  原来的运行编号与批次，不会因为刷新被改写成新值；
- 已确认结果的字段版本关系持久化到血缘表，可沿上游结果逐级追溯到原始字段版本。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.transform_engine import (
    ExpressionError,
    PipelineConfigError,
    execute_pipeline,
)
from app.store import store

NODE_MODULE = "transform"
INPUT_MODULE = "transform_input"
BATCH_MODULE = "transform_batch"
RUN_MODULE = "transform_run"
NODE_RUN_MODULE = "transform_node_run"
RESULT_MODULE = "transform_result"
LINEAGE_MODULE = "lineage"

INPUT_FIELDS = ["箱号", "地区", "重量克"]
DETAIL_KEY = "明细编号"
FIRST_BATCH = "BATCH-001"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _next_number(rows: list[dict[str, Any]], field: str, prefix: str) -> str:
    """按前缀取下一个顺序号，已用编号不会重复。"""
    used: list[int] = []
    for row in rows:
        raw = str(row.get(field) or "")
        if raw.startswith(prefix):
            try:
                used.append(int(raw[len(prefix):]))
            except ValueError:
                continue
    return f"{prefix}{max(used, default=0) + 1:04d}"


class PipelineError(ValueError):
    """管道操作不成立：批次不存在、明细不存在或没有可执行节点。"""


class TransformPipelineService:
    # ------------------------------------------------------------------ 批次
    def list_batches(self) -> list[dict[str, Any]]:
        rows = sorted(
            store.rows(BATCH_MODULE), key=lambda row: int(row.get("数据版本", 0))
        )
        for row in rows:
            row["明细数量"] = len(self._detail_ids(int(row["数据版本"])))
        return rows

    def current_batch(self) -> dict[str, Any] | None:
        rows = self.list_batches()
        return rows[-1] if rows else None

    def _batch(self, batch: str | None) -> dict[str, Any]:
        rows = sorted(
            store.rows(BATCH_MODULE), key=lambda row: int(row.get("数据版本", 0))
        )
        if not rows:
            raise PipelineError("还没有任何输入批次，请先登记输入明细")
        if not batch:
            return rows[-1]
        for row in rows:
            if row.get("批次编号") == batch:
                return row
        raise PipelineError(f"批次 {batch} 不存在或已归档")

    def _detail_ids(self, data_version: int) -> list[str]:
        ids: list[str] = []
        for row in store.rows(INPUT_MODULE):
            if int(row.get("数据版本", 0)) <= data_version:
                detail = str(row.get(DETAIL_KEY) or "")
                if detail and detail not in ids:
                    ids.append(detail)
        return sorted(ids)

    def input_snapshot(self, batch: str | None = None) -> list[dict[str, Any]]:
        """按数据版本取每条明细当时生效的输入版本。"""
        target = self._batch(batch)
        version = int(target["数据版本"])
        latest: dict[str, dict[str, Any]] = {}
        for row in store.rows(INPUT_MODULE):
            if int(row.get("数据版本", 0)) > version:
                continue
            detail = str(row.get(DETAIL_KEY) or "")
            if detail not in latest or int(row.get("数据版本", 0)) >= int(
                latest[detail].get("数据版本", 0)
            ):
                latest[detail] = row
        return [latest[key] for key in sorted(latest)]

    # ------------------------------------------------------------------ 输入
    def list_inputs(
        self,
        *,
        batch: str | None = None,
        detail: str | None = None,
        current: bool = True,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self.input_snapshot(batch) if current else list(store.rows(INPUT_MODULE))
        if detail:
            rows = [row for row in rows if detail in str(row.get(DETAIL_KEY) or "")]
        rows = sorted(rows, key=lambda row: str(row.get(DETAIL_KEY) or ""))
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def input_versions(self, detail: str) -> list[dict[str, Any]]:
        rows = [
            row
            for row in store.rows(INPUT_MODULE)
            if str(row.get(DETAIL_KEY) or "") == detail
        ]
        return sorted(rows, key=lambda row: int(row.get("数据版本", 0)))

    def correct_input(
        self, detail: str, values: dict[str, Any], *, remark: str = "", operator: str = ""
    ) -> dict[str, Any]:
        detail = str(detail or "").strip()
        if not detail:
            raise PipelineError("缺少明细编号，无法定位要更正的输入明细")
        current_rows = [row for row in self.input_snapshot() if row.get(DETAIL_KEY) == detail]
        if not current_rows:
            raise PipelineError(f"明细 {detail} 在当前批次中不存在")
        current = current_rows[0]
        known = [field for field in INPUT_FIELDS if field in values]
        if not known:
            raise PipelineError(f"没有可更正的字段，仅支持 {'、'.join(INPUT_FIELDS)}")
        changed = {
            field: values[field]
            for field in known
            if str(values[field]) != str(current.get(field))
        }
        if not changed:
            raise PipelineError(f"明细 {detail} 的提交值与当前版本一致，未产生更正")

        batches = self.list_batches()
        next_version = max(int(row.get("数据版本", 0)) for row in batches) + 1
        batch_no = f"BATCH-{next_version:03d}"
        batch_row = {
            "id": _next_id(store.rows(BATCH_MODULE)),
            "批次编号": batch_no,
            "数据版本": next_version,
            "说明": remark or f"更正明细 {detail}：{'、'.join(changed)}",
            "操作人": operator or "值班管理员",
            "创建时间": _now(),
            "status": "已发布",
            "pending": False,
            "abnormal": False,
        }
        store.rows(BATCH_MODULE).append(batch_row)

        field_versions = dict(current.get("字段版本") or {})
        changed_versions: dict[str, str] = {}
        for field in changed:
            old = str(field_versions.get(field) or "")
            tag = 1
            if "-v" in old:
                try:
                    tag = int(old.rsplit("-v", 1)[1]) + 1
                except ValueError:
                    tag = 1
            version = f"{detail}-{field}-v{tag}"
            field_versions[field] = version
            changed_versions[field] = version

        for row in store.rows(INPUT_MODULE):
            if str(row.get(DETAIL_KEY) or "") == detail:
                row["是否当前"] = False

        new_row = {
            **{key: value for key, value in current.items() if key != "id"},
            **{field: values[field] for field in changed},
            "id": _next_id(store.rows(INPUT_MODULE)),
            "status": "已更正",
            "输入版本": _next_number(store.rows(INPUT_MODULE), "输入版本", "IN-"),
            "数据批次": batch_no,
            "数据版本": next_version,
            "版本序号": int(current.get("版本序号", 1)) + 1,
            "是否当前": True,
            "字段版本": field_versions,
            "更正说明": remark or "",
            "操作人": operator or "值班管理员",
            "创建时间": _now(),
            "pending": False,
            "abnormal": False,
        }
        store.rows(INPUT_MODULE).append(new_row)
        return {
            "batch": batch_row,
            "detail": new_row,
            "changed_fields": changed,
            "field_versions": changed_versions,
        }

    # ------------------------------------------------------------------ 运行
    def list_runs(self, *, page: int = 1, size: int = 20) -> tuple[list[dict[str, Any]], int]:
        rows = sorted(
            store.rows(RUN_MODULE), key=lambda row: int(row.get("id", 0)), reverse=True
        )
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def _node_runs(self, run_number: str) -> list[dict[str, Any]]:
        return [
            row
            for row in store.rows(NODE_RUN_MODULE)
            if row.get("运行编号") == run_number
        ]

    def _results_by_numbers(self, numbers: list[str]) -> list[dict[str, Any]]:
        wanted = set(numbers)
        order = {number: index for index, number in enumerate(numbers)}
        rows = [row for row in store.rows(RESULT_MODULE) if row.get("结果编号") in wanted]
        return sorted(rows, key=lambda row: order.get(str(row.get("结果编号")), 0))

    def get_run(self, run_id: int) -> dict[str, Any] | None:
        run = store.find(RUN_MODULE, run_id)
        if run is None:
            return None
        numbers = [num for num in str(run.get("结果编号") or "").split("|") if num]
        return {
            **run,
            "node_runs": self._node_runs(str(run.get("运行编号"))),
            "results": self._results_by_numbers(numbers),
        }

    def find_run(self, run_number: str) -> dict[str, Any] | None:
        for row in store.rows(RUN_MODULE):
            if row.get("运行编号") == run_number:
                return self.get_run(int(row["id"]))
        return None

    def _previous_run(self, data_version: int) -> dict[str, Any] | None:
        """取本次批次的基准运行：优先本批次最近一次，否则取更早版本的最近一次。"""
        runs = sorted(
            store.rows(RUN_MODULE), key=lambda row: int(row.get("id", 0)), reverse=True
        )
        candidates = [
            run
            for run in runs
            if int(run.get("数据版本", 0) or 0) <= data_version
            and str(run.get("结果编号") or "")
        ]
        for run in candidates:
            numbers = [num for num in str(run.get("结果编号") or "").split("|") if num]
            return {
                "run_number": run.get("运行编号"),
                "node_runs": self._node_runs(str(run.get("运行编号"))),
                "results": self._results_by_numbers(numbers),
            }
        return None

    def run_pipeline(
        self,
        *,
        batch: str | None = None,
        retry: bool = True,
        operator: str = "",
        remark: str = "",
    ) -> dict[str, Any]:
        target = self._batch(batch)
        nodes = list(store.rows(NODE_MODULE))
        if not nodes:
            raise PipelineError("没有可执行的转换节点，请先登记节点")
        snapshot = self.input_snapshot(str(target["批次编号"]))
        if not snapshot:
            raise PipelineError(f"批次 {target['批次编号']} 没有输入明细")
        run_number = _next_number(store.rows(RUN_MODULE), "运行编号", "RUN-")
        previous = (
            self._previous_run(int(target["数据版本"])) if retry else None
        )
        try:
            outcome = execute_pipeline(
                nodes=nodes,
                snapshot=snapshot,
                run_number=run_number,
                batch=str(target["批次编号"]),
                data_version=int(target["数据版本"]),
                result_numbers=[str(row.get("结果编号")) for row in store.rows(RESULT_MODULE)],
                previous=previous,
            )
        except (PipelineConfigError, ExpressionError) as exc:
            raise PipelineError(str(exc)) from exc

        created = _now()
        run_row = {
            "id": _next_id(store.rows(RUN_MODULE)),
            "运行编号": run_number,
            "数据批次": target["批次编号"],
            "数据版本": int(target["数据版本"]),
            "执行状态": outcome["run_status"],
            "触发方式": "更正重试" if previous else "首次执行",
            "基准运行": previous.get("run_number") if previous else "",
            "输入快照": [
                {"明细编号": row.get(DETAIL_KEY), "输入版本": row.get("输入版本")}
                for row in snapshot
            ],
            "结果数量": outcome["result_count"],
            "结果编号": "|".join(
                dict.fromkeys(
                    number
                    for node_run in outcome["node_runs"]
                    for number in str(node_run.get("结果编号") or "").split("|")
                    if number
                )
            ),
            "失败节点": "|".join(outcome["failed_nodes"]),
            "阻塞节点": "|".join(outcome["blocked_nodes"]),
            "复用节点": "|".join(outcome["reused_nodes"]),            "说明": remark or ("使用更正后的输入重试" if previous else "按当前输入执行转换"),
            "操作人": operator or "值班管理员",
            "创建时间": created,
            "status": "已完成" if outcome["run_status"] == "成功" else "有失败",
            "pending": outcome["run_status"] != "成功",
            "abnormal": outcome["run_status"] != "成功",
        }
        store.rows(RUN_MODULE).append(run_row)

        for node_run in outcome["node_runs"]:
            store.rows(NODE_RUN_MODULE).append(
                {
                    **node_run,
                    "id": _next_id(store.rows(NODE_RUN_MODULE)),
                    "创建时间": created,
                    "status": "已完成"
                    if node_run["执行状态"] in ("成功", "复用")
                    else "有失败",
                    "pending": node_run["执行状态"] not in ("成功", "复用"),
                    "abnormal": node_run["执行状态"] not in ("成功", "复用"),
                }
            )
        for result in outcome["results"]:
            store.rows(RESULT_MODULE).append(
                {**result, "id": _next_id(store.rows(RESULT_MODULE)), "创建时间": created}
            )
        for relation in outcome["lineage"]:
            rows = store.rows(LINEAGE_MODULE)
            rows.append(
                {
                    **relation,
                    "id": _next_id(rows),
                    "关系编号": _next_number(rows, "关系编号", "LINE-"),
                    "创建时间": created,
                }
            )
        return self.get_run(run_row["id"]) or run_row

    # ------------------------------------------------------------------ 结果
    def list_results(
        self,
        *,
        batch: str | None = None,
        node: str | None = None,
        detail: str | None = None,
        run: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(RESULT_MODULE))
        if batch:
            rows = [row for row in rows if row.get("数据批次") == batch]
        if node:
            rows = [row for row in rows if row.get("节点编号") == node]
        if detail:
            rows = [row for row in rows if row.get("明细编号") == detail]
        if run:
            wanted = set(
                str(row.get("结果编号") or "")
                for row in store.rows(NODE_RUN_MODULE)
                if row.get("运行编号") == run
            )
            rows = [row for row in rows if row.get("结果编号") in wanted]
        if status:
            rows = [row for row in rows if row.get("确认状态") == status]
        rows = sorted(rows, key=lambda row: int(row.get("id", 0)), reverse=True)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def result_history(self, detail: str, node: str | None = None) -> list[dict[str, Any]]:
        """同一条明细在各批次的转换结果，用来核对变更前后是否各自对得上。"""
        rows = [
            row
            for row in store.rows(RESULT_MODULE)
            if str(row.get("明细编号") or "") == detail
        ]
        if node:
            rows = [row for row in rows if row.get("节点编号") == node]
        return sorted(
            rows,
            key=lambda row: (int(row.get("数据版本", 0) or 0), int(row.get("id", 0))),
        )

    def _result(self, number: str) -> dict[str, Any]:
        for row in store.rows(RESULT_MODULE):
            if row.get("结果编号") == number:
                return row
        raise PipelineError(f"结果 {number} 不存在")

    # ------------------------------------------------------------------ 血缘
    def list_lineage(
        self,
        *,
        result: str | None = None,
        batch: str | None = None,
        node: str | None = None,
        field: str | None = None,
        field_version: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(LINEAGE_MODULE))
        if result:
            rows = [row for row in rows if row.get("结果编号") == result]
        if batch:
            rows = [row for row in rows if row.get("数据批次") == batch]
        if node:
            rows = [
                row
                for row in rows
                if row.get("上游节点") == node or row.get("下游节点") == node
            ]
        if field:
            rows = [
                row
                for row in rows
                if field in (row.get("来源字段"), row.get("目标字段"))
            ]
        if field_version:
            rows = [
                row
                for row in rows
                if field_version in str(row.get("来源字段版本") or "").split("|")
                or field_version == row.get("输出字段版本")
            ]
        rows = sorted(rows, key=lambda row: int(row.get("id", 0)))
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def trace(self, result_number: str, field: str | None = None) -> dict[str, Any]:
        """从已确认结果沿血缘向上追到原始输入字段版本。"""
        root = self._result(result_number)
        if root.get("确认状态") != "已确认":
            raise PipelineError(f"结果 {result_number} 未确认，不能作为血缘追溯起点")
        relations = [
            row
            for row in store.rows(LINEAGE_MODULE)
            if row.get("结果编号") == result_number
        ]
        if field:
            relations = [row for row in relations if row.get("目标字段") == field]
            if not relations:
                raise PipelineError(f"结果 {result_number} 没有字段 {field} 的血缘记录")
        sources = []
        for relation in relations:
            sources.append(self._trace_relation(relation, visited=set()))
        return {"result": root, "sources": sources}

    def _trace_relation(
        self, relation: dict[str, Any], *, visited: set[str]
    ) -> dict[str, Any]:
        marker = f"{relation.get('结果编号')}:{relation.get('目标字段')}"
        if marker in visited:
            return {"relation": relation, "upstream": [], "cycle": True}
        visited = visited | {marker}
        upstream: list[dict[str, Any]] = []
        for number in str(relation.get("上游结果编号") or "").split("|"):
            if not number:
                continue
            try:
                self._result(number)
            except PipelineError:
                continue
            for child in store.rows(LINEAGE_MODULE):
                if child.get("结果编号") == number and child.get("目标字段") == relation.get("来源字段"):
                    upstream.append(self._trace_relation(child, visited=visited))
        return {
            "relation": relation,
            "upstream": upstream,
            "cycle": False,
        }
