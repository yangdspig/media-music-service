"""Web 控制台配置接口：GET 脱敏快照 + PUT 部分更新。

设计要点：
- 敏感字段（sources.* 的 cookies、fnos_music.password、api_key）GET 时脱敏为 MASK，
  PUT 时收到 MASK 视为"未修改"跳过（不写 yaml、不动运行态）；
- 回写 config.yaml 用 ruamel.yaml round-trip 保注释，临时文件 + os.replace 原子写；
  路径解析与 config.py 一致（MUSIC_SERVICE_CONFIG 环境变量优先），host/port 写回 server: 段；
- 热应用 = 直接改全局 settings 可变字段（各模块每次调用现读，无需通知）；
  fnos_music 热应用后调 fnos.reset_client() 丢弃惰性单例；
- 需重启字段（路径/线程/监听/mcp）只回写 yaml，不改运行态，响应逐字段标注 hot/restart/unchanged。
"""
from __future__ import annotations

import json
import errno
import io
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from . import fnos as fnos_svc
from .config import _DEFAULT_CONFIG_PATH, FnosMusicConfig, Settings, SourceConfig, settings

MASK = "••••••••"

# 热应用字段：改完立即生效（各模块现读 settings）
_HOT_FIELDS = {"api_key", "default_sources", "sources", "cleanup", "auth_refresh",
               "max_size_mb", "archive_comment", "fnos_music", "download_timeout_s"}
_LOCK = threading.RLock()
_ENV_FIELDS = {"api_key": "MUSIC_SERVICE_API_KEY", "download_root": "MUSIC_SERVICE_DOWNLOAD_ROOT",
               "library_root": "MUSIC_SERVICE_LIBRARY_ROOT"}
# 需重启字段：只回写 yaml，运行态不变
_RESTART_FIELDS = {"download_root", "db_path", "library_root", "extra_library_roots",
                   "num_threads", "host", "port", "mcp"}
# config.yaml 中 host/port 归属 server: 段（config.py 加载时合并到顶层）
_SERVER_KEYS = {"host", "port"}
# sources.* 下的敏感字段
_SENSITIVE_SOURCE_KEYS = ("search_cookies", "download_cookies", "parse_cookies", "quark_cookies")


# ---- 更新模型（全部可选，extra=forbid 拦笔误；exclude_unset 区分"未传"与"传了 null"） ----

class SourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    search_cookies: str | dict | None = None
    download_cookies: str | dict | None = None
    parse_cookies: str | dict | None = None
    quark_cookies: str | dict | None = None
    extra: dict[str, Any] | None = None


class CleanupUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    after_archive: bool | None = None
    periodic: bool | None = None
    interval_s: int | None = Field(default=None, ge=1)
    max_size_gb: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    keep_hours: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class AuthRefreshUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    interval_s: int | None = Field(default=None, ge=1)


class FnosMusicUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str | None = None
    username: str | None = None
    password: str | None = None
    verify_tls: bool | None = None
    scan_wait_s: int | None = Field(default=None, ge=0)
    path_map: dict[str, str] | None = None


class MCPUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transport: str | None = None
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    service_url: str | None = None


class ConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    download_root: str | None = None
    db_path: str | None = None
    num_threads: int | None = Field(default=None, ge=1, le=128)
    download_timeout_s: int | None = Field(default=None, ge=1)
    api_key: str | None = None
    library_root: str | None = None
    extra_library_roots: dict[str, str] | None = None
    max_size_mb: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    archive_comment: str | None = None
    default_sources: list[str] | None = None
    sources: dict[str, SourceUpdate] | None = None
    mcp: MCPUpdate | None = None
    cleanup: CleanupUpdate | None = None
    auth_refresh: AuthRefreshUpdate | None = None
    fnos_music: FnosMusicUpdate | None = None


# ---- GET：脱敏快照 ----

def get_masked_config() -> dict:
    """当前 Settings 的 dict，敏感字段非空时替换为 MASK（空值原样返回）。"""
    with _LOCK:
        return _mask(settings.model_dump())


def _mask(d: dict) -> dict:
    if d.get("api_key"):
        d["api_key"] = MASK
    fm = d.get("fnos_music")
    if fm and fm.get("password"):
        fm["password"] = MASK
    for src in (d.get("sources") or {}).values():
        for key in _SENSITIVE_SOURCE_KEYS:
            if src.get(key):
                src[key] = MASK
    return d


def _saved_settings() -> Settings:
    y = YAML(typ="safe")
    path = _config_path()
    data = y.load(path.read_text(encoding="utf-8")) if path.exists() else {}
    data = dict(data or {})
    server = data.pop("server", None) or {}
    return Settings.model_validate({**server, **data})


def get_editor() -> dict:
    """分别返回已保存及运行配置，路径比较沿用加载规则，不创建目录。"""
    with _LOCK:
        saved = _saved_settings().model_dump()
        normalized = dict(saved)
        root = _config_path().resolve().parent
        for key in ("download_root", "db_path", "library_root"):
            if normalized[key] and not os.path.isabs(normalized[key]):
                normalized[key] = str(root / normalized[key])
        normalized["extra_library_roots"] = {
            k: v if os.path.isabs(v) else str(root / v)
            for k, v in normalized["extra_library_roots"].items()
        }
        runtime = settings.model_dump()
        overrides = [key for key, env in _ENV_FIELDS.items() if os.environ.get(env)]
        return {"config": _mask(saved), "runtime": _mask(runtime),
                "modes": {**dict.fromkeys(_HOT_FIELDS, "hot"), **dict.fromkeys(_RESTART_FIELDS, "restart")},
                "pending_restart": sorted(k for k in _RESTART_FIELDS
                                          if k not in overrides and normalized[k] != runtime[k]),
                "environment_overrides": overrides}


# ---- PUT：校验 → 回写 yaml → 热应用 ----

def _config_path() -> Path:
    """与 config.py 的加载路径解析保持一致（MUSIC_SERVICE_CONFIG 优先）。"""
    return Path(os.environ.get("MUSIC_SERVICE_CONFIG", str(_DEFAULT_CONFIG_PATH)))


def _merge(target: dict, key: str, value: Any) -> None:
    """逐键合并：两边都是 mapping 时递归（保注释），否则整体替换。"""
    old = target.get(key)
    replace_keys = {"path_map", "extra_library_roots", "extra", *_SENSITIVE_SOURCE_KEYS}
    if key not in replace_keys and isinstance(value, dict) and isinstance(old, (dict, CommentedMap)):
        for k, v in value.items():
            _merge(old, k, v)
    else:
        target[key] = value


def _write_back(updates: dict[str, Any]) -> None:
    """把变更回写 config.yaml：round-trip 保注释，临时文件 + os.replace 原子写。"""
    path = _config_path()
    y = YAML()
    y.preserve_quotes = True
    data = y.load(path.read_text(encoding="utf-8")) if path.exists() else None
    if data is None:
        data = CommentedMap()
    for key, value in updates.items():
        target = data
        if key in _SERVER_KEYS:
            if key in data:
                # 兼容同时存在的旧版顶层别名，避免其覆盖新 server 值。
                data[key] = value
            server = data.get("server")
            if not isinstance(server, (dict, CommentedMap)):
                server = CommentedMap()
                data["server"] = server
            target = server
        _merge(target, key, value)
    serialized = io.StringIO()
    y.dump(data, serialized)
    content = serialized.getvalue()
    path.parent.mkdir(parents=True, exist_ok=True)
    old_content = path.read_bytes() if path.exists() else None
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o777)
        try:
            os.replace(tmp, path)
        except OSError as e:
            if e.errno != errno.EBUSY:
                raise
            # Docker 单文件 bind mount 无法替换 inode；同一把锁串行保存并尽力回滚。
            try:
                with path.open("wb") as f:
                    f.write(content.encode("utf-8"))
                    f.flush()
                    os.fsync(f.fileno())
            except OSError:
                if old_content is not None:
                    with path.open("wb") as f:
                        f.write(old_content)
                        f.flush()
                        os.fsync(f.fileno())
                raise
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _validation_400(e: ValidationError) -> HTTPException:
    return HTTPException(status_code=400, detail=json.loads(e.json(include_input=False, include_context=False)))


def apply_update(body: dict[str, Any]) -> dict:
    with _LOCK:
        return _apply_update(body)


def save_login_cookies(source_name: str, cookies: dict, is_current: Callable[[], bool]) -> bool:
    """等待配置锁后再次检查取消/过期，避免排队的扫码写入在关窗后执行。"""
    with _LOCK:
        if not is_current():
            return False
        _apply_update({"sources": {source_name: {
            key: cookies for key in ("search_cookies", "download_cookies", "parse_cookies")
        }}})
        return True


def _apply_update(body: dict[str, Any]) -> dict:
    """部分更新入口：掩码跳过 → 回写 yaml → 热应用；响应逐字段标注生效方式。"""
    try:
        upd = ConfigUpdate.model_validate(body)
    except ValidationError as e:
        raise _validation_400(e)

    for key, env in _ENV_FIELDS.items():
        if key in body and os.environ.get(env) and body[key] != MASK:
            raise HTTPException(status_code=400, detail=f"{key} 由环境变量 {env} 覆盖，请在部署配置中修改")

    yaml_updates: dict[str, Any] = {}
    fields: dict[str, str] = {}
    apply_actions: list = []  # 热应用动作，yaml 写成功后才执行

    for name in sorted(upd.model_fields_set):
        value = getattr(upd, name)
        if name == "sources":
            _stage_sources(value, yaml_updates, fields, apply_actions)
        elif name == "fnos_music":
            _stage_fnos(value, yaml_updates, fields, apply_actions)
        elif isinstance(value, BaseModel):  # cleanup / auth_refresh / mcp
            sub = value.model_dump(exclude_unset=True)
            yaml_updates[name] = sub
            if name in _HOT_FIELDS:
                fields[name] = "hot"
                apply_actions.append(_make_submodel_apply(name, sub))
            else:
                fields[name] = "restart"
        else:  # 标量 / list / dict 顶层字段
            if name == "api_key" and value == MASK:
                fields[name] = "unchanged"
                continue
            yaml_updates[name] = value
            if name in _HOT_FIELDS:
                fields[name] = "hot"
                apply_actions.append(_make_attr_apply(name, value))
            else:
                fields[name] = "restart"

    if yaml_updates:
        candidate = _saved_settings().model_dump()
        for key, value in yaml_updates.items():
            _merge(candidate, key, value)
        try:
            Settings.model_validate(candidate)
        except ValidationError as e:
            raise _validation_400(e)
        if not candidate["host"].strip() or not candidate["download_root"].strip() or not candidate["db_path"].strip():
            raise HTTPException(status_code=400, detail="监听地址、下载目录和数据库路径不能为空")
        if candidate["mcp"]["transport"] not in {"stdio", "http", "sse", "streamable-http"}:
            raise HTTPException(status_code=400, detail="不支持的 MCP transport")
        try:
            _write_back(yaml_updates)
        except OSError:
            raise HTTPException(status_code=500, detail="配置写入失败，请检查 config.yaml 及其目录的写入权限")
    for action in apply_actions:
        action()
    if "cleanup" in yaml_updates:
        from .cleanup import start_periodic_sweep
        start_periodic_sweep()
    if "auth_refresh" in yaml_updates:
        from .qqauth import start_keepalive
        start_keepalive()
    return {"ok": True, "fields": fields, "config": get_masked_config(), "editor": get_editor()}


def _make_attr_apply(name: str, value: Any):
    def _apply() -> None:
        setattr(settings, name, value)
    return _apply


def _make_submodel_apply(name: str, sub: dict[str, Any]):
    def _apply() -> None:
        target = getattr(settings, name)
        for k, v in sub.items():
            setattr(target, k, v)
    return _apply


def _stage_sources(value: dict[str, SourceUpdate], yaml_updates: dict, fields: dict,
                   apply_actions: list) -> None:
    """sources 段：逐源逐字段合并；cookies 收到 MASK 跳过。"""
    if value is None:
        raise HTTPException(status_code=400, detail="sources 需为对象；清除凭证请将对应 cookie 设为 null")
    merged: dict[str, Any] = {}
    staged: list[tuple[str, dict]] = []
    for src_name, src_upd in value.items():
        clean: dict[str, Any] = {}
        for k, v in src_upd.model_dump(exclude_unset=True).items():
            dotted = f"sources.{src_name}.{k}"
            if k in _SENSITIVE_SOURCE_KEYS and v == MASK:
                fields[dotted] = "unchanged"
                continue
            clean[k] = v
            fields[dotted] = "hot"
        if clean:
            merged[src_name] = clean
            staged.append((src_name, clean))
    if merged:
        yaml_updates["sources"] = merged

    def _apply() -> None:
        for src_name, clean in staged:
            cfg = settings.sources.get(src_name)
            if cfg is None:
                cfg = SourceConfig()
                settings.sources[src_name] = cfg
            for k, v in clean.items():
                setattr(cfg, k, v)
            if src_name == "QQMusicClient" and ({"search_cookies", "download_cookies"} & clean.keys()):
                from . import qqauth
                try:
                    qqauth.reset_seed()
                except OSError:
                    # 新配置已生效；旧状态指纹失配，不会覆盖新凭证。
                    pass

    if staged:
        apply_actions.append(_apply)


def _stage_fnos(value: FnosMusicUpdate | None, yaml_updates: dict, fields: dict,
                apply_actions: list) -> None:
    """fnos_music 段：null 整体清除；部分更新与现有配置合并后整体校验；password MASK 跳过。"""
    if value is None:
        yaml_updates["fnos_music"] = None
        fields["fnos_music"] = "hot"

        def _clear() -> None:
            settings.fnos_music = None
            fnos_svc.reset_client()

        apply_actions.append(_clear)
        return
    sub = value.model_dump(exclude_unset=True)
    if sub.get("password") == MASK:
        sub.pop("password")
        fields["fnos_music.password"] = "unchanged"
    if not sub:
        return
    base = settings.fnos_music.model_dump() if settings.fnos_music else {}
    try:
        new_cfg = FnosMusicConfig(**{**base, **sub})
    except ValidationError as e:
        raise _validation_400(e)
    # 新建配置以及原配置由环境/运行态提供时，也要确保保存文件可完整加载。
    yaml_updates["fnos_music"] = new_cfg.model_dump()
    for k in sub:
        fields[f"fnos_music.{k}"] = "hot"

    def _apply() -> None:
        settings.fnos_music = new_cfg
        fnos_svc.reset_client()

    apply_actions.append(_apply)
