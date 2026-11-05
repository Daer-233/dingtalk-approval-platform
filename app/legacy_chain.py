"""Fail-closed forecast to legacy approvers_v2 translation (no network calls)."""
from __future__ import annotations

import hashlib
import json
from typing import Any

CHAIN_VERSION = 5
LEGACY_WARNING = "旧接口将按下列完整审批链发起，不复用模板审批流程；手写签名、表单操作权限等模板高级设置不支持。请核对所有固定节点、手选人员和抄送配置。"


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _order_forecast_path(nodes: list[dict[str, Any]], graph: Any = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Prove a unique full path; never presume an opaque edge ID is a node ID."""
    if not nodes:
        return nodes, ["预测未返回审批节点，无法确认完整审批链。"]
    ids = [node.get("activity_id") for node in nodes]
    if any(not key for key in ids) or len(set(ids)) != len(ids):
        return nodes, ["预测节点 ID 缺失或重复，无法确认完整审批链。"]
    if graph is None:
        graph = nodes[0].get("forecast_path")
    if graph is not None:
        if not isinstance(graph, list) or not graph:
            return nodes, ["完整预测路径格式缺失或无法识别，已阻止提交。"]
        graph_ids = [item.get("activityId") if isinstance(item, dict) else None for item in graph]
        if any(not isinstance(key, str) or not key for key in graph_ids) or len(set(graph_ids)) != len(graph_ids):
            return nodes, ["完整预测路径的节点 ID 缺失或重复，已阻止提交。"]
        known = set(graph_ids)
        if not set(ids).issubset(known):
            return nodes, ["完整预测路径未覆盖所有业务节点，已阻止提交。"]
        edges, incoming = {}, {key: 0 for key in graph_ids}
        for item in graph:
            source, out = item["activityId"], item.get("outId")
            if out in (None, ""):
                continue
            if not isinstance(out, str):
                return nodes, ["预测存在并行或无法识别的出边，已阻止提交。"]
            candidates = [target for target in graph_ids if out == target or out == f"line-random-{source}-{target}"]
            if len(candidates) != 1:
                return nodes, [f"预测路径出边无法确认来源和目标（{source}），请核对模板；未猜测节点顺序。"]
            target = candidates[0]
            edges[source] = target
            incoming[target] += 1
        roots = [key for key, count in incoming.items() if count == 0]
        if len(roots) != 1 or any(count > 1 for count in incoming.values()):
            return nodes, ["预测包含并行、分支或多个起点，旧接口不能安全转换为完整顺序审批链。"]
        path, seen, current = [], set(), roots[0]
        while current is not None and current not in seen:
            path.append(current)
            seen.add(current)
            current = edges.get(current)
        if current is not None or seen != known:
            return nodes, ["预测路径有循环或未连接的节点，已阻止提交。"]
        # prevActivityId may describe a routing edge rather than the activated path.
        by_id = dict(zip(ids, nodes))
        return [by_id[key] for key in path if key in by_id], []
    by_id = dict(zip(ids, nodes))
    roots, children = [], {}
    for node in nodes:
        key, prev = node["activity_id"], node.get("prev_activity_id")
        if prev is None:
            return nodes, ["预测缺少前序节点信息，请重新检测审批路径。"]
        if prev == key or prev not in by_id:
            roots.append(node)
        else:
            children.setdefault(prev, []).append(node)
    if len(roots) != 1 or any(len(items) != 1 for items in children.values()):
        return nodes, ["预测包含并行、分支或多个起点，旧接口不能安全转换为完整顺序审批链。"]
    ordered, seen, current = [], set(), roots[0]
    while current and current["activity_id"] not in seen:
        ordered.append(current)
        seen.add(current["activity_id"])
        current = (children.get(current["activity_id"]) or [None])[0]
    if current or len(ordered) != len(nodes):
        return nodes, ["预测路径有循环或未连接的节点，已阻止提交。"]
    return ordered, []


def inspect_forecast_path(nodes: list[dict[str, Any]], graph: Any = None) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    """Separate proven connections from unresolved routing; never fill in missing edges.

    A connected business segment is diagnostic evidence, not proof that unknown
    conditional routes are safe to submit. Full graph validation remains required.
    """
    if graph is None and nodes:
        graph = nodes[0].get("forecast_path")
    ordered, errors = _order_forecast_path(nodes, graph)
    diagnostic: dict[str, Any] = {
        "status": "unconfirmed" if errors else "confirmed",
        "business_node_count": len(nodes), "confirmed_connections": [],
        "unresolved_connections": [], "business_connections": [],
        "order_confirmed": not errors,
    }
    if isinstance(graph, list) and graph and all(isinstance(item, dict) for item in graph):
        ids = [item.get("activityId") for item in graph]
        if all(isinstance(key, str) and key for key in ids) and len(set(ids)) == len(ids):
            business_ids = {node.get("activity_id") for node in nodes}
            for item in graph:
                source, out = item["activityId"], item.get("outId")
                if out in (None, ""):
                    continue
                targets = [key for key in ids if isinstance(out, str) and
                           (out == key or out == f"line-random-{source}-{key}")]
                if len(targets) == 1:
                    edge = {"source_activity_id": source, "target_activity_id": targets[0], "out_id": out}
                    diagnostic["confirmed_connections"].append(edge)
                    if source in business_ids and targets[0] in business_ids:
                        diagnostic["business_connections"].append(edge)
                else:
                    diagnostic["unresolved_connections"].append({
                        "source_activity_id": source, "out_id": out,
                        "source_kind": "business" if source in business_ids else "routing",
                        "reason": "parallel_or_unknown_edge" if not isinstance(out, str) else "target_not_returned",
                    })
            # Refine only the opaque-edge diagnosis; do not hide structural errors.
            if errors and "出边无法确认来源和目标" in errors[0]:
                count = len(diagnostic["unresolved_connections"])
                errors = [f"已识别 {len(nodes)} 个业务节点，但预测中的 {count} 处路由/节点连接未返回可确认的目标。"
                          "这是流程路径信息待确认，不代表抄送人选择错误；已阻止提交，未猜测顺序或跳过节点。"]
    return ordered, errors, diagnostic


def order_forecast_path(nodes: list[dict[str, Any]], graph: Any = None) -> tuple[list[dict[str, Any]], list[str]]:
    ordered, errors, _ = inspect_forecast_path(nodes, graph)
    return ordered, errors


def _template_execution_plan(nodes: list[dict[str, Any]], selections: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
    """Leave handler semantics to DingTalk; never serialize handlers as approvers.

    The legacy create contract has no verified actor-key binding. A manual node
    therefore blocks this mode, even when its selected users are valid. Full-path
    and metadata checks remain in place; this is not a compatibility bypass.
    """
    ordered, path_errors, diagnostic = inspect_forecast_path(nodes)
    errors = [*path_errors, *[error for node in nodes for error in node.get("path_errors", [])]]
    if any(node.get("metadata_version") != CHAIN_VERSION for node in nodes):
        errors.append("旧预测缺少当前模板执行策略，请重新检测流程并生成预览。")
    manual_nodes = [node for node in nodes if node.get("kind") == "manual"]
    if manual_nodes:
        errors.append("保留原模板流程时，旧版创建接口尚未验证手选节点的人员绑定方式；"
                      "已保留本次选择，但不能用 approvers_v2 重建审批链，也不能遗漏所选人员后发起。")
    previews = []
    for node in ordered:
        name = str(node.get("name") or "未命名节点")
        role, manual = node.get("actor_type"), node.get("kind") == "manual"
        users = selections.get(node.get("actor_key", ""), []) if manual else node.get("fixed_actioners", [])
        previews.append({
            "activity_id": node.get("activity_id", ""), "name": name,
            "source": "manual" if manual else "fixed", "role": role, "users": users,
            "method": node.get("approval_method", ""), "skipped": False,
            "legacy_steps": [], "cc_position": "", "execution_owner": "dingtalk",
        })
        if role not in {"approver", "notifier", "audit"}:
            errors.append(f"{name} 的角色 {role or '未返回'} 无法识别，不能确认模板执行方式。")
        if node.get("activity_type") not in {"target_approval", "target_select", "target_managers_dept", "target_label"}:
            errors.append(f"{name} 的节点类型无法识别，已阻止提交。")
        if role == "approver" and node.get("approval_type") not in {"MANUAL", "AUTO_AGREE", "AUTO_REFUSE"}:
            errors.append(f"{name} 的审批行为未由接口明确确认，已阻止提交。")
        # A recognized automatic node may legitimately have no human actioners.
        automatic = role == "approver" and node.get("approval_type") in {"AUTO_AGREE", "AUTO_REFUSE"}
        if not manual and not automatic:
            ids = [str(user.get("user_id") or "").strip() for user in users]
            if not users or any(not uid or ',' in uid for uid in ids) or len(ids) != len(set(ids)):
                errors.append(f"{name} 的人员未解析、缺少 UserId 或重复，请重新核对。")
            if not node.get("actioners_complete", False):
                errors.append(f"{name} 的固定人员解析不完整，不能遗漏后继续发起。")
    result = {
        "mode": "template", "preserves_template": True, "execution_owner": "dingtalk",
        "manual_binding_required": bool(manual_nodes), "manual_binding_supported": False,
        "handlers": [node["activity_id"] for node in previews if node["role"] == "audit"],
        "approvers_v2": [], "cc_list": "", "cc_position": "", "nodes": previews,
        "errors": list(dict.fromkeys(errors)), "path_diagnostics": diagnostic,
        "warnings": ["保留钉钉原模板流程；平台只发起申请，不跳过办理节点、不代为办理，后续办理由钉钉按模板流转。"],
        "version": CHAIN_VERSION,
    }
    result["issues"] = [{"code": "template_execution_constraint", "activity_id": "", "message": error} for error in result["errors"]]
    result["fingerprint"] = fingerprint({key: value for key, value in result.items() if key not in {"issues", "path_diagnostics"}})
    return result


def build_legacy_chain(nodes: list[dict[str, Any]], selections: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
    """Keep handler paths template-owned; other manual paths require chain consent."""
    if any(node.get("actor_type") == "audit" for node in nodes):
        return _template_execution_plan(nodes, selections)
    explicit = any(node.get("kind") == "manual" for node in nodes)
    result: dict[str, Any] = {
        "mode": "explicit_chain" if explicit else "template",
        "approvers_v2": [], "cc_list": "", "cc_position": "", "nodes": [],
        "errors": [], "warnings": [LEGACY_WARNING] if explicit else [], "version": CHAIN_VERSION,
    }
    errors = result["errors"]
    ordered, path_errors, path_diagnostics = inspect_forecast_path(nodes)
    result["path_diagnostics"] = path_diagnostics
    errors.extend(path_errors)
    errors.extend(dict.fromkeys(error for node in nodes for error in node.get("path_errors", [])))
    if any(node.get("metadata_version") != CHAIN_VERSION for node in nodes):
        errors.append("旧预测缺少完整审批角色信息，请重新检测流程并生成预览。")
    approval_indices = [i for i, node in enumerate(ordered) if node.get("actor_type") == "approver"
                        and not (node.get("kind") == "manual" and not node.get("required")
                                 and not selections.get(node.get("actor_key", "")))]
    cc_by_position: dict[str, list[str]] = {"START": [], "FINISH": []}
    actor_keys = [node.get("actor_key") for node in ordered if node.get("kind") == "manual"]
    if any(not key for key in actor_keys):
        errors.append("手选节点 actionerKey 缺失，无法绑定对应人员。")
    if len(actor_keys) != len(set(actor_keys)):
        errors.append("手选节点 actionerKey 重复，无法绑定对应人员。")
    for index, node in enumerate(ordered):
        name = str(node.get("name") or "未命名节点")
        role = node.get("actor_type")
        manual = node.get("kind") == "manual"
        users = selections.get(node.get("actor_key", ""), []) if manual else node.get("fixed_actioners", [])
        user_ids = [str(user.get("user_id") or "").strip() for user in users]
        preview = {
            "activity_id": node.get("activity_id", ""), "name": name,
            "source": "manual" if manual else "fixed", "role": role,
            "users": users, "method": node.get("approval_method", ""),
            "skipped": False, "legacy_steps": [], "cc_position": "",
        }
        result["nodes"].append(preview)
        if role not in {"approver", "notifier"}:
            errors.append(f"{name} 的角色 {role or '未返回'} 无法转换；办理人或未知角色不能当作审批人提交。")
            continue
        if node.get("activity_type") not in {"target_approval", "target_select", "target_managers_dept", "target_label"}:
            errors.append(f"{name} 的节点类型无法识别，已阻止提交。")
            continue
        if role == "approver" and node.get("approval_type") != "MANUAL":
            errors.append(f"{name} 为自动审批或未返回审批类型，不能改写为人工审批。")
            continue
        if manual and not users and not node.get("required"):
            preview["skipped"] = True
            continue
        if not users or any(not key or ',' in key for key in user_ids) or len(set(user_ids)) != len(user_ids):
            errors.append(f"{name} 的人员未解析、缺少 UserId 或重复，请重新核对。")
            continue
        if not manual and not node.get("actioners_complete", False):
            errors.append(f"{name} 的固定人员解析不完整，不能遗漏后继续发起。")
            continue
        if len(user_ids) > 20:
            errors.append(f"{name} 超出旧接口每节点最多 20 人的限制。")
            continue
        if role == "notifier":
            if not approval_indices:
                errors.append("没有可用审批节点，无法确定抄送位置。")
                continue
            position = "START" if index < approval_indices[0] else "FINISH" if index > approval_indices[-1] else ""
            if not position:
                errors.append(f"{name} 为流程中途抄送，旧接口无法保持此抄送时机。")
                continue
            preview["cc_position"] = position
            cc_by_position[position].extend(user_ids)
            continue
        method = str(node.get("approval_method") or "").upper()
        activation = str(node.get("activation_type") or "").upper()
        if method not in {"ONE_BY_ONE", "AND", "OR"} or activation not in {"ONE_BY_ONE", "ALL"}:
            errors.append(f"{name} 的审批方式或激活方式无法确认，不能猜测会签/或签。")
            continue
        if len(user_ids) > 1 and ((method == "ONE_BY_ONE") != (activation == "ONE_BY_ONE")):
            errors.append(f"{name} 的审批方式与人员激活方式不一致，旧接口无法等价转换。")
            continue
        if len(user_ids) == 1:
            steps = [{"task_action_type": "NONE", "user_ids": user_ids}]
        elif method == "ONE_BY_ONE":
            steps = [{"task_action_type": "NONE", "user_ids": [key]} for key in user_ids]
        else:
            steps = [{"task_action_type": method, "user_ids": user_ids}]
        preview["legacy_steps"] = steps
        result["approvers_v2"].extend(steps)
    if not result["approvers_v2"]:
        errors.append("完整审批链没有可提交的人工审批节点。")
    elif len(result["approvers_v2"]) > 20:
        errors.append("完整审批链超出旧接口最多 20 个审批步骤的限制。")
    start = list(dict.fromkeys(cc_by_position["START"]))
    finish = list(dict.fromkeys(cc_by_position["FINISH"]))
    if start and finish and set(start) != set(finish):
        errors.append("开始和结束的抄送人员不同，旧接口不能分别设置，已阻止提交。")
    else:
        cc = start or finish
        if len(cc) > 20:
            errors.append("抄送人员超出旧接口最多 20 人的限制。")
        result["cc_list"] = ",".join(cc)
        result["cc_position"] = "START_FINISH" if start and finish else "START" if start else "FINISH" if finish else ""
    result["errors"] = errors = list(dict.fromkeys(errors))
    result["issues"] = [{"code": "legacy_incompatible", "activity_id": next((node.get("activity_id", "") for node in ordered if error.startswith(str(node.get("name") or "未命名节点"))), ""), "message": error} for error in errors]
    result["fingerprint"] = fingerprint({
        "version": CHAIN_VERSION, "mode": result["mode"],
        "approvers_v2": result["approvers_v2"], "cc_list": result["cc_list"],
        "cc_position": result["cc_position"], "nodes": result["nodes"], "errors": errors,
    })
    return result

