# 钉钉审批平台 · 只读诊断实验版（diagnostic）

用于钉钉查询、流程预览和诊断，保留隔离的前后端入口与原有操作限制。

## 版本分支

默认分支为 `main`。各分支的项目文件位于仓库根目录。

| 分支 | 版本 |
| --- | --- |
| [main](https://github.com/Daer-233/dingtalk-approval-platform/tree/main) | 旧接口版 |
| [modern](https://github.com/Daer-233/dingtalk-approval-platform/tree/modern) | 新接口版 |
| [diagnostic](https://github.com/Daer-233/dingtalk-approval-platform/tree/diagnostic) | 只读诊断实验版 |
| [sdk-test](https://github.com/Daer-233/dingtalk-approval-platform/tree/sdk-test) | Python SDK 测试版 |

## 从仓库根目录运行

按下文和项目的 `.env.example` 配置本地环境。网页前端位于 [frontend](frontend/)，在该目录安装依赖并运行 `npm ci`、`npm run build` 验证构建。启动入口为 `启动局域网服务.bat`，前端使用 5175 端口，后端使用 8012 端口。Python 依赖见 [requirements.txt](requirements.txt)。

实验版也提供 `启动审批诊断实验版.bat`，详细步骤见 [实验版使用说明](实验版使用说明.md)。

本副本只做钉钉查询和本地诊断，上传、创建及审批操作在发送前被阻止。前端 5175，后端 8012。

请阅读 [实验版使用说明](实验版使用说明.md) 和 [验收结果](验收结果.md)。双击 `启动审批诊断实验版.bat` 运行。

其他源版本说明中涉及正式提交的内容不适用于本实验版。

## 开源依赖与来源

### 实际使用

- [FastAPI](https://github.com/fastapi/fastapi)：提供后端 HTTP API。
- [Uvicorn](https://github.com/encode/uvicorn)：运行 ASGI 服务。
- [python-multipart](https://github.com/Kludex/python-multipart)：处理表单文件上传。
- [openpyxl](https://pypi.org/project/openpyxl/)：读取 Excel 工作簿并生成拆分结果。
- [odfpy](https://github.com/eea/odfpy)：读取 OpenDocument 表格。
- [numbers-parser](https://pypi.org/project/numbers-parser/)：读取 Apple Numbers 表格。
- [pywin32](https://github.com/mhammond/pywin32)：调用 Windows COM 和自动化接口。
- [HTTPX](https://github.com/encode/httpx)：发起 HTTP 请求及测试接口。
- [python-dotenv](https://github.com/theskumar/python-dotenv)：加载本地环境文件。
- [React / React DOM](https://github.com/facebook/react)：渲染 Web 界面。
- [Vite](https://github.com/vitejs/vite)：本地开发与前端构建。
- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react)：为 Vite 提供 React 编译支持。
- [Lucide](https://github.com/lucide-icons/lucide)：提供界面图标。
## 版本来源

代码提取自 `Daer-233/dingtalk-approval-platform` 的 `补贴付款拆分平台_审批诊断实验版` 目录，固定提交为 `4b618d810467355b65386f1e72da7342392db76f`。迁移前的完整目录结构保存在归档标签 [`archive/pre-version-split-20261105-4b618d810467`](https://github.com/Daer-233/dingtalk-approval-platform/tree/archive/pre-version-split-20261105-4b618d810467)。交付资料与本地配置范围见 [上传说明](GITHUB_UPLOAD_NOTES.md)。
