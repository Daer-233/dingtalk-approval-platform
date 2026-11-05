# 钉钉审批平台 · 旧接口版（main）

用于旧版钉钉审批接口的付款拆分与局域网工作台，包含配套前端和后端。

## 版本分支

默认分支为 `main`。各分支的项目文件位于仓库根目录。

| 分支 | 版本 |
| --- | --- |
| [main](https://github.com/Daer-233/dingtalk-approval-platform/tree/main) | 旧接口版 |
| [modern](https://github.com/Daer-233/dingtalk-approval-platform/tree/modern) | 新接口版 |
| [diagnostic](https://github.com/Daer-233/dingtalk-approval-platform/tree/diagnostic) | 只读诊断实验版 |
| [sdk-test](https://github.com/Daer-233/dingtalk-approval-platform/tree/sdk-test) | Python SDK 测试版 |

## 本地启动

准备 Python、Node.js 和 npm，在 Windows PowerShell 中从仓库根目录执行安装命令。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
Push-Location frontend
npm ci
npm run build
Pop-Location
.\启动局域网服务.ps1
```

按配置模板在本机填写企业应用、审批模板和发起人信息。启动脚本会使用仓库内的 Python 环境。前端入口为 `http://127.0.0.1:5173`，后端为 `http://127.0.0.1:8000`。通过同目录中的关闭或重启脚本管理服务。

## 前后端结构

- [app](app/) 包含后端服务、表格拆分、目录查询与审批创建逻辑。
- [frontend](frontend/) 包含 React/Vite 源码、样式、依赖锁文件和前端测试。开发时在该目录运行 `npm run dev`，发布构建使用 `npm run build`。
- [tests](tests/) 保存已有后端测试。安装测试依赖后，在仓库根目录使用 `python -m pytest tests`。

## 旧接口行为

审批创建使用 `/topapi/processinstance/create`。执行显式审批链时，服务会验证已确认的链路与预览指纹。运行时应按本地企业配置与测试模板完成联调。数据库、付款附件与企业凭据从有权访问的交付资料恢复，或使用测试资料初始化。

## 版本来源

代码提取自 `Daer-233/approval-and-early-projects` 的 `补贴付款拆分平台_局域网版` 目录，固定提交为 `8ed44e6b74121d09eff9b1251ea6dd2e283fb59d`。迁移前的完整目录结构保存在归档标签 [`archive/pre-version-split-20261105-4b618d810467`](https://github.com/Daer-233/dingtalk-approval-platform/tree/archive/pre-version-split-20261105-4b618d810467)。交付资料与本地配置范围见 [上传说明](GITHUB_UPLOAD_NOTES.md)。
