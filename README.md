<p align="center">
  <img src="logo.png" alt="Suwayomi 漫画助手" width="128" height="128">
  <br>
  <h1 align="center"><b>📖 Suwayomi 漫画助手</b></h1>
  <p align="center">
    基于 <a href="https://github.com/Suwayomi/Suwayomi-Server">Suwayomi-Server</a> 的 NoneBot2 漫画插件
    <br>
    搜索 · 阅读 · 下载 · 订阅更新 · Web 管理面板
  </p>
  <p align="center">
    <img src="https://img.shields.io/badge/NoneBot2-%3E%3D2.3-00BCD4?style=flat-square" alt="NoneBot2 >= 2.3">
    <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+">
    <img src="https://img.shields.io/badge/license-AGPL--3.0-orange?style=flat-square" alt="License AGPL-3.0">
    <img src="https://img.shields.io/badge/version-0.8.1-8A2BE2?style=flat-square" alt="Version 0.8.1">
  </p>
</p>

---

基于强大的 [Suwayomi-Server](https://github.com/Suwayomi/Suwayomi-Server) 后端，为聊天平台提供漫画搜索、在线阅读、批量下载和订阅更新推送服务。

> 🚀 **首次使用？** 先按 [Suwayomi-Server 部署教程](https://github.com/Suwayomi/Suwayomi-Server) 起好服务并安装漫画源扩展。

本插件移植自 AstrBot 插件
[FFFold/astrbot_plugin_suwayomi_server](https://github.com/FFFold/astrbot_plugin_suwayomi_server)，
功能对齐 v0.7.1（Agent 工具除外，见「与 AstrBot 版的差异」）。

---

## ✨ 特性

| | 功能 | 说明 |
|---|---|---|
| 🔍 | **多源搜索** | 跨多个已安装漫画源全局搜索，按相关度混排展示（Bangumi 别名扩展） |
| 📖 | **在线阅读** | 直接在聊天中阅读漫画章节，支持逐条发送或合并转发 |
| ⬇️ | **章节下载** | 下载章节页面并打包为 ZIP/PDF/CBZ 文件发送 |
| 🔔 | **订阅更新** | 订阅漫画后自动推送新章节通知，支持自定义检查间隔 |
| 🔄 | **章节缓存** | 章节数据自动缓存可配置时长（默认 6h），也支持强制刷新 |
| 🏷️ | **重复章节处理** | 智能识别重复章节号，通过 ID 精确选择 |
| 🔐 | **多种认证** | 支持无认证 / Basic / JWT 三种 Suwayomi 认证模式 |
| 🖼️ | **卡片渲染** | 搜索 / 订阅 / 章节列表 / 更新通知可渲染为带封面的精美卡片 |
| 🌐 | **Web 管理面板** | 连接状态总览、订阅管理、配置编辑 |

---

## 📦 安装

要求：Python 3.10+，一个可用的 NoneBot2 实例（OneBot v11 适配器，如 NapCat / go-cqhttp / LLOneBot）。

当前从 GitHub 安装（尚未发布到 PyPI），再按 NoneBot 项目的既有方式加载：

```bash
python -m pip install "git+https://github.com/LoCCai/nonebot_plugin_suwayomi_server.git"
```

```python
nonebot.load_plugin("nonebot_plugin_suwayomi_server")
```

可选：安装卡片渲染依赖（Playwright 截图）：

```bash
python -m pip install "nonebot_plugin_suwayomi_server[cards] @ git+https://github.com/LoCCai/nonebot_plugin_suwayomi_server.git"
playwright install chromium
```

---

## 后端 Docker Compose

仓库提供 [deploy/docker-compose.yml](deploy/docker-compose.yml)。复制
`deploy/.env.example` 为 `deploy/.env`，设置随机密码并限制文件权限，然后：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d
```

固定 Suwayomi v2.4.2366，数据保存在命名卷，只监听宿主 `127.0.0.1:4567`。
Bot 也在 Docker 中时，给服务接入 Bot 的既有网络，并用
`http://suwayomi-server:4567`，不能用 Bot 容器自己的 localhost。
插件认证模式为 `basic`（后端环境变量为 `AUTH_MODE=basic_auth`）。
通过本地连接或 SSH 隧道访问后端，按需配置你信任的扩展仓库、安装漫画源；
空书库和未安装源不会被视为连接失败。不要把未鉴权后端暴露到公网。

## ⚙️ 配置

在 NoneBot 的 `.env` / `.env.prod` 中配置（全部可选，WebUI 修改后以
`data/suwayomi/config.json` 为准）：

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `SUWAYOMI_SERVER_URL` | `http://localhost:4567` | Suwayomi-Server 地址。请勿随意更换实例，否则订阅 ID 会混乱 |
| `SUWAYOMI_AUTH_MODE` | `none` | 认证模式：`none` / `basic` / `jwt` |
| `SUWAYOMI_USERNAME` / `SUWAYOMI_PASSWORD` | 空 | 认证凭据 |
| `SUWAYOMI_RESULT_CARDS_ENABLED` | `false` | 指令结果卡片渲染（纯文本回退） |
| `SUWAYOMI_T2I_SOURCE` | `system` | 卡片渲染来源：`system`（本地 htmlrender）/ `custom`（自建 T2I 服务） |
| `SUWAYOMI_T2I_ENDPOINT` | 空 | 自建 T2I 服务端点（astrbot-t2i-service 兼容协议） |
| `SUWAYOMI_MAX_PAGES` | `30` | 单次阅读最多发送页数 |
| `SUWAYOMI_SEND_MODE` | `image` | 发送方式：`image` 逐条 / `forward` 合并转发（仅 OneBot v11 群聊） |
| `SUWAYOMI_IMAGE_FETCH_MODE` | `download` | 图片获取：`download` 下载后发 / `url` 直接引用 URL |
| `SUWAYOMI_DOWNLOAD_FORMAT` | `pdf` | 下载打包默认格式：`zip` / `pdf` / `cbz` |
| `SUWAYOMI_CHECK_INTERVAL` | `60` | 更新检查间隔（分钟） |
| `SUWAYOMI_CHAPTER_CACHE_HOURS` | `6` | 章节缓存时长（小时，`-1` 永久缓存，`0` 不缓存） |
| `SUWAYOMI_AUTO_PUSH_MODE` | `image` | 自动推送内容：`image` 图片 / `file` 打包文件 |
| `SUWAYOMI_DATA_DIR` | `data/suwayomi` | 数据目录（订阅 KV、运行时配置） |
| `SUWAYOMI_DASHBOARD_TOKEN` | 空 | Web 管理 API 的 Bearer 令牌；留空禁用面板 |

其余高级项（搜索排序、Bangumi 别名扩展、下载并发/重试等）可在 Web 面板「设置」页调整。

> **注意**：Web 面板保存的配置写入 `data/suwayomi/config.json`，会覆盖 `.env` 中的同名项。

---

## 📋 命令

| 命令 | 说明 |
|---|---|
| `/漫画 源` | 列出所有已安装的漫画源 |
| `/漫画 搜索 <关键词> [源名]` | 从多个源搜索漫画（多词标题用 `+` 连接，如 `香格里拉+再`） |
| `/漫画 订阅 <编号>` | 订阅搜索结果中的漫画 |
| `/漫画 批量订阅 <名称1>, <名称2>, ... [源名]` | 批量订阅多部漫画（逗号分隔） |
| `/漫画 取消订阅 <ID或名称>` | 取消订阅指定漫画 |
| `/漫画 我的订阅` | 查看当前会话的订阅列表 |
| `/漫画 更新` | 仅 SUPERUSER 手动触发全局更新 |
| `/漫画 章节 <漫画名或ID> [--刷新]` | 查看章节列表，`--刷新` 强制从源拉取最新数据 |
| `/漫画 阅读 <漫画名或ID> <章节号>` | 阅读指定章节（发送页面图片） |
| `/漫画 下载 <漫画名或ID> <章节号> [格式]` | 下载章节并打包发送（格式: zip/pdf/cbz） |
| `/漫画 推送 开/关/状态` | 控制自动推送（开记住偏好，新订阅自动继承；关清除偏好） |
| `/漫画 帮助` | 显示完整用法说明 |

重复编号章节可用 `ID:数字` 精确指定，如 `/漫画 阅读 一拳超人 ID:123`。

---

## 🔔 订阅推送说明

订阅按 **会话** 记账（群聊以群为单位，私聊以用户为单位）。`/漫画 更新` 或
后台检查发现新章节时：

1. 所有订阅会话收到更新通知（卡片或文本）；
2. 对开启「自动推送」的会话，进一步把新章节内容（图片或打包文件）直接发进会话。

推送通过 OneBot v11 的 `send_group_msg` / `send_private_msg` /
`upload_group_file` / `send_group_forward_msg` 完成；bot 需要在线，
且会话内有发言权限。失败会话不推进发送进度，下次重试；已确认发送的会话
不重复发送同批通知。进程在实际发送后、落盘前中断时仍可能重复，不能承诺恰好一次。

---

## 🌐 Web 管理面板

启动 bot 后访问：

```
http://<bot主机>:<端口>/suwayomi/
```

端口为 NoneBot FastAPI 驱动的监听端口。必须先设置 `SUWAYOMI_DASHBOARD_TOKEN`，
否则面板不注册。打开页面后填写令牌；令牌只存当前标签页的 sessionStorage，API
通过 `Authorization: Bearer ...` 校验。不要把令牌写进 URL。静态页面可公开读取，
管理数据与操作均需认证。建议仅通过本地连接、SSH 隧道或 HTTPS 管理。

面板提供：连接状态总览、订阅管理（删除订阅 / 切换推送）、配置编辑、
手动更新检查。

---

## 🖼️ 卡片渲染

- **system**（默认）：使用 [`nonebot-plugin-htmlrender`](https://github.com/kexue-z/nonebot-plugin-htmlrender)
  本地渲染 HTML 卡片，需要 Playwright 浏览器（`playwright install chromium`）。
  未安装时自动回退纯文本。
- **custom**：调用自建 T2I 服务（astrbot-t2i-service 兼容的
  `/text2img` + `/generate` 协议），适合无浏览器环境。

渲染失败或超时会自动回退纯文本，并进入冷却期避免反复等待。

---

## 🗃️ 数据目录

```
data/suwayomi/
├── kv.json        # 订阅、章节缓存时间戳、上次检查时间
├── config.json    # Web 面板保存的运行时配置，可能包含密码，0600
└── cache/         # 下载、打包与渲染临时文件，自动延迟清理
```

相对数据路径基于 Bot 的启动目录；`SUWAYOMI_TEMP_DIR` 为空时使用 `cache/`，
相对临时路径基于数据目录。图片以字节发送，QQ 网关无需读取 Bot 的本地图片路径；
文件上传仍要求网关与 Bot 共享数据目录并使用相同的绝对路径。

从 AstrBot 迁移时可导入 KV 结构，但必须将旧会话地址映射为
`OneBot V11|机器人QQ|group:群号` 或 `OneBot V11|机器人QQ|private:QQ号`；
不能直接沿用 AstrBot unified_msg_origin。损坏 JSON 会报错并保留原件，修复后再启动。

---

## 🆚 与 AstrBot 版的差异

- **适配平台**：AstrBot 版支持 8 个平台；本版针对 NoneBot2 生态，
  消息收发基于 **OneBot v11**（NapCat / go-cqhttp / LLOneBot）。其他适配器
  下文本类命令可用，图片/文件推送暂不支持。
- **Agent 工具**：AstrBot 的 LLM FunctionTool 注册在 NoneBot2 中无对应
  设施，AI 漫画工具（`suwayomi_search_manga` 等）未移植。提供完整的
  PluginMetadata 菜单、结构化命令与自然语言意图，支持七七现有 LLM
  发现功能后投递原生 `/漫画` 指令；无需另建一套漫画工具接口。
- **配置体系**：`_conf_schema.json` → `.env`（`SUWAYOMI_*`）+ Web 面板
  运行时配置（`config.json`）。
- **WebUI**：AstrBot WebUI 内嵌面板 → 独立 FastAPI 面板（`/suwayomi/`）。
- **菜单**：文字帮助、PicMenu、QWeb 功能目录和 LLM 发现共用 `menu.py`；
  沿用宿主的菜单主题和分类，管理指令明确标注权限与影响范围。
- **卡片渲染**：AstrBot 系统 T2I → `nonebot-plugin-htmlrender` 或自建 T2I。

---

## 🧪 开发与测试

```bash
python -m pip install -e ".[test]"
python -m pytest -q      # 包含真实 NoneBot 加载与发送/持久化边界回归
```

联机集成测试默认跳过。显式设置 `SUWAYOMI_LIVE_TESTS=1`、`SUWAYOMI_URL`，
并按需提供认证变量后运行 `tests/test_live_*.py`，它们会访问并可能修改测试书库。
开发说明见 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## License

[AGPL-3.0](LICENSE)
