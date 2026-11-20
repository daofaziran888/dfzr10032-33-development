"""采集转换管道执行：依赖顺序、失败传播、更正重试与血缘追溯。"""
from __future__ import annotations

from app.services.transform_pipeline import PipelineError, TransformPipelineService
from app.store import store


def _service() -> TransformPipelineService:
    return TransformPipelineService()


def _result(run: dict, node: str, detail: str = "", group: str = "") -> dict:
    for row in run["results"]:
        if row["节点编号"] != node:
            continue
        if detail and row["明细编号"] != detail:
            continue
        if group and row["分组键"] != group:
            continue
        return row
    raise AssertionError(f"运行中找不到节点 {node} 的结果（明细={detail} 分组={group}）")


def test_first_run_executes_nodes_in_dependency_order():
    run = _service().run_pipeline(retry=False)

    assert run["执行状态"] == "成功"
    assert [row["节点编号"] for row in run["node_runs"]] == [
        "TRAN-BOX",
        "TRAN-KG",
        "TRAN-REGION",
    ]
    assert _result(run, "TRAN-KG", "D-0001")["输出值"] == 1
    assert _result(run, "TRAN-REGION", group="华东")["输出值"] == 3
    assert _result(run, "TRAN-BOX", "D-0003")["输出值"] == "BOX-003"


def test_result_carries_input_versions_and_output_field_version():
    service = _service()
    run = service.run_pipeline(retry=False)

    kg = _result(run, "TRAN-KG", "D-0001")
    assert kg["输入版本"] == "IN-0001"
    assert kg["输入字段版本"] == {"重量克": "D-0001-重量克-v1"}
    assert kg["输出字段版本"].startswith("OUT-RES-")
    assert kg["数据批次"] == "BATCH-001"

    region = _result(run, "TRAN-REGION", group="华东")
    assert set(region["输入字段版本"]["重量公斤"].split("|")) == {
        _result(run, "TRAN-KG", "D-0001")["输出字段版本"],
        _result(run, "TRAN-KG", "D-0002")["输出字段版本"],
    }
    assert set(region["输入版本"].split("|")) == {"IN-0001", "IN-0002"}


def test_upstream_failure_blocks_successor_instead_of_using_unconfirmed_results():
    service = _service()
    service.run_pipeline(retry=False)
    kg_node = next(row for row in store.rows("transform") if row["节点编号"] == "TRAN-KG")
    kg_node["转换表达式"] = "重量克 / 0"

    run = service.run_pipeline(retry=True)

    assert run["执行状态"] != "成功"
    assert "TRAN-KG" in run["失败节点"]
    assert "TRAN-REGION" in run["阻塞节点"]
    assert not [row for row in run["results"] if row["节点编号"] == "TRAN-REGION"]
    failed = _result(run, "TRAN-KG", "D-0001")
    assert failed["确认状态"] == "未确认"
    assert failed["失败节点"] == "TRAN-KG"
    assert "除数为 0" in failed["失败原因"]
    assert _result(run, "TRAN-BOX", "D-0001")["确认状态"] == "已确认"


def test_unconfirmed_result_cannot_be_lineage_start():
    service = _service()
    service.run_pipeline(retry=False)
    kg_node = next(row for row in store.rows("transform") if row["节点编号"] == "TRAN-KG")
    kg_node["转换表达式"] = "重量克 / 0"
    run = service.run_pipeline(retry=True)
    failed = _result(run, "TRAN-KG", "D-0001")

    try:
        service.trace(failed["结果编号"])
    except PipelineError as exc:
        assert "未确认" in str(exc)
    else:  # pragma: no cover - 未确认结果必须被拒
        raise AssertionError("未确认结果不应能作为血缘起点")


def test_correction_retry_keeps_unaffected_node_results():
    service = _service()
    first = service.run_pipeline(retry=False)

    correction = service.correct_input("D-0001", {"重量克": 1200}, remark="分拣秤校准")
    assert correction["batch"]["批次编号"] == "BATCH-002"
    assert correction["field_versions"] == {"重量克": "D-0001-重量克-v2"}

    retry = service.run_pipeline(retry=True)

    assert retry["执行状态"] == "成功"
    assert retry["复用节点"].split("|") == ["TRAN-BOX"]
    box_run = next(row for row in retry["node_runs"] if row["节点编号"] == "TRAN-BOX")
    assert box_run["复用运行编号"] == first["运行编号"]
    # 箱号清洗分支没有重跑，结果仍是第一批次的编号，不会被改写成新值。
    for row in retry["results"]:
        if row["节点编号"] == "TRAN-BOX":
            assert row["运行编号"] == first["运行编号"]
            assert row["数据批次"] == "BATCH-001"
    assert _result(retry, "TRAN-KG", "D-0001")["输出值"] == 1.2
    assert _result(retry, "TRAN-REGION", group="华东")["输出值"] == 3.2


def test_corrected_and_original_batches_stay_consistent():
    service = _service()
    service.run_pipeline(retry=False)
    service.correct_input("D-0001", {"重量克": 1200})
    service.run_pipeline(retry=True)

    history = service.result_history("D-0001", node="TRAN-KG")
    by_batch = {row["数据批次"]: row for row in history}
    assert by_batch["BATCH-001"]["输出值"] == 1
    assert by_batch["BATCH-001"]["输入字段版本"] == {"重量克": "D-0001-重量克-v1"}
    assert by_batch["BATCH-002"]["输出值"] == 1.2
    assert by_batch["BATCH-002"]["输入字段版本"] == {"重量克": "D-0001-重量克-v2"}

    pinned = service.run_pipeline(batch="BATCH-001", retry=False)
    assert pinned["数据批次"] == "BATCH-001"
    assert _result(pinned, "TRAN-KG", "D-0001")["输出值"] == 1
    assert _result(pinned, "TRAN-REGION", group="华东")["输出值"] == 3


def test_lineage_locates_original_field_versions_from_confirmed_result():
    service = _service()
    service.run_pipeline(retry=False)
    service.correct_input("D-0001", {"重量克": 1200})
    retry = service.run_pipeline(retry=True)
    region = _result(retry, "TRAN-REGION", group="华东")

    trace = service.trace(region["结果编号"], field="地区重量合计")

    weight_branch = next(
        item for item in trace["sources"] if item["relation"]["来源字段"] == "重量公斤"
    )
    versions = {
        child["relation"]["来源字段版本"] for child in weight_branch["upstream"]
    }
    assert versions == {"D-0001-重量克-v2", "D-0002-重量克-v1"}
    for child in weight_branch["upstream"]:
        assert child["relation"]["数据批次"] == "BATCH-002"

    old = service.list_lineage(field_version="D-0001-重量克-v1", result=None)
    assert old[1] > 0


def test_node_definition_change_is_not_reused():
    service = _service()
    first = service.run_pipeline(retry=False)
    kg_node = next(row for row in store.rows("transform") if row["节点编号"] == "TRAN-KG")
    kg_node["转换表达式"] = "重量克 / 100"

    retry = service.run_pipeline(retry=True)

    assert "TRAN-KG" not in retry["复用节点"].split("|")
    assert _result(retry, "TRAN-KG", "D-0001")["输出值"] == 10
    assert _result(first, "TRAN-KG", "D-0001")["输出值"] == 1
