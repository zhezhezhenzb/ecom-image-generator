# 云端部署指南（Render）

## 概述

本服务支持部署到 Render 等云端平台，实现 7x24 小时自动跑图。

**与本地版本的区别**：
- 本地版本：用 lark-cli 用户身份下载附件
- 云端版本：用飞书 OAuth 用户授权 + token 自动刷新下载附件

---

## 部署步骤

### 第一步：准备代码

1. 把 `cloud_service/` 目录的代码推到 GitHub 或 Gitee 仓库
2. 确保仓库包含：`app.py`, `config.py`, `feishu_client.py`, `image_generator.py`, `requirements.txt`, `render.yaml`

### 第二步：飞书开放平台配置

1. 进入飞书开放平台 → 你的应用 →「安全设置」
2. 在「重定向 URL」中添加：
   ```
   https://你的-render-域名.onrender.com/auth/callback
   ```
   （部署后会得到域名，先随便填，部署后再改）
3. 确保已开通权限：`bitable:app`, `drive:drive`, `drive:file:download`
4. 重新发布应用版本

### 第三步：Render 部署

1. 注册 Render 账号：https://render.com
2. 点击「New +」→「Web Service」
3. 连接你的 GitHub/Gitee 仓库
4. 配置：
   - **Runtime**: Python
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn app:app --host 0.0.0.0 --port $PORT`
   - **Plan**: Free（免费）
5. 点击「Advanced」→「Add Environment Variable」，添加以下环境变量：

| 变量名 | 值 | 说明 |
|--------|-----|------|
| `FEISHU_APP_ID` | cli_aa16f4979938dbcd | 飞书应用 App ID |
| `FEISHU_APP_SECRET` | 你的 App Secret | 飞书应用 App Secret |
| `BASE_TOKEN` | ZeQebBA2qaUwNksPJZfckXZ9nfb | 多维表格 token |
| `TABLE_ID` | tblKkLKHAHdvT4E5 | 数据表 ID |
| `API_KEYS` | 你的 API Key | laozhang.ai API Key（多个用逗号分隔） |
| `FEISHU_REDIRECT_URI` | https://你的域名/auth/callback | OAuth 回调地址 |
| `ENABLE_POLLING` | true | 启用轮询 |
| `POLL_INTERVAL` | 15 | 轮询间隔（秒） |

6. 点击「Create Web Service」开始部署

### 第四步：完成 OAuth 授权

1. 部署完成后，访问 `https://你的域名/auth/login`
2. 跳转到飞书授权页面，用飞书扫码授权
3. 授权成功后，会显示「✅ 授权成功！」
4. 访问 `https://你的域名/auth/status` 确认授权状态为 `authorized: true`

### 第五步：测试跑图

1. 去飞书表单提交一条新产品
2. 访问 `https://你的域名/health` 查看服务状态
3. 等待 15-30 秒，飞书表格中该记录状态会变成「生成中」→「已完成」
4. 「结果ZIP」字段会出现打包好的图片

---

## 注意事项

### Render 免费版限制
- 15 分钟无请求会休眠，下次访问需要 30-60 秒唤醒
- 每月 750 小时运行时间（单实例够用）
- 512MB 内存
- 文件系统是临时的，重启后用户 token 会丢失（需要重新授权）

### token 持久化（可选）
如果不想每次重启都重新授权，可以：
1. 升级 Render 付费版（有持久磁盘）
2. 或者把 token 保存到环境变量中（需要手动获取 refresh_token）

### 飞书应用权限
确保应用已添加为多维表格的协作者（可编辑权限），否则无法读写记录和上传附件。

---

## 常见问题

**Q: 授权后还是下载不了附件？**
A: 检查飞书开放平台的重定向 URL 是否配置正确，并且应用已发布新版本。

**Q: 服务休眠了怎么办？**
A: 免费版正常现象。可以用 UptimeRobot 等工具定时访问 /health 防止休眠。

**Q: 怎么查看日志？**
A: Render Dashboard → 你的服务 → Logs 标签页。

**Q: 本地版本和云端版本能共用吗？**
A: 可以，代码是同一套。本地用 lark-cli，云端用 OAuth，通过 FEISHU_REDIRECT_URI 是否配置自动切换。
