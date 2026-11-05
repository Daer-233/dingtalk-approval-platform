"""Evidence-based reports. All functions leave tasks and submitted data untouched."""
from __future__ import annotations
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from pathlib import Path
import json
import re
from .dingtalk import DingTalkClient, wire_form_values, forecast_signature, form_values_from_schema
from .modern_diagnostics import audit_request
from .platform_dingtalk import normalize_forecast

def summarize(report):
    findings = report.get("findings", [])
    errors = sum(f["level"] == "error" for f in findings)
    pending = sum(f["level"] == "unverified" for f in findings)
    return {"local_errors": errors, "unverified": pending,
            "status": "发现本地问题" if errors else "待验证" if pending else "本地检查通过",
            "server_acceptance_verified": False, "read_only": True}

def payload_for_preview(client, schema, fields, nodes, selections):
    values = form_values_from_schema(schema, fields)
    cfg = client.config
    payload = {"processCode": cfg.process_code, "originatorUserId": cfg.originator_user_id,
               "deptId": -1 if int(cfg.originator_dept_id) == 1 else int(cfg.originator_dept_id),
               "microappAgentId": int(cfg.agent_id), "formComponentValues": wire_form_values(values, include_details=True)}
    payload["targetSelectActioners"] = [
        {"actionerKey": n["actor_key"], "actionerUserIds": [str(u.get("user_id") or "") for u in selections.get(n["actor_key"], [])]}
        for n in nodes if n.get("kind") == "manual" and n.get("actor_key") and selections.get(n["actor_key"])
    ]
    return payload

def failure_record(response, stage="创建审批", *, origin="钉钉历史响应"):
    response = response or {}
    body = response.get("body") or {}
    if not isinstance(body, dict): body = {"message": str(body)}
    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    headers = {str(k).lower(): v for k, v in (response.get("headers") or {}).items()}
    instance = body.get("instanceId") or result.get("instanceId") or response.get("instance_id")
    return {"origin": origin, "stage": response.get("stage") or stage,
            "http_status": response.get("http_status"), "error_code": body.get("code") or body.get("errcode") or response.get("error_code"),
            "trace_id": response.get("trace_id") or body.get("requestid") or body.get("requestId") or body.get("request_id") or headers.get("x-acs-request-id") or headers.get("x-request-id"),
            "message": body.get("message") or body.get("errmsg") or response.get("error"),
            "has_instance_id": bool(instance), "instance_id": instance or None,
            "cause": "通用错误不能确定具体失败字段，需钉钉服务端日志核实。",
            "replayed": origin == "钉钉历史响应", "retried": False}

def inspect_people(nodes, selections, client=None):
    cache = {}
    rows, findings = [], []
    for index, node in enumerate(nodes):
        manual = node.get("kind") == "manual"
        role = {"audit": "办理人", "notifier": "抄送人"}.get(node.get("actor_type"), "自选审批人" if manual else "固定审批人")
        users = selections.get(node.get("actor_key"), []) if manual else node.get("fixed_actioners", [])
        required = bool(node.get("required"))
        row = {"index": index + 1, "name": node.get("name") or "未命名节点", "role": role,
               "actor_key": node.get("actor_key"), "required": required,
               "source": "现有人员选择" if manual else "钉钉模板", "users": [],
               "status": "未验证", "prev_activity_id": node.get("prev_activity_id"),
               "forecast_path": node.get("forecast_path")}
        if required and not users:
            row["status"] = "缺少必选人员"
            findings.append({"code": "PERSON_REQUIRED", "level": "error", "field": row["name"], "message": f"{role}必选但未返回或未选择人员。"})
        elif not users:
            row["status"] = "无人员，待核对节点规则"
        for user in users:
            uid = str(user.get("user_id") or "")
            if uid not in cache:
                check = {"status": "未验证", "reason": "本地预览未查询人员；可运行只读诊断。"}
                if client is not None and uid:
                    try:
                        resolved = client.users_by_ids([uid])
                        found = next((u for u in resolved if u.get("user_id") == uid and u.get("name") and u.get("name") != uid), None)
                        check = {"status": "已查询", "name": found["name"], "reason": "接口可返回该人员，不据此判断在职状态。"} if found else {"status": "未验证", "reason": "查询未返回可验证的人员信息。"}
                    except Exception:
                        check = {"status": "未验证", "reason": "人员查询失败或无权限，不能据此判断离职。"}
                cache[uid] = check
            check = cache[uid]
            name = str(user.get("name") or check.get("name") or uid)
            marked = any("离职" in str(n) for n in [name, check.get("name", "")])
            entry = {**deepcopy(user), "name": name, "check": check["status"], "reason": check["reason"], "departure_marker": marked}
            row["users"].append(entry)
            if marked:
                findings.append({"code": "DEPARTURE_MARKER", "level": "unverified", "field": row["name"], "message": f"{name}包含离职标记，请向企业管理员核对；未替换人员。"})
            if check["status"] == "未验证":
                findings.append({"code": "PERSON_UNVERIFIED", "level": "unverified", "field": row["name"], "message": f"{name}：{check['reason']}"})
        if row["users"]: row["status"] = "需核对离职标记" if any(u["departure_marker"] for u in row["users"]) else "已查询" if all(u["check"] == "已查询" for u in row["users"]) else "未验证"
        rows.append(row)
    return rows, findings

def build_report(schema, payload, forecast, selections=None, *, client=None, expected_amount=None,
                 previous_nodes=None, local_errors=None, response=None, pending_attachment=False):
    payload = deepcopy(payload)
    nodes = forecast.get("nodes") or []
    try:
        report = audit_request(schema, payload, forecast=forecast)
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        report = {"findings": [{"code": "UNREADABLE_FORM", "level": "error", "field": "表单", "message": f"表单结构无法完成校验：{type(exc).__name__}"}], "field_summary": []}
    findings = report["findings"]
    def add(code, level, field, message):
        findings.append({"code": code, "level": level, "field": field, "message": message})
    items = DingTalkClient._schema_items(schema)
    by_name = {f.get("name"): f for f in payload.get("formComponentValues", []) if isinstance(f, dict)}
    calculated = {DingTalkClient._label(i) for i in items if i.get("componentName") == "CalculateField"}
    all_controls = []
    def collect(controls):
        for control in controls:
            if isinstance(control, dict):
                all_controls.append(control)
                collect(control.get("children", []))
    collect(items)
    controlled_ids = {target.get("fieldId") for control in all_controls
                      for rule in ((control.get("props") or {}).get("behaviorLinkage") or []) if isinstance(rule, dict)
                      for target in rule.get("targets", []) if isinstance(target, dict)}
    controlled_names = {DingTalkClient._label(c) for c in all_controls
                        if ((c.get("props") or {}).get("id") or c.get("id")) in controlled_ids}
    for finding in findings:
        if finding["code"] in ("REQUIRED_EMPTY", "TABLE_REQUIRED"):
            label = finding["field"].split("/")[-1]
            if label in calculated or label in controlled_names:
                finding.update(code="CALCULATED_REQUIRED_UNVERIFIED" if label in calculated else "CONDITIONAL_REQUIRED",
                               level="unverified", message="计算控件或条件分支的必填行为待验证；没有自动补值。")
    if pending_attachment:
        attachment_names = {DingTalkClient._label(i) for i in items if i.get("componentName") == "DDAttachment"}
        for finding in findings:
            if finding["code"] == "REQUIRED_EMPTY" and finding["field"] in attachment_names:
                finding.update(code="ATTACHMENT_NOT_UPLOADED", level="unverified", message="实验版未上传附件；上传后的 fileId 和审批空间归属待验证。")
    for item in items:
        name, kind = DingTalkClient._label(item), item.get("componentName")
        value = by_name.get(name, {}).get("value")
        if kind == "CalculateField" and name in by_name:
            add("CALCULATED_PRESENT", "unverified", name, "计算控件的服务端计算和校验行为待验证；未补占位值或修改公式。")
        if not value or not isinstance(value, str): continue
        try: parsed = json.loads(value)
        except ValueError: continue
        if kind == "DDAttachment" and isinstance(parsed, list):
            for attachment in parsed:
                ok = isinstance(attachment, dict) and all(isinstance(attachment.get(k), str) and attachment[k].strip() for k in ("fileId", "spaceId", "fileName", "fileType"))
                try: ok = ok and Decimal(str(attachment.get("fileSize"))).is_finite() and Decimal(str(attachment.get("fileSize"))) >= 0 and Decimal(str(attachment.get("fileSize"))) == Decimal(str(attachment.get("fileSize"))).to_integral_value()
                except (InvalidOperation, AttributeError): ok = False
                if not ok: add("ATTACHMENT_FIELDS", "error", name, "附件需包含 fileId、spaceId、fileName、fileType 和有效的非负 fileSize。")
        if kind == "TableField" and isinstance(parsed, list):
            children = {DingTalkClient._label(c): c for c in item.get("children", []) if isinstance(c, dict)}
            # Only totals of explicitly named amount columns are compared, never arbitrary numeric columns.
            amounts = {n for n, c in children.items() if "金额" in n and c.get("componentName") in ("NumberField", "MoneyField")}
            for column in amounts:
                values = []
                for row in parsed:
                    if not isinstance(row, list): continue
                    for cell in row:
                        if isinstance(cell, dict) and cell.get("name") == column:
                            try:
                                number = Decimal(str(cell.get("value")))
                                if number.is_finite(): values.append(number)
                            except InvalidOperation: pass
                if values and expected_amount is not None and sum(values) != Decimal(str(expected_amount)):
                    add("TABLE_AMOUNT_MISMATCH", "error", name + "/" + column, f"明细金额合计 {sum(values)} 与付款分组金额 {expected_amount} 不一致。")
            for row in parsed:
                if not isinstance(row, list): continue
                for cell in row:
                    if not isinstance(cell, dict): continue
                    child = children.get(cell.get("name"))
                    if child is None:
                        add("TABLE_UNKNOWN_CELL", "error", name, "明细中存在不属于模板的子控件名称。")
                    elif child.get("componentName") == "DDSelectField":
                        options = []
                        for option in (child.get("props") or {}).get("options", []):
                            try: option = json.loads(option) if isinstance(option, str) else option
                            except ValueError: pass
                            options.append(str(option.get("value") or option.get("label")) if isinstance(option, dict) else str(option))
                        if options and cell.get("value") and cell["value"] not in options:
                            add("TABLE_OPTION_MISMATCH", "error", name + "/" + str(cell.get("name")), "子控件值不在模板选项中。")
    selections = selections if selections is not None else {
        b.get("actionerKey"): [{"user_id": u, "name": u} for u in b.get("actionerUserIds", [])]
        for b in payload.get("targetSelectActioners", []) if isinstance(b, dict)}
    people, person_findings = inspect_people(nodes, selections, client)
    findings.extend(person_findings)
    for error in local_errors or []: add("LOCAL_PREVIEW", "error", "流程预览", str(error))
    if previous_nodes is not None and forecast_signature(previous_nodes) != forecast_signature(nodes):
        add("WORKFLOW_CHANGED", "error", "审批路径", "当前预测节点与已保存预览不同，请重新检测并核对人员。")
    report.update(mode="read_only", real_create_calls=0, real_upload_calls=0, personnel=people,
                  failure=failure_record(response) if response else None,
                  structural_errors=sum(f["level"] == "error" for f in findings),
                  unverified_count=sum(f["level"] == "unverified" for f in findings))
    report["diagnostic_summary"] = summarize(report)
    return report

def historical_replay(root: Path):
    directory = root / "samples" / "1200元历史失败"
    def read(name): return json.loads((directory / name).read_text(encoding="utf-8-sig"))
    report = build_report(read("schema.json"), read("sent_request.json"),
                          read("forecast_with_attachment.json")["normalized"], expected_amount="1200",
                          response=read("create_response.json"))
    report.update(sample="总经办 1200元历史失败 · 2026-10-09", offline=True,
                  sample_is_task=False, note="本次仅回放历史文件，没有上传、创建或重试。")
    return report

SECRET_KEY = re.compile(r"secret|token|password|authorization|api[_-]?key|bank[_-]?account|银行[账帐]号|银行卡|uploadurl|downloadurl|resourceurls|fileurl|signedurl|临时.*链接", re.I)
def redact(value, key=""):
    if SECRET_KEY.search(key): return "[已隐藏]"
    if isinstance(value, dict): return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list): return [redact(v, key) for v in value]
    if isinstance(value, str):
        # Nested JSON strings are common in DingTalk form values.
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (dict, list)): return json.dumps(redact(parsed), ensure_ascii=False)
        except ValueError: pass
        if key == "value": return "[表单值已隐藏]"
        value = re.sub(r"https?://[^\s\"<>]+", "[链接已隐藏]", value)
        value = re.sub(r"(?<!\d)\d{13,19}(?!\d)", "[账号已隐藏]", value)
        value = re.sub(r"(?i)(access[_-]?token|client[_-]?secret|password)\s*[:=]\s*[^\s,;]+", r"\1=[已隐藏]", value)
    return value
