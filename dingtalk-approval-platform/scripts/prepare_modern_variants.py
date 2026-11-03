"""Prepare isolated request comparisons offline; no submit/upload options."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.dingtalk import DingTalkClient, wire_form_values
from app.modern_diagnostics import audit_request

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
EVIDENCE = ("modern_create_request.json", "modern_schema.json", "modern_test_fixture.json",
            "modern_live_report.json", "modern_create_once_guard.json")
TUTORIAL = "https://open.dingtalk.com/document/orgapp/new-version-of-initiate-and-cancel-approval-flow"
SDK = "https://javadoc.io/static/com.aliyun/dingtalk/2.2.58/com/aliyun/dingtalkworkflow_1_0/models/StartProcessInstanceRequest.StartProcessInstanceRequestFormComponentValues.html"


def digest():
    return {name: hashlib.sha256((OUT / name).read_bytes()).hexdigest() for name in EVIDENCE}


def redact(payload):
    result = deepcopy(payload)
    result["processCode"] = "<PROCESS_CODE>"
    result["originatorUserId"] = "<ORIGINATOR_USER_ID>"
    result["deptId"] = result["microappAgentId"] = 0
    for binding in result.get("targetSelectActioners", []):
        binding["actionerUserIds"] = ["<USER_ID>" for _ in binding["actionerUserIds"]]
    for field in result["formComponentValues"]:
        if field["name"] in {"付款主体", "供应商名称", "银行账号", "开户银行", "申请人部门"}:
            field["value"] = "<REDACTED>"
        if field["name"] == "附件":
            files = json.loads(field["value"])
            for item in files:
                item["spaceId"], item["fileId"] = "<SPACE_ID>", "<FILE_ID>"
            field["value"] = json.dumps(files, ensure_ascii=False)
    return result


def run():
    before = digest()
    original = json.loads((OUT / EVIDENCE[0]).read_text(encoding="utf-8-sig"))
    schema = json.loads((OUT / EVIDENCE[1]).read_text(encoding="utf-8-sig"))
    fixture = json.loads((OUT / EVIDENCE[2]).read_text(encoding="utf-8-sig"))
    amount = Decimal(str(fixture["amount"]))
    if not fixture.get("test_only") or amount != Decimal("0.01"):
        raise ValueError("仅使用已保存的 0.01 元虚拟测试数据。")
    items = {DingTalkClient._label(i): i for i in DingTalkClient._schema_items(schema)}
    if items.get("付款金额含税（元）", {}).get("componentName") != "CalculateField":
        raise ValueError("当前证据没有预期计算控件，不能生成计算值对照。")
    typed = [dict(f, componentType=items[f["name"]].get("componentName"))
             for f in original["formComponentValues"]]
    total = Decimal("0")
    for field in typed:
        if field["componentType"] == "TableField":
            money_labels = {DingTalkClient._label(c) for c in items[field["name"]].get("children", [])
                            if c.get("componentName") == "MoneyField"}
            for row in json.loads(field["value"]):
                total += sum((Decimal(c["value"]) for c in row if c["name"] in money_labels), Decimal("0"))
    if total != amount:
        raise ValueError("明细金额与测试金额不一致，未生成占位计算值。")
    cases = {}
    for name, details, calculated in (
        ("baseline", False, False), ("details_only", True, False),
        ("calculated_only", False, True), ("details_and_calculated", True, True),
    ):
        payload = deepcopy(original)
        payload["formComponentValues"] = wire_form_values(typed, include_details=details)
        if calculated:
            payload["formComponentValues"].append({"name":"付款金额含税（元）", "value":format(amount, "f")})
        cases[name] = payload
    if cases["baseline"] != original:
        raise ValueError("基线序列化与历史请求不同，不能可靠比较。")
    destination = OUT / ("modern_variants_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    destination.mkdir()
    summary = {"mode":"offline_only", "real_create_calls":0, "real_upload_calls":0,
               "tutorial":TUTORIAL, "sdk_model":SDK, "cases":{}}
    for name, payload in cases.items():
        audit = audit_request(schema, payload)
        if audit["structural_errors"]:
            raise ValueError(f"{name} 存在结构错误。")
        for suffix, value in ((".json", payload), (".redacted.json", redact(payload))):
            (destination / (name + suffix)).write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        summary["cases"][name] = {
            "details_fields":sum("details" in f for f in payload["formComponentValues"]),
            "explicit_calculated_value":any(f["name"] == "付款金额含税（元）" for f in payload["formComponentValues"]),
            "structural_errors":audit["structural_errors"], "unverified_count":audit["unverified_count"],
        }
    summary["original_evidence_unchanged"] = before == digest()
    if not summary["original_evidence_unchanged"]:
        raise RuntimeError("源证据发生变化，请核查。")
    (destination / "manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    (destination / "README.md").write_text(
        "# 新版创建审批请求对照\n\n"
        "这些文件只在本地生成，没有调用网络、上传文件或创建审批。\n\n"
        "- baseline：历史失败请求。\n- details_only：只补明细 details。\n"
        "- calculated_only：只加付款金额计算值 0.01。\n"
        "- details_and_calculated：同时作两项调整。\n\n"
        "每组的 .redacted.json 可用于群内结构排查；标识已替换，不能直接执行。\n"
        "未脱敏版本用于本地对照，保留历史附件标识，不代表文件现在仍有效。\n"
        "真实联调前需使用当前模板、重新预测节点并核对附件有效性。\n"
        "计算控件是否需要传值仍待服务端验证，示例没有出现某字段不能证明它永远可省略。\n"
        "请保留模板必填项；不要直接删除附件或明细来推断根因。\n\n"
        f"官方教程：{TUTORIAL}\n官方 SDK 模型：{SDK}\n", encoding="utf-8")
    print(json.dumps({"directory":str(destination), **summary}, ensure_ascii=False))
    return destination


if __name__ == "__main__":
    run()
