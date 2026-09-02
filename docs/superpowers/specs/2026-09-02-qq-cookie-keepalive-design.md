# QQ 音乐 Cookie 保活（自动刷新）设计

日期：2026-09-02
状态：已确认

## 背景与问题

QQ 音乐源（QQMusicClient）依赖用户在 config.yaml 手动粘贴的登录 cookies。核心凭证 `qqmusic_key`（musickey）服务端有效期仅 3 天（`keyExpiresIn=259200`），过期后 VIP/无损解析失败，用户需重新粘贴。

已实测验证：QQ 音乐官方刷新接口 `music.login.LoginServer/Login`（POST `https://u.y.qq.com/cgi-bin/musicu.fcg`）可用旧凭证换新凭证，无需重新登录：

- 刷新参数：`openid / access_token / refresh_token / expired_in(int) / musicid(int) / musickey / refresh_key(可为空串) / loginMode=2`
- **参数必须是 int 的字段传 str 会返回 code=10006**（musicdl 的 `Credential.fromcookiesdict` 不转换 `expired_at` 类型，需自行修正）
- 请求需 ANDROID 平台 comm（ct=11, cv=14090008, tmeAppID=qqmusic, tmeLoginType, qq, authst, QIMEI/QIMEI36, OpenUDID/udid/OpenUDID2, aid, os_ver, phonetype, devicelevel, newdevicelevel, rom, uid, sid）
- 设备/QIMEI/session 均可用 musicdl 内置工具生成：`Device()` / `QQMusicClientUtils.obtainqimei` / `randomguid`，session 用 `music.getSession.session/GetSession` 获取（uid/sid，24h 内可复用）
- 响应下发新 `musickey`、`refresh_key`（浏览器 cookie 里没有，必须持久化）、`refresh_token`、`access_token`、`expired_at`、`keyExpiresIn`
- 空 `refresh_key` 可以刷新成功；后续刷新优先使用服务端下发的 `refresh_key`
- access_token 有效期约 60 天，它彻底失效后刷新会失败，需要用户重新粘贴 cookies

过期检测（已实测）：GET `https://c6.y.qq.com/rsc/fcgi-bin/fcg_get_profile_homepage.fcg`，`g_tk=hash33(musickey, 5381)`，code==0 即有效。

## 目标

用户粘贴一次 cookies 后，服务自动维持 QQ 音乐登录态不失效；凭证彻底失效（约 60 天后）时明确提示用户重新粘贴。

## 非目标

- 不实现扫码登录（后续如需再单独立项）
- 不为其他音乐源做保活（仅 QQ；其他源暂无刷新机制）
- 不改写 config.yaml 中的 cookies（避免破坏注释、与用户手动编辑冲突）

## 架构

### 新模块 `app/qqauth.py`

纯函数 + 状态读写，不依赖 FastAPI：

- `parse_credential(cookies: dict) -> QQCredential`：从 cookie dict 提取凭证字段，**修正 int 类型**（musicid、expired_at、musickey_createtime）
- `credential_to_cookies(cred, base_cookies) -> dict`：把刷新后的凭证映射回 cookie dict（更新 `qqmusic_key`/`qm_keyst`/`psrf_qqaccess_token`/`psrf_qqrefresh_token`/`psrf_access_token_expiresAt`/`psrf_musickey_createtime`）
- `check_expired(cred) -> bool | None`：WEB profile 接口检测；None 表示网络失败
- `refresh(cred) -> QQCredential`：ANDROID 协议栈刷新（Device→QIMEI→GetSession→Login），失败抛 `QQAuthRefreshError`（含服务端 code）
- 状态文件 `data/qq_auth_state.json`：
  - 内容：完整凭证字段（含 `refresh_key`）、`refreshed_at`、`key_expires_in`、`config_createtime`（播种时 config 里 `psrf_musickey_createtime` 的值）
  - 优先级：**状态文件 > config.yaml**；config 仅作种子
  - 种子重置：config 的 `psrf_musickey_createtime` ≠ 状态文件的 `config_createtime`（说明用户重新粘贴了 cookies）→ 丢弃状态，以 config 重新播种。不能用 refresh_token 做判定（刷新可能轮换它，而刷新不回写 config，会误判）
- 刷新后的 `musickey_createtime`：取响应的 `musickeyCreateTime`，缺省用当前时间

### 周期任务（`app/main.py` 启动钩子，参照 cleanup 定时器模式）

- 每 `auth_refresh.interval_s`（默认 **3600**，1 小时）执行一次：
  1. QQ 源未配置 cookies → 跳过
  2. 加载有效凭证（状态文件优先，否则 config 播种）
  3. 本地判断剩余有效期（`musickey_createtime + key_expires_in - now`），**≥ 24h 则不动作**
  4. 剩余 < 24h（或未知）→ 调 `refresh()`：
     - 成功 → 写状态文件 + 热更新内存中 QQMusicClient 实例的 cookies
     - 失败 code ∈ {1000, 104401, 104400}（凭证彻底失效）→ 标记 QQ 源不可用，note 提示"登录凭证已失效，需重新粘贴 cookies"
     - 其他失败（网络/限流/未知 code）→ 记日志，下个周期重试（1h 周期天然带重试）

### registry 集成（`app/registry.py`）

- `build_client()` 组装 QQ cookies 时：优先用 `qqauth.load_effective_cookies()` 的结果
- `/api/v1/sources` 的 QQ 源条目附加凭证状态：`note` 中体现"凭证有效至 X"或"已失效需重新粘贴"

### 手动触发

- `POST /api/v1/auth/qq/refresh`：立即执行一次刷新，返回刷新结果（成功/失败原因），便于测试与运维

### 配置（config.yaml 新增段）

```yaml
auth_refresh:
  enabled: true        # QQ cookie 自动保活开关
  interval_s: 3600     # 检查周期（秒），默认 1 小时
```

`Settings` 新增 `AuthRefreshConfig`（enabled/interval_s），挂在 `Settings.auth_refresh`。

## 数据流

```
用户粘贴 cookies → config.yaml（种子）
                        ↓ 启动/每周期
              qqauth.load_effective_cookies()
                        ↓
        状态文件较新？ → 用状态文件凭证
                        ↓
        registry.build_client(QQMusicClient, cookies=有效凭证)
                        ↓
        剩余有效期 < 24h → refresh() → 写状态文件 → 热更新 client
```

## 错误处理

| 场景 | 行为 |
|---|---|
| 网络失败/超时 | 记 warning 日志，下周期重试 |
| code=1000/104401/104400 | 标记源不可用 + note 提示重新粘贴 |
| code=其他（含 10006） | 记 error 日志（含 code），下周期重试；连续失败不升级 |
| 状态文件损坏 | 丢弃，以 config 重新播种 |
| 刷新成功但 check 不通过 | 记 error，保留旧凭证继续用 |

## 测试（tests/test_qqauth.py，全部 mock 网络）

- cookie 解析：int 类型修正（expired_at/musicid 为 str 的输入）
- 凭证 → cookies 映射的字段完整性
- 状态文件优先级与种子重置逻辑（refresh_token 变化 → 重置）
- 周期任务：剩余 ≥24h 不刷新；<24h 触发刷新并写状态；刷新失败按 code 分类处理
- registry：状态文件存在时 build_client 用状态文件 cookies
- REST：`POST /api/v1/auth/qq/refresh` 成功/失败响应结构

## 部署注意

- 容器内 `data/` 目录已持久化挂载（/vol1 部署：`/vol1/1000/media-music-service/data`），状态文件天然持久
- 不新增第三方依赖（QIMEI/加密均复用 musicdl 内置实现）
