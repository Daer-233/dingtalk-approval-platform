"""Read-only diagnostics: no creation, attachment upload, task writes or SDK execution."""
from __future__ import annotations
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any
import json
import httpx
from .dingtalk import API_BASE, DingTalkClient

READ_ONLY_ROUTES = frozenset({
    ("POST", "/v1.0/oauth2/accessToken"),
    ("GET", "/v1.0/workflow/forms/schemas/processCodes"),
    ("POST", "/v1.0/workflow/processes/forecast"),
})

class ReadOnlyTransport(httpx.BaseTransport):
    """Fail BEFORE networking for every route other than auth/schema/forecast."""
    def __init__(self, inner: httpx.BaseTransport | None = None):
        self.inner = inner if inner is not None else httpx.HTTPTransport(retries=0)
        self.calls: list[dict[str, str]] = []
        self.blocked = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if (request.url.scheme != "https" or request.url.host != "api.dingtalk.com"
            or request.url.port not in (None, 443)
            or (request.method, request.url.path) not in READ_ONLY_ROUTES):
            self.blocked += 1
            raise RuntimeError("只读排查已拦截非白名单请求，未发送。")
        self.calls.append({"method": request.method, "path": request.url.path})
        return self.inner.handle_request(request)

    def close(self):
        self.inner.close()


def audit_request(schema: dict[str, Any], payload: dict[str, Any],
                  forecast: dict[str, Any] | None = None,
                  historical_nodes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Inspect shape and available evidence. Never claim server acceptance from a local pass."""
    findings: list[dict[str, str]] = []
    def add(code, level, field, message):
        item = {"code": code, "level": level, "field": field, "message": message}
        if item not in findings:
            findings.append(item)
    if schema.get("formCode") != payload.get("processCode"):
        add("PROCESS_MISMATCH", "error", "processCode", "请求模板与 Schema 不一致。")
    for key in ("originatorUserId", "processCode", "deptId", "microappAgentId"):
        if payload.get(key) in (None, ""):
            add("BASE_FIELD_MISSING", "error", key, "基础参数缺失。")
    for key in ("deptId", "microappAgentId"):
        if not isinstance(payload.get(key), int) or isinstance(payload.get(key), bool):
            add("BASE_FIELD_TYPE", "error", key, "官方 SDK 类型为整数。")
    if any(key in payload for key in ("approvers", "approvers_v2", "ccList", "ccPosition")):
        add("TEMPLATE_OVERRIDE", "error", "workflow", "检测到审批链或全局抄送覆盖，不能按保留模板模式验证。")
    if "requestId" in payload:
        add("UNDECLARED_REQUEST_ID", "error", "requestId", "官方新创建模型没有此字段；请求编号应仅本地保存。")
    fields = payload.get("formComponentValues")
    if not isinstance(fields, list):
        add("FORM_LIST", "error", "formComponentValues", "应为对象数组。")
        fields = []
    valid = [f for f in fields if isinstance(f, dict)]
    if len(valid) != len(fields):
        add("FORM_OBJECT", "error", "formComponentValues", "含非对象表单项。")
    names = [f.get("name") for f in valid]
    if any(v > 1 for v in Counter(str(n) for n in names).values()):
        add("DUPLICATE_FIELD", "error", "formComponentValues", "表单名称重复，不能确定服务器匹配结果。")
    by_name = {f.get("name"): f for f in valid}
    items = DingTalkClient._schema_items(schema)
    schema_by_name = {DingTalkClient._label(i): i for i in items}
    conditional = {t.get("fieldId") for i in items for rule in ((i.get("props") or {}).get("behaviorLinkage") or [])
                   if isinstance(rule, dict) for t in rule.get("targets", []) if isinstance(t, dict)}
    summary = []
    for name, item in schema_by_name.items():
        props = item.get("props") or {}
        kind = item.get("componentName") or item.get("componentType")
        present = name in by_name
        value = by_name.get(name, {}).get("value")
        summary.append({"name": name, "component_type": kind, "present": present,
                        "required": bool(props.get("required")), "has_value": value not in (None, "", "[]")})
        if kind == "CalculateField" and not present:
            add("CALCULATED_OMITTED", "unverified", name, "计算控件已省略；预测成功不能证明创建时能自动计算。")
        elif props.get("required") and value in (None, "", "[]"):
            controlled = (props.get("id") or item.get("id")) in conditional
            add("CONDITIONAL_REQUIRED" if controlled else "REQUIRED_EMPTY",
                "unverified" if controlled else "error", name,
                "该必填项受显隐联动影响，需要核对当前分支。" if controlled else "必填表单项缺失或为空。")
        if not present:
            continue
        if not isinstance(value, str):
            add("FORM_VALUE_TYPE", "error", name, "官方 SDK 的 value 为字符串，不能直接传数字/数组。")
            continue
        if kind == "DepartmentField" and value:
            try:
                parsed = json.loads(value)
            except ValueError:
                parsed = None
            if not isinstance(parsed, list):
                add("DEPARTMENT_SCALAR", "unverified", name, "历史请求部门控件为标量；需核实新版对该控件的值格式，暂不擅自转换。")
        if kind in ("TableField", "DDMultiSelectField", "DDAttachment") and value:
            try:
                parsed = json.loads(value)
            except ValueError:
                parsed = None
            if not isinstance(parsed, list):
                add("JSON_ARRAY", "error", name, "此控件应检查 JSON 数组格式。")
                continue
            if kind == "TableField":
                details = by_name[name].get("details")
                if details is None:
                    add("TABLE_DETAILS_OMITTED", "unverified", name,
                        "官方创建教程同时发送 value 与 details；此请求未传 details，建议单独验证。")
                elif (not isinstance(details, list) or any(
                    not isinstance(c, dict) or not isinstance(c.get("name"), str)
                    or not isinstance(c.get("value"), str) for c in details
                )):
                    add("TABLE_DETAILS_SHAPE", "error", name, "details 应为平铺的 name/value 字符串对象数组。")
                elif all(isinstance(row, list) and all(isinstance(c, dict) for c in row) for row in parsed):
                    expected = [{"name": c.get("name"), "value": c.get("value")} for row in parsed for c in row]
                    actual = [{"name": c.get("name"), "value": c.get("value")} for c in details]
                    if actual != expected:
                        add("TABLE_DETAILS_MISMATCH", "error", name, "details 与 value 中按行展开的单元格不一致。")
                children = {DingTalkClient._label(c): c for c in item.get("children", []) if isinstance(c, dict)}
                if not parsed:
                    add("EMPTY_TABLE", "unverified", name, "明细为空；需结合条件分支核实是否允许，不能从预测通过推断。")
                for row in parsed:
                    if not isinstance(row, list) or any(not isinstance(c, dict) for c in row):
                        add("TABLE_ROW", "error", name, "明细行应为单元格对象数组。")
                        continue
                    cells = {c.get("name"): c for c in row}
                    if len(cells) != len(row):
                        add("TABLE_CELL_DUPLICATE", "error", name, "明细行含重复单元格。")
                    for child_name, child in children.items():
                        cell_value = cells.get(child_name, {}).get("value")
                        if (child.get("props") or {}).get("required") and cell_value in (None, ""):
                            add("TABLE_REQUIRED", "error", name + "/" + child_name, "非空明细行缺少必填值。")
                        if cell_value is not None and not isinstance(cell_value, str):
                            add("TABLE_VALUE_TYPE", "error", name + "/" + child_name, "明细 value 需为字符串。")
                        if child.get("componentName") in ("MoneyField", "NumberField") and cell_value not in (None, ""):
                            try:
                                number = Decimal(str(cell_value))
                                if not number.is_finite(): raise InvalidOperation
                            except InvalidOperation:
                                add("TABLE_NUMBER", "error", name + "/" + child_name, "明细金额/数字不是有限数值。")
                if any(c.get("componentName") in ("DDSelectField", "MoneyField") for c in children.values()):
                    add("TABLE_COMPONENT_SUPPORT", "unverified", name, "含下拉/金额子控件；必须核实当前接口和模板的支持限制，不能当成普通文本处理。")
            elif kind == "DDAttachment":
                for entry in parsed:
                    if not isinstance(entry, dict) or any(not entry.get(k) for k in ("fileId", "spaceId", "fileName")):
                        add("ATTACHMENT_SHAPE", "error", name, "附件对象缺少文件、空间或名称。")
                add("ATTACHMENT_LIFECYCLE", "unverified", name, "本轮不上传或读取附件；仅核对结构，无法验证旧 fileId 是否仍有效及归属。")
        if kind == "DDSelectField" and value:
            options = []
            for raw in props.get("options", []):
                try: opt = json.loads(raw) if isinstance(raw, str) else raw
                except ValueError: opt = raw
                options.append(str((opt.get("value") or opt.get("label")) if isinstance(opt, dict) else opt))
            if options and value not in options:
                add("OPTION_MISMATCH", "error", name, "请求值不在当前 Schema 的下拉选项中。")
    for field in valid:
        if field.get("name") not in schema_by_name:
            add("UNKNOWN_FIELD", "error", str(field.get("name") or ""), "请求字段不在当前 Schema 中。")
    bindings = payload.get("targetSelectActioners", [])
    if not isinstance(bindings, list):
        add("BINDING_LIST", "error", "targetSelectActioners", "绑定参数应为对象数组。")
        bindings = []
    keys = []
    binding_summary = []
    for b in bindings:
        if not isinstance(b, dict):
            add("BINDING_OBJECT", "error", "targetSelectActioners", "绑定项必须为对象。")
            continue
        key, users = b.get("actionerKey"), b.get("actionerUserIds")
        if not isinstance(key, str) or not key.strip():
            add("BINDING_KEY", "error", "targetSelectActioners", "缺少有效 actionerKey。")
        keys.append(str(key or ""))
        if not isinstance(users, list) or not users or any(not isinstance(u, str) or not u.strip() for u in users):
            add("BINDING_USERS", "error", "targetSelectActioners", "UserId 必须为非空字符串数组。")
        elif len(set(users)) != len(users):
            add("BINDING_DUPLICATE_USER", "error", "targetSelectActioners", "同一节点人员重复。")
        binding_summary.append({"actor_key": str(key or ""), "user_count": len(users) if isinstance(users, list) else 0})
    if len(keys) != len(set(keys)):
        add("BINDING_DUPLICATE_KEY", "error", "targetSelectActioners", "节点绑定 key 重复。")
    node_summary = []
    if forecast is not None:
        if not forecast.get("success"):
            add("FORECAST_FAILED", "error", "workflow", "当前只读预测未成功。")
        nodes = forecast.get("nodes") or []
        for n in nodes:
            node_summary.append({"name": n.get("name"), "kind": n.get("kind"),
                                 "actor_type": n.get("actor_type"), "actor_key": n.get("actor_key"),
                                 "fixed_user_count": len(n.get("fixed_actioners") or [])})
        manual = {n.get("actor_key"): n for n in nodes if n.get("kind") == "manual"}
        for key in keys:
            if key not in manual:
                add("BINDING_NOT_IN_FORECAST", "error", "targetSelectActioners", "请求含不属于当前预测手选节点的 key。")
        for key, n in manual.items():
            if n.get("required") and key not in keys:
                add("REQUIRED_BINDING_MISSING", "error", str(n.get("name") or "workflow"), "当前必选节点未绑定人员。")
        if forecast.get("path_errors"):
            add("PATH_METADATA", "unverified", "workflow", "预测的路由元数据不能用于完整重建链；本模式不重建、不覆盖模板链。")
    elif historical_nodes is not None:
        historical_keys = {n.get("actor_key") for n in historical_nodes if n.get("kind") == "manual"}
        if set(keys) != historical_keys:
            add("ARCHIVE_BINDING_MISMATCH", "error", "targetSelectActioners", "绑定 key 与历史报告记录不一致。")
    add("SERVER_ACCEPTANCE", "unverified", "workflow", "只读/模拟验证不能证明服务器能创建本模板，未发起审批。")
    return {"mode": "read_only", "real_create_calls": 0, "real_upload_calls": 0,
            "structural_errors": sum(f["level"] == "error" for f in findings),
            "unverified_count": sum(f["level"] == "unverified" for f in findings),
            "field_summary": summary, "binding_summary": binding_summary,
            "node_summary": node_summary, "findings": findings}
