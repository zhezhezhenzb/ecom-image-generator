# -*- coding: utf-8 -*-
"""
飞书 OpenAPI 封装 - 支持应用身份和用户身份（OAuth）
"""
import json
import os
import time
import requests
from config import FEISHU_APP_ID, FEISHU_APP_SECRET, BASE_TOKEN, TABLE_ID, REQUEST_TIMEOUT, TEMP_DIR

# 禁用代理（本地运行时可能有系统代理导致连不上飞书）
os.environ['NO_PROXY'] = '*'
os.environ['no_proxy'] = '*'
NO_PROXY = {"http": None, "https": None}


# 用户 token 保存文件
USER_TOKEN_FILE = os.path.join(TEMP_DIR, "user_token.json")

# 禁用代理（本地运行时可能有系统代理导致连不上飞书）
NO_PROXY = {"http": None, "https": None}


def log(msg, level="info"):
    """简单日志"""
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")


class FeishuClient:
    """飞书 OpenAPI 客户端"""

    def __init__(self, app_id=None, app_secret=None):
        self.app_id = (app_id or FEISHU_APP_ID or "").strip()
        self.app_secret = (app_secret or FEISHU_APP_SECRET or "").strip()
        self._tenant_access_token = None
        self._token_expire_time = 0
        self._user_access_token = None
        self._user_refresh_token = None
        self._user_token_expire_time = 0
        log(f"飞书应用配置: app_id={self.app_id[:8]}... (长度{len(self.app_id)}), app_secret长度={len(self.app_secret)}")
        self._load_user_token()
        # 如果文件中没有 token，尝试从环境变量读取
        if not self._user_refresh_token:
            env_refresh = os.getenv("FEISHU_USER_REFRESH_TOKEN", "").strip()
            if env_refresh:
                self._user_refresh_token = env_refresh
                self._user_token_expire_time = 0  # 强制刷新
                log("从环境变量加载了用户 refresh_token", "success")
                # 立即尝试刷新 access_token
                try:
                    self.refresh_user_token()
                    log("环境变量 token 刷新成功", "success")
                except Exception as e:
                    log(f"环境变量 token 刷新失败: {e}", "warn")
                    log("请重新访问 /auth/login 授权", "warn")

    # ==================== 应用身份（tenant_access_token） ====================

    def _get_tenant_access_token(self):
        """获取 tenant_access_token（带缓存）"""
        if self._tenant_access_token and time.time() < self._token_expire_time - 60:
            return self._tenant_access_token

        if not self.app_id or not self.app_secret:
            raise Exception(f"飞书应用配置缺失: app_id={'已设置' if self.app_id else '空'}, app_secret={'已设置' if self.app_secret else '空'}。请检查 .env 文件中的 FEISHU_APP_ID 和 FEISHU_APP_SECRET")

        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        resp = requests.post(url, json={
            "app_id": self.app_id,
            "app_secret": self.app_secret
        }, timeout=REQUEST_TIMEOUT, proxies=NO_PROXY)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"获取 tenant_access_token 失败: {data}。请检查 .env 文件中的 FEISHU_APP_ID 和 FEISHU_APP_SECRET 是否正确")
        self._tenant_access_token = data["tenant_access_token"]
        self._token_expire_time = time.time() + data.get("expire", 7200)
        return self._tenant_access_token

    def _headers(self):
        """构建应用身份请求头"""
        token = self._get_tenant_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

    # ==================== 用户身份（OAuth） ====================

    def get_auth_url(self, redirect_uri, state="feishu_auth"):
        """生成飞书 OAuth 授权 URL"""
        return (
            f"https://open.feishu.cn/open-apis/authen/v1/authorize"
            f"?app_id={self.app_id}"
            f"&redirect_uri={redirect_uri}"
            f"&state={state}"
        )

    def exchange_code_for_token(self, code):
        """用授权码换取用户 access_token 和 refresh_token"""
        url = "https://open.feishu.cn/open-apis/authen/v1/oidc/access_token"
        headers = {
            "Authorization": f"Bearer {self._get_tenant_access_token()}",
            "Content-Type": "application/json"
        }
        resp = requests.post(url, headers=headers, json={
            "grant_type": "authorization_code",
            "code": code
        }, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"换取用户token失败: {data}")

        token_data = data["data"]
        self._user_access_token = token_data["access_token"]
        self._user_refresh_token = token_data["refresh_token"]
        self._user_token_expire_time = time.time() + token_data.get("expires_in", 7200)
        self._save_user_token()
        log("用户授权成功，token已保存", "success")
        log("=" * 60)
        log(f"【重要】请将以下 refresh_token 保存到 Render 环境变量 FEISHU_USER_REFRESH_TOKEN 中：")
        log(f"FEISHU_USER_REFRESH_TOKEN={self._user_refresh_token}")
        log("这样服务重启后就不需要重新授权了！")
        log("=" * 60)
        return token_data

    def refresh_user_token(self):
        """用 refresh_token 刷新用户 access_token"""
        if not self._user_refresh_token:
            raise Exception("无 refresh_token，请先授权")

        url = "https://open.feishu.cn/open-apis/authen/v1/oidc/refresh_access_token"
        headers = {
            "Authorization": f"Bearer {self._get_tenant_access_token()}",
            "Content-Type": "application/json"
        }
        resp = requests.post(url, headers=headers, json={
            "grant_type": "refresh_token",
            "refresh_token": self._user_refresh_token
        }, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"刷新用户token失败: {data}")

        token_data = data["data"]
        old_refresh = self._user_refresh_token
        self._user_access_token = token_data["access_token"]
        self._user_refresh_token = token_data.get("refresh_token", self._user_refresh_token)
        self._user_token_expire_time = time.time() + token_data.get("expires_in", 7200)
        self._save_user_token()
        log("用户token已刷新", "success")
        # 如果 refresh_token 变了，提醒用户更新环境变量
        if self._user_refresh_token != old_refresh:
            log("=" * 60)
            log("【重要】refresh_token 已刷新，请更新 Render 环境变量 FEISHU_USER_REFRESH_TOKEN：")
            log(f"FEISHU_USER_REFRESH_TOKEN={self._user_refresh_token}")
            log("=" * 60)
        return token_data

    def get_user_access_token(self):
        """获取用户 access_token（带缓存和自动刷新）"""
        if not self._user_access_token:
            raise Exception("用户未授权，请先访问 /auth/login 完成授权")
        if time.time() > self._user_token_expire_time - 120:
            self.refresh_user_token()
        return self._user_access_token

    def _user_headers(self):
        """构建用户身份请求头"""
        token = self.get_user_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

    def is_user_authorized(self):
        """检查用户是否已授权"""
        return bool(self._user_refresh_token)

    def _save_user_token(self):
        """保存用户 token 到文件"""
        try:
            os.makedirs(os.path.dirname(USER_TOKEN_FILE), exist_ok=True)
            with open(USER_TOKEN_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "access_token": self._user_access_token,
                    "refresh_token": self._user_refresh_token,
                    "expire_time": self._user_token_expire_time
                }, f)
        except Exception as e:
            log(f"保存用户token失败: {e}", "warn")

    def _load_user_token(self):
        """从文件加载用户 token"""
        try:
            if os.path.exists(USER_TOKEN_FILE):
                with open(USER_TOKEN_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._user_access_token = data.get("access_token")
                self._user_refresh_token = data.get("refresh_token")
                self._user_token_expire_time = data.get("expire_time", 0)
                if self._user_refresh_token:
                    log("用户token已从文件加载", "success")
        except Exception as e:
            log(f"加载用户token失败: {e}", "warn")

    # ==================== 记录操作（应用身份） ====================

    def get_record(self, record_id, base_token=None, table_id=None):
        """获取单条记录详情"""
        base_token = base_token or BASE_TOKEN
        table_id = table_id or TABLE_ID
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{base_token}/tables/{table_id}/records/{record_id}"
        resp = requests.get(url, headers=self._headers(), timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"获取记录失败: {data}")
        return data.get("data", {}).get("record", {})

    def list_records(self, base_token=None, table_id=None, page_size=100, page_token=None):
        """获取记录列表"""
        base_token = base_token or BASE_TOKEN
        table_id = table_id or TABLE_ID
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{base_token}/tables/{table_id}/records"
        params = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(url, headers=self._headers(), params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"获取记录列表失败: {data}")
        return data.get("data", {})

    def get_pending_records(self, base_token=None, table_id=None):
        """获取所有状态为'待生成'的记录"""
        pending = []
        page_token = None
        while True:
            data = self.list_records(base_token, table_id, page_size=100, page_token=page_token)
            items = data.get("items", [])
            for record in items:
                fields = record.get("fields", {})
                status = fields.get("状态")
                if isinstance(status, list) and len(status) > 0:
                    status = status[0].get("text", status[0]) if isinstance(status[0], dict) else status[0]
                if status == "待生成":
                    pending.append(record)
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
        return pending

    def update_record(self, record_id, fields, base_token=None, table_id=None):
        """更新记录"""
        base_token = base_token or BASE_TOKEN
        table_id = table_id or TABLE_ID
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{base_token}/tables/{table_id}/records/{record_id}"
        resp = requests.put(url, headers=self._headers(), json={"fields": fields}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"更新记录失败: {data}")
        return data

    def update_record_status(self, record_id, status, error_msg=None):
        """更新记录状态（便捷方法）"""
        # 先更新状态
        self.update_record(record_id, {"状态": status})
        # 再尝试更新错误信息（字段不存在时不影响状态更新）
        if error_msg:
            try:
                self.update_record(record_id, {"错误信息": error_msg})
            except Exception:
                pass  # 错误信息字段不存在时忽略

    # ==================== 附件上传（应用身份） ====================

    def upload_file_to_drive(self, file_path, parent_node=""):
        """上传文件到飞书云空间，返回 file_token
        小于20MB用 upload_all，大于等于20MB用分片上传
        """
        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)
        parent = parent_node or BASE_TOKEN
        headers = {"Authorization": f"Bearer {self._get_tenant_access_token()}"}

        # 小于20MB直接上传
        if file_size < 20 * 1024 * 1024:
            url = "https://open.feishu.cn/open-apis/drive/v1/medias/upload_all"
            with open(file_path, "rb") as f:
                files = {"file": (file_name, f, "application/octet-stream")}
                data = {
                    "file_name": file_name,
                    "parent_type": "bitable_file",
                    "parent_node": parent,
                    "size": str(file_size)
                }
                resp = requests.post(url, headers=headers, data=data, files=files, timeout=600, proxies=NO_PROXY)
            resp.raise_for_status()
            result = resp.json()
            if result.get("code") != 0:
                raise Exception(f"上传文件失败: {result}")
            return result["data"]["file_token"]

        # 大于等于20MB用分片上传
        log(f"文件较大（{file_size/1024/1024:.1f}MB），使用分片上传...")
        block_size = 4 * 1024 * 1024  # 4MB 一片
        block_num = (file_size + block_size - 1) // block_size

        # 第一步：准备上传
        prepare_url = "https://open.feishu.cn/open-apis/drive/v1/medias/upload_prepare"
        prepare_data = {
            "file_name": file_name,
            "parent_type": "bitable_file",
            "parent_node": parent,
            "size": file_size,
            "block_size": block_size
        }
        resp = requests.post(prepare_url, headers={**headers, "Content-Type": "application/json"},
                             json=prepare_data, timeout=REQUEST_TIMEOUT, proxies=NO_PROXY)
        resp.raise_for_status()
        prepare_result = resp.json()
        if prepare_result.get("code") != 0:
            raise Exception(f"分片上传准备失败: {prepare_result}")
        upload_id = prepare_result["data"]["upload_id"]
        log(f"分片上传准备成功，upload_id={upload_id}，共{block_num}片")

        # 第二步：逐片上传
        part_url = "https://open.feishu.cn/open-apis/drive/v1/medias/upload_part"
        with open(file_path, "rb") as f:
            for i in range(block_num):
                chunk = f.read(block_size)
                files = {"file": (file_name, chunk, "application/octet-stream")}
                data = {
                    "upload_id": upload_id,
                    "block_seq": i
                }
                resp = requests.post(part_url, headers=headers, data=data, files=files,
                                     timeout=600, proxies=NO_PROXY)
                resp.raise_for_status()
                part_result = resp.json()
                if part_result.get("code") != 0:
                    raise Exception(f"分片{i+1}/{block_num}上传失败: {part_result}")
                log(f"分片{i+1}/{block_num}上传成功")

        # 第三步：完成上传
        finish_url = "https://open.feishu.cn/open-apis/drive/v1/medias/upload_finish"
        finish_data = {
            "upload_id": upload_id,
            "block_num": block_num
        }
        resp = requests.post(finish_url, headers={**headers, "Content-Type": "application/json"},
                             json=finish_data, timeout=REQUEST_TIMEOUT, proxies=NO_PROXY)
        resp.raise_for_status()
        finish_result = resp.json()
        if finish_result.get("code") != 0:
            raise Exception(f"分片上传完成失败: {finish_result}")
        file_token = finish_result["data"]["file_token"]
        log(f"分片上传完成，file_token={file_token}", "success")
        return file_token

    def attach_zip_to_record(self, record_id, zip_path, field_name="结果ZIP"):
        """把 ZIP 文件作为附件写入记录字段"""
        file_token = self.upload_file_to_drive(zip_path)
        fields = {
            field_name: [{"file_token": file_token}]
        }
        update_data = self.update_record(record_id, fields)
        return {"file_token": file_token}

    def upload_attachment(self, record_id, field_name, file_path):
        """上传附件到记录字段（通用方法）"""
        file_token = self.upload_file_to_drive(file_path)
        fields = {
            field_name: [{"file_token": file_token}]
        }
        self.update_record(record_id, fields)
        return {"file_token": file_token}

    # ==================== 附件下载（用户身份） ====================

    def download_attachment(self, file_token, save_path, record_id=None, base_token=None, table_id=None, tmp_url=None, field_id=None):
        """
        下载附件到本地（使用用户身份 token）
        应用身份无法下载多维表格附件，必须用用户身份
        """
        if not self.is_user_authorized():
            log(f"授权状态检查失败: refresh_token={'有' if self._user_refresh_token else '无'}, access_token={'有' if self._user_access_token else '无'}", "warn")
            raise Exception("用户未授权，无法下载附件。请先访问 /auth/login 完成飞书授权。")

        save_dir = os.path.dirname(os.path.abspath(save_path))
        save_filename = os.path.basename(save_path)
        os.makedirs(save_dir, exist_ok=True)

        bt = base_token or BASE_TOKEN
        tid = table_id or TABLE_ID

        def try_download(url, headers, label):
            """尝试下载，返回 response 或 None"""
            try:
                r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT * 2)
                log(f"  下载尝试[{label}]: status={r.status_code}, size={len(r.content)}")
                if r.status_code == 200 and len(r.content) > 0:
                    return r
                if r.status_code not in [400, 403, 404]:
                    r.raise_for_status()
            except Exception as e:
                log(f"  下载尝试[{label}]异常: {e}", "warn")
            return None

        # 方式0：如果有 tmp_url（记录详情返回的，已带正确的 extra 参数），直接用
        if tmp_url:
            log(f"尝试方式0: tmp_url")
            resp = try_download(tmp_url, self._user_headers(), "tmp_url")
            if resp:
                final_path = os.path.join(save_dir, save_filename)
                with open(final_path, "wb") as f:
                    f.write(resp.content)
                return final_path

        # 构建各种 extra 参数
        extra_simple = requests.utils.quote(json.dumps({"bitablePerm": {"tableId": tid, "rev": 0}}, separators=(',', ':')))
        if field_id and record_id:
            extra_complex = requests.utils.quote(json.dumps({
                "bitablePerm": {"tableId": tid, "attachments": {field_id: {record_id: [file_token]}}}
            }, separators=(',', ':')))
        else:
            extra_complex = extra_simple

        user_headers = self._user_headers()
        app_headers = self._headers()
        base = "https://open.feishu.cn/open-apis/drive/v1/medias"

        # 尝试多种下载方式
        download_attempts = [
            # (url, headers, label)
            (f"{base}/{file_token}/download", user_headers, "用户身份-无extra"),
            (f"{base}/{file_token}/download?extra={extra_simple}", user_headers, "用户身份-简单extra"),
            (f"{base}/{file_token}/download?extra={extra_complex}", user_headers, "用户身份-复杂extra"),
            (f"{base}/batch_get_tmp_download_url?file_tokens={file_token}", user_headers, "用户身份-临时链接-无extra"),
            (f"{base}/batch_get_tmp_download_url?file_tokens={file_token}&extra={extra_simple}", user_headers, "用户身份-临时链接-简单extra"),
            (f"{base}/{file_token}/download", app_headers, "应用身份-无extra"),
            (f"{base}/{file_token}/download?extra={extra_simple}", app_headers, "应用身份-简单extra"),
        ]

        for url, headers, label in download_attempts:
            log(f"尝试方式: {label}")
            resp = try_download(url, headers, label)
            if resp:
                # 如果是临时链接接口，需要解析出临时下载链接
                if "batch_get_tmp_download_url" in url:
                    try:
                        data = resp.json()
                        if data.get("code") == 0:
                            urls = data.get("data", {}).get("tmp_download_urls", [])
                            if urls and urls[0].get("tmp_download_url"):
                                tmp_dl_url = urls[0]["tmp_download_url"]
                                resp2 = try_download(tmp_dl_url, {}, "临时链接下载")
                                if resp2:
                                    resp = resp2
                                else:
                                    continue
                            else:
                                continue
                        else:
                            log(f"  临时链接接口返回错误: {data}", "warn")
                            continue
                    except Exception as e:
                        log(f"  解析临时链接失败: {e}", "warn")
                        continue

                final_path = os.path.join(save_dir, save_filename)
                with open(final_path, "wb") as f:
                    f.write(resp.content)
                if os.path.getsize(final_path) > 0:
                    log(f"下载成功，使用方式: {label}", "success")
                    return final_path

        raise Exception(f"所有下载方式均失败，file_token: {file_token}")


# 单例
_feishu_client = None

def get_feishu_client():
    """获取飞书客户端单例"""
    global _feishu_client
    if _feishu_client is None:
        _feishu_client = FeishuClient()
    return _feishu_client
