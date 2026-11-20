"""采集转换管道的接口：节点维护 + 执行期输入版本、结果与血缘查询。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _run(**values) -> dict:
    response = client.post("/api/transform/runs", json={"values": values})
    assert response.status_code == 200
    return response.json()


def test_health_and_overview_keep_module_list(fresh_store):
    assert client.get("/api/health").json()["modules"] == 17
    names = [item["name"] for item in client.get("/api/overview").json()["modules"]]
    assert "transform" in names
    assert "transform_result" not in names


def test_full_correction_cycle_through_http(fresh_store):
    first = _run(retry=False)
    assert first["ok"] is True
    assert "RUN-0001" in first["message"]

    correction = client.post(
        "/api/transform/inputs/D-0001/corrections",
        json={"values": {"重量克": 1200, "remark": "分拣秤校准"}},
    ).json()
    assert correction["ok"] is True
    assert "BATCH-002" in correction["message"]

    retry = _run(retry=True)
    assert "TRAN-BOX" in retry["message"]

    old_inputs = client.get(
        "/api/transform/inputs", params={"batch": "BATCH-001"}
    ).json()["items"]
    assert [row["重量克"] for row in old_inputs if row["明细编号"] == "D-0001"] == [1000]

    history = client.get("/api/transform/results/D-0001/history").json()["items"]
    by_batch = {row["数据批次"]: row for row in history if row["节点编号"] == "TRAN-KG"}
    assert by_batch["BATCH-001"]["输出值"] == 1
    assert by_batch["BATCH-002"]["输出值"] == 1.2

    relations = client.get(
        "/api/lineage/relations", params={"field_version": "D-0001-重量克-v2"}
    ).json()
    assert relations["total"] >= 1
    trace = client.get(f"/api/lineage/trace/{by_batch['BATCH-002']['结果编号']}").json()
    assert trace["result"]["确认状态"] == "已确认"
    assert trace["sources"]


def test_failed_input_correction_is_reported(fresh_store):
    response = client.post(
        "/api/transform/inputs/D-0001/corrections",
        json={"values": {"重量克": 1000}},
    ).json()
    assert response["ok"] is False
    assert "一致" in response["message"]

    missing = client.post(
        "/api/transform/inputs/D-9999/corrections",
        json={"values": {"重量克": 1}},
    ).json()
    assert missing["ok"] is False


def test_pinned_old_batch_still_reads_original_versions(fresh_store):
    _run(retry=False)
    client.post(
        "/api/transform/inputs/D-0001/corrections",
        json={"values": {"重量克": 1200}},
    )
    pinned = _run(retry=False, batch="BATCH-001")
    assert pinned["ok"] is True
    assert "BATCH-001" in pinned["message"]

    results = client.get(
        "/api/transform/results", params={"batch": "BATCH-001", "node": "TRAN-KG"}
    ).json()["items"]
    values = {row["明细编号"]: row["输出值"] for row in results}
    assert values["D-0001"] == 1


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("重量克 / 0", "除数为 0"),
        ("不存在的字段 * 2", "不可用"),
        ("重量克 +", "无法解析"),
    ],
)
def test_bad_expression_marks_node_failed(fresh_store, expression, expected):
    from app.store import store

    node = next(row for row in store.rows("transform") if row["节点编号"] == "TRAN-KG")
    node["转换表达式"] = expression
    result = _run(retry=False)
    assert result["ok"] is False

    run = result["entry"]
    kg_run = next(row for row in run["node_runs"] if row["节点编号"] == "TRAN-KG")
    assert kg_run["执行状态"] == "失败"
    assert expected in kg_run["失败原因"]
    assert "TRAN-KG" in run["失败节点"]

    # 表达式本身不成立时没有逐行结果，但仍要挡住宿主节点。
    region_run = next(row for row in run["node_runs"] if row["节点编号"] == "TRAN-REGION")
    assert region_run["执行状态"] == "阻塞"

    failed = client.get(
        "/api/transform/results", params={"status": "未确认", "node": "TRAN-KG"}
    ).json()["items"]
    assert all(item["失败节点"] == "TRAN-KG" for item in failed)
    if expression != "重量克 +":
        assert failed
        assert any(expected in item["失败原因"] for item in failed)
