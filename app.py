# -*- coding: utf-8 -*-
"""
FastAPI 云端服务 - 接收飞书自动化 Webhook，异步执行跑图流程
"""
import os
import sys
import json
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
import uvicorn

from config import SERVER_HOST, SERVER_PORT, WEBHOOK_SECRET, BASE_TOKEN, TABLE_ID, FEISHU_APP_ID
from image_generator import process_record, log
from feishu_client import get_feishu_client

# ==================== 云端环境适配 ====================
# Render 等平台会通过 $PORT 环境变量指定端口
import os as _os
CLOUD_PORT = int(_os.getenv("PORT", str(SERVER_PORT)))
REDIRECT_URI = _os.getenv("FEISHU_REDIRECT_URI", "")

# ==================== 轮询配置 ====================
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))  # 轮询间隔（秒）
ENABLE_POLLING = os.getenv("ENABLE_POLLING", "true").lower() == "true"  # 是否启用轮询

# 正在处理的记录集合（防止重复处理）
processing_records = set()

# ==================== 日志配置 ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("ecom-gen-service")

# ==================== 异步任务管理 ====================
background_tasks = set()


async def run_generation_async(record_id: str):
    """异步执行跑图任务"""
    # 标记为正在处理
    processing_records.add(record_id)
    try:
        log(f"开始异步处理记录: {record_id}")
        # 在线程池中执行同步的跑图逻辑，避免阻塞事件循环
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, process_record, record_id)
        log(f"异步处理完成: {record_id}", "success")
    except Exception as e:
        log(f"异步处理失败: {record_id}, 错误: {e}", "error")
        import traceback
        traceback.print_exc()
        # 失败时更新状态为"失败"
        try:
            feishu = get_feishu_client()
            error_msg = str(e)[:500]  # 限制长度
            feishu.update_record_status(record_id, "失败", error_msg)
            log(f"已更新记录 {record_id} 状态为失败", "warn")
        except Exception as e2:
            log(f"更新失败状态失败: {e2}", "warn")
    finally:
        # 移除正在处理的标记
        processing_records.discard(record_id)


async def poll_feishu_records():
    """轮询飞书表格，检查待生成记录"""
    log(f"轮询任务已启动，间隔 {POLL_INTERVAL} 秒")
    poll_count = 0
    while True:
        poll_count += 1
        try:
            feishu = get_feishu_client()
            pending = feishu.get_pending_records()

            if pending:
                log(f"[轮询#{poll_count}] 发现 {len(pending)} 条待生成记录")
            else:
                log(f"[轮询#{poll_count}] 无待生成记录，继续等待...")

            for record in pending:
                record_id = record.get("record_id")
                if not record_id:
                    continue
                if record_id in processing_records:
                    log(f"记录 {record_id} 正在处理中，跳过")
                    continue

                # 获取产品名称用于日志
                fields = record.get("fields", {})
                product_name = fields.get("品名", "未知产品")
                if isinstance(product_name, list) and len(product_name) > 0:
                    product_name = product_name[0].get("text", product_name[0]) if isinstance(product_name[0], dict) else product_name[0]

                log(f"触发跑图: {product_name} ({record_id})")

                # 先把状态改成"生成中"，防止重复触发
                try:
                    feishu.update_record_status(record_id, "生成中")
                except Exception as e:
                    log(f"更新状态失败: {e}", "warn")

                # 异步执行跑图
                task = asyncio.create_task(run_generation_async(record_id))
                background_tasks.add(task)
                task.add_done_callback(background_tasks.discard)

        except Exception as e:
            log(f"[轮询#{poll_count}] 出错: {e}", "warn")
            import traceback
            traceback.print_exc()

        await asyncio.sleep(POLL_INTERVAL)


def extract_record_id(payload: dict) -> str:
    """从 Webhook payload 中提取 record_id（兼容多种格式）"""
    # 格式1: 直接传 record_id
    if "record_id" in payload:
        return payload["record_id"]

    # 格式2: 飞书自动化 Webhook 格式
    if "event" in payload:
        event = payload["event"]
        if isinstance(event, dict):
            if "record_id" in event:
                return event["record_id"]
            # 飞书事件订阅格式
            if "value" in event:
                value = event["value"]
                if isinstance(value, dict) and "record_id" in value:
                    return value["record_id"]

    # 格式3: 飞书事件订阅 v2 格式
    if "header" in payload and "event" in payload:
        event = payload["event"]
        if isinstance(event, dict):
            for key in ["record_id", "record"]:
                if key in event:
                    val = event[key]
                    if isinstance(val, str):
                        return val
                    if isinstance(val, dict) and "record_id" in val:
                        return val["record_id"]

    # 格式4: 自定义格式 { "data": { "record_id": "..." } }
    if "data" in payload:
        data = payload["data"]
        if isinstance(data, dict) and "record_id" in data:
            return data["record_id"]

    return ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期"""
    log("=" * 60)
    log("电商产品图套装生成 - 云端服务启动")
    log(f"监听地址: {SERVER_HOST}:{SERVER_PORT}")
    log(f"飞书表格: {BASE_TOKEN} / {TABLE_ID}")
    log(f"Webhook 密钥: {'已配置' if WEBHOOK_SECRET else '未配置（不验证）'}")
    log(f"轮询模式: {'已启用' if ENABLE_POLLING else '已禁用'}, 间隔 {POLL_INTERVAL}秒")
    log("=" * 60)

    # 启动轮询任务
    poll_task = None
    if ENABLE_POLLING:
        poll_task = asyncio.create_task(poll_feishu_records())
        log("轮询任务已启动", "success")

    yield

    # 关闭轮询任务
    if poll_task:
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass
    log("服务关闭")


app = FastAPI(title="电商产品图套装生成服务", lifespan=lifespan)


# ==================== OAuth 授权路由 ====================

@app.get("/auth/login")
async def auth_login():
    """跳转到飞书授权页面"""
    feishu = get_feishu_client()
    if not REDIRECT_URI:
        return HTMLResponse("""
        <h2>未配置 FEISHU_REDIRECT_URI</h2>
        <p>请在环境变量中设置 FEISHU_REDIRECT_URI，格式为：https://你的域名/auth/callback</p>
        """, status_code=400)
    auth_url = feishu.get_auth_url(REDIRECT_URI)
    return RedirectResponse(auth_url)


@app.get("/auth/callback")
async def auth_callback(code: str = "", state: str = "", error: str = ""):
    """处理飞书授权回调"""
    if error:
        return HTMLResponse(f"<h2>授权失败</h2><p>错误: {error}</p>", status_code=400)
    if not code:
        return HTMLResponse("<h2>授权失败</h2><p>未收到授权码</p>", status_code=400)

    try:
        feishu = get_feishu_client()
        token_data = feishu.exchange_code_for_token(code)
        refresh_token = token_data.get("refresh_token", "")
        return HTMLResponse(f"""
        <h2>✅ 授权成功！</h2>
        <p>用户授权已完成，现在可以正常下载附件和跑图了。</p>
        <p><a href="/health">查看服务状态</a> | <a href="/auth/status">查看授权状态</a></p>
        <hr>
        <h3>⚠️ 重要：请保存 refresh_token 到环境变量（避免重启后重新授权）</h3>
        <p>复制下面的值，到 Render → Environment → 添加环境变量：</p>
        <p><strong>Key:</strong> <code>FEISHU_USER_REFRESH_TOKEN</code></p>
        <p><strong>Value:</strong></p>
        <textarea rows="3" cols="80" readonly onclick="this.select()">{refresh_token}</textarea>
        <p style="color: #666; font-size: 12px;">保存后服务重启会自动从环境变量加载，不需要再授权。token 刷新后会在日志中提示更新。</p>
        """)
    except Exception as e:
        return HTMLResponse(f"<h2>授权失败</h2><p>错误: {e}</p>", status_code=500)


@app.get("/auth/status")
async def auth_status():
    """查看用户授权状态"""
    feishu = get_feishu_client()
    return {
        "authorized": feishu.is_user_authorized(),
        "message": "已授权" if feishu.is_user_authorized() else "未授权，请访问 /auth/login 完成授权"
    }


@app.get("/auth/logout")
async def auth_logout():
    """清除用户授权（用于重新授权）"""
    feishu = get_feishu_client()
    feishu._user_access_token = None
    feishu._user_refresh_token = None
    feishu._user_token_expire_time = 0
    try:
        from feishu_client import USER_TOKEN_FILE
        if os.path.exists(USER_TOKEN_FILE):
            os.remove(USER_TOKEN_FILE)
    except:
        pass
    return {"ok": True, "message": "已清除授权，可重新访问 /auth/login 授权"}


# ==================== 健康检查 ====================

@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "ok",
        "service": "ecom-image-generator",
        "pending_tasks": len(background_tasks),
        "processing_records": len(processing_records),
        "polling_enabled": ENABLE_POLLING,
        "poll_interval": POLL_INTERVAL,
        "base_token": BASE_TOKEN,
        "table_id": TABLE_ID
    }


@app.post("/webhook")
async def webhook(request: Request):
    """接收飞书自动化 Webhook"""
    try:
        payload = await request.json()
    except Exception:
        body = await request.body()
        try:
            payload = json.loads(body)
        except:
            raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.info(f"收到 Webhook 请求: {json.dumps(payload, ensure_ascii=False)[:500]}")

    # 飞书事件订阅 URL 验证（challenge）
    if "challenge" in payload:
        logger.info("收到飞书 URL 验证请求")
        return {"challenge": payload["challenge"]}

    # 验证 Webhook 密钥（如果配置了）
    if WEBHOOK_SECRET:
        # 从 header 或 payload 中验证
        token = request.headers.get("X-Webhook-Token", "")
        if token != WEBHOOK_SECRET:
            # 也可能在 payload 中
            if payload.get("token") != WEBHOOK_SECRET:
                logger.warning("Webhook 密钥验证失败")
                raise HTTPException(status_code=403, detail="Invalid webhook secret")

    # 提取 record_id
    record_id = extract_record_id(payload)
    if not record_id:
        logger.error(f"无法从请求中提取 record_id: {payload}")
        raise HTTPException(status_code=400, detail="Cannot extract record_id from payload")

    logger.info(f"提取到 record_id: {record_id}")

    # 异步执行跑图（不阻塞响应）
    task = asyncio.create_task(run_generation_async(record_id))
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)

    return JSONResponse({
        "ok": True,
        "message": "任务已接收，正在后台处理",
        "record_id": record_id,
        "task_id": str(id(task))
    })


@app.post("/generate/{record_id}")
async def generate_direct(record_id: str):
    """直接触发指定记录的跑图（用于测试）"""
    logger.info(f"直接触发跑图: {record_id}")

    task = asyncio.create_task(run_generation_async(record_id))
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)

    return JSONResponse({
        "ok": True,
        "message": "任务已接收，正在后台处理",
        "record_id": record_id
    })


@app.get("/")
async def root():
    """根路径"""
    feishu = get_feishu_client()
    authorized = feishu.is_user_authorized()
    return {
        "service": "电商产品图套装生成服务",
        "user_authorized": authorized,
        "auth_url": "/auth/login" if not authorized else None,
        "endpoints": {
            "GET /health": "健康检查",
            "GET /auth/login": "飞书用户授权（首次使用必须先授权）",
            "GET /auth/callback": "飞书授权回调",
            "GET /auth/status": "查看授权状态",
            "POST /webhook": "飞书自动化 Webhook 入口",
            "POST /generate/{record_id}": "直接触发指定记录跑图（测试用）"
        }
    }


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=CLOUD_PORT,
        reload=False,
        log_level="info"
    )
