# 电商产品图套装生成 - 云端自动化服务

完全云端自动化：飞书表单提交 → 自动跑图 → ZIP 自动回传表格，无需本地运行脚本。

---

## 整体架构

```
用户填写飞书表单
       ↓ 提交
飞书多维表格（记录新增，状态=待生成）
       ↓ 触发自动化
飞书自动化 → Webhook 调用云端服务
       ↓
云端 FastAPI 服务（接收 record_id）
       ↓ 异步执行
  ┌─────────────────────────┐
  │ 1. 调用飞书API获取记录  │
  │ 2. 下载产品图/参考图    │
  │ 3. 生成参考图表格       │
  │ 4. 调用LLM生成设计方案  │
  │ 5. 批量调用生图API      │
  │ 6. 打包ZIP              │
  │ 7. 上传ZIP到飞书表格    │
  │ 8. 更新状态为"已完成"   │
  └─────────────────────────┘
       ↓
飞书表格「结果ZIP」字段（自动下载）
```

---

## 部署步骤（共 3 步）

### 第一步：创建飞书自建应用

1. 打开 [飞书开放平台](https://open.feishu.cn/app)，点击「创建应用」→「自建应用」
2. 填写应用名称（如「电商图生成助手」），创建完成
3. 进入应用 →「凭证与基础信息」，复制 **App ID** 和 **App Secret**（后面配置要用）
4. 进入「权限管理」，开通以下权限：
   - `bitable:app`（查看、评论、编辑和管理多维表格）
   - `bitable:app:readonly`（查看多维表格）
   - `drive:drive`（查看、评论、编辑和管理云空间中所有文件）
   - `drive:file`（查看、评论、编辑和管理云空间中所有文件）
5. 进入「应用发布」→「版本管理与发布」，创建版本并发布（企业自建应用需要管理员审核）

### 第二步：部署云端服务

#### 方式 A：Docker 部署（推荐）

```bash
# 1. 克隆或上传 cloud_service 目录到服务器
cd cloud_service

# 2. 修改 docker-compose.yml 中的环境变量
# 必须修改：FEISHU_APP_ID、FEISHU_APP_SECRET、API_KEYS
vim docker-compose.yml

# 3. 构建并启动
docker-compose up -d --build

# 4. 查看日志
docker-compose logs -f

# 5. 验证服务
curl http://localhost:8000/health
```

#### 方式 B：直接运行（适合测试）

```bash
cd cloud_service

# 安装依赖
pip install -r requirements.txt

# 设置环境变量
export FEISHU_APP_ID="your_app_id"
export FEISHU_APP_SECRET="your_app_secret"
export API_KEYS="sk-xxx1,sk-xxx2,sk-xxx3"

# 启动服务
python app.py
```

#### 方式 C：云函数 / Serverless 部署

支持部署到阿里云函数计算、腾讯云函数、Vercel、Railway、Render 等平台。
注意：需要平台支持长时间运行的后台任务（生图可能需要 1-3 分钟）。

### 第三步：配置飞书自动化

1. 打开飞书多维表格：https://my.feishu.cn/base/ZeQebBA2qaUwNksPJZfckXZ9nfb
2. 点击右上角「自动化」→「创建自动化」
3. 配置触发条件：
   - 触发方式：「当记录被创建时」
   - 数据表：「产品任务表」
4. 配置执行动作：
   - 动作：「发送 Webhook 请求」
   - 请求方式：POST
   - 请求 URL：`http://你的服务器IP:8000/webhook`
   - 请求体（JSON）：
     ```json
     {
       "record_id": "{{记录ID}}",
       "app_token": "{{多维表格Token}}",
       "table_id": "{{数据表ID}}"
     }
     ```
5. 保存并启用自动化

> **注意**：如果飞书自动化的「发送 Webhook」动作不支持自定义请求体，可以用默认格式，服务会自动从多种格式中提取 record_id。

---

## 环境变量说明

| 变量名 | 必填 | 默认值 | 说明 |
|--------|------|--------|------|
| `FEISHU_APP_ID` | ✅ | - | 飞书自建应用 App ID |
| `FEISHU_APP_SECRET` | ✅ | - | 飞书自建应用 App Secret |
| `BASE_TOKEN` | - | ZeQebBA2qaUwNksPJZfckXZ9nfb | 飞书多维表格 Token |
| `TABLE_ID` | - | tblKkLKHAHdvT4E5 | 数据表 ID |
| `API_BASE` | - | https://api2.laozhang.ai/v1 | API 基础地址 |
| `API_KEYS` | ✅ | - | API Key，多个用逗号分隔 |
| `LLM_MODEL` | - | gpt-5.5 | LLM 模型名称 |
| `IMG_MODEL` | - | gpt-image-2-vip | 生图模型名称 |
| `WEBHOOK_SECRET` | - | - | Webhook 验证密钥（可选） |
| `MAX_CONCURRENT_IMAGES` | - | 3 | 同时生成的图片数量 |
| `REQUEST_TIMEOUT` | - | 120 | API 请求超时时间（秒） |
| `SERVER_HOST` | - | 0.0.0.0 | 服务监听地址 |
| `SERVER_PORT` | - | 8000 | 服务监听端口 |
| `TEMP_DIR` | - | /tmp/ecom_gen | 临时文件目录 |

---

## API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/webhook` | POST | 飞书自动化 Webhook 入口 |
| `/generate/{record_id}` | POST | 直接触发指定记录跑图（测试用） |
| `/` | GET | 服务信息 |

### Webhook 请求格式（兼容多种）

服务会自动从以下格式中提取 record_id：

```json
// 格式1：直接传
{"record_id": "recxxx"}

// 格式2：飞书自动化格式
{"event": {"record_id": "recxxx"}}

// 格式3：飞书事件订阅格式
{"header": {...}, "event": {"record_id": "recxxx"}}
```

---

## 使用流程

1. **填写表单**：打开表单链接 https://my.feishu.cn/share/base/shrcniFDM3x2BEnMmQYuctYIJye ，填写产品信息，上传产品图，点击提交
2. **自动跑图**：提交后自动触发飞书自动化，Webhook 调用云端服务，后台自动跑图（通常 1-3 分钟）
3. **查看结果**：打开飞书表格，对应记录的「状态」会从「待生成」→「生成中」→「已完成」，「结果ZIP」字段自动出现打包好的图片，点击下载即可

---

## 常见问题

**Q: 提交表单后没有触发跑图？**
A: 检查飞书自动化是否启用，Webhook URL 是否正确，云端服务是否正常运行（访问 /health 验证）。

**Q: 状态一直停留在「生成中」？**
A: 查看云端服务日志，可能是 API Key 失效、网络问题或生图超时。

**Q: 状态变成「失败」怎么办？**
A: 查看「错误信息」字段，会记录具体失败原因。修复后把状态改回「待生成」重新触发。

**Q: 可以同时处理多个产品吗？**
A: 可以，服务会异步处理所有提交的记录，并发数量由 `MAX_CONCURRENT_IMAGES` 控制。

**Q: 如何更换 API Key？**
A: 修改环境变量 `API_KEYS`（多个用逗号分隔），重启服务即可。

**Q: 飞书应用需要发布吗？**
A: 企业自建应用需要发布并通过管理员审核后才能使用。测试阶段可以用「测试企业和人员」功能。

---

## 文件结构

```
cloud_service/
├── app.py              # FastAPI 主应用（Webhook 入口 + 异步任务）
├── config.py           # 配置文件（环境变量）
├── feishu_client.py    # 飞书 OpenAPI 封装
├── image_generator.py  # 跑图核心逻辑（完整复刻 index.html）
├── requirements.txt    # Python 依赖
├── Dockerfile          # Docker 配置
├── docker-compose.yml  # docker-compose 配置
└── README.md           # 部署说明
```
