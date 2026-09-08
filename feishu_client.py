# -*- coding: utf-8 -*-
"""
飞书 OpenAPI 封装 - 支持应用身份和用户身份（OAuth）
"""
import json
import os
import time
import requests
from config import FEISHU_APP_ID, FEISHU_APP_SECRET, BASE_TOKEN, TABLE_ID, REQUEST_TIMEOUT, TEMP_DIR


# 用户 token 保存文件
USER_TOKEN_FILE = os.path.join(TEMP_DIR, "user_token.json")


def log(msg, level="info"):
    """简单日志"""
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")


class FeishuClient:
    """飞书 OpenAPI 客户端"""

    def __init__(self, app_id=None, app_secret=None):
        self.app_id = app_id or FEISHU_APP_ID
        self.app_secret = app_secret or FEISHU_APP_SECRET
        self._tenant_access_token = None
        self._token_expire_time = 0
        self._user_access_token = None
        self._user_refresh_token = None
        self._user_token_expire_time = 0
        self._load_user_token()

    # ==================== 应用身份（tenant_access_token） ====================

    def _get_tenant_access_token(self):
        """获取 tenant_access_token（带缓存）"""
        if self._tenant_access_token and time.time() < self._token_expire_time - 60:
            return self._tenant_access_token

        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        resp = requests.post(url, json={
            "app_id": self.app_id,
            "app_secret": self.app_secret
        }, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"获取 tenant_access_token 失败: {data}")
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
        self._user_access_token = token_data["access_token"]
        self._user_refresh_token = token_data.get("refresh_token", self._user_refresh_token)
        self._user_token_expire_time = time.time() + token_data.get("expires_in", 7200)
        self._save_user_token()
        log("用户token已刷新", "success")
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
        """上传文件到飞书云空间，返回 file_token"""
        url = "https://open.feishu.cn/open-apis/drive/v1/files/create_file"
        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)

        with open(file_path, "rb") as f:
            files = {"file": (file_name, f, "application/zip")}
            data = {
                "file_name": file_name,
                "parent_type": "explorer",
                "parent_node": parent_node,
                "size": str(file_size)
            }
            headers = {"Authorization": f"Bearer {self._get_tenant_access_token()}"}
            resp = requests.post(url, headers=headers, data=data, files=files, timeout=REQUEST_TIMEOUT * 2)

        resp.raise_for_status()
        result = resp.json()
        if result.get("code") != 0:
            raise Exception(f"上传文件失败: {result}")
        return result["data"]["file_token"]

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

    def download_attachment(self, file_token, save_path, record_id=None, base_token=None, table_id=None):
        """
        下载附件到本地（使用用户身份 token）
        应用身份无法下载多维表格附件，必须用用户身份
        """
        if not self.is_user_authorized():
            raise Exception("用户未授权，无法下载附件。请先访问 /auth/login 完成飞书授权。")

        save_dir = os.path.dirname(os.path.abspath(save_path))
        save_filename = os.path.basename(save_path)
        os.makedirs(save_dir, exist_ok=True)

        # 构建用户身份请求头，加上 extra 参数（多维表格附件下载必需）
        headers = self._user_headers()
        bt = base_token or BASE_TOKEN
        tid = table_id or TABLE_ID
        extra = json.dumps({"bitablePerm": {"tableId": tid, "rev": 0}})
        headers["extra"] = extra

        # 方式1：直接用下载接口
        download_url = f"https://open.feishu.cn/open-apis/drive/v1/medias/{file_token}/download"
        resp = requests.get(download_url, headers=headers, timeout=REQUEST_TIMEOUT * 2)
        
        if resp.status_code in [400, 404]:
            # 方式2：先获取临时下载链接（POST）
            tmp_url_api = "https://open.feishu.cn/open-apis/drive/v1/medias/batch_get_tmp_download_url"
            resp2 = requests.post(tmp_url_api, headers=self._user_headers(), json={"file_tokens": [file_token]}, timeout=REQUEST_TIMEOUT)
            if resp2.status_code == 404:
                raise Exception(f"下载接口404，file_token可能无效或无权限。token: {file_token}")
            resp2.raise_for_status()
            data = resp2.json()
            if data.get("code") != 0:
                raise Exception(f"获取临时下载链接失败: {data}")
            urls = data.get("data", {}).get("tmp_download_urls", [])
            if not urls or not urls[0].get("tmp_download_url"):
                raise Exception(f"临时下载链接为空（file_token: {file_token}），用户可能无权限访问该附件")
            tmp_download_url = urls[0]["tmp_download_url"]
            resp = requests.get(tmp_download_url, timeout=REQUEST_TIMEOUT * 2)

        resp.raise_for_status()

        final_path = os.path.join(save_dir, save_filename)
        with open(final_path, "wb") as f:
            f.write(resp.content)

        if os.path.getsize(final_path) == 0:
            raise Exception(f"下载后文件为空: {final_path}")

        return final_path


# 单例
_feishu_client = None

def get_feishu_client():
    """获取飞书客户端单例"""
    global _feishu_client
    if _feishu_client is None:
        _feishu_client = FeishuClient()
    return _feishu_client
