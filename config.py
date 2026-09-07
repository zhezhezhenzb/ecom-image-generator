# -*- coding: utf-8 -*-
"""
配置文件 - 所有配置通过环境变量或 .env 文件传入
"""
import os
import sys

# ==================== 自动加载 .env 文件 ====================
def load_env_file():
    """加载当前目录下的 .env 文件到环境变量（不覆盖已有的环境变量）"""
    # 尝试多个可能的 .env 文件位置
    env_paths = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        os.path.join(os.getcwd(), ".env"),
    ]

    for env_path in env_paths:
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        # 跳过空行和注释
                        if not line or line.startswith("#"):
                            continue
                        # 解析 KEY=VALUE 格式
                        if "=" in line:
                            key, value = line.split("=", 1)
                            key = key.strip()
                            value = value.strip()
                            # 去除引号
                            if (value.startswith('"') and value.endswith('"')) or \
                               (value.startswith("'") and value.endswith("'")):
                                value = value[1:-1]
                            # 只在环境变量不存在时设置（不覆盖已有的）
                            if key and key not in os.environ:
                                os.environ[key] = value
                print(f"[配置] 已加载 .env 文件: {env_path}")
                return
            except Exception as e:
                print(f"[配置] 加载 .env 文件失败: {e}")

# 自动加载 .env 文件
load_env_file()

# ==================== 飞书应用配置 ====================
# 飞书自建应用的 App ID 和 App Secret
# 创建方式：飞书开放平台 → 创建应用 → 凭证与基础信息
FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")

# 飞书多维表格配置
BASE_TOKEN = os.getenv("BASE_TOKEN", "ZeQebBA2qaUwNksPJZfckXZ9nfb")
TABLE_ID = os.getenv("TABLE_ID", "tblKkLKHAHdvT4E5")

# OAuth 回调地址（云端部署时必须配置，格式：https://你的域名/auth/callback）
FEISHU_REDIRECT_URI = os.getenv("FEISHU_REDIRECT_URI", "")

# ==================== API 配置（与 index.html 一致） ====================
API_BASE = os.getenv("API_BASE", "https://api2.laozhang.ai/v1")
# 多个 Key 用逗号分隔
API_KEYS_STR = os.getenv("API_KEYS", "sk-6rrB1xqAsmLI4I2079975d2f269447B690D633F84c888a69,sk-6rrB1xqAsmLI4I2079975d2f269447B690D633F84c888a69,sk-6rrB1xqAsmLI4I2079975d2f269447B690D633F84c888a69")
API_KEYS = [k.strip() for k in API_KEYS_STR.split(",") if k.strip()]

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-5.5")
IMG_MODEL = os.getenv("IMG_MODEL", "gpt-image-2-vip")

# ==================== 服务配置 ====================
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))

# Webhook 验证密钥（可选，用于验证请求来源）
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# 并发控制
MAX_CONCURRENT_IMAGES = int(os.getenv("MAX_CONCURRENT_IMAGES", "3"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "120"))

# 临时文件目录（建在脚本所在目录下，确保 lark-cli 能访问）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TEMP_DIR = os.path.join(SCRIPT_DIR, "temp")
TEMP_DIR = os.getenv("TEMP_DIR", DEFAULT_TEMP_DIR)
os.makedirs(TEMP_DIR, exist_ok=True)
