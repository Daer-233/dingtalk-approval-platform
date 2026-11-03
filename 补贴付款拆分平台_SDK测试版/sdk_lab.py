"""Isolated DingTalk Tea SDK lab. No network is used by compare or preview."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent


def configure_paths(root=ROOT):
    root = Path(root).resolve()
    if root.drive.lower() == "c:":
        raise ValueError("Place this project on a drive other than C.")
    paths = {"PIP_CACHE_DIR": root / ".cache/pip", "TEMP": root / ".cache/tmp",
             "TMP": root / ".cache/tmp", "PYTHONPYCACHEPREFIX": root / ".cache/pycache"}
    for key, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(path)
    sys.pycache_prefix = str(paths["PYTHONPYCACHEPREFIX"])
    tempfile.tempdir = str(paths["TEMP"])
    return {key: str(path) for key, path in paths.items()}


configure_paths()

from alibabacloud_dingtalk.workflow_1_0.client import Client as WorkflowClient
from alibabacloud_dingtalk.workflow_1_0 import models as workflow
from alibabacloud_dingtalk.oauth2_1_0.client import Client as OAuthClient
from alibabacloud_dingtalk.oauth2_1_0 import models as oauth
from alibabacloud_tea_openapi import models as openapi
from alibabacloud_tea_util import models as util
from dotenv import dotenv_values


def runtime():
    return util.RuntimeOptions(autoretry=False, max_attempts=1,
                               connect_timeout=10000, read_timeout=30000)


def client_config():
    return openapi.Config(protocol="https", region_id="central", endpoint="api.dingtalk.com")


def load_request(path):
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Request JSON must be an object.")
    return value


def build_request(payload):
    for name in ("originatorUserId", "processCode"):
        if not isinstance(payload.get(name), str) or not payload[name].strip():
            raise ValueError(f"{name} must be a nonempty string.")
    for name in ("deptId", "microappAgentId"):
        if name in payload and type(payload[name]) is not int:
            raise ValueError(f"{name} must be an integer; no implicit conversion is performed.")
    values = payload.get("formComponentValues")
    if not isinstance(values, list) or not values:
        raise ValueError("formComponentValues must be a nonempty list.")
    def check_fields(fields):
        if not isinstance(fields, list):
            raise ValueError("details must be a list of field objects, not row arrays.")
        for field in fields:
            if not isinstance(field, dict):
                raise ValueError("Each field/detail must be an object.")
            if not isinstance(field.get("name"), str) or not isinstance(field.get("value"), str):
                raise ValueError("Field name and value must be strings.")
            if "details" in field:
                check_fields(field["details"])
    check_fields(values)
    request = workflow.StartProcessInstanceRequest().from_map(payload)
    # Generated from_map initializes absent lists as []. Restore omission so
    # approvers/details are not accidentally added to the actual HTTP body.
    def restore_omitted(model, source):
        for attr, item in vars(model).items():
            key = re.sub(r"_([a-z])", lambda match: match.group(1).upper(), attr)
            if key not in source:
                setattr(model, attr, None)
            elif isinstance(item, list) and isinstance(source[key], list):
                for child, original in zip(item, source[key]):
                    if hasattr(child, "to_map") and isinstance(original, dict):
                        restore_omitted(child, original)
        original_to_map = model.to_map
        # to_map itself also emits [] for absent list fields. This instance-only
        # wrapper preserves the source key set without patching the SDK package.
        model.to_map = lambda: {key: value for key, value in original_to_map().items()
                                if key in source}
    restore_omitted(request, payload)
    return request


def differences(left, right, path="$"):
    if type(left) is not type(right):
        return [{"path": path, "reason": "type_changed"}]
    if isinstance(left, dict):
        result = []
        for key in sorted(left.keys() | right.keys()):
            child = f"{path}.{key}"
            if key not in left or key not in right:
                result.append({"path": child, "reason": "missing_or_added"})
            else:
                result.extend(differences(left[key], right[key], child))
        return result
    if isinstance(left, list):
        if len(left) != len(right):
            return [{"path": path, "reason": "length_changed"}]
        return [d for i, (a, b) in enumerate(zip(left, right))
                for d in differences(a, b, f"{path}[{i}]")]
    return [] if left == right else [{"path": path, "reason": "value_changed"}]


def compare(payload):
    mapped = build_request(payload).to_map()
    diff = differences(payload, mapped)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"equal": not diff, "differences": diff,
            "request_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
            "endpoint": "https://api.dingtalk.com/v1.0/workflow/processInstances",
            "method": "POST", "sdk": "alibabacloud-dingtalk==2.2.60"}


def redact(value, secrets=()):
    """Reports are deliberately narrower than the original requests/responses."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if re.search(r"token|secret|password|authorization|appkey|client.?id", key, re.I):
                result[key] = "[REDACTED]"
            elif key in {"originatorUserId", "actionerUserIds", "userIds", "fileId", "spaceId"}:
                result[key] = "[REDACTED]"
            elif key == "value" and "name" in value:
                result[key] = "[REDACTED]"
            else:
                result[key] = redact(item, secrets)
        return result
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"https?://[^\s\"<>]+", "[URL REDACTED]", value)
        value = re.sub(r"(?<!\d)\d{12,}(?!\d)", "[NUMBER REDACTED]", value)
    return value


def load_credentials(env_file):
    # Do not discover .env files or change the parent process environment.
    if not env_file or not Path(env_file).is_file():
        raise ValueError("An explicit existing --env-file is required for network access.")
    values = dotenv_values(Path(env_file), interpolate=False)
    required = ("DINGTALK_CLIENT_ID", "DINGTALK_CLIENT_SECRET")
    if any(not values.get(name) for name in required):
        raise ValueError("The selected env file is missing Client ID or Client Secret.")
    return values


def access_token(credentials, client=None):
    client = client or OAuthClient(client_config())
    response = client.get_access_token_with_options(
        oauth.GetAccessTokenRequest(app_key=credentials["DINGTALK_CLIENT_ID"],
                                   app_secret=credentials["DINGTALK_CLIENT_SECRET"]),
        {}, runtime())
    token = response.body.access_token
    if not token:
        raise ValueError("Token response did not contain accessToken.")
    return token


def error_record(exc):
    data = getattr(exc, "data", None) or {}
    if not isinstance(data, dict):
        data = {}
    code = getattr(exc, "code", None) or data.get("code")
    status = (data.get("statusCode") or data.get("status_code")
              or getattr(exc, "status_code", None) or getattr(exc, "statusCode", None))
    try:
        status = int(status) if status is not None else None
    except (ValueError, TypeError):
        status = None
    request_id = (data.get("requestId") or data.get("RequestId") or data.get("request_id")
                  or getattr(exc, "request_id", None))
    message = getattr(exc, "message", None) or str(exc)
    if not request_id:
        match = re.search(r"request\s*id\s*:\s*([A-Za-z0-9-]+)", message, re.I)
        request_id = match.group(1) if match else None
    # An unclassified error after dispatch may mean the request reached the server.
    uncertain = status is None or status >= 500 or status in (408, 429)
    return {"status": "uncertain" if uncertain else "failed", "http_status": status,
            "code": code, "request_id": request_id, "instance_id": None,
            "message": message,
            "retry_allowed": False}


def create_once(payload, token, client=None):
    result = compare(payload)
    if not result["equal"]:
        raise ValueError("SDK serialization differs; inspect compare output before creating.")
    client = client or WorkflowClient(client_config())
    try:
        response = client.start_process_instance_with_options(
            build_request(payload), workflow.StartProcessInstanceHeaders(
                x_acs_dingtalk_access_token=token), runtime())
        body = response.body.to_map()
        nested = body.get("result") if isinstance(body.get("result"), dict) else {}
        instance_id = body.get("instanceId") or nested.get("instanceId")
        return {"status": "created" if instance_id else "uncertain",
                "http_status": response.status_code, "instance_id": instance_id,
                "request_id": body.get("requestId"), "retry_allowed": False}
    except Exception as exc:
        return redact(error_record(exc), (token,))


def build_forecast_request(payload):
    # Forecast uses userId; creation uses originatorUserId. The generated SDK
    # silently drops unknown fields, so do not reuse the creation identity key.
    return workflow.ProcessForecastRequest().from_map({
        "processCode": payload["processCode"],
        "userId": payload["originatorUserId"],
        "deptId": payload["deptId"],
        "formComponentValues": payload["formComponentValues"],
    })


def inspect_readonly(payload, credentials, client=None, token=None):
    token = token or access_token(credentials)
    client = client or WorkflowClient(client_config())
    schema = client.query_schema_by_process_code_with_options(
        workflow.QuerySchemaByProcessCodeRequest(process_code=payload["processCode"]),
        workflow.QuerySchemaByProcessCodeHeaders(x_acs_dingtalk_access_token=token), runtime())
    forecast = client.process_forecast_with_options(
        build_forecast_request(payload),
        workflow.ProcessForecastHeaders(x_acs_dingtalk_access_token=token), runtime())
    # Never export full schema/personnel/business data by default.
    return {"token_obtained": True, "schema_http_status": schema.status_code,
            "forecast_http_status": forecast.status_code,
            "forecast_success": (forecast.body.to_map().get("result") or {}).get("isForecastSuccess"),
            "write_operations": 0}


def save_report(report, output=None):
    folder = ROOT / "output"
    folder.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    path = Path(output).resolve() if output else folder / f"report_{stamp}.json"
    if path.drive.lower() == "c:":
        raise ValueError("Reports must not be written to C.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Report: {path}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("compare", "inspect", "create"))
    parser.add_argument("--request", required=True, help="Explicit request JSON path")
    parser.add_argument("--env-file", help="Explicit local credential file; never auto-discovered")
    parser.add_argument("--execute", action="store_true", help="Execute exactly one create request")
    parser.add_argument("--output", help="Redacted report path on a non-C drive")
    args = parser.parse_args(argv)
    secrets = []
    try:
        # Validate output before network dispatch.
        if args.output and Path(args.output).resolve().drive.lower() == "c:":
            raise ValueError("Reports must not be written to C.")
        payload = load_request(args.request)
        report = compare(payload)
        if args.execute and args.command != "create":
            raise ValueError("--execute is valid only with create.")
        if not report["equal"]:
            save_report(report, args.output)
            return 2
        if args.command == "compare":
            report["status"] = "offline_equal"
        elif args.command == "create" and not args.execute:
            report.update(status="preview_only", request=redact(payload), network_calls=0)
        else:
            credentials = load_credentials(args.env_file)
            secrets = [credentials["DINGTALK_CLIENT_ID"], credentials["DINGTALK_CLIENT_SECRET"]]
            if args.command == "inspect":
                report.update(inspect_readonly(payload, credentials), status="readonly_completed")
            else:
                token = access_token(credentials)
                secrets.append(token)
                report.update(create_once(payload, token))
        report = redact(report, secrets)
        save_report(report, args.output)
        return 0 if report.get("status") not in {"failed", "uncertain"} else 3
    except Exception as exc:
        report = redact({"status": "failed_before_create", "error": str(exc)}, secrets)
        save_report(report, None if args.output and Path(args.output).resolve().drive.lower() == "c:" else args.output)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
