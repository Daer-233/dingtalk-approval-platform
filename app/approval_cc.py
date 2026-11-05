"""Task-scoped CC defaults and shared compatibility, without network/DB side effects."""
from __future__ import annotations
from typing import Any
from .legacy_chain import build_legacy_chain
from .dingtalk import validate_node_actioners


def seed_cc_nodes(nodes: list[dict[str, Any]], snapshot: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    selections = {key: list(users) for key, users in previous.items()}
    defaults = snapshot.get("users") or []
    for node in nodes:
        if node.get("kind") != "manual" or node.get("actor_type") != "notifier":
            continue
        key = node.get("actor_key")
        if not key:
            continue
        template = [{**user, "source": "template"} for user in node.get("fixed_actioners", [])]
        locked = [user["user_id"] for user in template]
        node["template_default_user_ids"] = locked
        node["template_default_users"] = template
        # Do not overwrite explicit per-group choices on a normal reforecast.
        local = selections[key] if key in selections else [{**user, "source": "local_default"} for user in defaults]
        combined = {user["user_id"]: user for user in template}
        for user in local:
            if user.get("user_id") not in combined:
                combined[user["user_id"]] = user
        selections[key] = list(combined.values())
        node["cc_default_errors"] = []
        if node.get("selection_range_kind") == "unsupported":
            node["cc_default_errors"].append(f"{node['name']} 的模板抄送范围无法识别，未忽略预填人员；请核对模板。")
        elif not (node.get("template_allowed_user_ids") or node.get("selection_range_kind") == "users" and node.get("selection_range_source") != "local"):
            # Only authorized saved defaults/local candidates and template users;
            # no arbitrary client IDs, and never widen an explicit template limit.
            allowed = list(dict.fromkeys([*node.get("allowed_user_ids", []), *locked, *[u["user_id"] for u in defaults]]))
            node.update(allowed_user_ids=allowed, selection_range_restricted=bool(allowed),
                        selection_range_resolvable=bool(allowed), selection_range_source="local",
                        template_allowed_user_ids=[], selection_range_kind="users" if allowed else "missing")
    return selections


def compatibility(nodes: list[dict[str, Any]], selections: dict[str, Any]) -> dict[str, Any]:
    chain = build_legacy_chain(nodes, selections)
    errors = list(dict.fromkeys([*chain["errors"], *validate_node_actioners(nodes, selections),
                                *[error for node in nodes for error in node.get("cc_default_errors", [])]]))
    issues = []
    for error in errors:
        matches = [n for n in nodes if error.startswith(str(n.get("name") or "审批节点") + " ")]
        node = matches[0] if len(matches) == 1 else None
        if "旧版创建接口尚未验证手选节点" in error: code = "template_manual_binding_unverified"
        elif "角色" in error: code = "unsupported_role"
        elif "自动审批" in error: code = "automatic_behavior"
        elif "路径" in error or "起点" in error or "前序" in error or "路由" in error: code = "path_unconfirmed"
        elif "范围" in error: code = "selection_range"
        elif "人员" in error or "必选" in error or "UserId" in error: code = "actioners_unconfirmed"
        else: code = "legacy_constraint"
        issue = {"code": code, "activity_id": node.get("activity_id", "") if node else "", "message": error, "scope": "node" if node else "workflow"}
        if not any(existing["code"] == code and existing["activity_id"] == issue["activity_id"] for existing in issues):
            issues.append(issue)
    errors = [issue["message"] for issue in issues]
    global_issues = [issue for issue in issues if not issue["activity_id"]]
    for node in nodes:
        # A path-level blocker does not make each person's selection incompatible.
        own = [issue for issue in issues if issue["activity_id"] == node.get("activity_id") and issue["activity_id"]]
        node["compatibility"] = {
            "status": "incompatible" if own else "verified" if errors else "submittable",
            "issues": own, "workflow_blocked": bool(errors),
            "execution_owner": "dingtalk" if chain.get("preserves_template") else "platform_chain",
            "preserves_template": bool(chain.get("preserves_template")),
            "automatic_behavior": "接口未确认" if node.get("approval_type") == "MANUAL" else node.get("approval_type") or "接口未确认",
        }
    return {"status": "incompatible" if errors else "submittable", "issues": issues,
            "global_issues": global_issues, "errors": errors, "chain": chain,
            "path_diagnostics": chain["path_diagnostics"]}
