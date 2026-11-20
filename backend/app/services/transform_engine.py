"""转换执行引擎：按节点依赖执行表达式，产出带输入版本的结果与血缘关系。

这一层不碰内存仓库，只接收“节点定义 + 运行开始时的输入版本快照 + 上一次运行”，
把要落库的结果、节点执行记录和血缘关系算出来，方便被服务层和测试直接调用。

几条硬规则：

- 节点按 ``依赖节点`` 做拓扑排序，依赖缺失或成环直接拒绝执行；
- 前置节点没有可用结果时，后继节点不执行（阻塞），绝不拿未确认结果顶替；
- 单个节点按行产出结果，行上记录它读取的每个字段版本；
- 上次运行中读取字段版本完全没变、且上游也沿用的节点，结果原样保留。
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from typing import Any

_MISSING = object()
_DEP_SPLIT = re.compile(r"[,，、;；\s]+")
_AGGREGATE = re.compile(
    r"^\s*(sum|count|avg|min|max)\s*\(\s*([^()]*?)\s*\)\s+by\s+(.+?)\s*$",
    re.IGNORECASE,
)
CONFIRMED = "已确认"
UNCONFIRMED = "未确认"
USABLE_STATUSES = {"成功", "部分失败", "复用"}

_FUNCTIONS = {
    "trim": lambda value: value.strip() if isinstance(value, str) else value,
    "upper": lambda value: value.upper() if isinstance(value, str) else value,
    "lower": lambda value: value.lower() if isinstance(value, str) else value,
    "abs": abs,
    "len": len,
    "round": round,
}


class ExpressionError(ValueError):
    """表达式无法求值：字段缺失、类型不支持或除数为零。"""


class PipelineConfigError(ValueError):
    """节点配置不成立：依赖缺失、循环依赖或产出无法合并。"""


def parse_dependencies(raw: Any) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    ordered: list[str] = []
    for item in _DEP_SPLIT.split(text):
        item = item.strip()
        if item and item not in ordered:
            ordered.append(item)
    return ordered


def parse_aggregate(expression: str) -> tuple[str, str, str] | None:
    match = _AGGREGATE.match(str(expression or ""))
    if not match:
        return None
    func = match.group(1).lower()
    field = match.group(2).strip() or "*"
    group_field = match.group(3).strip()
    return func, field, group_field


def _parse(expression: str) -> ast.Expression:
    try:
        return ast.parse(str(expression or ""), mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"表达式无法解析：{exc.msg}") from exc


def referenced_fields(expression: str) -> list[str]:
    """取出表达式直接读取的字段，函数名不算字段。"""
    aggregate = parse_aggregate(expression)
    if aggregate:
        _, field, group_field = aggregate
        fields = [group_field] if field == "*" else [field, group_field]
        return list(dict.fromkeys(fields))
    fields: list[str] = []
    for node in ast.walk(_parse(expression)):
        if isinstance(node, ast.Name) and node.id not in _FUNCTIONS and node.id not in fields:
            fields.append(node.id)
    return fields


def validate_expression(expression: str) -> list[str]:
    """校验表达式语法并返回它依赖的字段。"""
    if not str(expression or "").strip():
        raise ExpressionError("转换表达式为空")
    aggregate = parse_aggregate(expression)
    if aggregate:
        _, field, group_field = aggregate
        if not group_field:
            raise ExpressionError("聚合表达式缺少分组字段")
        return referenced_fields(expression)
    fields = referenced_fields(expression)
    if not fields:
        raise ExpressionError("表达式没有读取任何字段")
    return fields


def _require_number(value: Any, action: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExpressionError(f"{action}需要数值，实际为「{value}」")
    return value


def _eval(node: ast.AST, row: dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval(node.body, row)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, str)):
            return node.value
        raise ExpressionError("表达式只能使用数字、文本或字段")
    if isinstance(node, ast.Name):
        if node.id in _FUNCTIONS:
            raise ExpressionError(f"函数「{node.id}」不能单独使用")
        if node.id not in row or row[node.id] is _MISSING or row[node.id] is None:
            raise ExpressionError(f"字段「{node.id}」不可用")
        return row[node.id]
    if isinstance(node, ast.UnaryOp):
        value = _eval(node.operand, row)
        if isinstance(node.op, ast.USub):
            return -_require_number(value, "一元负号")
        if isinstance(node.op, ast.UAdd):
            return +_require_number(value, "一元正号")
        raise ExpressionError("表达式包含不支持的一元运算")
    if isinstance(node, ast.BinOp):
        left = _eval(node.left, row)
        right = _eval(node.right, row)
        if isinstance(node.op, ast.Add):
            if isinstance(left, str) or isinstance(right, str):
                return f"{left}{right}"
            return _require_number(left, "加法") + _require_number(right, "加法")
        if isinstance(node.op, ast.Sub):
            return _require_number(left, "减法") - _require_number(right, "减法")
        if isinstance(node.op, ast.Mult):
            return _require_number(left, "乘法") * _require_number(right, "乘法")
        if isinstance(node.op, ast.Div):
            divisor = _require_number(right, "除法")
            if divisor == 0:
                raise ExpressionError("除数为 0，无法完成转换")
            return _require_number(left, "除法") / divisor
        raise ExpressionError("表达式包含不支持的运算符")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            raise ExpressionError("表达式只能调用 trim、upper、lower、abs、len、round")
        if node.keywords:
            raise ExpressionError("表达式函数不支持关键字参数")
        args = [_eval(arg, row) for arg in node.args]
        try:
            return _FUNCTIONS[node.func.id](*args)
        except (TypeError, ValueError) as exc:
            raise ExpressionError(f"函数「{node.func.id}」参数不合法：{exc}") from exc
    raise ExpressionError("表达式包含不支持的语法，只允许字段、四则运算和内置清洗函数")


def evaluate(expression: str, row: dict[str, Any]) -> Any:
    if parse_aggregate(expression):
        raise ExpressionError("聚合表达式不能按单行求值")
    return _eval(_parse(expression), row)


def normalize_value(value: Any) -> Any:
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ExpressionError("表达式产生了非法数值")
        rounded = round(value, 6)
        return int(rounded) if rounded.is_integer() else rounded
    return value


def _sort_key(node: dict[str, Any]) -> tuple[int, str]:
    raw = str(node.get("执行顺序") or "").strip()
    try:
        order = int(raw)
    except ValueError:
        order = 999
    return order, str(node.get("节点编号") or "")


def topological_order(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_code = {str(node.get("节点编号")): node for node in nodes}
    if len(by_code) != len(nodes):
        raise PipelineConfigError("节点编号重复，无法确定执行顺序")
    dependencies = {
        code: parse_dependencies(node.get("依赖节点")) for code, node in by_code.items()
    }
    for code, deps in dependencies.items():
        for dep in deps:
            if dep not in by_code:
                raise PipelineConfigError(f"节点 {code} 依赖的节点 {dep} 不存在")
    pending = {code: len(deps) for code, deps in dependencies.items()}
    children: dict[str, list[str]] = {code: [] for code in by_code}
    for code, deps in dependencies.items():
        for dep in deps:
            children[dep].append(code)
    ready = sorted(
        (code for code, count in pending.items() if count == 0),
        key=lambda code: _sort_key(by_code[code]),
    )
    ordered: list[dict[str, Any]] = []
    while ready:
        code = ready.pop(0)
        ordered.append(by_code[code])
        for child in children[code]:
            pending[child] -= 1
            if pending[child] == 0:
                ready.append(child)
        ready.sort(key=lambda item: _sort_key(by_code[item]))
    if len(ordered) != len(nodes):
        raise PipelineConfigError("节点依赖存在循环，无法确定执行顺序")
    return ordered


def _unique_join(values: list[Any]) -> str:
    unique: list[str] = []
    for value in values:
        if value is None:
            continue
        for text in str(value).split("|"):
            text = text.strip()
            if text and text != "None" and text not in unique:
                unique.append(text)
    return "|".join(unique)


def _fingerprint(value: Any) -> str:
    """节点读取内容的指纹：输入字段版本 + 表达式 + 输出目标。"""
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def execute_pipeline(
    *,
    nodes: list[dict[str, Any]],
    snapshot: list[dict[str, Any]],
    run_number: str,
    batch: str,
    data_version: int = 1,
    result_numbers: list[str] | None = None,
    relation_version: str = "v1",
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """按依赖顺序执行整条转换管道。

    ``snapshot`` 是运行开始时固定下来的输入版本行；``previous`` 传入上一次运行时，
    只复用“所读字段版本完全没变且上游也复用”的节点结果。``result_numbers`` 是全局
    已占用的结果编号，会被就地追加。
    """
    ordered = topological_order(nodes)
    node_by_code = {str(node["节点编号"]): node for node in ordered}
    pool = result_numbers if result_numbers is not None else []
    previous_nodes = {
        str(row.get("节点编号")): row for row in (previous or {}).get("node_runs", [])
    }
    previous_results = {
        str(row.get("结果编号")): row for row in (previous or {}).get("results", [])
    }

    state: dict[str, dict[str, Any]] = {}
    node_runs: list[dict[str, Any]] = []
    new_results: list[dict[str, Any]] = []
    lineage: list[dict[str, Any]] = []
    failed_nodes: list[str] = []
    blocked_nodes: list[str] = []
    reused_nodes: list[str] = []
    next_seq = len(pool) + 1

    def new_number() -> str:
        nonlocal next_seq
        number = f"RES-{next_seq:04d}"
        while number in pool:
            next_seq += 1
            number = f"RES-{next_seq:04d}"
        next_seq += 1
        pool.append(number)
        return number

    for node in ordered:
        code = str(node["节点编号"])
        node_name = str(node.get("节点名称") or code)
        expression = str(node.get("转换表达式") or "")
        output_field = str(node.get("输出目标") or "")
        dependencies = parse_dependencies(node.get("依赖节点"))

        blocked_by = [
            dep
            for dep in dependencies
            if dep not in state or state[dep]["status"] not in USABLE_STATUSES
        ]
        if blocked_by:
            blocked_nodes.append(code)
            state[code] = {"status": "阻塞", "results": []}
            node_runs.append(
                {
                    "运行编号": run_number,
                    "数据批次": batch,
                    "节点编号": code,
                    "节点名称": node_name,
                    "执行状态": "阻塞",
                    "结果数量": 0,
                    "结果编号": "",
                    "失败节点": "|".join(blocked_by),
                    "失败原因": f"前置节点 {'、'.join(blocked_by)} 未确认，禁止使用未确认结果",
                    "复用运行编号": "",
                    "输入字段版本": {},
                    "输入版本": "",
                }
            )
            continue

        try:
            if not output_field:
                raise PipelineConfigError("节点未配置输出目标")
            aggregate = parse_aggregate(expression)
            fields = validate_expression(expression)
        except (ExpressionError, PipelineConfigError) as exc:
            failed_nodes.append(code)
            state[code] = {"status": "失败", "results": []}
            node_runs.append(
                {
                    "运行编号": run_number,
                    "数据批次": batch,
                    "节点编号": code,
                    "节点名称": node_name,
                    "执行状态": "失败",
                    "结果数量": 0,
                    "结果编号": "",
                    "失败节点": code,
                    "失败原因": str(exc),
                    "复用运行编号": "",
                    "输入字段版本": {},
                    "输入版本": "",
                }
            )
            continue

        dependency_outputs = {
            str(node_by_code[dep].get("输出目标") or ""): dep
            for dep in dependencies
            if str(node_by_code[dep].get("输出目标") or "")
        }
        upstream_fields = [field for field in fields if field in dependency_outputs]
        raw_fields = [field for field in fields if field not in dependency_outputs]
        if raw_fields and not snapshot:
            failed_nodes.append(code)
            state[code] = {"status": "失败", "results": []}
            node_runs.append(
                {
                    "运行编号": run_number,
                    "数据批次": batch,
                    "节点编号": code,
                    "节点名称": node_name,
                    "执行状态": "失败",
                    "结果数量": 0,
                    "结果编号": "",
                    "失败节点": code,
                    "失败原因": "没有可用的输入明细",
                    "复用运行编号": "",
                    "输入字段版本": {},
                    "输入版本": "",
                }
            )
            continue

        consumed = {
            str(row.get("明细编号") or ""): {
                field: (row.get("字段版本") or {}).get(field) for field in raw_fields
            }
            for row in snapshot
        }
        consumed_versions = _unique_join([row.get("输入版本") for row in snapshot])
        # 指纹覆盖“读取的字段版本 + 表达式 + 输出目标”：节点定义被改过也不能沿用旧结果。
        fingerprint = _fingerprint(
            {"consumed": consumed, "expression": expression, "output": output_field}
        )

        previous_node = previous_nodes.get(code)
        reused_numbers: list[str] = []
        if (
            previous
            and previous_node
            and previous_node.get("执行状态") in ("成功", "复用")
            and previous_node.get("节点指纹") == fingerprint
            and all(state[dep]["status"] == "复用" for dep in dependencies)
        ):
            reused_numbers = [
                num
                for num in str(previous_node.get("结果编号") or "").split("|")
                if num
            ]
        if reused_numbers and all(num in previous_results for num in reused_numbers):
            reused_nodes.append(code)
            reused_rows = [previous_results[num] for num in reused_numbers]
            pool.extend(num for num in reused_numbers if num not in pool)
            state[code] = {"status": "复用", "results": reused_rows}
            node_runs.append(
                {
                    "运行编号": run_number,
                    "数据批次": batch,
                    "节点编号": code,
                    "节点名称": node_name,
                    "执行状态": "复用",
                    "结果数量": len(reused_rows),
                    "结果编号": "|".join(reused_numbers),
                    "失败节点": "",
                    "失败原因": "",
                    "复用运行编号": str(previous_node.get("运行编号") or ""),
                    "输入字段版本": consumed,
                    "输入版本": consumed_versions,
                    "节点指纹": fingerprint,
                }
            )
            continue

        actions: list[dict[str, Any]] = []
        for row in snapshot:
            detail_id = str(row.get("明细编号") or "")
            context: dict[str, Any] = {key: value for key, value in row.items() if key != "字段版本"}
            used: dict[str, dict[str, Any]] = {}
            missing: list[tuple[str, str]] = []
            for field, dep in dependency_outputs.items():
                candidates = [
                    item
                    for item in state[dep]["results"]
                    if str(item.get("明细编号") or "") == detail_id
                ]
                confirmed = [item for item in candidates if item.get("确认状态") == CONFIRMED]
                if len(confirmed) == 1:
                    used[field] = confirmed[0]
                    context[field] = confirmed[0].get("输出值")
                elif len(confirmed) > 1:
                    missing.append((dep, f"依赖节点 {dep} 对明细 {detail_id} 产出了多条已确认结果"))
                else:
                    missing.append((dep, f"依赖节点 {dep} 没有明细 {detail_id} 的已确认结果"))
            actions.append(
                {
                    "row": row,
                    "detail_id": detail_id,
                    "context": context,
                    "used": used,
                    "missing": missing,
                }
            )

        def build_result(
            *,
            item: dict[str, Any] | None,
            detail_id: str,
            group_key: str,
            source_rows: list[dict[str, Any]],
            value: Any,
            failure: str,
            failed_node: str,
            confirmed: bool,
        ) -> dict[str, Any]:
            number = new_number()
            version_map: dict[str, str] = {}
            for field in raw_fields:
                version_map[field] = _unique_join(
                    [(row.get("字段版本") or {}).get(field) for row in source_rows]
                )
            input_versions = _unique_join([row.get("输入版本") for row in source_rows])
            used_items = list(item["used"].values()) if item else []
            for upstream in used_items:
                field = str(upstream.get("输出字段") or "")
                version_map[field] = _unique_join(
                    [version_map.get(field), upstream.get("输出字段版本")]
                )
                input_versions = _unique_join([input_versions, upstream.get("输入版本")])
            result = {
                "结果编号": number,
                "运行编号": run_number,
                "数据批次": batch,
                "数据版本": data_version,
                "节点编号": code,
                "节点名称": node_name,
                "明细编号": detail_id,
                "分组键": group_key,
                "输入版本": input_versions,
                "输入字段版本": version_map,
                "输出字段": output_field,
                "输出值": normalize_value(value) if confirmed else None,
                "输出字段版本": f"OUT-{number}" if confirmed else "",
                "确认状态": CONFIRMED if confirmed else UNCONFIRMED,
                "状态": "成功" if confirmed else "未确认",
                "失败节点": failed_node,
                "失败原因": failure,
                "复用自结果编号": "",
                "pending": False,
                "abnormal": not confirmed,
            }
            new_results.append(result)
            return result

        node_results: list[dict[str, Any]] = []
        node_failures: list[str] = []

        if aggregate:
            func, agg_field, group_field = aggregate
            groups: dict[str, list[dict[str, Any]]] = {}
            for item in actions:
                if item["missing"]:
                    node_failures.extend(message for _, message in item["missing"])
                    node_results.append(
                        build_result(
                            item=item,
                            detail_id=item["detail_id"],
                            group_key="",
                            source_rows=[item["row"]],
                            value=None,
                            failure="；".join(message for _, message in item["missing"]),
                            failed_node=item["missing"][0][0],
                            confirmed=False,
                        )
                    )
                    continue
                key = item["context"].get(group_field, _MISSING)
                if key is _MISSING or key in (None, ""):
                    message = f"明细 {item['detail_id']} 的分组字段「{group_field}」为空"
                    node_failures.append(message)
                    node_results.append(
                        build_result(
                            item=item,
                            detail_id=item["detail_id"],
                            group_key="",
                            source_rows=[item["row"]],
                            value=None,
                            failure=message,
                            failed_node=code,
                            confirmed=False,
                        )
                    )
                    continue
                groups.setdefault(str(key), []).append(item)

            for group_key in sorted(groups):
                members = groups[group_key]
                numbers: list[float] = []
                group_error = ""
                for member in members:
                    value = member["context"].get(agg_field, _MISSING)
                    if value is _MISSING or value is None:
                        group_error = f"字段「{agg_field}」不可用"
                        break
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        group_error = f"字段「{agg_field}」不是数值"
                        break
                    numbers.append(float(value))
                if not group_error and func in ("min", "max", "avg") and not numbers:
                    group_error = "分组内没有可汇总的数值"
                member_rows = [member["row"] for member in members]
                if group_error:
                    node_failures.append(f"分组 {group_key}：{group_error}")
                    node_results.append(
                        build_result(
                            item=None,
                            detail_id="",
                            group_key=group_key,
                            source_rows=member_rows,
                            value=None,
                            failure=group_error,
                            failed_node=code,
                            confirmed=False,
                        )
                    )
                    continue
                if func == "count":
                    value: Any = len(members)
                elif func == "sum":
                    value = sum(numbers)
                elif func == "avg":
                    value = sum(numbers) / len(numbers)
                elif func == "min":
                    value = min(numbers)
                else:
                    value = max(numbers)
                result = build_result(
                    item=None,
                    detail_id="",
                    group_key=group_key,
                    source_rows=member_rows,
                    value=value,
                    failure="",
                    failed_node="",
                    confirmed=True,
                )
                # 聚合结果的“我用了什么”要覆盖分组内全部上游结果。
                for member in members:
                    _merge_upstream(result, member["used"], raw_fields, member_rows)
                node_results.append(result)
        else:
            for item in actions:
                if item["missing"]:
                    node_failures.extend(message for _, message in item["missing"])
                    node_results.append(
                        build_result(
                            item=item,
                            detail_id=item["detail_id"],
                            group_key="",
                            source_rows=[item["row"]],
                            value=None,
                            failure="；".join(message for _, message in item["missing"]),
                            failed_node=item["missing"][0][0],
                            confirmed=False,
                        )
                    )
                    continue
                try:
                    value = evaluate(expression, item["context"])
                except (ExpressionError, TypeError, ValueError) as exc:
                    node_failures.append(f"明细 {item['detail_id']}：{exc}")
                    node_results.append(
                        build_result(
                            item=item,
                            detail_id=item["detail_id"],
                            group_key="",
                            source_rows=[item["row"]],
                            value=None,
                            failure=str(exc),
                            failed_node=code,
                            confirmed=False,
                        )
                    )
                    continue
                node_results.append(
                    build_result(
                        item=item,
                        detail_id=item["detail_id"],
                        group_key="",
                        source_rows=[item["row"]],
                        value=value,
                        failure="",
                        failed_node="",
                        confirmed=True,
                    )
                )

        confirmed_results = [row for row in node_results if row["确认状态"] == CONFIRMED]
        if node_failures and not confirmed_results:
            status = "失败"
            state[code] = {"status": "失败", "results": []}
        elif node_failures:
            status = "部分失败"
            state[code] = {"status": "部分失败", "results": confirmed_results}
        else:
            status = "成功"
            state[code] = {"status": "成功", "results": confirmed_results}
        if node_failures:
            failed_nodes.append(code)
        node_runs.append(
            {
                "运行编号": run_number,
                "数据批次": batch,
                "节点编号": code,
                "节点名称": node_name,
                "执行状态": status,
                "结果数量": len(node_results),
                "结果编号": "|".join(row["结果编号"] for row in node_results),
                "失败节点": code if node_failures else "",
                "失败原因": "；".join(node_failures[:3]) if node_failures else "",
                "复用运行编号": "",
                "输入字段版本": consumed,
                "输入版本": consumed_versions,
                "节点指纹": fingerprint,
            }
        )

    # 只为本次新产出的已确认结果写血缘；跨运行复用的结果沿用原血缘。
    for result in new_results:
        if result["确认状态"] != CONFIRMED:
            continue
        node = node_by_code[result["节点编号"]]
        expression = str(node.get("转换表达式") or "")
        aggregate = parse_aggregate(expression)
        dependencies = parse_dependencies(node.get("依赖节点"))
        dependency_nodes = {dep: node_by_code[dep] for dep in dependencies}
        output_to_dep = {
            str(node.get("输出目标") or ""): code
            for code, node in dependency_nodes.items()
        }
        fields = referenced_fields(expression)
        upstream_fields = [field for field in fields if field in output_to_dep]
        if aggregate:
            _, _, group_field = aggregate
            source_rows = [
                row for row in snapshot if str(row.get(group_field, "")) == result["分组键"]
            ]
        else:
            source_rows = [
                row
                for row in snapshot
                if str(row.get("明细编号") or "") == str(result.get("明细编号") or "")
            ]
        upstream_used: dict[str, list[dict[str, Any]]] = {}
        for field in upstream_fields:
            dep = output_to_dep[field]
            for row in source_rows:
                for item in state[dep]["results"]:
                    if str(item.get("明细编号") or "") == str(row.get("明细编号") or ""):
                        upstream_used.setdefault(field, []).append(item)
        for field in fields:
            if field in upstream_fields:
                items = upstream_used.get(field, [])
                versions = _unique_join([item.get("输出字段版本") for item in items])
                input_versions = _unique_join([item.get("输入版本") for item in items])
                upstream_node = output_to_dep[field]
                upstream_numbers = _unique_join([item.get("结果编号") for item in items])
            else:
                versions = _unique_join(
                    [(row.get("字段版本") or {}).get(field) for row in source_rows]
                )
                input_versions = _unique_join([row.get("输入版本") for row in source_rows])
                upstream_node = str(node.get("输入来源") or "")
                upstream_numbers = ""
            lineage.append(
                {
                    "结果编号": result["结果编号"],
                    "运行编号": run_number,
                    "数据批次": batch,
                    "数据版本": data_version,
                    "上游节点": upstream_node,
                    "下游节点": result["节点编号"],
                    "来源字段": field,
                    "目标字段": result["输出字段"],
                    "转换说明": expression,
                    "采集批次": batch,
                    "关系版本": relation_version,
                    "输入版本": input_versions,
                    "来源字段版本": versions,
                    "上游结果编号": upstream_numbers,
                    "输出字段版本": result["输出字段版本"],
                    "明细编号": result.get("明细编号") or "",
                    "分组键": result.get("分组键") or "",
                    "status": "已记录",
                    "pending": True,
                    "abnormal": False,
                }
            )

    successful = sum(1 for row in node_runs if row["执行状态"] in ("成功", "复用"))
    partial = sum(1 for row in node_runs if row["执行状态"] == "部分失败")
    if failed_nodes or blocked_nodes or partial:
        run_status = "部分失败" if successful else "失败"
    else:
        run_status = "成功"

    return {
        "run_status": run_status,
        "node_runs": node_runs,
        "results": new_results,
        "lineage": lineage,
        "failed_nodes": failed_nodes,
        "blocked_nodes": blocked_nodes,
        "reused_nodes": reused_nodes,
        "result_numbers": list(pool),
        "result_count": len(new_results),
    }


def _merge_upstream(
    result: dict[str, Any],
    used: dict[str, dict[str, Any]],
    raw_fields: list[str],
    source_rows: list[dict[str, Any]],
) -> None:
    """把分组内每条明细的上游结果并入聚合结果行的版本记录。"""
    versions = dict(result.get("输入字段版本") or {})
    input_versions = str(result.get("输入版本") or "")
    for field, item in used.items():
        versions[field] = _unique_join([versions.get(field), item.get("输出字段版本")])
        input_versions = _unique_join([input_versions, item.get("输入版本")])
    for field in raw_fields:
        versions.setdefault(
            field,
            _unique_join([(row.get("字段版本") or {}).get(field) for row in source_rows]),
        )
    result["输入字段版本"] = versions
    result["输入版本"] = _unique_join(
        [input_versions, *[row.get("输入版本") for row in source_rows]]
    )
