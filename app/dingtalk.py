from __future__ import annotations

from .legacy_chain import CHAIN_VERSION, build_legacy_chain, order_forecast_path, inspect_forecast_path

import json
import os
import hashlib
import time
import threading
import uuid
from dataclasses import dataclass
from collections import deque
from pathlib import Path
from typing import Any, Iterable

import httpx
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"

API_BASE = "https://api.dingtalk.com"
OAPI_BASE = "https://oapi.dingtalk.com"
EXPECTED_FIELDS = ["付款主体", "申请人部门", "供应商名称", "银行账号", "开户行", "成本分摊", "付款方式", "付款类型", "付款金额含税（元）", "付款事由", "部门分摊明细", "发票", "附件"]
FIELD_ALIASES = {"开户行": ("开户行", "开户银行"), "付款金额含税（元）": ("付款金额含税", "付款金额")}
FORM_OPTION_ALIASES = {
    "发生费用部门": {
        "人力资源组": "人力行政中心-人力资源组",
        "行政管理组": "人力行政中心-行政管理组",
        "上海人力行政中心": "人力行政中心-上海中心",
        "软装C组": "软装二部-软装深圳C组",
        "软装D组": "软装二部-软装深圳D组",
    },
}
# Kept for loading historic task records created before dynamic node prediction.
APPROVER_ROLES = {
    "director": "项目/设计总监",
    "manager": "项目经理",
    "department_head": "费用发生部门主管",
}


@dataclass(frozen=True)
class DingTalkConfig:
    client_id: str = ""
    client_secret: str = ""
    agent_id: str = ""
    corp_id: str = ""
    originator_user_id: str = ""
    originator_dept_id: str = ""
    applicant_dept_id: str = ""
    process_code: str = ""
    live_submit_enabled: bool = False
    attachment_enabled: bool = False

    @property
    def missing(self) -> list[str]:
        values = {
            "Client ID": self.client_id, "Client Secret": self.client_secret, "AgentId": self.agent_id,
            "CorpId": self.corp_id, "固定发起人 UserId": self.originator_user_id,
            "固定发起人 DeptId": self.originator_dept_id, "processCode": self.process_code,
        }
        return [label for label, value in values.items() if not value]

    @property
    def actioner_keys(self) -> dict[str, str]:
        try:
            value = json.loads(os.getenv("DINGTALK_ACTIONER_KEYS_JSON", "{}"))
            return {str(key): str(item) for key, item in value.items()}
        except json.JSONDecodeError:
            return {}

    @property
    def effective_applicant_dept_id(self) -> str:
        return self.applicant_dept_id or self.originator_dept_id


class DingTalkError(RuntimeError):
    pass


class DingTalkUncertainSubmissionError(DingTalkError):
    """The create request may have reached DingTalk but no result was received."""


def load_dingtalk_config() -> DingTalkConfig:
    load_dotenv(ENV_PATH, override=True)
    enabled = lambda key: os.getenv(key, "false").strip().lower() == "true"
    return DingTalkConfig(
        client_id=os.getenv("DINGTALK_CLIENT_ID", "").strip(),
        client_secret=os.getenv("DINGTALK_CLIENT_SECRET", "").strip(),
        agent_id=os.getenv("DINGTALK_AGENT_ID", "").strip(),
        corp_id=os.getenv("DINGTALK_CORP_ID", "").strip(),
        originator_user_id=os.getenv("DINGTALK_ORIGINATOR_USER_ID", "").strip(),
        originator_dept_id=os.getenv("DINGTALK_ORIGINATOR_DEPT_ID", "").strip(),
        applicant_dept_id=os.getenv("DINGTALK_APPLICANT_DEPT_ID", "").strip(),
        process_code=os.getenv("DINGTALK_PROCESS_CODE", "").strip(),
        live_submit_enabled=enabled("DINGTALK_LIVE_SUBMIT_ENABLED"),
        attachment_enabled=enabled("DINGTALK_ATTACHMENT_ENABLED"),
    )


class DingTalkClient:
    def __init__(self, config: DingTalkConfig | None = None) -> None:
        self.config = config or load_dingtalk_config()
        self._token = ""
        self._token_expires = 0.0
        self._token_lock = threading.RLock()
        self._context_lock = threading.RLock()
        self._originator_union_id_cache = ""
        self._approval_space_id_cache = ""
        self._schema_cache: dict[str, Any] | None = None
        self._schema_cached_at = 0.0
        self._directory_lock = threading.RLock()
        self._directory_cache: list[dict[str, str]] | None = None
        self._directory_cached_at = 0.0
        self._http = httpx.Client(timeout=30)

    def reload_config(self) -> DingTalkConfig:
        # Do not publish a directory loaded using the previous application's config.
        with self._directory_lock:
            self.config = load_dingtalk_config()
            self._token = ""
            self._token_expires = 0.0
            self._originator_union_id_cache = ""
            self._approval_space_id_cache = ""
            self._schema_cache = None
            self._schema_cached_at = 0.0
            self._directory_cache = None
            self._directory_cached_at = 0.0
            return self.config

    def close(self) -> None:
        self._http.close()

    @property
    def batch_concurrency(self) -> int:
        try:
            return max(1, min(5, int(os.getenv("DINGTALK_BATCH_CONCURRENCY", "3"))))
        except ValueError:
            return 3

    def public_config(self) -> dict[str, Any]:
        return {
            "configured": not self.config.missing,
            "missing": self.config.missing,
            "process_code": self.config.process_code,
            "agent_id": self.config.agent_id,
            "corp_id": self.config.corp_id,
            "originator_user_id": self.config.originator_user_id,
            "originator_dept_id": self.config.originator_dept_id,
            "applicant_dept_id": self.config.effective_applicant_dept_id,
            "live_submit_enabled": self.config.live_submit_enabled,
            "attachment_enabled": self.config.attachment_enabled,
            "dynamic_workflow_nodes": True,
            "legacy_actioner_keys_configured": bool(self.config.actioner_keys),
            "approval_submit_api": "legacy_topapi",
            "manual_node_binding_supported": False,
            "handler_execution_owner": "dingtalk",
            "handler_template_manual_binding_supported": False,
            "legacy_full_chain_supported": True,
            "batch_concurrency": self.batch_concurrency,
        }

    def _request(
        self,
        method: str,
        url: str,
        *,
        token: bool = True,
        retries: int = 0,
        uncertain_on_transport: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}))
        if token:
            headers["x-acs-dingtalk-access-token"] = self.access_token()
        for attempt in range(retries + 1):
            try:
                response = self._http.request(method, url, headers=headers, **kwargs)
            except httpx.RequestError as exc:
                if attempt < retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                error_type = DingTalkUncertainSubmissionError if uncertain_on_transport else DingTalkError
                raise error_type(f"钉钉接口连接失败：{exc}") from exc
            try:
                data = response.json()
            except ValueError:
                data = {}
            if response.status_code in {429, 500, 502, 503, 504} and attempt < retries:
                time.sleep(0.5 * (attempt + 1))
                continue
            if uncertain_on_transport and response.status_code in {502, 503, 504}:
                raise DingTalkUncertainSubmissionError(
                    f"钉钉创建审批返回 HTTP {response.status_code}，结果待确认，请先核对审批列表。"
                )
            if response.is_error:
                message = data.get("message") or data.get("errmsg") or response.text[:300]
                code = data.get("code") or data.get("errcode")
                trace_id = (
                    data.get("requestId")
                    or data.get("request_id")
                    or response.headers.get("x-acs-request-id")
                    or response.headers.get("x-request-id")
                    or response.headers.get("x-tt-trace-id")
                )
                details = [f"错误码 {code}"] if code not in (None, "") else []
                if trace_id:
                    details.append(f"钉钉追踪号 {trace_id}")
                suffix = f"（{'；'.join(details)}）" if details else ""
                raise DingTalkError(f"钉钉接口返回 {response.status_code}：{message}{suffix}")
            if data.get("code") not in (None, "", "0", 0) or data.get("errcode") not in (None, 0):
                message = data.get("message") or data.get("errmsg") or "钉钉接口调用失败"
                code = data.get("code") or data.get("errcode")
                request_id = data.get("requestId") or data.get("request_id")
                suffix = f"；钉钉追踪号 {request_id}" if request_id else ""
                raise DingTalkError(f"{message}（错误码 {code}{suffix}）")
            if not data and response.content:
                raise DingTalkError("钉钉接口返回了无法解析的响应。")
            return data
        raise DingTalkError("钉钉接口调用失败。")

    def access_token(self) -> str:
        if self.config.missing:
            raise DingTalkError("钉钉连接资料不完整。")
        with self._token_lock:
            if self._token and time.time() < self._token_expires:
                return self._token
            data = self._request(
                "POST", f"{API_BASE}/v1.0/oauth2/accessToken", token=False, retries=2,
                json={"appKey": self.config.client_id, "appSecret": self.config.client_secret},
            )
            self._token = data.get("accessToken", "")
            if not self._token:
                raise DingTalkError("钉钉未返回 accessToken。")
            self._token_expires = time.time() + max(60, int(data.get("expireIn", 7200)) - 120)
            return self._token

    def get_schema(self, force_refresh: bool = False) -> dict[str, Any]:
        with self._context_lock:
            if not force_refresh and self._schema_cache and time.time() - self._schema_cached_at < 60:
                return self._schema_cache
            data = self._request("GET", f"{API_BASE}/v1.0/workflow/forms/schemas/processCodes", retries=2, params={"processCode": self.config.process_code})
            self._schema_cache = data.get("result", data)
            self._schema_cached_at = time.time()
            return self._schema_cache

    @staticmethod
    def _schema_items(schema: dict[str, Any]) -> list[dict[str, Any]]:
        content = schema.get("schemaContent", schema)
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except json.JSONDecodeError:
                return []
        return content.get("items", []) if isinstance(content, dict) else []

    @staticmethod
    def _label(item: dict[str, Any]) -> str:
        props = item.get("props") or {}
        return str(props.get("label") or item.get("name") or "").strip()

    def preflight(self, remote: bool = True) -> dict[str, Any]:
        status = self.public_config()
        status.update({"ok": False, "schema_ok": False, "nodes_ok": True, "attachment_ok": False, "messages": []})
        if self.config.missing:
            status["messages"].append("请先在本机 .env 中补充钉钉应用和审批模板资料。")
            return status
        if not remote:
            status["messages"].append("本地配置已读取，尚未向钉钉发起在线预检。")
            return status
        try:
            schema = self.get_schema(force_refresh=True)
            items = self._schema_items(schema)
            labels = [self._label(item) for item in items]
            missing_fields = [field for field in EXPECTED_FIELDS if not any(any(alias in label for alias in FIELD_ALIASES.get(field, (field.replace("（元）", ""),))) for label in labels)]
            status.update({
                "schema_ok": not missing_fields,
                "schema_fields": labels,
                "missing_schema_fields": missing_fields,
                "template_name": str(schema.get("name") or ""),
                "template_status": str(schema.get("status") or ""),
                "template_visible_range": str(schema.get("visibleRange") or ""),
            })
            if missing_fields:
                status["messages"].append("测试审批模板缺少或未识别字段：" + "、".join(missing_fields))
            if str(schema.get("visibleRange") or "").upper() == "PRIVATE":
                status["messages"].append(
                    "审批模板的可发起范围为指定人员；请确认固定发起人已包含在模板“谁可以发起”范围内。"
                )
            try:
                access = self.originator_process_access()
                status.update({
                    "originator_access_ok": access["allowed"],
                    "originator_process_count": access["count"],
                })
                if access["allowed"]:
                    status["messages"].append("固定发起人已在该审批模板的可发起范围内。")
                else:
                    status["messages"].append("固定发起人不在该审批模板的可发起范围内。")
            except DingTalkError as exc:
                status["originator_access_ok"] = None
                status["messages"].append("无法自动核验固定发起人的模板权限：" + str(exc))
            if self.config.attachment_enabled:
                self.originator_union_id()
                self.approval_space_id()
                status["attachment_ok"] = True
            else:
                status["messages"].append("附件上传仍为安全关闭状态；联调通过后再开启。")
            status["ok"] = bool(
                status["schema_ok"]
                and status["nodes_ok"]
                and status["attachment_ok"]
                and status.get("originator_access_ok") is not False
            )
            if status["ok"]:
                status["messages"].append("表单、审批节点和附件配置预检通过。")
        except DingTalkError as exc:
            status["messages"].append(str(exc))
        return status

    def originator_process_access(self) -> dict[str, Any]:
        token = self.access_token()
        offset = 0
        process_codes: set[str] = set()
        for _ in range(100):
            data = self._request(
                "POST",
                f"{OAPI_BASE}/topapi/process/listbyuserid",
                token=False,
                params={"access_token": token},
                retries=2, json={"userid": self.config.originator_user_id, "offset": offset, "size": 100},
            )
            result = data.get("result") or {}
            page = result.get("process_list") or result.get("list") or []
            for process in page:
                code = str(process.get("process_code") or process.get("processCode") or "").strip()
                if code:
                    process_codes.add(code)
            next_cursor = result.get("next_cursor")
            if not page or next_cursor is None:
                break
            try:
                next_offset = int(next_cursor)
            except (TypeError, ValueError):
                break
            if next_offset <= offset:
                break
            offset = next_offset
        return {"allowed": self.config.process_code in process_codes, "count": len(process_codes)}

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

    def users_by_ids(self, user_ids: Iterable[str]) -> list[dict[str, str]]:
        """Resolve the exact users allowed by a target-select node."""
        token = self.access_token()
        result: list[dict[str, str]] = []
        for user_id in dict.fromkeys(str(value).strip() for value in user_ids):
            if not user_id:
                continue
            data = self._request(
                "POST",
                f"{OAPI_BASE}/topapi/v2/user/get",
                token=False,
                params={"access_token": token},
                json={"userid": user_id},
            )
            user = data.get("result") or {}
            result.append({
                "user_id": user_id,
                "name": str(user.get("name") or user_id),
                "dept_id": str((user.get("dept_id_list") or [""])[0]),
            })
        return result

    def originator_union_id(self) -> str:
        if self._originator_union_id_cache:
            return self._originator_union_id_cache
        with self._context_lock:
            if self._originator_union_id_cache:
                return self._originator_union_id_cache
            token = self.access_token()
            data = self._request(
                "POST",
                f"{OAPI_BASE}/topapi/v2/user/get",
                token=False,
                retries=2,
                params={"access_token": token},
                json={"userid": self.config.originator_user_id},
            )
            union_id = str((data.get("result") or {}).get("unionid", "")).strip()
            if not union_id:
                raise DingTalkError("未取得固定发起人的 unionId，请检查通讯录用户读取权限和 UserId。")
            self._originator_union_id_cache = union_id
            return union_id

    def approval_space_id(self) -> str:
        if self._approval_space_id_cache:
            return self._approval_space_id_cache
        with self._context_lock:
            if self._approval_space_id_cache:
                return self._approval_space_id_cache
            data = self._request(
                "POST",
                f"{API_BASE}/v1.0/workflow/processInstances/spaces/infos/query",
                retries=2,
                json={"userId": self.config.originator_user_id, "agentId": int(self.config.agent_id)},
            )
            result = data.get("result", data)
            space_id = str(result.get("spaceId", "")).strip()
            if not space_id:
                raise DingTalkError("未取得审批钉盘空间，请检查 Workflow.Instance.Write 权限。")
            self._approval_space_id_cache = space_id
            return space_id

    def forecast(self, form_values: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {"requestId": uuid.uuid4().hex, "processCode": self.config.process_code, "deptId": int(self.config.originator_dept_id), "userId": self.config.originator_user_id, "formComponentValues": wire_form_values(form_values)}
        return self._request("POST", f"{API_BASE}/v1.0/workflow/processes/forecast", retries=2, json=payload).get("result", {})

    @staticmethod
    def submission_api(nodes: list[dict[str, Any]]) -> str:
        """This deployment uses the proven legacy create endpoint only."""
        return "legacy_topapi"

    @staticmethod
    def submission_errors(nodes: list[dict[str, Any]], node_actioners: dict[str, Any] | None = None) -> list[str]:
        return build_legacy_chain(nodes, node_actioners or {})["errors"]

    def start_process(
        self,
        form_values: list[dict[str, Any]],
        nodes: list[dict[str, Any]],
        node_actioners: dict[str, list[dict[str, str]]],
        request_id: str,
        *,
        legacy_chain_confirmed: bool = False,
        expected_chain_fingerprint: str = "",
    ) -> str:
        if not self.config.live_submit_enabled:
            raise DingTalkError("正式提交开关未开启。请先用测试审批模板完成联调。")
        errors = validate_node_actioners(nodes, node_actioners)
        chain = build_legacy_chain(nodes, node_actioners)
        errors.extend(chain["errors"])
        if chain["mode"] == "explicit_chain":
            if not legacy_chain_confirmed:
                errors.append("旧版创建接口完整审批链必须经用户单独确认后才能发起。")
            if not expected_chain_fingerprint or expected_chain_fingerprint != chain["fingerprint"]:
                errors.append("完整审批链与已确认的预览不一致，请重新生成并确认预览。")
        if errors:
            raise DingTalkError("；".join(errors))
        if chain.get("preserves_template") and expected_chain_fingerprint and expected_chain_fingerprint != chain["fingerprint"]:
            raise DingTalkError("模板执行计划与已确认预览不一致，请重新检测并生成预览。")
        # Handler plans retain process_code only: no approvers/approvers_v2 or
        # cc overrides, handler completion calls, or fallback create endpoint.
        # Other explicitly reviewed whole chains may override the template.
        payload = {
            "agent_id": int(self.config.agent_id),
            "process_code": self.config.process_code,
            "originator_user_id": self.config.originator_user_id,
            "dept_id": int(self.config.originator_dept_id),
            "request_id": request_id,
            "form_component_values": legacy_wire_form_values(form_values),
        }
        if chain["mode"] == "explicit_chain":
            payload["approvers_v2"] = chain["approvers_v2"]
            if chain["cc_list"]:
                payload.update({"cc_list": chain["cc_list"], "cc_position": chain["cc_position"]})
        data = self._request(
            "POST", f"{OAPI_BASE}/topapi/processinstance/create",
            token=False, params={"access_token": self.access_token()},
            json=payload, uncertain_on_transport=True,
        )
        instance_id = data.get("process_instance_id") or (data.get("result") or {}).get("process_instance_id")
        if not instance_id:
            raise DingTalkUncertainSubmissionError("钉钉未返回审批实例 ID，结果待确认，请先核对审批列表，勿直接重试。")
        return str(instance_id)

    def upload_approval_attachment(self, file_path: Path) -> dict[str, str]:
        if not self.config.attachment_enabled:
            raise DingTalkError("附件上传开关未开启。")
        space_id = self.approval_space_id()
        union_id = self.originator_union_id()
        upload_url = f"{API_BASE}/v1.0/storage/spaces/{space_id}/files/uploadInfos/query"
        commit_url = f"{API_BASE}/v1.0/storage/spaces/{space_id}/files/commit"
        request_body = {
            "protocol": "HEADER_SIGNATURE",
            "multipart": False,
            "option": {
                "storageDriver": "DINGTALK",
                "preCheckParam": {"name": file_path.name, "size": file_path.stat().st_size, "parentId": "0"},
            },
        }
        info_data = self._request("POST", upload_url, retries=2, params={"unionId": union_id}, json=request_body)
        info = info_data.get("result", info_data)
        header_info = info.get("headerSignatureInfo") or {}
        resource_urls = header_info.get("resourceUrls") or []
        if not resource_urls:
            raise DingTalkError("钉钉未返回附件上传地址。")
        content = file_path.read_bytes()
        response = None
        for attempt in range(3):
            try:
                response = self._http.put(resource_urls[0], content=content, headers=header_info.get("headers") or {}, timeout=120)
            except httpx.RequestError as exc:
                if attempt == 2:
                    raise DingTalkError(f"附件二进制上传失败：{exc}") from exc
                time.sleep(0.5 * (attempt + 1))
                continue
            if not response.is_error or response.status_code not in {429, 500, 502, 503, 504} or attempt == 2:
                break
            time.sleep(0.5 * (attempt + 1))
        if response is None or response.is_error:
            raise DingTalkError(f"附件二进制上传失败：HTTP {response.status_code if response else '未知'}")
        commit_data = self._request(
            "POST",
            commit_url,
            retries=2,
            params={"unionId": union_id},
            json={
                "uploadKey": info.get("uploadKey"),
                "name": file_path.name,
                "parentId": "0",
                "option": {"size": file_path.stat().st_size, "conflictStrategy": "AUTO_RENAME"},
            },
        )
        commit = commit_data.get("result", commit_data)
        dentry = commit.get("dentry") or commit
        file_id = str(dentry.get("id") or dentry.get("dentryUuid") or dentry.get("fileId") or "")
        if not file_id:
            raise DingTalkError("附件提交成功但未返回 fileId。")
        return {"fileId": file_id, "fileName": file_path.name, "fileType": file_path.suffix.lstrip("."), "spaceId": space_id, "fileSize": str(file_path.stat().st_size)}


def _strings_from_range(value: Any, key: str = "") -> set[str]:
    """Extract explicit user identifiers from the forecast selection range."""
    identifiers: set[str] = set()
    id_keys = {"userid", "userids", "workno", "worknos", "useridlist", "userlist"}
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            identifiers.update(_strings_from_range(child_value, str(child_key).lower()))
    elif isinstance(value, list):
        for item in value:
            identifiers.update(_strings_from_range(item, key))
    elif isinstance(value, (str, int)) and key.lower() in id_keys:
        text = str(value).strip()
        if text:
            identifiers.add(text)
    return identifiers


def _forecast_actioners(value: Any) -> list[dict[str, str]]:
    actioners = value if isinstance(value, list) else []
    result: list[dict[str, str]] = []
    for actioner in actioners:
        if not isinstance(actioner, dict):
            continue
        user_id = str(actioner.get("userId") or actioner.get("userid") or "").strip()
        name = str(actioner.get("name") or actioner.get("userName") or user_id).strip()
        if user_id:
            result.append({"user_id": user_id, "name": name})
    return result


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


def apply_local_role_scopes(nodes: list[dict[str, Any]], scopes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply locally maintained user scopes without widening DingTalk template limits."""
    normalize_name = lambda value: "".join(str(value or "").split()).replace("／", "/")
    by_name = {normalize_name(scope.get("role_name")): scope for scope in scopes if scope.get("role_name")}
    for node in nodes:
        if node.get("kind") != "manual":
            continue
        scope = by_name.get(normalize_name(node.get("name")))
        if not scope:
            continue
        local_users = [user for user in scope.get("users", []) if isinstance(user, dict) and user.get("user_id")]
        local_ids = list(dict.fromkeys(str(user["user_id"]).strip() for user in local_users if str(user["user_id"]).strip()))
        if not local_ids:
            continue
        template_ids = list(node.get("allowed_user_ids") or [])
        node["local_scope_key"] = str(scope.get("role_key") or "")
        node["local_scope_name"] = str(scope.get("role_name") or node.get("name") or "")
        node["local_scope_user_ids"] = local_ids
        node["template_allowed_user_ids"] = template_ids
        if node.get("selection_range_kind") == "unsupported":
            node["selection_range_source"] = "unsupported"
            continue
        if template_ids:
            template_id_set = set(template_ids)
            allowed = [user_id for user_id in local_ids if user_id in template_id_set]
            node["selection_range_source"] = "intersection"
        else:
            allowed = local_ids
            node["selection_range_source"] = "local"
        node["allowed_user_ids"] = allowed
        node["selection_range_restricted"] = True
        node["selection_range_resolvable"] = bool(allowed)
        node["selection_range_kind"] = "users" if allowed else "empty"
    return nodes


def forecast_signature(nodes: list[dict[str, Any]]) -> str:
    stable = [{
        "version": CHAIN_VERSION, "metadata_version": node.get("metadata_version"),
        "activity_id": node.get("activity_id", ""), "kind": node.get("kind", ""),
        "prev_activity_id": node.get("prev_activity_id"), "name": node.get("name", ""),
        "actor_type": node.get("actor_type", ""), "approval_type": node.get("approval_type", ""),
        "activity_type": node.get("activity_type", ""),
        "approval_method": node.get("approval_method", ""), "activation_type": node.get("activation_type", ""),
        "fixed_actioners": node.get("fixed_actioners", []), "actioners_complete": node.get("actioners_complete", False),
        "actor_key": node.get("actor_key", ""), "required": bool(node.get("required")),
        "allowed_multi": bool(node.get("allowed_multi")),
        "allowed_user_ids": node.get("allowed_user_ids", []),
        "selection_range_restricted": bool(node.get("selection_range_restricted")),
        "selection_range_resolvable": bool(node.get("selection_range_resolvable")),
        "path_errors": node.get("path_errors", []),
        "forecast_path": node.get("forecast_path"),
        "template_default_user_ids": node.get("template_default_user_ids", []),
    } for node in nodes]
    source = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def validate_node_actioners(nodes: list[dict[str, Any]], node_actioners: dict[str, list[dict[str, str]]]) -> list[str]:
    errors: list[str] = []
    for node in nodes:
        if node.get("kind") != "manual":
            continue
        name, actor_key = str(node.get("name") or "审批节点"), str(node.get("actor_key") or "")
        users = node_actioners.get(actor_key, []) if actor_key else []
        user_ids = [str(item.get("user_id") or "").strip() for item in users if str(item.get("user_id") or "").strip()]
        if not actor_key:
            errors.append(f"{name} 未返回 actionerKey，无法提交")
        if not node.get("selection_range_restricted") and (users or node.get("required")):
            errors.append(f"{name} 未设置可选人员范围，请在钉钉模板中配置明确人员")
        if node.get("selection_range_restricted") and not node.get("selection_range_resolvable") and (users or node.get("required")):
            errors.append(f"{name} 的可选人员范围无法识别，请在钉钉模板中明确配置人员范围")
        if node.get("required") and not user_ids:
            errors.append(f"{name} 为必选审批节点")
        if not node.get("allowed_multi") and len(user_ids) > 1:
            errors.append(f"{name} 仅允许选择一位审批人")
        locked = set(node.get("template_default_user_ids") or [])
        if not locked.issubset(user_ids):
            errors.append(f"{name} 的模板默认抄送人不可删除")
        if len(user_ids) != len(users) or len(user_ids) != len(set(user_ids)):
            errors.append(f"{name} 的 UserId 缺失或重复")
        allowed = set(node.get("allowed_user_ids") or []) | locked
        if allowed and any(user_id not in allowed for user_id in user_ids):
            errors.append(f"{name} 包含不在钉钉允许范围内的人员")
    return list(dict.fromkeys(errors))


def build_target_select_actioners(nodes: list[dict[str, Any]], node_actioners: dict[str, list[dict[str, str]]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node in nodes:
        if node.get("kind") != "manual":
            continue
        actor_key = str(node.get("actor_key") or "")
        user_ids = [str(item.get("user_id") or "").strip() for item in node_actioners.get(actor_key, [])]
        user_ids = [user_id for user_id in user_ids if user_id]
        if user_ids:
            result.append({"actionerKey": actor_key, "actionerUserIds": user_ids})
    return result


def chinese_upper_money(value: float | int) -> str:
    value = round(float(value) + 1e-8, 2)
    if value == 0:
        return "零元整"
    digits, units, section_units = "零壹贰叁肆伍陆柒捌玖", ["", "拾", "佰", "仟"], ["", "万", "亿", "兆"]
    integer, decimal = int(value), int(round((value - int(value)) * 100))
    sections, section_index = [], 0
    while integer:
        section = integer % 10000
        if section:
            text, zero = "", False
            for index in range(4):
                digit = section % 10
                if digit:
                    text = digits[digit] + units[index] + text
                    if zero:
                        text = digits[0] + text
                    zero = False
                elif text:
                    zero = True
                section //= 10
            sections.insert(0, text + section_units[section_index])
        elif sections and not sections[0].startswith(digits[0]):
            sections.insert(0, digits[0])
        integer //= 10000
        section_index += 1
    result = "".join(sections).replace("零零", "零").rstrip("零") + "元"
    jiao, fen = divmod(decimal, 10)
    if jiao:
        result += digits[jiao] + "角"
    elif fen:
        result += "零"
    if fen:
        result += digits[fen] + "分"
    elif not jiao:
        result += "整"
    return result


def build_display_fields(group: dict[str, Any], profile: dict[str, str], month: str, attachment_name: str, originator_dept_id: str = "") -> list[dict[str, Any]]:
    amount = group["amount"]
    department_rows = group.get("department_allocations") or [{
        "费用科目": "福利费",
        "原表部门": group.get("scope") or group["payment_department"],
        "发生费用部门": group["payment_department"],
        "金额（元）": amount,
        "_department_candidates": [group["payment_department"]],
    }]
    return [
        {"name": "付款主体", "value": profile.get("payment_entity", "")}, {"name": "申请人部门", "value": originator_dept_id}, {"name": "关联审批单", "value": ""},
        {"name": "供应商名称", "value": profile.get("supplier_name", "")}, {"name": "银行账号", "value": profile.get("bank_account", "")},
        {"name": "开户行", "value": profile.get("bank_name", "")}, {"name": "成本分摊", "value": "部门费用"},
        {"name": "付款方式", "value": "银行转账"}, {"name": "付款类型", "value": "付全款"},
        {"name": "付款金额含税（元）", "value": amount}, {"name": "大写", "value": chinese_upper_money(amount)},
        {"name": "付款事由", "value": f"{group['payment_department']}{month}餐补发放"}, {"name": "项目分摊明细", "value": []},
        {"name": "部门分摊明细", "value": department_rows},
        {"name": "备注", "value": ""}, {"name": "发票", "value": "增值税普通发票"},
        {"name": "附件", "value": attachment_name}, {"name": "发送到聊天", "value": ""},
    ]


def form_values_from_schema(
    schema: dict[str, Any],
    display_fields: Iterable[dict[str, Any]],
    attachment: dict[str, str] | None = None,
    include_calculated: bool = False,
) -> list[dict[str, Any]]:
    fields = {field["name"]: field["value"] for field in display_fields}
    result: list[dict[str, Any]] = []
    for item in DingTalkClient._schema_items(schema):
        props = item.get("props") or {}
        label = DingTalkClient._label(item)
        match = next((name for name in fields if name == label or any(alias in label for alias in FIELD_ALIASES.get(name, (name.replace("（元）", ""),)))), None)
        if not match:
            continue
        value = fields[match]
        component_type = item.get("componentName") or item.get("componentType")
        # Forecast accepts formula controls as calculated values. The legacy
        # create API requires a value when the template marks one as required.
        if component_type == "CalculateField" and not include_calculated:
            continue
        if match == "附件":
            value = json.dumps([attachment], ensure_ascii=False) if attachment else ""
        elif component_type == "TableField" and isinstance(value, list):
            children = {
                DingTalkClient._label(child): child
                for child in item.get("children", [])
                if isinstance(child, dict) and DingTalkClient._label(child)
            }
            rows = []
            for row in value:
                if not isinstance(row, dict):
                    continue
                cells = []
                source = (
                    ((name, row[name], child) for name, child in children.items() if name in row)
                    if children
                    else ((str(name), cell_value, None) for name, cell_value in row.items() if not str(name).startswith("_"))
                )
                for name, cell_value, child in source:
                    if child:
                        candidates = [cell_value]
                        if name == "发生费用部门":
                            candidates.extend(row.get("_department_candidates") or [])
                        cell_value = _schema_option_from_candidates(child, candidates)
                    cells.append({"name": str(name), "value": str(cell_value)})
                rows.append(cells)
            value = json.dumps(rows, ensure_ascii=False)
        elif component_type == "DDMultiSelectField":
            selections = value if isinstance(value, list) else [value]
            value = json.dumps(
                [_schema_option_value(item, selection) for selection in selections if str(selection)],
                ensure_ascii=False,
            )
        elif component_type == "DDSelectField":
            value = _schema_option_value(item, value)
        elif isinstance(value, (list, dict)):
            value = json.dumps(value, ensure_ascii=False)
        if value in (None, "") and not bool(props.get("required")):
            continue
        result.append({"id": props.get("id") or item.get("id"), "name": label, "value": str(value), "componentType": component_type})
    return result


def resolve_display_fields_from_schema(
    schema: dict[str, Any], display_fields: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    schema_items = {
        DingTalkClient._label(item): item
        for item in DingTalkClient._schema_items(schema)
        if DingTalkClient._label(item)
    }
    resolved: list[dict[str, Any]] = []
    for field in display_fields:
        copy = {**field}
        item = schema_items.get(str(field.get("name") or ""))
        value = field.get("value")
        if item and (item.get("componentName") or item.get("componentType")) == "TableField" and isinstance(value, list):
            children = {
                DingTalkClient._label(child): child
                for child in item.get("children", [])
                if isinstance(child, dict) and DingTalkClient._label(child)
            }
            rows: list[dict[str, Any]] = []
            for row in value:
                row_copy = dict(row) if isinstance(row, dict) else {}
                for name, child in children.items():
                    if name not in row_copy:
                        continue
                    component_type = child.get("componentName") or child.get("componentType")
                    if component_type not in {"DDSelectField", "DDMultiSelectField"}:
                        continue
                    candidates = [row_copy[name]]
                    if name == "发生费用部门":
                        candidates.extend(row_copy.get("_department_candidates") or [])
                    row_copy[name] = _schema_option_from_candidates(child, candidates)
                rows.append(row_copy)
            copy["value"] = rows
        resolved.append(copy)
    return resolved


def _schema_options(item: dict[str, Any]) -> list[str]:
    options: list[str] = []
    for raw in (item.get("props") or {}).get("options") or []:
        option: Any = raw
        if isinstance(raw, str):
            try:
                option = json.loads(raw)
            except json.JSONDecodeError:
                option = raw
        if isinstance(option, dict):
            value = option.get("value") or option.get("label")
        else:
            value = option
        text = str(value or "").strip()
        if text:
            options.append(text)
    return options


def _schema_option_value(item: dict[str, Any], value: Any) -> str:
    text = str(value or "").strip()
    options = _schema_options(item)
    if not text or not options or text in options:
        return text
    label = DingTalkClient._label(item)
    mapped = FORM_OPTION_ALIASES.get(label, {}).get(text, "")
    if mapped in options:
        return mapped
    suffix_matches = [option for option in options if option.endswith(f"-{text}")]
    if len(suffix_matches) == 1:
        return suffix_matches[0]
    raise DingTalkError(
        f"表单字段“{label}”的值“{text}”不在当前钉钉模板的可选范围内。"
        "请在审批模板中增加该选项，或调整付款部门归属。"
    )


def _schema_option_from_candidates(item: dict[str, Any], candidates: Iterable[Any]) -> str:
    errors: list[DingTalkError] = []
    for candidate in dict.fromkeys(str(value or "").strip() for value in candidates):
        if not candidate:
            continue
        try:
            return _schema_option_value(item, candidate)
        except DingTalkError as exc:
            errors.append(exc)
    if errors:
        raise errors[-1]
    return ""


def wire_form_values(form_values: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    """Keep only fields accepted by DingTalk's FormComponentValue API type."""
    return [
        {"name": str(field.get("name") or ""), "value": str(field.get("value") or "")}
        for field in form_values
        if field.get("name")
    ]


def legacy_wire_form_values(form_values: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    """Serialize form values for DingTalk's legacy process-instance API."""
    result: list[dict[str, str]] = []
    for field in form_values:
        name = str(field.get("name") or "")
        if not name:
            continue
        value = str(field.get("value") or "")
        if field.get("componentType") == "DepartmentField" and value:
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                parsed = None
            if not isinstance(parsed, list):
                try:
                    parsed = [int(part.strip()) for part in value.split(",") if part.strip()]
                except ValueError as exc:
                    raise DingTalkError(f"部门控件“{name}”必须填写有效的部门 ID。") from exc
            value = json.dumps(parsed, ensure_ascii=False)
        result.append({"name": name, "value": value})
    return result


def form_submission_diagnostics(form_values: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return field metadata that is safe to persist in approval attempt logs."""
    return [
        {
            "name": str(field.get("name") or ""),
            "component_type": str(field.get("componentType") or ""),
            "has_value": field.get("value") not in (None, ""),
        }
        for field in form_values
    ]
