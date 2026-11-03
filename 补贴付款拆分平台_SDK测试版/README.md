# 钉钉审批 Python SDK 独立测试工具

使用 `alibabacloud-dingtalk==2.2.60`（Tea SDK），对照新接口请求序列化，并支持显式的只读联通检查与单次创建测试。与现有网页平台、数据库、任务队列完全独立。

## 安装与缓存

将项目放在非 C 盘。已有 Python 安装可以位于 C 盘；项目虚拟环境、pip 缓存、临时文件、Python 缓存、pytest 缓存和输出都位于本项目目录。脚本只设置子进程环境，不修改系统全局设置。

```powershell
.\setup.ps1 -Python 'C:\Users\YOUR_USER\AppData\Local\Programs\Python\Python314\python.exe'
.\run.ps1 test
```

验证环境为 Python 3.14.5。安装优先使用 `requirements.lock.txt`，不复制或运行压缩包里的字节码。仓库不重复存放第三方 SDK 源码。

## 三个命令

```powershell
# 完全离线；对照现有 HTTP JSON 与 SDK 模型序列化结果。
.\run.ps1 compare --request fixtures/request.example.json

# 仅预览，不读取凭据、不联网。
.\run.ps1 create --request fixtures/request.example.json

# 显式只读：取 token、读取模板、预测流程。不创建审批或上传附件。
.\run.ps1 inspect --request requests.local/request.json --env-file 'I:\YOUR_PROJECT\.env'
```

`fixtures/request.example.json` 仅为合成数据，不能用于真实业务。可将现有程序产生的完整请求 JSON 保存在被 Git 忽略的 `requests.local` 目录；工具不会自动导入其他平台的数据库、附件或任务。

未来得到真实测试授权后，可执行：

```powershell
.\run.ps1 create --request requests.local/request.json --env-file 'I:\YOUR_PROJECT\.env' --execute
```

一次调用只发送一次创建请求，SDK 自动重试关闭，不调用旧接口。无实例 ID、连接中断或服务端状态不明时返回 `uncertain`，必须先人工核对钉钉审批列表，不能直接重复运行。工具不上传附件、不代选审批人、不审批、不付款。

## 请求与结果

- 请求字段使用新接口的 camelCase。`deptId` 和 `microappAgentId` 为整数；控件 `value` 为字符串。
- 明细 `value` 可包含二维数组 JSON 字符串，`details` 是控件对象列表；不自动改变金额、选项、计算字段或人员。
- 不传的可选参数保持不传。SDK 丢弃或改变字段时，报告差异并阻止创建。
- SDK 的 `from_map` / `to_map` 会给遗漏的列表补 `[]`；工具仅对本次请求模型保留输入字段集合，避免多传 `approvers` 或 `details`，不修改第三方 SDK 文件。
- 请求体不包含 accessToken；token 通过 `x-acs-dingtalk-access-token` 请求头传入。
- 创建请求使用 `originatorUserId`；工具执行只读预测时显式映射为预测接口要求的 `userId`。
- 凭据必须通过明确的 `--env-file` 路径读取，不自动搜索 `.env`，不修改原配置。
- 脱敏结果保存在 `output`；隐藏 token、密钥、人员 ID、控件值及 URL。退出码：0 为离线一致、预览、只读完成或返回实例 ID；2 为本地或创建前错误；3 为创建失败或结果不明。

切换 SDK 不会改变钉钉服务端校验规则，也不保证解决 `processInstanceStartFailed`。

## 各版本

GitHub 默认 `main` 为现有新接口平台，`legacy` 为旧接口平台，`diagnostic` 为只读诊断实验版，`sdk-test` 为本独立工具。版本间不自动合并。
