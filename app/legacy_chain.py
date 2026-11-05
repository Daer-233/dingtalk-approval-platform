"""Fail-closed forecast to legacy approvers_v2 translation (no network calls)."""
from __future__ import annotations

import hashlib
import json
from typing import Any

CHAIN_VERSION = 2
LEGACY_WARNING = "旧接口将按下列完整审批链发起，不复用模板审批流程；手写签名、表单操作权限等模板高级设置不支持。请核对所有固定节点、手选人员和抄送配置。"


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def order_forecast_path(nodes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """prevActivityId must establish one complete linear path; never flatten branches."""
    if not nodes:
        return nodes, ["预测未返回审批节点，无法确认完整审批链。"]
    ids = [node.get("activity_id") for node in nodes]
    if any(not key for key in ids) or len(set(ids)) != len(ids):
        return nodes, ["预测节点 ID 缺失或重复，无法确认完整审批链。"]
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


def build_legacy_chain(nodes: list[dict[str, Any]], selections: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
    """Manual paths require explicit full-chain consent; fixed paths reuse template."""
    explicit = any(node.get("kind") == "manual" for node in nodes)
    result: dict[str, Any] = {
        "mode": "explicit_chain" if explicit else "template",
        "approvers_v2": [], "cc_list": "", "cc_position": "", "nodes": [],
        "errors": [], "warnings": [LEGACY_WARNING] if explicit else [], "version": CHAIN_VERSION,
    }
    errors = result["errors"]
    if not explicit:
        result["fingerprint"] = fingerprint(result)
        return result
    ordered, path_errors = order_forecast_path(nodes)
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
        if node.get("activity_type") not in {"target_approval", "target_select"}:
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
    result["fingerprint"] = fingerprint({
        "version": CHAIN_VERSION, "mode": result["mode"],
        "approvers_v2": result["approvers_v2"], "cc_list": result["cc_list"],
        "cc_position": result["cc_position"], "nodes": result["nodes"], "errors": errors,
    })
    return result

