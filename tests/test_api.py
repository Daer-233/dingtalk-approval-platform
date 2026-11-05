from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.splitter import HEADERS
from app.dingtalk import (
    DingTalkClient,
    DingTalkConfig,
    DingTalkError,
    apply_local_role_scopes,
    build_display_fields,
    build_target_select_actioners,
    forecast_signature,
    form_submission_diagnostics,
    form_values_from_schema,
    legacy_wire_form_values,
    normalize_forecast,
    resolve_display_fields_from_schema,
    validate_node_actioners,
    wire_form_values,
)


class ApiTest(unittest.TestCase):
    def test_upload_prepare_persistence_and_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            os.environ["APP_DATA_DIR"] = str(Path(temp) / "data")
            main = importlib.import_module("app.main")
            client = TestClient(main.app)
            source = Path(temp) / "8月.xlsx"
            wb = Workbook(); ws = wb.active; ws.cell(1, 1, "2026年8月补贴明细"); ws.append(HEADERS)
            ws.append([1, "示例公司", "甲", "供应链中心", "采购部", "采购组", 100, 20, 0, None, ""])
            ws.append([2, "新进化", "乙", "创新中心", "产品部", "新组", 80, 0, 0, None, ""])
            wb.save(source)
            with source.open("rb") as stream:
                created = client.post("/api/tasks", files={"file": (source.name, stream, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
            self.assertEqual(created.status_code, 200, created.text)
            task = created.json(); self.assertEqual(task["status"], "review"); self.assertEqual(task["analysis"]["actual_groups"], 2)
            groups = []
            for group in task["analysis"]["groups"]:
                groups.append({"group_id": group["id"], "selected": True, "payment_department": group["payment_department"] or "创新组", "approvers": {role: {"user_id": role + "1", "name": role} for role in ("director", "manager", "department_head")}})
            saved = client.put(f"/api/tasks/{task['id']}/groups", json={"groups": groups})
            self.assertEqual(saved.status_code, 200, saved.text)
            prepared = client.post(f"/api/tasks/{task['id']}/prepare", json={})
            self.assertEqual(prepared.status_code, 200, prepared.text)
            self.assertEqual(prepared.json()["result"]["payment_departments"], 2)
            detail_preview = client.get(f"/api/tasks/{task['id']}/preview/department/0?limit=1")
            self.assertEqual(detail_preview.status_code, 200, detail_preview.text)
            self.assertEqual(detail_preview.json()["total"], 2)  # detail plus calculated total row
            self.assertEqual(detail_preview.json()["rows"][0]["cells"][9], 120)
            total_preview = client.get(f"/api/tasks/{task['id']}/preview/department/0?offset=1&limit=1")
            self.assertEqual(total_preview.json()["rows"][0]["cells"][0], "合计")
            self.assertEqual(total_preview.json()["rows"][0]["cells"][9], 120)
            self.assertEqual(client.get(f"/api/tasks/{task['id']}/preview/department/99").status_code, 404)
            mismatched = client.get(f"/api/tasks/{task['id']}/preview/department/0?group_id=wrong")
            self.assertEqual(mismatched.status_code, 409)
            self.assertEqual(client.get(f"/api/tasks/{task['id']}/download/department/0?group_id=wrong").status_code, 409)
            selected_download = client.post(f"/api/tasks/{task['id']}/download/selected", json={"indices": [0, 1]})
            self.assertEqual(selected_download.status_code, 200, selected_download.text)
            self.assertEqual(selected_download.headers["content-type"], "application/zip")
            self.assertTrue(selected_download.content.startswith(b"PK"))
            with ZipFile(BytesIO(selected_download.content)) as archive:
                self.assertEqual(len(archive.namelist()), 2)
            preview = client.post(f"/api/tasks/{task['id']}/approvals/preview", json={})
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertFalse(preview.json()["valid"])  # Company profiles intentionally missing.
            self.assertEqual(client.get("/api/tasks").json()["items"][0]["id"], task["id"])
            scopes = client.get("/api/approval-role-scopes")
            self.assertEqual(scopes.status_code, 200, scopes.text)
            self.assertEqual(len(scopes.json()["items"]), 3)
            saved_scopes = client.put("/api/approval-role-scopes", json={"scopes": [{
                "role_key": "manager", "role_name": "项目经理", "users": [{"user_id": "u1", "name": "甲"}]
            }]})
            self.assertEqual(saved_scopes.status_code, 200, saved_scopes.text)
            manager = next(item for item in saved_scopes.json()["items"] if item["role_key"] == "manager")
            self.assertEqual(manager["users"], [{"user_id": "u1", "name": "甲"}])


class CustomNodeScopeTest(unittest.TestCase):
    def test_custom_node_search_scope_verification_and_merge(self) -> None:
        main = importlib.import_module("app.main")
        client = TestClient(main.app)
        stored: dict[str, dict] = {}
        directory = {"u1": "甲", "u2": "乙"}

        def users_by_ids(ids):
            return [{"user_id": user_id, "name": directory[user_id]}
                    for user_id in ids if user_id in directory]

        def save_scope(scope):
            stored[scope["role_key"]] = scope

        with patch.object(main.db, "list_approval_role_scopes", side_effect=lambda: list(stored.values())), \
             patch.object(main.db, "save_approval_role_scope", side_effect=save_scope), \
             patch.object(main.dingtalk, "users_by_ids", side_effect=users_by_ids):
            endpoint = "/api/approval-role-scopes/custom"
            response = client.post(endpoint, json={"role_name": "新增审批节点", "users": [{"user_id": "u1", "name": "客户端伪造"}]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["users"], [{"user_id": "u1", "name": "甲"}])
            response = client.post(endpoint, json={"role_name": "新增审批节点", "users": [{"user_id": "u2", "name": "乙"}]})
            self.assertEqual([user["user_id"] for user in response.json()["users"]], ["u1", "u2"])
            self.assertEqual(len(stored), 1)
            self.assertEqual(client.post(endpoint, json={"role_name": "新增审批节点", "users": [{"user_id": "unknown", "name": "未知"}]}).status_code, 400)
            self.assertEqual(client.post(endpoint, json={"role_name": "项目经理", "users": [{"user_id": "u1", "name": "甲"}]}).status_code, 400)
            self.assertEqual(len(stored), 1)

            nodes = normalize_forecast({"workflowActivityRules": [{"activityId": "custom", "activityName": "新增审批节点", "isTargetSelect": True,
                "workflowActor": {"actorKey": "custom-key", "required": True}}]})["nodes"]
            apply_local_role_scopes(nodes, main.approval_role_scope_items())
            self.assertEqual(nodes[0]["allowed_user_ids"], ["u1", "u2"])
            self.assertEqual(validate_node_actioners(nodes, {"custom-key": [{"user_id": "u1", "name": "甲"}]}), [])

    def test_custom_scope_cannot_widen_explicit_or_unrecognized_template_range(self) -> None:
        scopes = [{"role_key": "custom", "role_name": "新增审批节点", "users": [
            {"user_id": "u1", "name": "甲"}, {"user_id": "u2", "name": "乙"}]}]
        nodes = normalize_forecast({"workflowActivityRules": [{"activityId": "explicit", "activityName": "新增审批节点", "isTargetSelect": True,
            "workflowActor": {"actorKey": "key", "required": True, "actorSelectionRange": {"userIds": ["u1"]}}}]})["nodes"]
        apply_local_role_scopes(nodes, scopes)
        self.assertEqual(nodes[0]["allowed_user_ids"], ["u1"])
        self.assertTrue(validate_node_actioners(nodes, {"key": [{"user_id": "u2", "name": "乙"}]}))
        opaque = normalize_forecast({"workflowActivityRules": [{"activityId": "opaque", "activityName": "新增审批节点", "isTargetSelect": True,
            "workflowActor": {"actorKey": "key", "actorSelectionRange": {"departments": ["1"]}}}]})["nodes"]
        apply_local_role_scopes(opaque, scopes)
        self.assertEqual(opaque[0]["selection_range_kind"], "unsupported")
        self.assertTrue(validate_node_actioners(opaque, {"key": [{"user_id": "u1", "name": "甲"}]}))


class ForecastNodeTest(unittest.TestCase):
    def test_applicant_department_can_differ_from_originator_department(self) -> None:
        config = DingTalkConfig(originator_dept_id="999999999", applicant_dept_id="999999999")
        self.assertEqual(config.effective_applicant_dept_id, "999999999")
        fallback = DingTalkConfig(originator_dept_id="999999999")
        self.assertEqual(fallback.effective_applicant_dept_id, "999999999")

        fields = build_display_fields(
            {"amount": 123, "payment_department": "总经办"},
            {"payment_entity": "甲公司", "supplier_name": "供应商", "bank_account": "1", "bank_name": "银行"},
            "9月",
            "总经办.xlsx",
            config.effective_applicant_dept_id,
        )
        values = {field["name"]: field["value"] for field in fields}
        self.assertEqual(values["申请人部门"], "999999999")
        self.assertEqual(values["成本分摊"], "部门费用")
        self.assertEqual(values["项目分摊明细"], [])
        self.assertEqual(values["部门分摊明细"][0]["金额（元）"], 123)

    def test_normalizes_fixed_and_manual_nodes_and_validates_selection(self) -> None:
        raw = {
            "isForecastSuccess": True,
            "workflowActivityRules": [
                {"activityId": "fixed", "activityName": "固定审批", "isTargetSelect": False,
                 "workflowActor": {"required": False}, "activityActioners": [{"name": "黄晓熙", "userId": "fixed-user"}]},
                {"activityId": "single", "activityName": "项目经理", "isTargetSelect": True,
                 "workflowActor": {"actorKey": "manager-key", "required": True, "allowedMulti": False,
                                   "actorSelectionRange": {"userIds": ["u1", "u2"]}}},
                {"activityId": "multi", "activityName": "抄送审批", "activityType": "target_select",
                 "workflowActor": {"actorKey": "multi-key", "required": False, "allowedMulti": True,
                                   "actorSelectionRange": {"userIds": ["u1", "u2"]}}},
            ],
        }
        nodes = normalize_forecast(raw)["nodes"]
        self.assertEqual(nodes[0]["kind"], "fixed")
        self.assertEqual(nodes[0]["fixed_actioners"][0]["name"], "黄晓熙")
        self.assertEqual(nodes[1]["allowed_user_ids"], ["u1", "u2"])
        self.assertEqual(nodes[1]["selection_range_kind"], "users")
        self.assertTrue(validate_node_actioners(nodes, {}))
        actioners = {"manager-key": [{"user_id": "u1", "name": "甲"}], "multi-key": [{"user_id": "u2", "name": "乙"}, {"user_id": "u1", "name": "甲"}]}
        self.assertEqual(validate_node_actioners(nodes, actioners), [])
        self.assertEqual(build_target_select_actioners(nodes, actioners), [{"actionerKey": "manager-key", "actionerUserIds": ["u1"]}, {"actionerKey": "multi-key", "actionerUserIds": ["u2", "u1"]}])
        self.assertNotEqual(forecast_signature(nodes), forecast_signature(nodes[:2]))

    def test_rejects_out_of_range_and_ambiguous_range(self) -> None:
        nodes = normalize_forecast({"workflowActivityRules": [{"activityId": "a", "activityName": "审批人", "isTargetSelect": True,
            "workflowActor": {"actorKey": "key", "required": True, "allowedMulti": False, "actorSelectionRange": {"departments": ["1"]}}}]})["nodes"]
        errors = validate_node_actioners(nodes, {"key": [{"user_id": "someone", "name": "某人"}]})
        self.assertTrue(any("无法识别" in error for error in errors))

        missing_range = normalize_forecast({"workflowActivityRules": [{"activityId": "b", "activityName": "项目经理", "isTargetSelect": True,
            "workflowActor": {"actorKey": "manager-key", "required": True, "allowedMulti": False}}]})["nodes"]
        errors = validate_node_actioners(missing_range, {"manager-key": [{"user_id": "u1", "name": "甲"}]})
        self.assertTrue(any("未设置可选人员范围" in error for error in errors))

    def test_applies_local_scope_to_matching_manual_node(self) -> None:
        nodes = normalize_forecast({"workflowActivityRules": [{"activityId": "b", "activityName": "项目经理", "isTargetSelect": True,
            "workflowActor": {"actorKey": "manager-key", "required": True, "allowedMulti": False}}]})["nodes"]
        apply_local_role_scopes(nodes, [{"role_key": "manager", "role_name": "项目经理", "users": [
            {"user_id": "u1", "name": "甲"}, {"user_id": "u2", "name": "乙"}
        ]}])
        self.assertEqual(nodes[0]["allowed_user_ids"], ["u1", "u2"])
        self.assertEqual(nodes[0]["selection_range_source"], "local")
        self.assertEqual(validate_node_actioners(nodes, {"manager-key": [{"user_id": "u1", "name": "甲"}]}), [])

    def test_fixed_workflow_omits_empty_target_select_actioners(self) -> None:
        client = DingTalkClient(DingTalkConfig(
            agent_id="1", originator_user_id="user", originator_dept_id="2",
            process_code="PROC", live_submit_enabled=True,
        ))
        captured = {}
        client.access_token = lambda: "token"

        def fake_request(method, url, **kwargs):
            captured.update(kwargs["json"])
            return {"instanceId": "instance-1"}

        client._request = fake_request
        instance_id = client.start_process([], [{"kind": "fixed"}], {}, "request-1")
        self.assertEqual(instance_id, "instance-1")
        self.assertNotIn("targetSelectActioners", captured)
        self.assertNotIn("requestId", captured)
        self.assertEqual(captured["formComponentValues"], [])

    def test_modern_manual_workflow_binds_actor_key_without_overriding_template(self) -> None:
        client = DingTalkClient(DingTalkConfig(
            agent_id="1", originator_user_id="user", originator_dept_id="2",
            process_code="PROC", live_submit_enabled=True,
        ))
        captured = {}
        client.access_token = lambda: "token"

        def fake_request(method, url, **kwargs):
            captured.update(kwargs["json"])
            return {"instanceId": "instance-2"}

        client._request = fake_request
        nodes = [{
            "kind": "manual", "name": "项目经理", "actor_key": "manager-key",
            "required": True, "allowed_multi": False, "allowed_user_ids": ["u1"],
            "selection_range_kind": "users", "selection_range_restricted": True,
            "selection_range_resolvable": True,
        }]
        instance = client.start_process([], nodes, {"manager-key": [{"user_id": "u1", "name": "甲"}]}, "request-2")
        self.assertEqual(instance, "instance-2")
        self.assertEqual(captured["targetSelectActioners"], [{"actionerKey": "manager-key", "actionerUserIds": ["u1"]}])
        self.assertNotIn("approvers", captured)
        client.close()

    def test_originator_process_access_reads_all_pages(self) -> None:
        client = DingTalkClient(DingTalkConfig(originator_user_id="user", process_code="TARGET"))
        client.access_token = lambda: "token"
        offsets = []

        def fake_request(method, url, **kwargs):
            offset = kwargs["json"]["offset"]
            offsets.append(offset)
            if offset == 0:
                return {"result": {"next_cursor": 100, "process_list": [{"process_code": "OTHER"}]}}
            return {"result": {"next_cursor": None, "process_list": [{"process_code": "TARGET"}]}}

        client._request = fake_request
        self.assertEqual(client.originator_process_access(), {"allowed": True, "count": 2})
        self.assertEqual(offsets, [0, 100])

    def test_optional_empty_form_fields_are_omitted(self) -> None:
        schema = {"schemaContent": {"items": [
            {"componentName": "TextField", "props": {"id": "required", "label": "付款主体", "required": True}},
            {"componentName": "RelateField", "props": {"id": "optional", "label": "关联审批单", "required": False}},
        ]}}
        values = form_values_from_schema(schema, [
            {"name": "付款主体", "value": "示例公司"},
            {"name": "关联审批单", "value": ""},
        ])
        self.assertEqual([value["name"] for value in values], ["付款主体"])
        diagnostics = form_submission_diagnostics(values)
        self.assertEqual(diagnostics, [{"name": "付款主体", "component_type": "TextField", "has_value": True}])

    def test_complex_form_fields_follow_dingtalk_wire_format(self) -> None:
        schema = {"schemaContent": {"items": [
            {"componentName": "CalculateField", "props": {"id": "total", "label": "付款金额含税（元）", "required": True}},
            {"componentName": "TableField", "props": {"id": "table", "label": "部门分摊明细", "required": False}},
            {"componentName": "DDMultiSelectField", "props": {"id": "invoice", "label": "发票", "required": True}},
        ]}}
        values = form_values_from_schema(schema, [
            {"name": "付款金额含税（元）", "value": 300},
            {"name": "部门分摊明细", "value": [{"费用科目": "福利费", "发生费用部门": "总经办", "金额（元）": 300}]},
            {"name": "发票", "value": "增值税普通发票"},
        ])
        self.assertEqual([value["name"] for value in values], ["部门分摊明细", "发票"])
        self.assertEqual(
            values[0]["value"],
            '[[{"name": "费用科目", "value": "福利费"}, {"name": "发生费用部门", "value": "总经办"}, {"name": "金额（元）", "value": "300"}]]',
        )
        self.assertEqual(values[1]["value"], '["增值税普通发票"]')
        self.assertEqual(wire_form_values(values), [
            {"name": "部门分摊明细", "value": values[0]["value"]},
            {"name": "发票", "value": '["增值税普通发票"]'},
        ])
        create_values = form_values_from_schema(schema, [
            {"name": "付款金额含税（元）", "value": 300},
            {"name": "部门分摊明细", "value": []},
            {"name": "发票", "value": "增值税普通发票"},
        ], include_calculated=True)
        self.assertEqual(create_values[0]["name"], "付款金额含税（元）")
        self.assertEqual(create_values[0]["value"], "300")

    def test_legacy_department_field_uses_id_array(self) -> None:
        values = legacy_wire_form_values([
            {"name": "申请人部门", "value": "999999999", "componentType": "DepartmentField"},
        ])
        self.assertEqual(values, [{"name": "申请人部门", "value": "[999999999]"}])

    def test_inactive_project_table_is_submitted_as_an_empty_array(self) -> None:
        schema = {"schemaContent": {"items": [
            {"componentName": "TableField", "props": {"id": "project", "label": "项目分摊明细", "required": False}},
        ]}}
        values = form_values_from_schema(schema, [{"name": "项目分摊明细", "value": []}])
        self.assertEqual(values[0]["value"], "[]")
        self.assertEqual(wire_form_values(values), [{"name": "项目分摊明细", "value": "[]"}])

    def test_table_select_value_uses_schema_department_alias(self) -> None:
        schema = {"schemaContent": {"items": [{
            "componentName": "TableField",
            "props": {"id": "table", "label": "部门分摊明细"},
            "children": [{
                "componentName": "DDSelectField",
                "props": {
                    "id": "department",
                    "label": "发生费用部门",
                    "options": [
                        '{"value":"软装二部-软装深圳D组","key":"d"}',
                        '{"value":"软装上海组","key":"sh"}',
                    ],
                },
            }],
        }]}}
        values = form_values_from_schema(schema, [{
            "name": "部门分摊明细",
            "value": [{"发生费用部门": "软装D组"}],
        }])
        self.assertEqual(
            values[0]["value"],
            '[[{"name": "发生费用部门", "value": "软装二部-软装深圳D组"}]]',
        )

    def test_table_select_value_rejects_missing_schema_option(self) -> None:
        schema = {"schemaContent": {"items": [{
            "componentName": "TableField",
            "props": {"id": "table", "label": "部门分摊明细"},
            "children": [{
                "componentName": "DDSelectField",
                "props": {
                    "id": "department",
                    "label": "发生费用部门",
                    "options": ['{"value":"软装A组","key":"a"}'],
                },
            }],
        }]}}
        with self.assertRaisesRegex(DingTalkError, "不在当前钉钉模板的可选范围内"):
            form_values_from_schema(schema, [{
                "name": "部门分摊明细",
                "value": [{"发生费用部门": "软装A、B组"}],
            }])

    def test_department_hierarchy_resolves_to_live_table_option(self) -> None:
        schema = {"schemaContent": {"items": [{
            "componentName": "TableField",
            "props": {"id": "table", "label": "部门分摊明细"},
            "children": [{
                "componentName": "DDSelectField",
                "props": {
                    "id": "department",
                    "label": "发生费用部门",
                    "options": ['{"value":"软装二部-软装深圳D组","key":"d"}'],
                },
            }],
        }]}}
        fields = [{
            "name": "部门分摊明细",
            "value": [{
                "原表部门": "软装事业部 / 软装二部 / 软装深圳D组",
                "发生费用部门": "软装二部-软装深圳D组",
                "_department_candidates": ["软装二部-软装深圳D组", "软装D组"],
            }],
        }]
        resolved = resolve_display_fields_from_schema(schema, fields)
        self.assertEqual(
            resolved[0]["value"][0]["发生费用部门"],
            "软装二部-软装深圳D组",
        )

if __name__ == "__main__":
    unittest.main()
