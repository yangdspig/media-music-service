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
import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from . import fnos as fnos_svc
from .config import _DEFAULT_CONFIG_PATH, FnosMusicConfig, SourceConfig, settings

MASK = "••••••••"

# 热应用字段：改完立即生效（各模块现读 settings）
_HOT_FIELDS = {"api_key", "default_sources", "sources", "cleanup", "auth_refresh",
               "max_size_mb", "archive_comment", "fnos_music"}
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
    interval_s: int | None = None
    max_size_gb: float | None = None
    keep_hours: float | None = None


class AuthRefreshUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    interval_s: int | None = None


class FnosMusicUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str | None = None
    username: str | None = None
    password: str | None = None
    verify_tls: bool | None = None
    scan_wait_s: int | None = None
    path_map: dict[str, str] | None = None


class MCPUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transport: str | None = None
    host: str | None = None
    port: int | None = None
    service_url: str | None = None


class ConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str | None = None
    port: int | None = None
    download_root: str | None = None
    db_path: str | None = None
    num_threads: int | None = None
    api_key: str | None = None
    library_root: str | None = None
    extra_library_roots: dict[str, str] | None = None
    max_size_mb: float | None = None
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
    d = settings.model_dump()
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


# ---- PUT：校验 → 回写 yaml → 热应用 ----

def _config_path() -> Path:
    """与 config.py 的加载路径解析保持一致（MUSIC_SERVICE_CONFIG 优先）。"""
    return Path(os.environ.get("MUSIC_SERVICE_CONFIG", str(_DEFAULT_CONFIG_PATH)))


def _merge(target: dict, key: str, value: Any) -> None:
    """逐键合并：两边都是 mapping 时递归（保注释），否则整体替换。"""
    old = target.get(key)
    if isinstance(value, dict) and isinstance(old, (dict, CommentedMap)):
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
            server = data.get("server")
            if not isinstance(server, (dict, CommentedMap)):
                server = CommentedMap()
                data["server"] = server
            target = server
        _merge(target, key, value)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        y.dump(data, f)
    os.replace(tmp, path)


def _validation_400(e: ValidationError) -> HTTPException:
    return HTTPException(status_code=400, detail=json.loads(e.json()))


def apply_update(body: dict[str, Any]) -> dict:
    """部分更新入口：掩码跳过 → 回写 yaml → 热应用；响应逐字段标注生效方式。"""
    try:
        upd = ConfigUpdate.model_validate(body)
    except ValidationError as e:
        raise _validation_400(e)

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
        _write_back(yaml_updates)  # 写失败抛 500，此时运行态尚未改
    for action in apply_actions:
        action()
    return {"ok": True, "fields": fields, "config": get_masked_config()}


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
    yaml_updates["fnos_music"] = sub
    for k in sub:
        fields[f"fnos_music.{k}"] = "hot"

    def _apply() -> None:
        settings.fnos_music = new_cfg
        fnos_svc.reset_client()

    apply_actions.append(_apply)
