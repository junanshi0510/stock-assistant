# 金融投资助手上线部署

推荐方式：一台 Linux 云服务器运行 FastAPI 后端，Nginx 托管 React 构建产物，并把 `/api` 反向代理到后端。

这样其他人访问 `https://你的域名` 就能直接使用，前端仍然请求相对路径 `/api/...`，不需要在前端写死后端地址。

## 1. 服务器准备

建议配置：

- Ubuntu 22.04/24.04
- 2 核 4G 起步，基金/多股对比会抓真实数据，1 核 1G 容易慢
- 开放安全组端口：`80`、`443`

安装依赖：

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nodejs npm nginx git
```

如果系统 Node.js 版本低于 18，建议用 NodeSource 或 nvm 安装 Node.js 20+。

## 2. 上传项目

假设部署到：

```bash
/opt/stock-assistant
```

可以用 Git 拉取，也可以把当前项目打包上传：

```bash
sudo mkdir -p /opt/stock-assistant
sudo chown -R $USER:$USER /opt/stock-assistant
cd /opt/stock-assistant
```

把本项目文件放到该目录后继续。

## 3. 安装后端

```bash
cd /opt/stock-assistant
python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt
```

本地测试后端：

```bash
cd /opt/stock-assistant/backend
/opt/stock-assistant/venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

另开一个终端测试：

```bash
curl http://127.0.0.1:8000/api/markets
```

## 4. 构建前端

```bash
cd /opt/stock-assistant/frontend
npm install
npm run build
sudo mkdir -p /var/www/stock-assistant
sudo rm -rf /var/www/stock-assistant/*
sudo cp -r dist/* /var/www/stock-assistant/
```

## 5. 配置后端常驻服务

复制模板：

```bash
sudo cp /opt/stock-assistant/deploy/stock-assistant-api.service /etc/systemd/system/
```

编辑域名：

```bash
sudo nano /etc/systemd/system/stock-assistant-api.service
```

把：

```ini
Environment="ALLOWED_ORIGINS=https://your-domain.com"
```

改成你的真实域名，例如：

```ini
Environment="ALLOWED_ORIGINS=https://fund.example.com"
```

启动服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now stock-assistant-api
sudo systemctl status stock-assistant-api
```

查看日志：

```bash
journalctl -u stock-assistant-api -f
```

## 6. 配置 Nginx

复制模板：

```bash
sudo cp /opt/stock-assistant/deploy/nginx-stock-assistant.conf /etc/nginx/sites-available/stock-assistant
sudo ln -s /etc/nginx/sites-available/stock-assistant /etc/nginx/sites-enabled/stock-assistant
```

编辑域名：

```bash
sudo nano /etc/nginx/sites-available/stock-assistant
```

把：

```nginx
server_name your-domain.com;
```

改成你的真实域名。

检查并重载：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

现在访问：

```text
http://你的域名
```

应该已经能打开页面。

## 7. 配置 HTTPS

使用 Certbot 自动签发免费证书：

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d 你的域名
```

完成后访问：

```text
https://你的域名
```

## 8. 更新发布

每次代码更新后：

```bash
cd /opt/stock-assistant
git pull

source venv/bin/activate
pip install -r backend/requirements.txt

cd frontend
npm install
npm run build
sudo rm -rf /var/www/stock-assistant/*
sudo cp -r dist/* /var/www/stock-assistant/

sudo systemctl restart stock-assistant-api
sudo systemctl reload nginx
```

## 9. 生产注意事项

- 前端必须通过 Nginx 托管 `frontend/dist`，不要用 `npm run dev` 对外提供服务。
- 后端只监听 `127.0.0.1:8000`，不要直接暴露公网端口 8000。
- 真实数据源依赖服务器网络环境；如果服务器访问东方财富、天天基金、Tushare、海外源不稳定，需要在服务器网络或代理规则里处理。
- 如果美股基本面/新闻要稳定使用，需要在 `backend/config.py` 配置 Alpha Vantage/Polygon 等真实数据源 Key。
- 如果前端和后端拆成两个域名，需要设置后端环境变量 `ALLOWED_ORIGINS=https://前端域名`。
- “我的持仓”截图导入功能需要真实 OCR 服务。默认支持阿里云 OCR，未配置 AccessKey 时可以先用“粘贴识别文本/手动添加”。

## 10. 云服务器部署建议

本项目推荐整站部署到云服务器，不建议用 GitHub Pages，因为 GitHub Pages 不能运行 FastAPI 后端。

推荐最终结构：

```text
用户 -> https://your-domain.com/        -> Nginx 静态前端
用户 -> https://your-domain.com/api/... -> Nginx 反代到 127.0.0.1:8000 FastAPI
```

这样浏览器看到的是同一个域名，前端可以继续请求相对路径 `/api/...`，不用处理跨域问题。

如果你后续想拆成两个域名，例如：

```text
https://www.your-domain.com 前端
https://api.your-domain.com 后端
```

则需要：

- 前端构建时设置 `VITE_API_BASE_URL=https://api.your-domain.com`
- 后端服务设置 `ALLOWED_ORIGINS=https://www.your-domain.com`

但第一版上线建议先用单域名同站部署，排障最少。

## 11. 启用阿里云 OCR 截图识别

“我的持仓”支持上传基金/股票持仓截图并识别文字。该功能不会使用假数据；如果没有配置真实 OCR，接口会明确提示需要配置 AccessKey。

启用步骤：

1. 在阿里云控制台开通文字识别 OCR。
2. 创建 RAM 用户或使用已有 AccessKey，并授权 OCR 调用权限。
3. 编辑后端 systemd 服务：

```bash
sudo nano /etc/systemd/system/stock-assistant-api.service
```

加入或取消注释：

```ini
Environment="ALIBABA_CLOUD_ACCESS_KEY_ID=你的AccessKeyId"
Environment="ALIBABA_CLOUD_ACCESS_KEY_SECRET=你的AccessKeySecret"
Environment="ALIYUN_OCR_ENDPOINT=ocr-api.cn-hangzhou.aliyuncs.com"
```

4. 更新依赖并重启：

```bash
cd /opt/stock-assistant
source venv/bin/activate
pip install -r backend/requirements.txt
sudo systemctl daemon-reload
sudo systemctl restart stock-assistant-api
sudo systemctl status stock-assistant-api
```

隐私建议：

- 上传截图前尽量打码姓名、手机号、账号和银行卡。
- 第一版只保存用户确认后的持仓结果，不长期保存原图。
- 后续接入登录系统后，应把持仓表从默认用户迁移到真实 `user_id`。

## 12. 配置批量基金 Agent 容量

2 核 4 GB 服务器建议保持以下配置：

```ini
Environment="AGENT_WORKER_CONCURRENCY=2"
Environment="AGENT_MAX_BATCH_SIZE=6"
Environment="AGENT_MAX_PENDING_RUNS=20"
```

- `AGENT_WORKER_CONCURRENCY` 是同时执行的单基金 Run 数，不是批次数。代码会把值限制在 `1-4`。
- `AGENT_MAX_BATCH_SIZE` 默认允许一次提交 2-6 只基金，API 代码硬上限为 8。
- `AGENT_MAX_PENDING_RUNS` 同时限制单基金任务和批次子任务，创建批次前会原子检查所需队列名额。
- 单个基金的持仓/新闻工具内部还会使用短生命周期 I/O 线程；不要在 2 核机器上把 Run 并发直接提高到 4。

修改后执行：

```bash
sudo systemctl daemon-reload
sudo systemctl restart stock-assistant-api
curl -s http://127.0.0.1:8000/api/v1/agent/batches?limit=1
```

## 13. 启用证据约束的大模型研判

未配置模型时，Agent 会继续完成真实数据和确定性风险门禁，但明确显示
`model_not_configured`，不会用模板文本冒充模型研判。

可以在阿里云服务器调用 DeepSeek 官方 API。密钥只写入独立环境文件，不写入代码、Git、systemd unit 或聊天记录：

```bash
sudo install -d -m 750 /etc/stock-assistant
sudo install -m 600 /dev/null /etc/stock-assistant/stock-assistant.env
sudo nano /etc/stock-assistant/stock-assistant.env
```

```dotenv
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-v4-flash
DEEPSEEK_API_KEY=你的DeepSeek-API-Key
LLM_THINKING_MODE=disabled
LLM_DATA_REGION=cn
LLM_PRIVATE_CONTEXT_ENABLED=false
```

`deepseek-v4-flash` 适合默认批量研判；需要更强推理且能接受更高延迟时，可改为
`deepseek-v4-pro`，并设置 `LLM_THINKING_MODE=enabled`。不要使用即将停用的
`deepseek-chat` 或 `deepseek-reasoner` 旧别名。

确认 `/etc/systemd/system/stock-assistant-api.service` 的 `[Service]` 包含：

```ini
EnvironmentFile=-/etc/stock-assistant/stock-assistant.env
```

然后执行：

```bash
sudo systemctl daemon-reload
sudo systemctl restart stock-assistant-api
curl -s http://127.0.0.1:8000/api/v1/agent/model/status
```

只有返回 `"configured": true` 才代表模型已接通。当前没有登录和多租户隔离，必须保持
`LLM_PRIVATE_CONTEXT_ENABLED=false`；此时个人持仓只在本机确定性门禁中使用，不会发送给模型。

再执行一次受控基金研究任务，并在 Run 详情中确认 `model.status=available`、模型 ID、
响应 ID、Token 用量和 Evidence 引用均已保存，才算完成真实调用验收。禁止用模拟响应代替。
