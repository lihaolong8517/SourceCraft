"""
许可证管理模块 - License Manager
用于管理软件授权、用户认证和设备绑定

功能：
- 15天免费试用
- 终身版 ¥28
- 设备限制：1台
"""

import os
import sys
import json
import uuid
import hashlib
import hmac
import base64
import platform
import re
import requests
from requests.adapters import HTTPAdapter
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlparse
from cryptography.fernet import Fernet
from i18n import t, t_format

# 不再全局禁用 SSL 警告

# 导入安全配置模块
try:
    from secure_config import (
        get_secret_key,
        get_license_salt,
        is_release_build,
        require_strict_online,
        allow_trial_self_register,
        get_trial_register_endpoint,
        get_trial_register_signing_key,
    )
except ImportError:
    # 如果 secure_config 不存在，使用环境变量
    def get_secret_key(): return os.environ.get('VDL_SECRET_KEY', hashlib.sha256(platform.node().encode()).hexdigest())
    def get_license_salt(): return os.environ.get('VDL_LICENSE_SALT', 'default_salt_change_me')
    def is_release_build(): return False
    def require_strict_online(): return False
    def allow_trial_self_register(): return True
    def get_trial_register_endpoint(): return os.environ.get('VDL_TRIAL_REGISTER_ENDPOINT')
    def get_trial_register_signing_key(): return os.environ.get('VDL_TRIAL_REGISTER_SIGNING_KEY')

# ============== 配置区域 ==============
# 【安全】敏感信息从环境变量加载，不再硬编码

# 本地存储文件
LICENSE_FILE = "license_data.enc"  # 使用加密格式
TRIAL_FILE = "trial_data.enc"

# 试用期天数
TRIAL_DAYS = 15

# 【安全密钥】从 secure_config 模块获取
# 密钥通过环境变量设置，请参考 .env.template

def _get_cached_secret_key() -> str:
    """获取并缓存安全密钥"""
    return get_secret_key()

def _get_cached_license_salt() -> str:
    """获取并缓存许可证盐值"""
    return get_license_salt()

# 延迟初始化 - 安全密钥从配置模块获取
# 首次调用时会从环境变量加载
_cached_secret_key = None
_cached_license_salt = None
_DEVICE_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_HW_HASH_RE = re.compile(r"^[a-f0-9]{32}$")
_LICENSE_KEY_RE = re.compile(r"^[A-Z0-9-]{16,64}$")
_ALLOWED_PLAN_TYPES = {"yearly", "lifetime"}

def _is_dev_license_bypass_enabled() -> bool:
    """
    源码运行默认跳过授权校验；打包后在开发机上可通过标记文件跳过。

    跳过授权的方式（任一满足即可）：
    1. 非 frozen 模式（源码运行）→ 自动跳过
    2. 项目根目录存在 .dev_license_bypass 文件 → 跳过
    3. 设置 VDL_DEV_BYPASS=1 → 跳过

    如需测试完整授权流程，设置 VDL_ENABLE_DEV_LICENSE_CHECK=1 恢复校验。
    """
    # 源码运行：默认跳过
    if not getattr(sys, 'frozen', False):
        env_value = (os.environ.get("VDL_ENABLE_DEV_LICENSE_CHECK") or "").strip().lower()
        if env_value in ("1", "true", "yes", "y", "on"):
            return False
        return True

    # 打包后：检查开发标记
    # 标记文件：项目根目录下的 .dev_license_bypass
    try:
        marker_path = os.path.join(os.path.dirname(sys.executable), ".dev_license_bypass")
        if os.path.exists(marker_path):
            return True
    except Exception:
        pass

    # 环境变量标记
    env_bypass = (os.environ.get("VDL_DEV_BYPASS") or "").strip().lower()
    if env_bypass in ("1", "true", "yes", "y", "on"):
        return True

    return False

def is_debugger_present() -> bool:
    """检测是否有调试器附加"""
    # 1. 检查 sys.gettrace
    if sys.gettrace() is not None:
        return True
    
    # 2. 检查环境变量
    for env in ['PYCHARM_HOSTED', 'PYTHONBREAKPOINT']:
        if os.environ.get(env):
            return True
            
    return False

def _init_keys():
    global _cached_secret_key, _cached_license_salt
    
    # 安全检查：如果检测到调试器，增加随机干扰
    debug_salt = ""
    if is_debugger_present():
        print("[Security] 检测到调试环境，启用安全保护")
        debug_salt = str(uuid.uuid4())
        
    if _cached_secret_key is None:
        key = get_secret_key()
        if debug_salt:
            key = hashlib.sha256((key + debug_salt).encode()).hexdigest()
        _cached_secret_key = key
        
    if _cached_license_salt is None:
        salt = get_license_salt()
        if debug_salt:
            salt = hashlib.sha256((salt + debug_salt).encode()).hexdigest()[:24]
        _cached_license_salt = salt

# 为了兼容性保留这些变量名，但值通过函数获取
def get_SECRET_KEY():
    _init_keys()
    return _cached_secret_key

def get_LICENSE_SALT():
    _init_keys()
    return _cached_license_salt


def _is_valid_device_id(value: Any) -> bool:
    return bool(_DEVICE_ID_RE.fullmatch(str(value or "").strip()))


def _is_valid_hw_hash(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return bool(text and _HW_HASH_RE.fullmatch(text))


def _is_basic_license_key_shape(value: Any) -> bool:
    """
    仅校验激活码外形是否合法。

    在线激活场景下，真实有效性应由服务端判断，避免因客户端内置 salt
    与发码工具/服务端配置不一致而把已入库的激活码提前拦截掉。
    """
    text = str(value or "").strip().upper()
    if not text:
        return False

    if not _LICENSE_KEY_RE.fullmatch(text):
        return False

    clean_key = text.replace("-", "")
    return len(clean_key) == 16


def _normalize_plan_type(value: Any) -> str:
    text = str(value or "yearly").strip().lower() or "yearly"
    return text if text in _ALLOWED_PLAN_TYPES else "yearly"


def _normalize_trial_cache(data: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(data, dict):
        return None
    device_id = str(data.get("device_id") or "").strip().lower()
    start_date = str(data.get("start_date") or "").strip()
    last_check_time = str(data.get("last_check_time") or "").strip()
    if not _is_valid_device_id(device_id):
        return None
    if not _parse_datetime(start_date):
        return None
    if last_check_time and not _parse_datetime(last_check_time):
        return None
    return {
        "device_id": device_id,
        "start_date": start_date,
        "trial_days": TRIAL_DAYS,
        "last_check_time": last_check_time,
        "online_verified": bool(data.get("online_verified")),
    }


def _normalize_license_cache(data: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(data, dict):
        return None
    license_key = str(data.get("license_key") or "").strip().upper()
    plan_type = _normalize_plan_type(data.get("plan_type"))
    device_id = str(data.get("device_id") or "").strip().lower()
    if not _LICENSE_KEY_RE.fullmatch(license_key):
        return None
    if device_id and not _is_valid_device_id(device_id):
        return None
    expires_at = str(data.get("expires_at") or "").strip()
    if expires_at and not _parse_datetime(expires_at):
        return None
    activated_at = str(data.get("activated_at") or "").strip()
    if activated_at and not _parse_datetime(activated_at):
        activated_at = ""
    last_online_check = str(data.get("last_online_check") or "").strip()
    if last_online_check and not _parse_datetime(last_online_check):
        last_online_check = ""
    normalized = {
        "license_key": license_key,
        "plan_type": plan_type,
        "device_id": device_id,
        "expires_at": expires_at,
    }
    if activated_at:
        normalized["activated_at"] = activated_at
    if last_online_check:
        normalized["last_online_check"] = last_online_check
    return normalized


def _normalize_trial_endpoint(endpoint: str) -> str:
    text = (endpoint or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.username or parsed.password:
        return ""
    scheme = (parsed.scheme or "").lower()
    hostname = parsed.hostname or ""
    if scheme not in ("http", "https") or not hostname:
        return ""
    path = parsed.path or ""
    normalized = f"{scheme}://{parsed.netloc}{path}"
    return normalized.rstrip("/")


# ============== 设备指纹 ==============

# 设备ID缓存文件名
DEVICE_ID_FILE = "device_id.enc"

# 内存缓存
_cached_device_id = None

def _get_stable_hardware_info() -> list:
    """
    收集稳定的硬件信息
    按优先级排序，优先使用不易变化的信息
    """
    info_parts = []
    
    # 1. 计算机名（一般不会变）
    try:
        info_parts.append(f"node:{platform.node()}")
    except:
        pass
    
    # 2. 处理器架构（稳定）
    try:
        info_parts.append(f"machine:{platform.machine()}")
    except:
        pass
    
    # 3. 尝试获取 Windows 产品ID 或机器 GUID（非常稳定）
    if sys.platform == 'win32':
        try:
            import winreg
            # 优先使用 MachineGuid（Windows 安装时生成，非常稳定）
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY
            )
            machine_guid, _ = winreg.QueryValueEx(key, "MachineGuid")
            winreg.CloseKey(key)
            if machine_guid:
                info_parts.append(f"mguid:{machine_guid}")
        except:
            pass
        
        # 备用：Windows ProductId
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY
            )
            product_id, _ = winreg.QueryValueEx(key, "ProductId")
            winreg.CloseKey(key)
            if product_id:
                info_parts.append(f"pid:{product_id}")
        except:
            pass
    
    # 4. 处理器信息（较稳定）
    try:
        processor = platform.processor()
        if processor:
            info_parts.append(f"cpu:{processor}")
    except:
        pass
    
    # 5. 系统盘序列号（Windows，较稳定）
    if sys.platform == 'win32':
        try:
            import subprocess
            # 使用系统盘卷序列号而不是磁盘物理序列号（更稳定）
            result = subprocess.run(
                ['cmd', '/c', 'vol', 'C:'],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            output = result.stdout.strip()
            # 提取卷序列号（格式如 "卷序列号是 XXXX-XXXX"）
            if output:
                import re
                match = re.search(r'[A-F0-9]{4}-[A-F0-9]{4}', output, re.IGNORECASE)
                if match:
                    info_parts.append(f"vol:{match.group()}")
        except:
            pass
    
    # 6. 网卡 MAC 地址（虽然可修改，但作为辅助指纹很有用）
    try:
        import uuid
        mac = ':'.join(['{:02x}'.format((uuid.getnode() >> ele) & 0xff) for ele in range(0, 8*6, 8)][::-1])
        if mac and mac != '00:00:00:00:00:00':
            info_parts.append(f"mac:{mac}")
    except:
        pass
        
    # 7. 主板序列号 (Windows)
    if sys.platform == 'win32':
        try:
            import subprocess
            result = subprocess.run(
                ['wmic', 'baseboard', 'get', 'serialnumber'],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            output = result.stdout.strip().split('\n')
            if len(output) > 1:
                serial = output[1].strip()
                if serial and serial.lower() != 'to be filled by o.e.m.':
                    info_parts.append(f"baseboard:{serial}")
        except:
            pass

    return info_parts


def get_hw_fingerprint_hash() -> str:
    """
    生成独立于 device_id 的硬件指纹摘要，用于服务端交叉验证。
    优先使用强标识（MachineGuid、主板序列号）；若缺失，再退回到
    node/mac/卷序列号等组合，避免仅靠 CPU/ProductId 这类弱标识导致
    不同机器被误判为同一台。
    """
    try:
        parts = _get_stable_hardware_info()
        strong_prefixes = ("mguid:", "baseboard:")
        fallback_prefixes = ("node:", "mac:", "vol:", "machine:", "cpu:")
        weak_prefixes = ("pid:",)

        strong_parts = sorted(p for p in parts if any(p.startswith(pfx) for pfx in strong_prefixes))
        fallback_parts = sorted(p for p in parts if any(p.startswith(pfx) for pfx in fallback_prefixes))
        weak_parts = sorted(p for p in parts if any(p.startswith(pfx) for pfx in weak_prefixes))

        if strong_parts:
            key_parts = strong_parts + fallback_parts[:3] + weak_parts[:1]
        elif fallback_parts:
            # 没有强标识时，至少要求多项组合，避免 CPU/ProductId 这种弱标识单独撞库。
            key_parts = fallback_parts[:4]
            if len(key_parts) < 2:
                key_parts = []
        else:
            key_parts = []

        if not key_parts:
            # 回退到 device_id 派生值，宁可降低“跨 device_id 关联”能力，
            # 也不要把不同机器误判成同一试用设备。
            fallback_device_id = get_device_id()
            if not _is_valid_device_id(fallback_device_id):
                return ""
            return hashlib.sha256(f"hw:{fallback_device_id}".encode()).hexdigest()[:32]
        combined = "|".join(key_parts)
        return hashlib.sha256(combined.encode()).hexdigest()[:32]
    except Exception:
        return ""


def is_running_in_vm() -> bool:
    """
    检测是否在虚拟机中运行
    用于防止通过虚拟机快照无限重置试用期
    """
    if sys.platform != 'win32':
        return False
        
    vm_indicators = [
        'vmware', 'virtualbox', 'vbox', 'qemu', 'xen', 'hyper-v', 'microsoft corporation'
    ]
    
    try:
        # 1. 检查主板信息
        import subprocess
        result = subprocess.run(
            ['wmic', 'baseboard', 'get', 'manufacturer,product'],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        output = result.stdout.lower()
        for indicator in vm_indicators:
            if indicator in output:
                return True
                
        # 2. 检查 MAC 地址前缀
        import uuid
        mac = '{:012x}'.format(uuid.getnode())
        vm_mac_prefixes = ['080027', '000569', '000c29', '005056', '00155d']
        for prefix in vm_mac_prefixes:
            if mac.startswith(prefix):
                return True
    except:
        pass
        
    return False


def _load_cached_device_id() -> Optional[str]:
    """从本地缓存加载设备ID"""
    try:
        device_id_path = os.path.join(get_app_data_dir(), DEVICE_ID_FILE)
        if os.path.exists(device_id_path):
            with open(device_id_path, 'r', encoding='utf-8') as f:
                encrypted = f.read().strip()
            data = _decrypt_data(encrypted)
            if data and 'device_id' in data:
                cached = str(data.get('device_id') or '').strip().lower()
                if _is_valid_device_id(cached):
                    return cached
    except:
        pass
    return None


def _save_device_id(device_id: str) -> bool:
    """保存设备ID到本地缓存"""
    try:
        device_id = str(device_id or "").strip().lower()
        if not _is_valid_device_id(device_id):
            return False
        device_id_path = os.path.join(get_app_data_dir(), DEVICE_ID_FILE)
        encrypted = _encrypt_data({'device_id': device_id, 'created': datetime.now().isoformat()})
        with open(device_id_path, 'w', encoding='utf-8') as f:
            f.write(encrypted)
        return True
    except:
        return False


def get_device_id() -> str:
    """
    生成唯一设备标识符（带缓存）
    
    改进：
    1. 优先使用缓存的设备ID，避免每次重新计算导致的不稳定
    2. 使用更稳定的硬件信息（如 Windows MachineGuid）
    3. 多重容错，确保即使部分信息获取失败也能生成一致的ID
    """
    global _cached_device_id
    
    # 1. 内存缓存
    if _cached_device_id:
        return _cached_device_id
    
    # 2. 本地文件缓存
    cached = _load_cached_device_id()
    if cached:
        _cached_device_id = cached
        return cached
    
    # 3. 生成新的设备ID
    try:
        info_parts = _get_stable_hardware_info()
        
        if len(info_parts) >= 2:
            # 至少有2个稳定信息才生成
            combined = '|'.join(sorted(info_parts))  # 排序确保一致性
            device_hash = hashlib.sha256(combined.encode()).hexdigest()[:32]
        else:
            # 信息不足，使用随机UUID
            device_hash = str(uuid.uuid4()).replace('-', '')[:32]
        
        # 保存到缓存
        _save_device_id(device_hash)
        _cached_device_id = device_hash
        return device_hash
        
    except Exception as e:
        # 如果无法生成，使用随机 UUID
        fallback_id = str(uuid.uuid4()).replace('-', '')[:32]
        _save_device_id(fallback_id)
        _cached_device_id = fallback_id
        return fallback_id


# ============== 注册表备份（防止试用期重置） ==============

REGISTRY_KEY_PATH = r"SOFTWARE\VideoDownloader"
REGISTRY_TRIAL_VALUE = "TrialInfo"

def _save_trial_to_registry(trial_start_date: str, device_id: str) -> bool:
    """
    将试用开始时间备份到 Windows 注册表
    这是防止用户删除 %APPDATA% 目录后试用期被重置的关键措施
    """
    if sys.platform != 'win32':
        return False
    if not _is_valid_device_id(device_id):
        return False
        
    try:
        import winreg
        # 使用 HKEY_CURRENT_USER，不需要管理员权限
        key = winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            REGISTRY_KEY_PATH,
            0,
            winreg.KEY_WRITE
        )
        
        # 将试用信息加密后存储
        trial_info = {
            'start_date': trial_start_date,
            'device_id': device_id,
            'backup_time': datetime.now().isoformat()
        }
        encrypted = _encrypt_data(trial_info)
        
        winreg.SetValueEx(key, REGISTRY_TRIAL_VALUE, 0, winreg.REG_SZ, encrypted)
        winreg.CloseKey(key)
        return True
    except Exception as e:
        print(f"[Security] 注册表备份失败: {e}")
        return False


def _load_trial_from_registry() -> Optional[Dict]:
    """
    从 Windows 注册表恢复试用开始时间
    当本地文件丢失时使用
    """
    if sys.platform != 'win32':
        return None
        
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            REGISTRY_KEY_PATH,
            0,
            winreg.KEY_READ
        )
        
        encrypted, _ = winreg.QueryValueEx(key, REGISTRY_TRIAL_VALUE)
        winreg.CloseKey(key)
        
        if encrypted:
            return _normalize_trial_cache(_decrypt_data(encrypted))
    except FileNotFoundError:
        # 注册表项不存在
        pass
    except Exception as e:
        print(f"[Security] 读取注册表备份失败: {e}")
    
    return None


# ============== 安全存储 ==============

def _compute_signature(data: str) -> str:
    """计算数据签名（HMAC-SHA256）"""
    return hmac.new(
        get_SECRET_KEY().encode(), 
        data.encode(), 
        hashlib.sha256
    ).hexdigest()


def _derive_aes_key() -> bytes:
    """从密钥派生 AES 加密密钥（32 字节，URL-safe Base64 编码供 Fernet 使用）"""
    raw = hashlib.sha256(get_SECRET_KEY().encode()).digest()
    return base64.urlsafe_b64encode(raw)


def _encrypt_data(data: Dict) -> str:
    """AES-128-CBC 加密 + HMAC 签名（Fernet 格式）"""
    json_str = json.dumps(data, ensure_ascii=False, sort_keys=True)
    signature = _compute_signature(json_str)

    combined = {
        'data': data,
        'sig': signature,
        'ts': datetime.now().isoformat()
    }

    plaintext = json.dumps(combined, ensure_ascii=False, sort_keys=True).encode('utf-8')
    fernet = Fernet(_derive_aes_key())
    return fernet.encrypt(plaintext).decode()


def _decrypt_data(encrypted: str) -> Optional[Dict]:
    """解密并验证数据，兼容旧版 Base64 格式"""
    token = (encrypted or "").strip()
    if not token:
        return None

    def _verify_and_extract(combined: dict) -> Optional[Dict]:
        data = combined.get('data')
        signature = combined.get('sig')
        if not data or not signature:
            return None
        json_str = json.dumps(data, ensure_ascii=False, sort_keys=True)
        expected_sig = _compute_signature(json_str)
        if not hmac.compare_digest(signature, expected_sig):
            print("[安全警告] 数据签名不匹配，可能被篡改！")
            return None
        return data

    # 1) 尝试 Fernet AES 解密
    try:
        fernet = Fernet(_derive_aes_key())
        plaintext = fernet.decrypt(token.encode())
        combined = json.loads(plaintext.decode('utf-8'))
        return _verify_and_extract(combined)
    except Exception:
        pass

    # 2) 回退兼容旧版 Base64 格式（迁移由 _load_and_migrate 负责）
    try:
        decoded = base64.b64decode(token.encode()).decode()
        combined = json.loads(decoded)
        return _verify_and_extract(combined)
    except Exception:
        return None


def get_app_data_dir() -> str:
    """获取应用数据目录。开发态与发布态分离，避免共享试用/授权缓存。"""
    override_dir = os.environ.get('VDL_APP_DATA_DIR')
    if override_dir:
        data_dir = os.path.abspath(override_dir)
    elif getattr(sys, 'frozen', False):
        if sys.platform == 'win32':
            app_data = os.environ.get('APPDATA', os.path.expanduser('~'))
            data_dir = os.path.join(app_data, 'VideoDownloader')
        else:
            data_dir = os.path.expanduser('~/.video_downloader')
    else:
        project_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(project_dir, '.dev_data', 'VideoDownloader')

    os.makedirs(data_dir, exist_ok=True)
    return data_dir


def _is_legacy_token(token: str) -> bool:
    """Fernet 格式以 'gAAAAA' 开头，旧格式为纯 Base64 JSON。"""
    return not (token or "").strip().startswith("gAAAAA")


def _load_and_migrate(file_path: str, normalize_fn) -> Optional[Dict]:
    """读取加密文件，若为旧版 Base64 格式则自动迁移至 AES。"""
    if not os.path.exists(file_path):
        return None
    with open(file_path, 'r', encoding='utf-8') as f:
        token = f.read().strip()
    if not token:
        return None
    data = _decrypt_data(token)
    if data is None:
        return None
    normalized = normalize_fn(data) if normalize_fn else data
    if normalized and _is_legacy_token(token):
        try:
            new_token = _encrypt_data(normalized)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_token)
            print(f"[Security] 已将 {os.path.basename(file_path)} 迁移至 AES 加密")
        except Exception:
            pass
    return normalized


def load_local_license() -> Optional[Dict]:
    """加载本地缓存的许可证信息（带签名验证）"""
    try:
        license_path = os.path.join(get_app_data_dir(), LICENSE_FILE)
        return _load_and_migrate(license_path, _normalize_license_cache)
    except Exception as e:
        print(f"加载许可证失败: {e}")
    return None


def save_local_license(license_data: Dict) -> bool:
    """保存许可证信息到本地（AES 加密）"""
    try:
        normalized = _normalize_license_cache(license_data)
        if not normalized:
            return False
        license_path = os.path.join(get_app_data_dir(), LICENSE_FILE)
        encrypted = _encrypt_data(normalized)
        with open(license_path, 'w', encoding='utf-8') as f:
            f.write(encrypted)
        return True
    except Exception as e:
        print(f"保存许可证失败: {e}")
        return False


def load_trial_data() -> Optional[Dict]:
    """加载试用信息（带签名验证）"""
    try:
        trial_path = os.path.join(get_app_data_dir(), TRIAL_FILE)
        return _load_and_migrate(trial_path, _normalize_trial_cache)
    except:
        pass
    return None


def save_trial_data(trial_data: Dict) -> bool:
    """保存试用信息（加密存储）"""
    try:
        normalized = _normalize_trial_cache(trial_data)
        if not normalized:
            return False
        trial_path = os.path.join(get_app_data_dir(), TRIAL_FILE)
        encrypted = _encrypt_data(normalized)
        with open(trial_path, 'w', encoding='utf-8') as f:
            f.write(encrypted)
        return True
    except:
        return False


def _to_utc_naive(dt: datetime) -> datetime:
    """将 datetime 统一转换为“无时区的 UTC 时间”以便安全比较。"""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _parse_datetime(value: str) -> Optional[datetime]:
    """兼容解析 ISO 时间字符串（支持 Z 后缀），并统一为 UTC naive。"""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return _to_utc_naive(parsed)
    except:
        return None


# ============== 试用管理 ==============

# ============== HTTPS 证书钉扎 ==============

_CERT_CACHE_DIR_NAME = "cert_cache"


def _get_cert_cache_dir() -> str:
    cache_dir = os.path.join(get_app_data_dir(), _CERT_CACHE_DIR_NAME)
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def _url_to_cert_filename(url: str) -> str:
    """将 URL 的 host:port 转为安全文件名。"""
    parsed = urlparse(url)
    host = (parsed.hostname or "unknown").replace(".", "_")
    port = parsed.port or 443
    return f"{host}_{port}.pem"


def _get_ca_bundle_path() -> Optional[str]:
    """优先返回 certifi CA 包路径，失败时返回 None（回退系统 CA）。"""
    try:
        import certifi

        ca_path = certifi.where()
        if ca_path and os.path.isfile(ca_path):
            return ca_path
    except Exception:
        pass
    return None


def _requests_verify_target() -> Any:
    """requests 的 verify 参数：优先 certifi，回退系统 CA(True)。"""
    ca_path = _get_ca_bundle_path()
    return ca_path if ca_path else True


class _SystemSSLContextAdapter(HTTPAdapter):
    """让 requests 显式使用操作系统证书库。"""

    def __init__(self, ssl_context, **kwargs):
        self._ssl_context = ssl_context
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs["ssl_context"] = self._ssl_context
        return super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        proxy_kwargs["ssl_context"] = self._ssl_context
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def _request_with_system_ca(method: str, url: str, timeout: int, **kwargs):
    """使用系统证书库重试 HTTPS 请求。"""
    import ssl

    session = requests.Session()
    adapter = _SystemSSLContextAdapter(ssl.create_default_context())
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    try:
        request_kwargs = dict(kwargs)
        request_kwargs.pop("verify", None)
        return session.request(method.upper(), url, timeout=timeout, **request_kwargs)
    finally:
        session.close()


def _is_dev_relaxed_tls() -> bool:
    """
    开发机放宽 TLS 校验（仅开发态生效）：
    - 需要显式设置环境变量 VDL_DEV_RELAX_TLS=1
    - 发布版永远返回 False
    """
    if getattr(sys, "frozen", False):
        return False
    return os.environ.get("VDL_DEV_RELAX_TLS", "").strip() in ("1", "true", "yes", "on")


def _download_and_pin_cert(url: str, cert_path: str) -> Tuple[bool, str]:
    """首次连接时下载服务器证书并保存到本地。
    
    安全策略：
    1. 优先使用系统 CA 验证后获取证书（安全）
    2. 仅在非发布/非严格模式下允许跳过验证获取证书（TOFU）
    3. 发布+严格模式下如果系统 CA 验证失败则拒绝连接
    """
    import ssl
    import socket
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    port = parsed.port or 443

    reason = ""

    # 尝试 1：优先 Certifi，再回退系统证书库。
    # 这样既能保证发布版默认严格校验，也兼容用户机器里已经信任、但 certifi
    # 暂时无法直接校验的企业/系统证书链场景。
    verify_attempts = []
    ca_bundle = _get_ca_bundle_path()
    if ca_bundle:
        verify_attempts.append(("certifi CA", ssl.create_default_context(cafile=ca_bundle)))
    verify_attempts.append(("系统证书库", ssl.create_default_context()))

    for verify_label, ctx in verify_attempts:
        try:
            with socket.create_connection((hostname, port), timeout=10) as sock:
                with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                    der_cert = ssock.getpeercert(True)
                    if der_cert:
                        pem_cert = ssl.DER_cert_to_PEM_cert(der_cert)
                        with open(cert_path, "w", encoding="utf-8") as f:
                            f.write(pem_cert)
                        print(f"[Security] 已钉扎服务器证书({verify_label}): {hostname}:{port}")
                        return True, ""
        except ssl.SSLError as e:
            reason = f"{verify_label} 验证失败: {e}"
            print(f"[Security] {reason}")
        except Exception as e:
            reason = f"{verify_label} 连接失败: {e}"
            print(f"[Security] {reason}")

    # 发布+严格模式下不允许跳过 CA 验证
    # 但是，在非打包环境（没有 frozen 标志）下，我们放宽这个限制，
    # 允许触发 TOFU（首次连接信任）机制，以便开发者可以使用自签名证书进行测试。
    is_frozen = getattr(sys, "frozen", False)
    strict = bool(is_frozen and require_strict_online())
    if strict:
        print(f"[Security] 发布严格模式（打包版）：拒绝跳过 CA 验证，证书钉扎失败")
        return False, (reason or "发布严格模式下证书钉扎失败")

    # 尝试 2：非严格模式下 TOFU（信任首次连接）——仅用于自签名证书开发/测试
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((hostname, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                der_cert = ssock.getpeercert(True)
                if not der_cert:
                    return False
                pem_cert = ssl.DER_cert_to_PEM_cert(der_cert)
                with open(cert_path, "w", encoding="utf-8") as f:
                    f.write(pem_cert)
                print(f"[Security] 已钉扎服务器证书(TOFU): {hostname}:{port}")
                return True, ""
    except Exception as e:
        reason = f"下载服务器证书失败: {e}"
        print(f"[Security] {reason}")
        return False, reason

    return False, (reason or "证书钉扎失败")


def _get_pinned_cert_path(url: str) -> Tuple[Optional[str], str]:
    """
    获取钉扎的证书路径。
    首次连接时自动下载并缓存证书，之后使用缓存的证书验证。
    如果无法获取证书，返回 None（调用方应拒绝连接）。
    """
    try:
        cache_dir = _get_cert_cache_dir()
        cert_file = _url_to_cert_filename(url)
        cert_path = os.path.join(cache_dir, cert_file)

        if os.path.isfile(cert_path):
            return cert_path, ""  # 使用已钉扎的证书

        # 首次连接：下载并保存证书
        ok, msg = _download_and_pin_cert(url, cert_path)
        if ok:
            return cert_path, ""
        return None, (msg or "首次证书钉扎失败")
    except Exception as e:
        msg = f"证书钉扎异常: {e}"
        print(f"[Security] {msg}")
        return None, msg

    # 无法获取证书时返回 None，调用方应拒绝请求
    return None, "无法获取证书"


def _is_https_url(url: str) -> bool:
    try:
        parsed = urlparse((url or "").strip())
        return parsed.scheme.lower() == "https" and bool(parsed.netloc)
    except Exception:
        return False


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _canonical_json(data: Dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default)


def _trial_endpoint_signing_key() -> str:
    # 仅使用独立的响应签名密钥，不再回退到 API Key
    return (get_trial_register_signing_key() or "").strip()


def _verify_trial_endpoint_response(data: Dict[str, Any], request_id: str) -> Tuple[bool, str]:
    if not isinstance(data, dict):
        return (False, t("响应不是对象"))

    echoed_request_id = str(data.get("request_id") or "").strip()
    if request_id and echoed_request_id != request_id:
        return (False, t("request_id 缺失或不匹配"))

    server_time_raw = data.get("server_time")
    server_time = _parse_datetime(str(server_time_raw or ""))

    signing_key = _trial_endpoint_signing_key()
    sig = str(data.get("sig") or "").strip()
    sig_alg = str(data.get("sig_alg") or "").strip().upper()
    has_signature = bool(sig and sig_alg == "HMAC-SHA256")

    if not server_time:
        return (False, t("缺少有效 server_time"))

    if not has_signature:
        return (False, t("缺少有效签名字段"))

    if not signing_key:
        return (False, t("未配置试用响应签名密钥"))

    signed_payload = dict(data)
    signed_payload.pop("sig", None)
    expected = hmac.new(
        signing_key.encode("utf-8"),
        _canonical_json(signed_payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return (False, t("响应签名校验失败"))

    return (True, "")


def _extract_endpoint_reference_time(data: Optional[Dict[str, Any]]) -> Optional[datetime]:
    if not isinstance(data, dict):
        return None
    return _parse_datetime(str(data.get("server_time") or ""))


def _extract_endpoint_remaining_days(data: Optional[Dict[str, Any]]) -> Optional[int]:
    if not isinstance(data, dict):
        return None
    value = data.get("remaining_days")
    if value is None:
        return None
    try:
        days = int(value)
        return max(days, 0)
    except Exception:
        return None


def _post_trial_endpoint(url: str, payload: Dict[str, Any]) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """向试用服务端点发起 POST 请求（直连优先，失败回退代理）。"""
    if is_release_build() and (not _is_https_url(url)):
        return (False, t("发布版要求试用服务使用 HTTPS"), None)

    # HMAC 请求签名（禁止静态 API Key 回退）
    signing_key = _trial_endpoint_signing_key()
    if not signing_key:
        print("[Auth] [FAIL] 试用服务签名密钥缺失")
        return (False, t("未配置试用响应签名密钥，拒绝不安全回退"), None)
    
    headers = {"Content-Type": "application/json"}

    request_payload = dict(payload or {})
    request_id = str(request_payload.get("request_id") or "").strip() or uuid.uuid4().hex
    request_payload["request_id"] = request_id

    # 使用 HMAC 签名，防止提取后重放 (防本地时钟不准)
    net_time = get_network_time()
    now_dt = _to_utc_naive(net_time) if net_time else datetime.utcnow()
    timestamp = now_dt.isoformat() + "Z"
    request_payload["requested_at"] = timestamp
    
    # 调试日志：记录关键签名要素 (混淆处理以防日志泄露密文)
    if is_release_build():
        key_hint = f"{signing_key[:4]}...{signing_key[-4:]}" if len(signing_key) > 8 else "***"
        print(f"[Auth] [DEBUG] API={url.split('/')[-1]} ID={request_id[:8]} TS={timestamp} KEY={key_hint}")
    
    sig_payload = timestamp + _canonical_json(request_payload)
    request_sig = hmac.new(
        signing_key.encode("utf-8"),
        sig_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    headers["X-Request-Timestamp"] = timestamp
    headers["X-Request-Signature"] = request_sig

    errors = []
    for mode in ("direct", "proxy"):
        pinned = None
        try:
            kwargs = {"headers": headers, "json": request_payload}
            if mode == "direct":
                kwargs["proxies"] = {"http": None, "https": None}

            # HTTPS 证书处理：
            # 1. 先尝试预热/缓存服务端证书，供后续安全审计和轮换检测使用
            # 2. 实际请求始终使用标准 CA 链校验，避免把服务端叶子证书误当 CA 包传给 requests.verify
            if str(url).startswith("https:"):
                pinned, pin_msg = _get_pinned_cert_path(url)
                kwargs["verify"] = _requests_verify_target()
                if pin_msg:
                    print(f"[Security] {mode} 证书预热不可用，继续使用 CA 校验: {pin_msg}")
                # 开发环境可选放宽 TLS 校验（仅用于测试）
                if _is_dev_relaxed_tls():
                    kwargs["verify"] = False

            response = requests.post(url, timeout=10, **kwargs)
            data = None
            try:
                data = response.json()
            except Exception:
                data = None

            if response.status_code != 200:
                err_msg = ""
                if isinstance(data, dict):
                    err_msg = str(data.get("message") or "")
                
                # 特殊处理 401 Unauthorized：这通常意味着签名校验失败 (Invalid API Key)
                if response.status_code == 401:
                    advise = "。请检查：1. 电脑系统时间是否准确 (当前校时: {})；2. 服务端 Signing Key 是否匹配。".format("成功" if net_time else "失败")
                    return (False, (err_msg or t("验证失败 (401)")) + advise, data)

                # 如果得到了服务端明确解析的业务拒绝回复，则中止后续同类重试 (如 proxy)
                if isinstance(data, dict) and 'ok' in data:
                    return (False, err_msg or t_format("请求被拒绝 (状态码 {})", response.status_code), data)

                if err_msg:
                    errors.append(f"{mode} 状态码 {response.status_code}: {err_msg}")
                else:
                    errors.append(f"{mode} 状态码 {response.status_code}")
                continue

            if not isinstance(data, dict):
                errors.append(f"{mode} 响应不是有效 JSON")
                continue

            verified, verify_msg = _verify_trial_endpoint_response(data, request_id)
            if not verified:
                errors.append(f"{mode} 响应安全校验失败: {verify_msg}")
                continue

            if data.get("success") is True or data.get("ok") is True:
                return (True, str(data.get("message") or t("请求成功")), data)

            errors.append(str(data.get("message") or f"{mode} 请求失败"))
        except requests.exceptions.SSLError as e:
            if str(url).startswith("https:") and not _is_dev_relaxed_tls():
                try:
                    response = _request_with_system_ca("post", url, 10, **kwargs)
                    data = None
                    try:
                        data = response.json()
                    except Exception:
                        data = None

                    if response.status_code == 200 and isinstance(data, dict):
                        verified, verify_msg = _verify_trial_endpoint_response(data, request_id)
                        if verified:
                            print(f"[Security] {mode} certifi 校验失败，系统证书库重试成功")
                            return (True, str(data.get("message") or t("请求成功")), data)
                        errors.append(f"{mode} 系统证书库重试后响应校验失败: {verify_msg}")
                        continue

                    if response.status_code == 401:
                        err_msg = ""
                        if isinstance(data, dict):
                            err_msg = str(data.get("message") or "")
                        advise = "。请检查：1. 电脑系统时间是否准确 (当前校时: {})；2. 服务端 Signing Key 是否匹配。".format("成功" if net_time else "失败")
                        return (False, (err_msg or t("验证失败 (401)")) + advise, data if isinstance(data, dict) else None)

                    if isinstance(data, dict) and 'ok' in data:
                        return (False, str(data.get("message") or t_format("请求被拒绝 (状态码 {})", response.status_code)), data)

                    if response.status_code != 200:
                        if isinstance(data, dict) and data.get("message"):
                            errors.append(f"{mode} 系统证书库重试状态码 {response.status_code}: {data.get('message')}")
                        else:
                            errors.append(f"{mode} 系统证书库重试状态码 {response.status_code}")
                        continue

                    errors.append(f"{mode} 系统证书库重试响应不是有效 JSON")
                    continue
                except Exception as retry_ex:
                    errors.append(f"{mode} HTTPS 校验失败(系统证书库重试也失败): {str(retry_ex)}")
                    continue

            # 开发环境下：如果标准 HTTPS 校验失败，尝试最后一次临时放宽校验（不推荐生产环境）
            if not getattr(sys, "frozen", False) and kwargs.get("verify") != False:
                print(f"[Security] {mode} HTTPS 校验失败，开发环境下尝试临时放宽校验...")
                try:
                    kwargs["verify"] = False
                    response = requests.post(url, timeout=10, **kwargs)
                    data = None
                    try:
                        data = response.json()
                    except:
                        pass
                    
                    if response.status_code == 200 and data:
                        verified, verify_msg = _verify_trial_endpoint_response(data, request_id)
                        if verified:
                            print(f"[Security] {mode} 放宽校验请求成功且签名验证通过")
                            return (True, str(data.get("message") or t("请求成功")), data)
                except Exception as ex:
                    print(f"[Security] {mode} 放宽校验尝试失败: {str(ex)}")

            errors.append(f"{mode} HTTPS 校验失败: {str(e)}")
        except requests.exceptions.ProxyError as e:
            errors.append(f"{mode} 代理连接失败: {str(e)}")
        except requests.exceptions.ConnectTimeout:
            errors.append(f"{mode} 连接超时")
        except requests.exceptions.ReadTimeout:
            errors.append(f"{mode} 响应超时")
        except requests.exceptions.ConnectionError as e:
            errors.append(f"{mode} 网络连接失败: {str(e)}")
        except Exception as e:
            errors.append(f"{mode} 请求失败: {str(e)}")

    return (False, "；".join(errors) if errors else t("调用登记服务失败"), None)


def _derive_trial_status_url(register_endpoint: str) -> str:
    endpoint = (register_endpoint or "").strip()
    if not endpoint:
        return endpoint
    if endpoint.endswith("/register-trial"):
        return endpoint[:-len("/register-trial")] + "/trial-status"
    return endpoint.rstrip("/") + "/trial-status"


def _derive_activate_license_url(register_endpoint: str) -> str:
    endpoint = (register_endpoint or "").strip()
    if not endpoint:
        return endpoint
    if endpoint.endswith("/register-trial"):
        return endpoint[:-len("/register-trial")] + "/activate-license"
    return endpoint.rstrip("/") + "/activate-license"


def _register_trial_via_endpoint(device_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    通过外部试用登记服务申请新设备试用资格。
    返回: (成功, 消息, 附加数据)
    """
    endpoint = _normalize_trial_endpoint(get_trial_register_endpoint() or "")
    if not endpoint:
        return (False, t("试用登记服务地址无效"), None)

    payload = {
        "device_id": device_id,
        "app": "chaos_downloader",
        "hw_hash": get_hw_fingerprint_hash(),  # 硬件指纹摘要，服务端交叉验证
        "requested_at": datetime.utcnow().isoformat(),
    }
    return _post_trial_endpoint(endpoint, payload)


def _query_trial_status_via_endpoint(device_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    查询服务端是否存在该设备试用记录。
    返回:
      - 成功且存在: (True, msg, {'trial_start_date': ..., 'server_time': ..., 'remaining_days': ...})
      - 成功但不存在: (False, "not found", None)
      - 请求失败: (False, error_msg, None)
    """
    endpoint = _normalize_trial_endpoint(get_trial_register_endpoint() or "")
    if not endpoint:
        return (False, t("试用登记服务地址无效"), None)

    status_url = _derive_trial_status_url(endpoint)
    payload = {
        "device_id": device_id,
        "app": "chaos_downloader",
        "hw_hash": get_hw_fingerprint_hash(),
        "requested_at": datetime.utcnow().isoformat(),
    }
    ok, msg, data = _post_trial_endpoint(status_url, payload)
    if not ok:
        return (False, msg, None)

    exists = bool(data.get("exists")) if isinstance(data, dict) else False
    if not exists:
        return (False, str(msg or "not found"), None)

    trial_start = data.get("trial_start_date") if isinstance(data, dict) else None
    result_data: Dict[str, Any] = {}
    if trial_start:
        result_data["trial_start_date"] = trial_start
    if isinstance(data, dict):
        for key in ("server_time", "remaining_days", "trial_active", "trial_days"):
            if key in data:
                result_data[key] = data.get(key)
    return (
        True,
        str(msg or "found"),
        result_data,
    )


def _verify_license_via_endpoint(license_key: str, device_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    endpoint = _normalize_trial_endpoint(get_trial_register_endpoint() or "")
    if not endpoint:
        return (False, t("授权服务地址无效"), None)

    activate_url = _derive_activate_license_url(endpoint)
    payload = {
        "device_id": device_id,
        "license_key": license_key,
        "app": "chaos_downloader",
        "hw_hash": get_hw_fingerprint_hash(),
        "requested_at": datetime.utcnow().isoformat(),
    }
    ok, msg, data = _post_trial_endpoint(activate_url, payload)
    if not ok:
        return (False, msg, None)
    if not isinstance(data, dict):
        return (False, t("授权服务返回异常"), None)

    response_device = str(data.get("device_id") or "").strip()
    if response_device and response_device != device_id:
        return (False, t("授权服务返回设备不匹配"), None)

    normalized = dict(data)
    normalized["license_key"] = license_key
    plan_type = str(data.get("plan_type") or "yearly").strip().lower() or "yearly"
    if plan_type not in ("yearly", "lifetime"):
        plan_type = "yearly"
    normalized["plan_type"] = plan_type
    return (True, str(data.get("message") or t("激活成功")), normalized)


def get_network_time() -> Optional[datetime]:
    """
    从多个 HTTPS 公共源获取网络时间（UTC）
    用于防止用户通过修改系统时间绕过试用期限制

    多源交叉校验：收集多个来源的时间，丢弃与中位数偏差过大的离群来源，
    避免第三方校时服务时钟损坏（如 timeapi.io 时钟漂移约 19 分钟）导致时间戳错误，
    也避免境外时间源被网络阻断后 fallback 到坏时钟源。
    """
    time_apis = [
        # 国内可直达的头部时间源（优先：响应快且准确）
        {"url": "https://www.qq.com", "type": "header"},
        # 国际头部时间源（国内通常也可达，响应快）
        {"url": "https://www.microsoft.com", "type": "header"},
        {"url": "https://www.apple.com", "type": "header"},
        # 国内备用头部源（响应较慢，作为冗余）
        {"url": "https://www.baidu.com", "type": "header"},
        # 专用时间 API（个别服务存在时钟漂移风险，由交叉校验兜底）
        {"url": "https://timeapi.io/api/Time/current/zone?timeZone=UTC", "type": "json", "key": "dateTime"},
        {"url": "https://worldtimeapi.org/api/timezone/Etc/UTC", "type": "json", "key": "datetime"},
        {"url": "https://www.google.com", "type": "header"}
    ]

    collected = []  # [(utc_naive_datetime, url), ...]
    for api in time_apis:
        for mode in ("direct", "proxy"):
            # 优先尝试严格证书校验，如果因为本地时钟太离谱导致 SSL 校验失败，
            # 则尝试在当前 API 上临时关闭校验（仅用于获取时间参考值）
            got = None
            for verify in (True, False):
                try:
                    kwargs = {}
                    if mode == "direct":
                        kwargs["proxies"] = {'http': None, 'https': None}

                    # 使用当前选择的 verify 状态
                    kwargs["verify"] = verify

                    response = requests.get(
                        api["url"],
                        timeout=8,
                        **kwargs,
                    )

                    if api["type"] == "header":
                        # 从 HTTP 响应头获取 Date
                        date_str = response.headers.get('Date')
                        if date_str:
                            # 格式通常为: Tue, 20 Jan 2026 05:30:00 GMT
                            from email.utils import parsedate_to_datetime
                            got = _to_utc_naive(parsedate_to_datetime(date_str))

                    if got is None and response.status_code == 200 and api["type"] == "json":
                        data = response.json()
                        # 支持嵌套键值，如 "result.datetime_1"
                        keys = api["key"].split('.')
                        val = data
                        for k in keys:
                            if isinstance(val, dict):
                                val = val.get(k)

                        if val:
                            try:
                                parsed = datetime.fromisoformat(str(val).replace('Z', '+00:00'))
                                got = _to_utc_naive(parsed)
                            except ValueError:
                                # 尝试处理 "YYYY-MM-DD HH:MM:SS" 格式
                                got = datetime.strptime(str(val), "%Y-%m-%d %H:%M:%S")

                    # 如果 verify=True 成功运行但没拿到数据，或者返回非 200，没必要再试 verify=False
                    break
                except requests.exceptions.SSLError:
                    # 如果是 SSL 错误且当前是 verify=True，则继续尝试 verify=False
                    if verify:
                        continue
                    break
                except Exception:
                    # 其他错误直接换 API 或 模式
                    break

            if got is not None:
                collected.append((got, api["url"]))
                break  # 该来源已成功，换下一个 API

        # 已收集到 3 个来源即可交叉校验，避免在不可达来源上浪费等待时间
        if len(collected) >= 3:
            break

    if not collected:
        return None

    # ---- 多源交叉校验：丢弃与中位数偏差超过 60 秒的离群来源 ----
    times = [t for t, _ in collected]
    sorted_times = sorted(times)
    n = len(sorted_times)
    if n % 2 == 1:
        median = sorted_times[n // 2]
    else:
        mid = n // 2
        median = sorted_times[mid - 1] + (sorted_times[mid] - sorted_times[mid - 1]) / 2

    max_skew = 60  # 秒；超过此偏差的来源视为不可信
    good = [(t, u) for t, u in collected if abs((t - median).total_seconds()) <= max_skew]

    if not good:
        # 来源互相矛盾：若只有一个来源则按旧行为返回（向后兼容），
        # 否则不采信任何不可信时间（由调用方回退到系统时间）
        if len(collected) == 1:
            return collected[0][0]
        return None

    # 取与中位数最接近的时间作为最终结果
    good.sort(key=lambda x: abs((x[0] - median).total_seconds()))
    return good[0][0]


def verify_integrity() -> bool:
    """运行时完整性检查：检测关键函数是否被篡改"""
    try:
        # 检查关键函数是否被 monkey-patch
        # 正常情况下，这些函数应该定义在当前模块中
        critical_funcs = [get_trial_status, get_device_id, _encrypt_data, _decrypt_data]
        for func in critical_funcs:
            if not hasattr(func, '__module__') or func.__module__ != __name__:
                return False
        return True
    except:
        return False

def get_trial_status() -> Tuple[bool, int, str]:
    """
    获取试用状态（安全验证版本）
    """
    # 运行时完整性检查
    if not verify_integrity():
        return (False, 0, t("程序完整性校验失败，可能已被篡改"))
        
    device_id = get_device_id()
    endpoint = _normalize_trial_endpoint(get_trial_register_endpoint() or "")
    endpoint_available = bool(endpoint)

    def _update_and_calc(
        start_date_str: str,
        reference_time: Optional[datetime] = None,
        remaining_days_hint: Optional[int] = None,
    ) -> Tuple[bool, int, str]:
        ref_time = reference_time or current_time
        start_date = _parse_datetime(start_date_str)
        if not start_date:
            return (False, 0, t("试用数据异常，请稍后重试"))
        if remaining_days_hint is None:
            elapsed = ref_time - start_date
            remaining_days = TRIAL_DAYS - elapsed.days
        else:
            remaining_days = max(int(remaining_days_hint), 0)
        save_trial_data({
            'device_id': device_id,
            'start_date': start_date_str,
            'trial_days': TRIAL_DAYS,
            'last_check_time': ref_time.isoformat(),
            'online_verified': True
        })
        _save_trial_to_registry(start_date_str, device_id)
        if remaining_days > 0:
            return (True, remaining_days, t_format("试用剩余 {} 天", remaining_days))
        return (False, 0, t("试用期已结束，请购买激活"))

    # 发布版至少要有试用/激活服务端在线验证通道。
    if is_release_build() and (not endpoint_available):
        return (False, 0, t("发布版授权配置异常：未配置在线验证通道"))

    # 商用安全策略：发布版只要启用试用/激活端点，就必须使用 HTTPS。
    if is_release_build() and endpoint_available and (not _is_https_url(endpoint)):
        return (False, 0, t("发布版试用登记服务必须使用 HTTPS 地址"))
    
    # 获取当前参考时间（优先使用网络时间，失败则回退到系统时间）
    network_time = get_network_time()
    current_time = _to_utc_naive(network_time) if network_time else datetime.utcnow()
    
    if network_time:
        print(f"[Security] 已成功获取网络时间: {network_time}")
    else:
        print("[Security] 警告: 无法获取网络时间，使用系统本地时间进行验证")
        if is_release_build() and require_strict_online():
            return (False, 0, t("网络时间校验失败，请检查网络后重试"))
    
    # 加载本地缓存数据
    trial_data = load_trial_data()
    
    # ============== 时间防篡改检测 ==============
    if trial_data:
        last_check_time = trial_data.get('last_check_time')
        if last_check_time:
            last_check = _parse_datetime(last_check_time)
            # 如果当前时间早于上次检查时间，说明时间被回退
            if last_check and current_time < last_check - timedelta(hours=1):
                return (False, 0, t("检测到系统时间异常（可能被回退），请校准后重试"))
    
    # ============== 试用服务端在线验证 ==============
    if endpoint_available:
        try:
            exists, ep_msg, ep_data = _query_trial_status_via_endpoint(device_id)
            if exists and ep_data:
                start_date = ep_data.get("trial_start_date")
                if start_date:
                    return _update_and_calc(
                        start_date,
                        reference_time=_extract_endpoint_reference_time(ep_data),
                        remaining_days_hint=_extract_endpoint_remaining_days(ep_data),
                    )

            # 服务端不存在记录，尝试登记新试用
            reg_ok, reg_msg, reg_data = _register_trial_via_endpoint(device_id)
            if reg_ok:
                ep_data2 = None
                start_date = reg_data.get("trial_start_date") if isinstance(reg_data, dict) else None
                if not start_date:
                    exists, _m, ep_data2 = _query_trial_status_via_endpoint(device_id)
                    if exists and isinstance(ep_data2, dict):
                        start_date = ep_data2.get("trial_start_date")
                if start_date:
                    ref_data = reg_data if isinstance(reg_data, dict) else ep_data2 if isinstance(ep_data2, dict) else None
                    ok, days, msg = _update_and_calc(
                        start_date,
                        reference_time=_extract_endpoint_reference_time(ref_data),
                        remaining_days_hint=_extract_endpoint_remaining_days(ref_data),
                    )
                    if ok:
                        return (True, days, t_format("欢迎使用！您有 {} 天免费试用期", TRIAL_DAYS))
                    return (ok, days, msg)
                return (False, 0, t("试用登记成功但未返回有效开始时间"))

            # 仅在明确 not found 场景给出登记失败，其余视为在线失败
            if "not found" in (ep_msg or "").lower():
                return (False, 0, t_format("试用登记失败：{}", reg_msg))
            if is_release_build() and require_strict_online():
                return (False, 0, t_format("在线验证失败：{}", reg_msg))
        except Exception as e:
            print(f"试用服务端在线验证失败: {e}")
            if is_release_build() and require_strict_online():
                return (False, 0, t("在线验证失败，请检查网络后重试"))

    # 发布版严格在线模式：未在线通过则拒绝离线回退
    if is_release_build() and require_strict_online():
        return (False, 0, t("发布版需联网验证授权状态"))
    
    # ============== 本地缓存验证（仅在无法联网时使用） ==============
    
    if trial_data is None:
        registry_backup = _load_trial_from_registry()
        if registry_backup and registry_backup.get('device_id') == device_id:
            backup_start_date = registry_backup.get('start_date')
            trial_data = {
                'device_id': device_id,
                'start_date': backup_start_date,
                'trial_days': TRIAL_DAYS,
                'last_check_time': current_time.isoformat(),
                'online_verified': False
            }
        elif is_release_build():
            return (False, 0, t("首次使用请连接网络验证试用状态"))
        else:
            trial_data = {
                'device_id': device_id,
                'start_date': current_time.isoformat(),
                'trial_days': TRIAL_DAYS,
                'last_check_time': current_time.isoformat(),
                'online_verified': False
            }
            _save_trial_to_registry(current_time.isoformat(), device_id)
        save_trial_data(trial_data)
    
    # 检查本地缓存是否有效
    if not trial_data.get('online_verified'):
        # 非严格在线模式允许离线试用（无域名/IP直连方案）
        if require_strict_online():
            return (False, 0, t("请连接网络验证试用状态"))
    
    # 检查设备匹配
    if trial_data.get('device_id') != device_id:
        return (False, 0, t("设备已更换，请连接网络重新验证"))
    
    # 检查本地缓存有效期（发布版 24 小时，开发版 3 天）
    last_check_time = trial_data.get('last_check_time')
    if last_check_time:
        try:
            last_check = _parse_datetime(last_check_time)
            if not last_check:
                raise ValueError("invalid last check time")
            cache_age = current_time - last_check
            max_offline_days = 1 if is_release_build() else 3
            if cache_age.days >= max_offline_days:
                if require_strict_online():
                    return (False, 0, t("离线时间过长，请连接网络验证"))
        except:
            pass
            
    # ============== 虚拟机限制 ==============
    if is_running_in_vm() and not trial_data.get('online_verified'):
        return (False, 0, t("检测到虚拟机环境，试用需在线验证"))
    
    # 计算剩余天数
    try:
        start_date = _parse_datetime(trial_data.get('start_date'))
        if not start_date:
            raise ValueError("invalid trial start date")
        elapsed = current_time - start_date
        remaining_days = TRIAL_DAYS - elapsed.days
        
        if remaining_days > 0:
            return (True, remaining_days, t_format("[离线模式] 试用剩余 {} 天", remaining_days))
        else:
            return (False, 0, t("试用期已结束，请购买激活"))
    except:
        return (False, 0, t("试用数据异常，请购买激活"))


# ============== 许可证管理器 ==============

class LicenseManager:
    """
    许可证管理器
    统一管理试用、激活、验证等功能
    """
    
    def __init__(self):
        self.license_data = load_local_license()
        self.device_id = get_device_id()
        
        # 许可证状态
        self.is_licensed = False
        self.is_trial = False
        self.trial_remaining = 0
        self.license_type = None  # 'trial', 'yearly', 'lifetime'
        self.expires_at = None
        self.status_message = ""
        
        # 初始化状态
        self._refresh_status()

    def _verify_license_online(self, license_key: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """统一在线验权入口：仅使用服务端接口。"""
        endpoint_available = bool(_normalize_trial_endpoint(get_trial_register_endpoint() or ""))
        if endpoint_available:
            return _verify_license_via_endpoint(license_key, self.device_id)
        return (False, t("当前部署未配置激活服务，仅支持试用模式"), None)
    
    def _refresh_status(self):
        """刷新许可证状态"""
        # 每次刷新先重置，避免沿用上次状态。
        self.is_licensed = False
        self.is_trial = False
        self.trial_remaining = 0
        self.license_type = None
        self.expires_at = None
        self.status_message = ""

        if _is_dev_license_bypass_enabled():
            self.is_licensed = True
            self.license_type = "lifetime"
            self.status_message = t("开发模式：已跳过授权校验")
            return

        endpoint_available = bool(_normalize_trial_endpoint(get_trial_register_endpoint() or ""))
        license_service_available = endpoint_available

        # 发布版至少要有一种在线通道（试用服务端或激活服务端）。
        if is_release_build() and (not license_service_available):
            self.status_message = t("发布版授权配置异常：无可用在线服务")
            return

        # 检查本地许可证
        if self.license_data:
            license_key = self.license_data.get('license_key')
            expires_at = self.license_data.get('expires_at')
            device_id = self.license_data.get('device_id')
            
            # 检查设备匹配
            if device_id and device_id != self.device_id:
                self.license_data = None
                self.status_message = t("设备不匹配，请重新激活")
            elif is_release_build():
                # 发布版不信任本地缓存，必须在线验权。
                if not license_key:
                    self.license_data = None
                    self.status_message = t("本地授权信息异常，请重新激活")
                elif not license_service_available:
                    # 未配置激活服务时，忽略本地已激活缓存，避免误判。
                    self.license_data = None
                    self.status_message = t("当前部署未配置激活服务，仅支持试用模式")
                else:
                    success, message, online_data = self._verify_license_online(license_key)
                    if success and online_data:
                        self.license_type = online_data.get('plan_type', 'yearly')
                        self.expires_at = _parse_datetime(online_data.get('expires_at'))
                        if self.expires_at and self.expires_at <= datetime.utcnow():
                            self.license_data = None
                            self.status_message = t("授权已过期，请续费后重试")
                            return

                        # 在线校验通过后刷新本地缓存
                        self.license_data = {
                            'license_key': license_key,
                            'plan_type': self.license_type,
                            'expires_at': online_data.get('expires_at'),
                            'device_id': self.device_id,
                            'activated_at': self.license_data.get('activated_at') or datetime.utcnow().isoformat(),
                            'last_online_check': datetime.utcnow().isoformat(),
                        }
                        save_local_license(self.license_data)

                        self.is_licensed = True
                        if self.license_type == 'lifetime':
                            self.status_message = t("已激活 (终身版)")
                        elif self.expires_at:
                            days_left = max((self.expires_at - datetime.utcnow()).days, 0)
                            self.status_message = t_format("已激活 ({}), 剩余 {} 天", self.license_type, days_left)
                        else:
                            self.status_message = t_format("已激活 ({})", self.license_type)
                        return

                    self.license_data = None
                    self.status_message = t_format("授权在线验证失败: {}", message)
            elif expires_at:
                # 开发模式下允许读取本地缓存（仅便于调试）
                expire_date = _parse_datetime(expires_at)
                if expire_date and datetime.utcnow() < expire_date:
                    self.is_licensed = True
                    self.license_type = self.license_data.get('plan_type', 'yearly')
                    self.expires_at = expire_date
                    days_left = max((expire_date - datetime.utcnow()).days, 0)
                    self.status_message = t_format("已激活 ({}), 剩余 {} 天", self.license_type, days_left)
                    return
            elif self.license_data.get('plan_type') == 'lifetime':
                # 开发模式下终身版可离线读取
                self.is_licensed = True
                self.license_type = 'lifetime'
                self.status_message = t("已激活 (终身版)")
                return
        
        # 检查试用状态
        if not self.is_licensed:
            is_trial, remaining, message = get_trial_status()
            self.is_trial = is_trial
            self.trial_remaining = remaining
            self.status_message = message
    
    def get_status(self) -> Dict[str, Any]:
        """获取当前许可证状态"""
        return {
            'is_licensed': self.is_licensed,
            'is_trial': self.is_trial,
            'trial_remaining': self.trial_remaining,
            'license_type': self.license_type,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'device_id': self.device_id,
            'message': self.status_message
        }
    
    def can_use_app(self) -> Tuple[bool, str]:
        """
        检查是否可以使用应用
        
        返回：
            (是否可用, 状态消息)
        """
        if is_release_build():
            endpoint_available = bool(_normalize_trial_endpoint(get_trial_register_endpoint() or ""))
            if not endpoint_available:
                return (False, t("发布版授权配置异常：无可用在线服务"))

        if self.is_licensed:
            return (True, self.status_message)
        
        if self.is_trial and self.trial_remaining > 0:
            return (True, self.status_message)

        if self.status_message:
            return (False, self.status_message)
        return (False, t("试用期已结束，请购买激活码"))
    
    def activate(self, license_key: str) -> Tuple[bool, str]:
        """
        使用激活码激活软件
        
        参数：
            license_key: 激活码
            
        返回：
            (是否成功, 消息)
        """
        # 清理激活码格式
        license_key = license_key.strip().upper()
        
        if not license_key:
            return (False, t("请输入激活码"))

        if not _is_basic_license_key_shape(license_key):
            return (False, t("激活码格式无效"))
        
        # 检查是否存在可用激活服务
        endpoint_available = bool(_normalize_trial_endpoint(get_trial_register_endpoint() or ""))
        if not endpoint_available:
            return (False, t("当前部署未配置激活服务，仅支持试用模式"))
        
        # 在线验证
        success, message, data = self._verify_license_online(license_key)
        
        if success and data:
            # 保存到本地
            self.license_data = {
                'license_key': license_key,
                'plan_type': data.get('plan_type', 'yearly'),
                'expires_at': data.get('expires_at'),
                'device_id': self.device_id,
                'activated_at': datetime.utcnow().isoformat(),
                'last_online_check': datetime.utcnow().isoformat(),
            }
            save_local_license(self.license_data)
            
            # 更新状态
            self.is_licensed = True
            self.is_trial = False
            self.license_type = data.get('plan_type', 'yearly')
            self.status_message = message
            
            return (True, message)
        
        return (False, message)
    
    def _offline_activate(self, license_key: str) -> Tuple[bool, str]:
        """
        离线激活 - 已禁用
        
        【安全更新 2026-01】
        为防止激活码被逆向工程伪造，离线激活功能已被禁用。
        所有激活请求必须通过在线验证完成。
        
        原因：
        1. 离线校验算法的盐值可通过反编译获取
        2. 获取盐值后可以生成任意有效激活码
        3. 在线验证可确保激活码真实有效且未被滥用
        """
        return (False, t("激活需要网络连接，请确保已连接互联网后重试"))
    
    def deactivate(self) -> bool:
        """注销当前激活（用于切换设备）"""
        self.license_data = None
        self.is_licensed = False
        self.license_type = None
        self.expires_at = None
        
        # 删除本地许可证文件
        try:
            license_path = os.path.join(get_app_data_dir(), LICENSE_FILE)
            if os.path.exists(license_path):
                os.remove(license_path)
            return True
        except:
            return False
    
    def get_purchase_url(self) -> str:
        """获取购买链接"""
        return "https://pay.ldxp.cn/shop/Q23SQVYU"


# ============== 激活码生成工具（管理员使用） ==============

def generate_license_key(prefix: str = "YR") -> str:
    """
    生成带校验和的激活码
    
    参数：
        prefix: 前缀，YR=年费版，LT=终身版
        
    返回：
        格式如 YRXX-XXXX-XXXX-XXCS 的激活码（CS为校验和）
    """
    import random
    
    # 有效字符集（移除容易混淆的字符）
    valid_chars = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    
    # 生成前14个字符（prefix 2字符 + 随机 12字符）
    prefix = prefix.upper()[:2]
    if prefix not in ('YR', 'LT'):
        prefix = 'YR'
    
    random_part = ''.join(random.choice(valid_chars) for _ in range(12))
    key_body = prefix + random_part  # 14 字符
    
    # 计算校验和（2字符）- 使用 SHA256
    checksum_input = key_body + get_LICENSE_SALT()
    checksum_hash = hashlib.sha256(checksum_input.encode()).hexdigest()[:2].upper()
    checksum = ''.join(valid_chars[int(c, 16) % len(valid_chars)] for c in checksum_hash)
    
    # 组合完整激活码
    full_key = key_body + checksum  # 16 字符
    
    # 格式化为 XXXX-XXXX-XXXX-XXXX
    formatted = '-'.join(full_key[i:i+4] for i in range(0, 16, 4))
    
    return formatted


def generate_batch_licenses(count: int, prefix: str = "YR") -> list:
    """批量生成激活码"""
    return [generate_license_key(prefix) for _ in range(count)]


def verify_license_key_format(license_key: str) -> bool:
    """验证激活码格式和校验和是否正确"""
    try:
        clean_key = license_key.replace('-', '').upper()
        if len(clean_key) != 16:
            return False
        
        key_body = clean_key[:14]
        key_checksum = clean_key[14:16]
        
        # 计算预期校验和 - 使用 SHA256
        valid_chars = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
        checksum_input = key_body + get_LICENSE_SALT()
        checksum_hash = hashlib.sha256(checksum_input.encode()).hexdigest()[:2].upper()
        expected_checksum = ''.join(valid_chars[int(c, 16) % len(valid_chars)] for c in checksum_hash)
        
        return key_checksum == expected_checksum
    except:
        return False


# ============== 测试 ==============

if __name__ == "__main__":
    print("=" * 50)
    print("许可证管理模块测试")
    print("=" * 50)
    
    # 显示设备ID
    print(f"\n设备ID: {get_device_id()}")
    
    # 初始化许可证管理器
    manager = LicenseManager()
    
    # 显示状态
    status = manager.get_status()
    print(f"\n当前状态:")
    for key, value in status.items():
        print(f"  {key}: {value}")
    
    # 检查是否可用
    can_use, message = manager.can_use_app()
    print(f"\n是否可用: {can_use}")
    print(f"消息: {message}")
    
    # 生成示例激活码
    print(f"\n示例年费激活码: {generate_license_key('YR')}")
    print(f"示例终身激活码: {generate_license_key('LT')}")
