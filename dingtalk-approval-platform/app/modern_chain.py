"""New API template-mode review. Never override fixed/CC template nodes."""
from typing import Any
from .legacy_chain import fingerprint

def build_modern_template_chain(nodes: list[dict[str, Any]], selections: dict[str, Any]) -> dict[str, Any]:
    keys = [str(n.get("actor_key") or "") for n in nodes if n.get("kind") == "manual"]
    errors = []
    if not nodes:
        errors.append("预测未返回审批节点，请重新检测流程。")
    if any(not k for k in keys) or len(keys) != len(set(keys)):
        errors.append("手选节点 actionerKey 缺失或重复，无法绑定人员。")
    if len(keys) > 20:
        errors.append("手选节点超过新接口最多 20 个节点的限制。")
    result = {"mode": "modern_template", "version": 2, "errors": errors,
              "warnings": [], "nodes": [], "approvers_v2": [], "cc_list": "", "cc_position": ""}
    for n in nodes:
        manual = n.get("kind") == "manual"
        users = selections.get(n.get("actor_key", ""), []) if manual else n.get("fixed_actioners", [])
        result["nodes"].append({"activity_id": n.get("activity_id", ""), "name": n.get("name", ""),
            "role": n.get("actor_type", ""), "source": "manual" if manual else "fixed",
            "method": n.get("approval_method", ""), "users": users,
            "skipped": manual and not n.get("required") and not users})
    # Include the complete normalized prediction, including selection ranges and fixed actors.
    result["fingerprint"] = fingerprint({"version": result["version"], "mode": result["mode"], "nodes": nodes, "selections": selections})
    return result
