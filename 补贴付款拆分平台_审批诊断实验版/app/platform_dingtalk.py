"""LAN directory and presentation features; modern create/upload code stays inherited."""
from __future__ import annotations
from collections import deque
import threading
import time
from typing import Any
from .dingtalk import (DingTalkClient as BaseDingTalkClient, DingTalkConfig, DingTalkError,
    OAPI_BASE, _strings_from_range, _forecast_actioners,
    validate_node_actioners as base_validate_node_actioners)
from .platform_paths import CHAIN_VERSION, inspect_forecast_path

class PlatformDingTalkClient(BaseDingTalkClient):
    def __init__(self, config=None):
        super().__init__(config)
        self._directory_lock = threading.RLock()
        self._directory_cache = None
        self._directory_cached_at = 0.0

    def reload_config(self):
        with self._directory_lock:
            result = super().reload_config()
            self._directory_cache = None
            self._directory_cached_at = 0.0
            return result

    def _load_directory_users(self) -> list[dict[str, str]]:
            """Read the authorized enterprise directory independently of applicant dept."""
            token = self.access_token()
            departments = deque(["1"])
            seen_depts: set[str] = set()
            users: dict[str, dict[str, str]] = {}
            while departments:
                dept = departments.popleft()
                if dept in seen_depts:
                    continue
                seen_depts.add(dept)
                child = self._request(
                    "POST", f"{OAPI_BASE}/topapi/v2/department/listsubid", token=False,
                    retries=2, params={"access_token": token}, json={"dept_id": int(dept)},
                )
                child_result = child.get("result")
                if not isinstance(child_result, dict) or not isinstance(child_result.get("dept_id_list"), list):
                    raise DingTalkError("钉钉通讯录子部门响应格式异常，请重试。")
                departments.extend(str(value) for value in child_result["dept_id_list"])
                cursor = 0
                while True:
                    page = self._request(
                        "POST", f"{OAPI_BASE}/topapi/v2/user/list", token=False,
                        retries=2, params={"access_token": token},
                        json={"dept_id": int(dept), "cursor": cursor, "size": 100},
                    )
                    page_result = page.get("result")
                    if not isinstance(page_result, dict) or not isinstance(page_result.get("list"), list):
                        raise DingTalkError("钉钉通讯录人员响应格式异常，请重试。")
                    for user in page_result["list"]:
                        if not isinstance(user, dict):
                            raise DingTalkError("钉钉通讯录人员响应格式异常，请重试。")
                        user_id = str(user.get("userid") or "").strip()
                        if user_id and user_id not in users:
                            users[user_id] = {
                                "user_id": user_id,
                                "name": str(user.get("name") or user_id),
                                "dept_id": dept,
                            }
                    if not page_result.get("has_more"):
                        break
                    try:
                        next_cursor = int(page_result["next_cursor"])
                    except (KeyError, TypeError, ValueError) as exc:
                        raise DingTalkError("钉钉通讯录分页信息异常，请重试。") from exc
                    if next_cursor <= cursor:
                        raise DingTalkError("钉钉通讯录分页游标未前进，请重试。")
                    cursor = next_cursor
            return list(users.values())

    def list_users(self, keyword: str = "", limit: int | None = None) -> list[dict[str, str]]:
            """Search all authorized departments; cache only complete successful reads."""
            if limit is not None and limit <= 0:
                return []
            with self._directory_lock:
                if self._directory_cache is None or time.monotonic() - self._directory_cached_at >= 120:
                    # Never return or cache a partial directory after a permission/network error.
                    self._directory_cache = None
                    self._directory_cached_at = 0.0
                    self._directory_cache = self._load_directory_users()
                    self._directory_cached_at = time.monotonic()
                query = keyword.strip().casefold()
                result = [dict(user) for user in self._directory_cache
                          if not query or query in user["name"].casefold() or query in user["user_id"].casefold()]
            return result if limit is None else result[:limit]

def normalize_forecast(forecast: dict[str, Any]) -> dict[str, Any]:
    """Convert DingTalk's variable-shaped forecast payload to a stable task format."""
    rules = forecast.get("workflowActivityRules") or []
    shape_errors = []
    if not isinstance(rules, list):
        rules = []
        shape_errors.append("预测节点列表格式无法识别，已阻止提交。")
    nodes: list[dict[str, Any]] = []
    for rule in rules:
        if not isinstance(rule, dict):
            shape_errors.append("预测包含无法识别的节点，不能省略后发起。")
            continue
        workflow_actor = rule.get("workflowActor") or {}
        if not isinstance(workflow_actor, dict):
            shape_errors.append("预测节点角色格式无法识别，已阻止提交。")
            workflow_actor = {}
        activity_type = str(rule.get("activityType") or "")
        target_select = bool(rule.get("isTargetSelect")) or activity_type == "target_select"
        actor_key = str(workflow_actor.get("actorKey") or rule.get("actorKey") or rule.get("actionerKey") or "").strip()
        raw_range = workflow_actor.get("actorSelectionRange") or rule.get("actorSelectionRange") or {}
        restricted = bool(raw_range)
        allowed_user_ids = sorted(_strings_from_range(raw_range))
        nodes.append({
            "activity_id": str(rule.get("activityId") or "").strip(),
            "prev_activity_id": str(rule["prevActivityId"] or "").strip() if "prevActivityId" in rule else None,
            "metadata_version": CHAIN_VERSION,
            "actor_type": str(workflow_actor.get("actorType") or ""),
            "approval_type": str(workflow_actor.get("approvalType") or ""),
            "actioners_complete": isinstance(rule.get("activityActioners"), list) and len(_forecast_actioners(rule.get("activityActioners"))) == len(rule["activityActioners"]),
            "name": str(rule.get("activityName") or "审批节点").strip(),
            "kind": "manual" if target_select else "fixed",
            "activity_type": activity_type,
            "actor_key": actor_key,
            "required": bool(workflow_actor.get("required")) if target_select else False,
            "allowed_multi": bool(workflow_actor.get("allowedMulti")),
            "approval_method": str(workflow_actor.get("approvalMethod") or ""),
            "activation_type": str(workflow_actor.get("actorActivateType") or ""),
            "fixed_actioners": _forecast_actioners(rule.get("activityActioners")),
            "allowed_user_ids": allowed_user_ids,
            "selection_range_kind": "users" if allowed_user_ids else "unsupported" if restricted else "missing",
            "selection_range_restricted": restricted,
            "selection_range_resolvable": restricted and bool(allowed_user_ids),
        })
    graph = forecast.get("workflowForecastNodes") if "workflowForecastNodes" in forecast else None
    for node in nodes:
        if graph is not None:
            node["forecast_path"] = graph
    nodes, path_errors, path_diagnostics = inspect_forecast_path(nodes, graph)
    path_errors = list(dict.fromkeys(shape_errors + path_errors))
    for node in nodes:
        node["path_errors"] = path_errors
    return {
        "workflowActivityRules": rules,
        "workflowForecastNodes": graph,
        "path_diagnostics": path_diagnostics,
        "metadata_version": CHAIN_VERSION,
        "path_errors": path_errors,
        "success": forecast.get("isForecastSuccess") is True and not shape_errors,
        "static_workflow": bool(forecast.get("isStaticWorkflow")),
        "nodes": nodes,
    }

def validate_node_actioners(nodes, selections):
    errors = base_validate_node_actioners(nodes, selections)
    for node in nodes:
        locked = set(node.get("template_default_user_ids") or [])
        users = selections.get(node.get("actor_key"), [])
        chosen = {str(u.get("user_id") or "") for u in users if isinstance(u, dict)} if isinstance(users, list) else set()
        if locked and not locked.issubset(chosen):
            errors.append(f"{node.get('name') or '抄送节点'} 的模板默认抄送人不可删除")
    return list(dict.fromkeys(errors))
