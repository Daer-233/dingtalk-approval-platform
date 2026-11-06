# 付款预览 MCP V1 验证记录

验证日期：2026-11-06。此记录区分模拟测试、真实平台验证和待完成的钉钉客户端验收。

| 验证 | 结果 | 范围 |
|---|---|---|
| 独立 MCP 与服务测试 | 15 项通过 | 实际 MCP HTTP 传输、文件校验、归属、重复事件、幂等、超时及重启恢复、签名与授权撤销 |
| LangBot Host 衔接测试 | 9 项通过 | 实际运行授权注册表和来源校验；插件身份、操作限制、撤销、非本人/群聊拒绝、原工具保留、流式上下文 |
| 原卡片及流式回归 | 12 项通过 | 图片/依据处理、状态及现有 Codex 增量兼容 |
| 任务链接函数 | 1 项通过 | 32 位任务编号、UUID、缺省与非法链接的区分 |
| 原平台真实 HTTP 路径 | 通过 | 使用已确认虚拟样表分析、生成拆分文件、流程预测及预览 |
| 网页真实浏览器检查 | 通过 | 链接进入目标任务；桌面 1280、手机 390 宽度均无横向溢出 |
| 本人钉钉真实文件上传 | 待验收 | 当前没有可用的钉钉桌面窗口，未替本人发送样表 |

真实平台样表为 3 名虚拟员工、2 个部门，总额 270.00 元，拆分结果为 120.00 和 150.00，人数与金额一致。平台返回 `needs_review`，网页正式提交按钮禁用；这属于预览及缺项行为验证，不代表已满足付款条件。

本轮测试的审批创建调用为 0，新任务审批提交记录为 0，没有向员工发送测试消息。没有进行真实付款或替审批人批准申请。

模拟测试包括：签名篡改、错误人员/企业/机器人/处理器、群聊、未知或其他归属的任务、错误工具名、非法下载地址、多附件、无用途文件输入、重复附件与事件冲突、非 `.xlsx`/非 ZIP 内容、大小限制、未知部门、已有提交记录、关闭后旧授权不复活。模拟输入不能等同于真实钉钉下载地址和所有生产附件格式的完整兼容验收。

## 复验

在接入组件根目录准备独立环境后执行：

```powershell
.\data\payment-mcp\venv\Scripts\python.exe -m unittest discover -s apps/payment-mcp/tests -v
node --test scripts/test-payment-task-link.cjs
```

Host 测试需在已经安装衔接代码及目录的 LangBot 容器内运行：

```powershell
docker cp scripts/test-payment-host.py langbot:/app/data/test-payment-host.py
docker exec langbot /app/.venv/bin/python /app/data/test-payment-host.py
```

卡片的 12 项回归来自工作知识助手原有测试文件，不随此独立组件重复拷贝；上传的是付款组件及衔接代码，未复制整个知识助手。

## 真实平台样表复验

仅当本人身份、企业配置及原平台已经核对后执行：

```powershell
.\data\payment-mcp\venv\Scripts\python.exe scripts/make-payment-test-workbook.py
.\data\payment-mcp\venv\Scripts\python.exe scripts/verify-payment-platform.py
```

脚本会在原平台创建虚拟测试任务、生成本地附件并请求审批路径预测，但不调用审批提交或重试接口。验收器核对指定文件确为固定虚拟样表，不能将其他员工工作簿替换进去。报告和生成文件保存在私有 `data/payments/acceptance`，不随源码上传。

后续本人在 LSD AI助手单聊中发送虚拟样表并说明用途，检查导入、金额汇总、缺项提示和任务链接。只有观察到真实事件及对应任务记录后，才能补记钉钉端到端通过。