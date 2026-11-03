# 钉钉审批平台 — main

这是对应本地版本的脱敏源码快照。仓库默认分支 `main` 为新接口平台；`legacy` 为旧接口平台；`diagnostic` 为只读诊断实验版；`sdk-test` 为独立 Tea SDK 测试工具。

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
cd frontend
npm ci
```

在本地 `.env` 中填写企业配置。业务数据库、付款资料、附件和历史任务未上传，需要在本地初始化并配置。具体运行入口见本分支使用说明和启动脚本。诊断版保留只读隔离；其他版本的正式提交由原有开关控制。

历史单次真实创建脚本仅作为源码参考，包含脱敏占位 ID，不能直接用于真实提交。不要通过运行测试脚本猜测或替换企业配置。

本仓库不保存密钥、token、真实数据库、付款附件、原始诊断输出、依赖环境或缓存。使用 SDK 测试工具时，所有缓存由其 G 盘启动脚本管理。
