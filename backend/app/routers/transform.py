"""采集转换管道接口：维护转换节点，并执行转换、查看输入版本、结果与失败节点。

节点维护仍走原有的登记/状态流转；执行相关接口都走 ``TransformPipelineService``，
把“按依赖执行、结果带输入版本、失败节点可查”放在服务层完成。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.transform import TransformService
from app.services.transform_pipeline import PipelineError, TransformPipelineService

router = APIRouter(prefix="/api/transform", tags=["采集转换管道"])

service = TransformService()
pipeline = TransformPipelineService()

LIST_FIELDS = ["节点编号", "输入来源", "输出目标", "转换表达式", "执行顺序", "依赖节点", "失败策略", "节点状态"]
STATUSES = ["草稿", "待校验", "已启用", "已停用"]


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出采集转换管道清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "transform", "total": total, "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按节点编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、已启用、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按节点编号与状态过滤采集转换管道列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    extra = {k: v for k, v in request.query_params.items() if k not in ("keyword", "status", "page", "size") and v.strip()}
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size, extra=extra)
    return PageResult(items=items, total=total, page=page, size=size)


# --------------------------------------------------------------------- 批次与输入
@router.get("/batches", response_model=dict)
def list_batches() -> dict[str, Any]:
    """列出输入批次：每次更正字段都会生成新的数据版本。"""
    return {"items": pipeline.list_batches()}


@router.get("/inputs", response_model=PageResult[dict])
def list_inputs(
    batch: str | None = Query(default=None, description="批次编号，缺省取最新批次"),
    detail: str | None = Query(default=None, description="按明细编号检索"),
    current: bool = Query(default=True, description="只取该批次生效的版本"),
    page: int = 1,
    size: int = 50,
) -> PageResult[dict]:
    """按批次查看输入明细；指定 batch 时看到的是那个版本当时的值。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    try:
        items, total = pipeline.list_inputs(
            batch=batch, detail=detail, current=current, page=page, size=size
        )
    except PipelineError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/inputs/{detail}/versions", response_model=dict)
def input_versions(detail: str) -> dict[str, Any]:
    """查看一条明细的全部输入版本，确认更正前后的字段版本。"""
    rows = pipeline.input_versions(detail)
    if not rows:
        raise HTTPException(status_code=404, detail=f"明细 {detail} 不存在")
    return {"detail": detail, "items": rows}


@router.post("/inputs/{detail}/corrections", response_model=ActionResult)
def correct_input(detail: str, payload: EntryPayload) -> ActionResult:
    """发布输入更正：生成新批次与新的字段版本，老批次保持可读。"""
    try:
        outcome = pipeline.correct_input(
            detail,
            payload.values,
            remark=str(payload.values.get("remark") or payload.remark or ""),
        )
    except PipelineError as exc:
        return ActionResult(ok=False, message=str(exc))
    changed = "、".join(outcome["field_versions"])
    return ActionResult(
        ok=True,
        message=f"已发布更正批次 {outcome['batch']['批次编号']}，变更字段：{changed}",
        entry={**outcome["detail"], "批次编号": outcome["batch"]["批次编号"]},
    )


# --------------------------------------------------------------------- 运行与结果
@router.get("/runs", response_model=PageResult[dict])
def list_runs(page: int = 1, size: int = 20) -> PageResult[dict]:
    """列出转换运行：含批次、失败节点、阻塞节点与复用节点。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = pipeline.list_runs(page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/runs/{run_id}", response_model=dict)
def get_run(run_id: int) -> dict[str, Any]:
    """查看一次运行的节点执行情况与产出的结果清单。"""
    run = pipeline.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"运行 {run_id} 不存在")
    return run


@router.post("/runs", response_model=ActionResult)
def run_pipeline(payload: EntryPayload) -> ActionResult:
    """按节点依赖执行转换；可指定批次，重试时默认沿用未受影响节点的原结果。"""
    values = payload.values
    retry = values.get("retry", True)
    if isinstance(retry, str):
        retry = retry.strip().lower() not in ("false", "0", "no", "否")
    try:
        run = pipeline.run_pipeline(
            batch=str(values.get("batch") or "") or None,
            retry=bool(retry),
            operator=str(values.get("operator") or ""),
            remark=str(values.get("remark") or payload.remark or ""),
        )
    except PipelineError as exc:
        return ActionResult(ok=False, message=str(exc))
    message = (
        f"运行 {run['运行编号']} 完成，产出 {run['结果数量']} 条结果"
        f"，数据批次 {run['数据批次']}"
    )
    if run.get("失败节点"):
        message += f"；失败节点：{run['失败节点']}"
    if run.get("阻塞节点"):
        message += f"；阻塞节点：{run['阻塞节点']}"
    if run.get("复用节点"):
        message += f"；沿用节点：{run['复用节点']}"
    return ActionResult(ok=run["执行状态"] == "成功", message=message, entry=run)


@router.get("/results", response_model=PageResult[dict])
def list_results(
    batch: str | None = Query(default=None, description="按数据批次过滤"),
    node: str | None = Query(default=None, description="按节点编号过滤"),
    detail: str | None = Query(default=None, description="按明细编号过滤"),
    run: str | None = Query(default=None, description="按运行编号过滤"),
    status: str | None = Query(default=None, description="已确认、未确认"),
    page: int = 1,
    size: int = 50,
) -> PageResult[dict]:
    """查看转换结果明细：每条结果都带输入版本、输出字段版本和失败节点。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = pipeline.list_results(
        batch=batch, node=node, detail=detail, run=run, status=status, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/results/{detail}/history", response_model=dict)
def result_history(detail: str, node: str | None = None) -> dict[str, Any]:
    """同一明细各批次的结果对照，用于核对变更前后是否与各自批次一致。"""
    rows = pipeline.result_history(detail, node=node)
    if not rows:
        raise HTTPException(status_code=404, detail=f"明细 {detail} 还没有转换结果")
    return {"detail": detail, "items": rows}


@router.get("/results/{number}/lineage", response_model=dict)
def result_lineage(number: str, field: str | None = None) -> dict[str, Any]:
    """从一条已确认结果出发追溯原始字段版本，未确认的结果不允许作为起点。"""
    try:
        return pipeline.trace(number, field=field)
    except PipelineError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条转换节点明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"转换节点 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条转换节点，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="转换节点已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条转换节点执行提交校验、启用节点、停用节点；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
