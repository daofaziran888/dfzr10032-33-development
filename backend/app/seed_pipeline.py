"""采集转换管道的示例数据：一条分拣重量链路 + 一条独立的箱号清洗链路。

这里的输入明细按“明细行版本 + 字段版本”组织：明细行每次更正都会产生新的
输入版本，但只有真正被改动的字段才会换字段版本。这样重试时才能判断哪些
节点受影响、哪些节点的原结果可以继续沿用。
"""
from __future__ import annotations

from typing import Any

BATCH = "BATCH-001"
CREATED_AT = "2026-11-20 09:00:00"

# 示例明细：字段值、来源系统展示值。箱号特意带空格，用来演示清洗分支。
_INPUTS = [
    {"明细编号": "D-0001", "箱号": " BOX-001 ", "地区": "华东", "重量克": 1000},
    {"明细编号": "D-0002", "箱号": " BOX-002 ", "地区": "华东", "重量克": 2000},
    {"明细编号": "D-0003", "箱号": "BOX-003", "地区": "华北", "重量克": 1500},
    {"明细编号": "D-0004", "箱号": " BOX-004 ", "地区": "华南", "重量克": 2500},
]


def _field_versions(detail: str, fields: dict[str, Any], version: int = 1) -> dict[str, str]:
    return {field: f"{detail}-{field}-v{version}" for field in fields}


def _input_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(_INPUTS, start=1):
        fields = {key: item[key] for key in ("箱号", "地区", "重量克")}
        rows.append(
            {
                "id": index,
                "status": "已发布",
                "pending": False,
                "abnormal": False,
                "输入版本": f"IN-{index:04d}",
                "明细编号": item["明细编号"],
                "数据批次": BATCH,
                "数据版本": 1,
                "版本序号": 1,
                "是否当前": True,
                **fields,
                "字段版本": _field_versions(item["明细编号"], fields),
                "创建时间": CREATED_AT,
            }
        )
    return rows


def _node_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "status": "已启用",
            "pending": False,
            "abnormal": False,
            "节点编号": "TRAN-KG",
            "节点名称": "克转公斤",
            "输入来源": "分拣明细",
            "输出目标": "重量公斤",
            "转换表达式": "重量克 / 1000",
            "执行顺序": "1",
            "依赖节点": "",
            "失败策略": "跳过",
            "节点状态": "已启用",
        },
        {
            "id": 2,
            "status": "已启用",
            "pending": False,
            "abnormal": False,
            "节点编号": "TRAN-REGION",
            "节点名称": "按地区汇总",
            "输入来源": "克转公斤",
            "输出目标": "地区重量合计",
            "转换表达式": "sum(重量公斤) by 地区",
            "执行顺序": "2",
            "依赖节点": "TRAN-KG",
            "失败策略": "跳过",
            "节点状态": "已启用",
        },
        {
            "id": 3,
            "status": "已启用",
            "pending": False,
            "abnormal": False,
            "节点编号": "TRAN-BOX",
            "节点名称": "箱号清洗",
            "输入来源": "分拣明细",
            "输出目标": "规范箱号",
            "转换表达式": "trim(箱号)",
            "执行顺序": "1",
            "依赖节点": "",
            "失败策略": "跳过",
            "节点状态": "已启用",
        },
    ]


PIPELINE_ROWS: dict[str, list[dict[str, Any]]] = {
    # 覆盖 seed.py 里的占位节点与占位血缘，换成真正能执行的示例链路。
    "transform": _node_rows(),
    "lineage": [],
    "transform_input": _input_rows(),
    "transform_batch": [
        {
            "id": 1,
            "批次编号": BATCH,
            "数据版本": 1,
            "说明": "首版分拣明细",
            "操作人": "值班管理员",
            "创建时间": CREATED_AT,
            "status": "已发布",
            "pending": False,
            "abnormal": False,
        }
    ],
    "transform_run": [],
    "transform_node_run": [],
    "transform_result": [],
}
