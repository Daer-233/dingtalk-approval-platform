"""LAN path display diagnostics; never constructs a submitted approval chain."""
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
