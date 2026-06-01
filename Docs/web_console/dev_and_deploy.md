# Dev & Deploy（最小流程）

目标：在云服务器（Linux）上一条命令启动 Web Console；手机用 SSH 端口转发访问。

## 服务器启动（Linux）

在 Godotter 仓库根目录执行：
- `bash bin/run_web.sh`
停止：
- `bash bin/stop_web.sh`
重启：
- `bash bin/restart_web.sh`

默认监听：`127.0.0.1:9898`（可通过环境变量覆盖）
- `GODOTTER_WEB_PORT=9898`
- `GODOTTER_WEB_HOST=127.0.0.1`

## 手机访问（Termux）

端口转发：
- `ssh -L 9898:127.0.0.1:9898 user@your-server`

然后在手机浏览器打开：
- `http://127.0.0.1:9898/`

健康检查：
- `http://127.0.0.1:9898/health`

## 编辑 `.env`

打开：
- `http://127.0.0.1:9898/env`

可选：设置环境变量 `GODOTTER_WEB_TOKEN` 后，请求必须带 Header：`x-godotter-token: <token>`
