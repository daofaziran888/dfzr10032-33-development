"""数据血缘追踪接口：维护血缘关系，覆盖记录关系、验证关系、归档关系等动作。

除人工登记的关系外，转换执行会把“输入字段版本 → 输出字段版本”的对应关系写进同一张
血缘表；这里的查询接口用来从已确认结果反查原始字段，或按字段版本定位影响范围。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.lineage import LineageService
from app.services.transform_pipeline import PipelineError, TransformPipelineService

router = APIRouter(prefix="/api/lineage", tags=["数据血缘追踪"])

service = LineageService()
pipeline = TransformPipelineService()

LIST_FIELDS = ["关系编号", "上游节点", "下游节点", "来源字段", "目标字段", "转换说明", "采集批次", "关系版本"]
STATUSES = ["草稿", "已记录", "已验证", "已归档"]


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出数据血缘追踪清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "lineage", "total": total, "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按关系编号检索"),
    status: str | None = Query(default=None, description="草稿、已记录、已验证、已归档"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按关系编号与状态过滤数据血缘追踪列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    extra = {k: v for k, v in request.query_params.items() if k not in ("keyword", "status", "page", "size") and v.strip()}
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size, extra=extra)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/relations", response_model=PageResult[dict])
def list_relations(
    result: str | None = Query(default=None, description="按结果编号过滤"),
    batch: str | None = Query(default=None, description="按数据批次过滤"),
    node: str | None = Query(default=None, description="按上游或下游节点过滤"),
    field: str | None = Query(default=None, description="按来源或目标字段过滤"),
    field_version: str | None = Query(default=None, description="按来源字段版本定位影响范围"),
    page: int = 1,
    size: int = 50,
) -> PageResult[dict]:
    """查询执行期持久化的字段版本关系，可按结果、批次、节点、字段版本筛选。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = pipeline.list_lineage(
        result=result,
        batch=batch,
        node=node,
        field=field,
        field_version=field_version,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/trace/{result_number}", response_model=dict)
def trace_result(result_number: str, field: str | None = None) -> dict[str, Any]:
    """从已确认结果向上追溯，一直到最初的输入字段版本。"""
    try:
        return pipeline.trace(result_number, field=field)
    except PipelineError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条血缘关系明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"血缘关系 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条血缘关系，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="血缘关系已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条血缘关系执行记录关系、验证关系、归档关系；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
