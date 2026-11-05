# 审批诊断实验版

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
- [@vitejs/plugin-react](https://www.npmjs.com/package/@vitejs/plugin-react)：为 Vite 提供 React 编译支持。
- [Lucide](https://github.com/lucide-icons/lucide)：提供界面图标。
