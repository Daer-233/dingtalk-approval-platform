# 付款预览 MCP · 工作知识助手接入 V1

此目录是接入组件源码，连接现有旧接口付款平台和已经部署的 LangBot Codex 工作知识助手。它不包含完整知识助手、员工资料、企业凭据或付款数据。

当前为本人单聊试用版本。已完成独立服务、Host 授权、原平台拆分和网页定位验证；真实钉钉文件上传链路仍待本人验收，不能将已有模拟测试称为完整上线验收。

## 能力

- 在已绑定人员的钉钉单聊中接收一个未加密 `.xlsx`，最大 20 MB。
- 使用现有餐补/补贴表格式和平台归集规则，返回公司、部门、人数、金额与待处理项。
- 生成本地拆分文件和审批预览；缺少部门、付款资料或手选审批人时进入平台处理。
- 卡片显示真实处理状态；正式提交始终留在原平台的确认页面。
- 仅本人通过助手创建的任务可被查询。原平台历史任务不自动开放。

五个工具为 `payment_import_excel`、`payment_get_task`、`payment_prepare_preview`、`payment_list_tasks`、`payment_get_review_link`。服务没有提交、重试提交、批准审批或修改付款账户的工具。

## 接入方式

```text
钉钉真实事件 → LangBot Host 身份验证及附件引用
            → 本次运行的工具授权快照
            → MCP 工具调用 → 旧接口付款平台
            → 部门汇总、待处理项和平台审核链接
```

MCP 使用官方 Python SDK `mcp==1.26.0`，默认只监听 `127.0.0.1:8070`。LangBot Host 通过临时签名授权调用 `/mcp/`；模型看不到共享密钥或原附件下载地址。工具调用保留原有插件身份、运行会话、操作权限和冻结工具来源检查。

本次接入不依赖 BM25/BERT 的分类结果，不改变其影子模式，也不替换既有知识检索、图片与引用流程。

## 运行前提

- Windows 与已验证的 Python 3.11；Node.js 22 或更新版本（平台辅助启动器使用 `util.parseEnv`）。
- 现有旧接口付款平台及前端依赖已经安装。
- LangBot 中已部署工作知识助手的 `knowledge_card`、部门身份验证和 Codex Host 桥接代码。此包不是面向任意 LangBot 版本的通用插件。
- Docker Desktop 正常运行。Host 安装器使用现有 `langbot` 容器并为修改文件建立独立备份。

可以单独保留此目录运行 MCP；如需并入现有工作助手，将这里的 `apps/payment-mcp`、`instances/payments` 及相关 `scripts` 合并到工作助手目录。不要覆盖原有完整 `knowledge_card` 目录。

## 初始化与启动

以下命令在本接入目录或合并后的工作助手根目录执行：

```powershell
python -m venv --system-site-packages data/payment-mcp/venv
.\data\payment-mcp\venv\Scripts\python.exe -m pip install -r apps/payment-mcp/requirements.txt
.\data\payment-mcp\venv\Scripts\python.exe scripts/payment-instance.py init
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-payment-mcp.ps1
```

初始化生成私有 `instances/payments/config.private.json` 和随机签名密钥，默认关闭。由部署者核实本人钉钉人员 ID、企业 ID、机器人和处理器绑定，填写后端地址及手机可访问的平台网页地址；身份为空不能开启。

现有平台启动器和前端补丁支持 `PAYMENT_PLATFORM_ROOT`。在其他电脑或本仓库副本使用前显式指定平台根目录；脚本中的原本机默认路径仅供原部署环境使用。

```powershell
$env:PAYMENT_PLATFORM_ROOT = '你的旧接口平台根目录'
node scripts/patch-payment-platform.cjs
node scripts/patch-payment-platform.cjs --apply
node scripts/start-payment-platform.cjs
```

前端补丁只增加 `?task_id=` 定位能力，先备份再修改；上下文不匹配时会停止，不能强行覆盖源码。修改后在平台 `frontend` 目录执行 `npm run build`。

## 安装 Host 衔接

先执行独立服务测试，确认本机配置正确，再安装：

```powershell
.\data\payment-mcp\venv\Scripts\python.exe scripts/install-payment-mcp.py --deploy
```

安装器仅给现有 Host 增加窄范围衔接，为原文件保存备份，保留本机已有修改。需要重启 LangBot 才加载代码，应在没有执行中任务时进行。

本人配置核实后才开启：

```powershell
.\data\payment-mcp\venv\Scripts\python.exe scripts/set-payment-mode.py on
```

开启会把已核对的本人信息及授权代次同步给 Host。每次切换都会改变代次，关闭后再开启不会恢复旧授权。关闭使用同一命令的 `off` 参数，下一次运行不再提供付款工具；服务器也拒绝旧代次的调用。

健康检查为 `http://127.0.0.1:8070/health`，其中 `enabled` 表示开关，`pilot_bound` 表示是否已绑定，`can_submit` 始终为 `false`。只输入 MCP 地址不能取得调用权限。

## 验证与边界

测试命令、实测口径及未完成项目见 [VALIDATION.md](VALIDATION.md)。后续部署和对话交接见 [HANDOFF.md](HANDOFF.md)。

原平台网页沿用既有局域网访问边界。本包的本人限制适用于新增机器人和 MCP 入口，不代表原平台已经具备多用户登录与完整网页访问隔离。

不确定的创建结果会标记待核对，不自动重新创建。已经开始执行的本地预览操作不会因关闭开关自动回滚。

## 恢复与私有数据

```powershell
.\data\payment-mcp\venv\Scripts\python.exe scripts/set-payment-mode.py off
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/stop-payment-mcp.ps1
```

仅关闭能力通常无需撤销 Host 源码。若要恢复源码，使用 `install-payment-mcp.py --rollback` 并重启 LangBot；文件若已被其他工作修改，脚本拒绝覆盖，应人工比较备份。前端补丁可用 `patch-payment-platform.cjs --rollback` 恢复，再重建前端。

不得提交 `config.private.json`、Host 签名密钥、人员 ID 配置、数据库、运行日志、付款附件、真实工作簿、浏览器状态或备份。仓库仅包含源码、默认关闭的配置示例和无真实员工数据的测试代码。