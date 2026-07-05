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
