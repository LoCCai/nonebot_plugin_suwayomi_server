# 首次部署与配置教程

按顺序完成下面四步，就能在 QQ 中搜索、查看章节和阅读漫画。已有 Suwayomi-Server 的用户可以从[第二步](#第二步安装漫画源扩展)开始，已有漫画源则直接进入[第三步](#第三步安装并配置-nonebot2-插件)。

## 先分清两个管理页面

| 页面 | 默认地址 | 在这里做什么 |
| --- | --- | --- |
| Suwayomi 后端 WebUI | `http://127.0.0.1:4567` | 添加扩展库、安装漫画源、设置源账号、测试搜索与阅读 |
| NoneBot2 插件管理面板 | `http://机器人地址:机器人端口/suwayomi/` | 设置插件连接、订阅、推送和卡片渲染；需单独开启 |

**漫画源要在第一个页面安装。** 插件管理面板不会替你安装漫画源，两个页面的密码或令牌也不是同一项。

## 第一步：部署 Suwayomi-Server

需要 Docker Compose，以及一个已接入 OneBot v11 的 NoneBot2 机器人。

### 1. 准备配置

在准备运行漫画后端的机器上执行：

```bash
git clone https://github.com/LoCCai/nonebot_plugin_suwayomi_server.git
cd nonebot_plugin_suwayomi_server
cp deploy/.env.example deploy/.env
chmod 600 deploy/.env
```

打开 `deploy/.env`，将用户名和密码改为自己的值：

```dotenv
SUWAYOMI_AUTH_USERNAME=suwayomi
SUWAYOMI_AUTH_PASSWORD=请替换为自己的长随机密码
SUWAYOMI_PORT=4567
```

这里配置的是 **Suwayomi 后端**的登录账号，第三步给插件填写相同账号即可。不要保留示例密码。

### 2. 启动后端

在仓库根目录执行：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d
docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps
```

仓库的 [Compose 文件](../deploy/docker-compose.yml)固定使用 Suwayomi `v2.4.2366`，书库、扩展等数据保存在命名卷中；默认只将端口发布到宿主机的 `127.0.0.1:4567`。

如果已有统一的 Compose 项目，可以把这个服务并入原项目、复用原有网络和数据卷，不必再开第二套漫画后端。已有服务正常运行时也无需重复部署。

### 3. 打开后端 WebUI

- 在部署机器本机使用浏览器：打开 `http://127.0.0.1:4567`。
- 在自己的电脑管理远程服务器：先建立 SSH 隧道，再打开本机的同一地址。例如，将下面的 SSH 用户和服务器地址替换为实际值：

```bash
ssh -N -L 4567:127.0.0.1:4567 SSH用户@服务器地址
```

SSH 不使用默认端口时，再加 `-p 实际SSH端口`。隧道保持运行，浏览器登录时填写 `deploy/.env` 中的后端用户名和密码。

能打开 WebUI 只说明服务已启动，**还需要安装漫画源扩展才能搜索作品**。

## 第二步：安装漫画源扩展

Suwayomi 本身不提供漫画源。这里有三个不同的东西：**扩展库是可安装扩展的目录，扩展提供一个或多个漫画源，漫画源才用于搜索作品。**

### 1. 添加扩展库

1. 进入 Suwayomi 后端 WebUI。
2. 打开「设置」（齿轮图标）→「浏览」→「扩展库」（Extension repositories）。不同 WebUI 版本的名称或层级可能稍有不同。
3. 添加自己选择的扩展库地址并保存。例如，使用 [Keiyoushi 入门说明](https://keiyoushi.github.io/docs/guides/getting-started)提供的当前索引地址：

   ```text
   https://raw.githubusercontent.com/keiyoushi/extensions/repo/index.pb
   ```

   仓库地址发生变更时，以维护者的入门说明为准。
4. 返回「浏览」（Browse）→「扩展」（Extensions），刷新可用扩展列表。

扩展库地址应使用仓库维护者提供、且当前 Suwayomi 版本支持的地址，不要直接填教程网页的地址。成功添加扩展库后，还没有安装其中的漫画源。

旧教程中的 `index.min.json` 可能只显示 `Outdated App`；此时先改用维护者当前提供的索引，再核对后端兼容版本。

### 2. 安装需要的扩展

1. 在「扩展」中按语言或名称找到自己需要的扩展，点击「安装」。建议先选一个来源验证，之后再按需增加。
2. 如果页面要求确认信任或签名，核对扩展来源后按页面提示处理。
3. 等待状态变为「已安装」。随后进入「浏览」→「源」（Sources），确认能看到该扩展提供的漫画源；必要时调整语言筛选。
4. 打开漫画源，完成它要求的账号登录或其他源设置。
5. **先在 WebUI 中搜索一部作品，打开章节确认能看到图片。** 这一步正常后，再配置机器人。

扩展、网站和源名称会变化，以当前列表为准。安装扩展不等于网站一定可用；源能列出但搜索或图片失败时，先在后端排查该源。

### 3. 没有看到漫画源时

| 现象 | 先检查什么 |
| --- | --- |
| 扩展列表为空 | 扩展库是否保存成功、后端能否访问仓库、语言筛选是否隐藏结果 |
| 只看到 `Outdated App` 等升级提示 | 仓库索引与当前后端版本的兼容性；不要把提示条目当成正常漫画源安装 |
| 扩展已安装，源列表仍为空 | 信任确认是否完成、源语言筛选，以及扩展自身是否加载成功 |
| WebUI 中也无法搜索或阅读 | 源网站状态、源账号、后端网络或源要求的验证；插件无法绕过后端的失败 |

仓库示例固定了后端版本；如扩展库已经要求更新的后端，请先对照 [Suwayomi 官方文档](https://github.com/Suwayomi/Suwayomi-Server)核对兼容版本，保留现有数据卷，再安排更新，不要通过清空书库解决兼容问题。

## 第三步：安装并配置 NoneBot2 插件

### 1. 安装插件并加载

回到 **NoneBot2 项目使用的 Python 环境**中安装：

```bash
python -m pip install "git+https://github.com/LoCCai/nonebot_plugin_suwayomi_server.git"
```

按原项目的插件加载方式加入 `nonebot_plugin_suwayomi_server`。例如，在已有启动文件的 `nonebot.init(...)` 之后添加：

```python
nonebot.load_plugin("nonebot_plugin_suwayomi_server")
```

项目已经通过 `pyproject.toml` 或插件目录加载时，沿用原方式即可，不需要重复注册。插件面板需要 NoneBot 的 FastAPI 驱动；QQ 命令使用 OneBot v11 适配器。

### 2. 选择机器人能访问的后端地址

| 部署方式 | 插件填写的地址 |
| --- | --- |
| Bot 直接运行在后端宿主机，使用仓库默认端口 | `http://127.0.0.1:4567` |
| Bot 与后端都在 Docker，且接入同一网络 | `http://suwayomi-server:4567`（沿用实际服务名） |
| 后端在另一台机器，已提供 Bot 可访问的入口 | 填实际内网或 HTTPS 地址，并匹配它的认证配置 |

**Bot 容器里的 `localhost` 指 Bot 容器自己。** 浏览器能打开宿主机的 WebUI，不代表 Bot 容器能用同一个地址访问。仓库默认只发布宿主环回端口；Bot 在容器内时，优先让两个服务使用同一 Docker 网络，不要直接照抄宿主机地址。

填写基础地址即可，不要附加 `/api/graphql`。

### 3. 填写 NoneBot 环境配置

编辑机器人当前实际使用的 `.env` / `.env.prod`。以下以 Bot 直接运行在后端宿主机为例；容器部署请按上表修改地址：

```dotenv
SUWAYOMI_SERVER_URL=http://127.0.0.1:4567
SUWAYOMI_AUTH_MODE=basic
SUWAYOMI_USERNAME=suwayomi
SUWAYOMI_PASSWORD=请替换为第一步设置的后端密码
SUWAYOMI_CHECK_INTERVAL=60
```

使用仓库 Compose 时，后端是 `basic_auth`，**插件应填 `basic`**。已有后端按下表对应：

| 后端认证模式 | 插件 `SUWAYOMI_AUTH_MODE` | 另外需要填写 |
| --- | --- | --- |
| `none` | `none` | 无 |
| `basic_auth` | `basic` | 后端用户名和密码 |
| `ui_login` | `jwt` | 后端用户名和密码 |

完整可选配置见 [README 配置表](../README.md#️-配置)。环境文件中有密码时应限制读取权限，不要提交到 Git。

配置后沿原项目方式加载或重启 Bot，不需要重建 QQ 网关。插件业务配置的优先级为 **默认值 → NoneBot 环境配置 → 插件数据目录的 `config.json`**；如果曾在插件面板保存过配置，修改环境变量不一定会覆盖面板值。

### 4. 可选：开启插件管理面板

在相同的环境文件中单独设置一个随机令牌：

```dotenv
SUWAYOMI_DASHBOARD_TOKEN=请替换为自己的长随机令牌
```

加载后打开 `http://机器人地址:机器人端口/suwayomi/`，在页面中输入该令牌。这里使用的是 **Bot 的监听端口，不是漫画后端的 4567**。令牌留空时不注册面板；页面静态外壳可读取，管理 API 仍需 Bearer 认证。令牌不要放入 URL。

这个面板可以管理插件订阅、推送和运行时配置；要安装扩展或调整漫画源账号，仍然回到第二步的 Suwayomi WebUI。

## 第四步：验证

先在机器人所在群或私聊中逐条发送：

```text
/漫画 源
/漫画 搜索 作品名称
```

第一条应列出已安装源，第二条应返回搜索结果。把 `作品名称` 换成第二步中已成功搜索的作品。

需要验证订阅时，再按实际结果操作：

```text
/漫画 订阅 1
/漫画 我的订阅
/漫画 章节 作品名称
```

`1` 是当前搜索结果编号，不是固定漫画 ID；选择自己需要的结果再订阅。群聊订阅属于整个群，私聊订阅属于当前用户。

阅读时使用上一步实际列出的章节号；例如确实有第 1 话时：

```text
/漫画 阅读 作品名称 1
```

需要自动发送新章节内容时使用 `/漫画 推送 开`，可用 `/漫画 推送 状态` 核对。未开启时仍有订阅更新通知；开启后才额外推送章节图片或文件。`/漫画 更新` 是超级管理员使用的全局操作，会检查所有会话的订阅，不必用于普通连接测试。

`/漫画助手`、`/manga` 是 `/漫画` 的别名。完整命令见 [README](../README.md#-命令)。

## 可选：图片卡片

默认使用纯文本。需要搜索结果、章节列表等卡片时，在 Bot 的 Python 环境安装可选依赖：

```bash
python -m pip install "nonebot_plugin_suwayomi_server[cards] @ git+https://github.com/LoCCai/nonebot_plugin_suwayomi_server.git"
playwright install chromium
```

然后配置：

```dotenv
SUWAYOMI_RESULT_CARDS_ENABLED=true
SUWAYOMI_T2I_SOURCE=system
```

`system` 使用 NoneBot 的 htmlrender；宿主已有远程浏览器时沿用宿主配置，无需再运行另一套浏览器。也支持插件单独配置兼容的 T2I 服务，见 [README 卡片渲染](../README.md#️-卡片渲染)。卡片失败会回退纯文本，不影响文字查询。

## 常见问题

| 问题 | 排查方法 |
| --- | --- |
| “未找到已安装的漫画源” | 返回第二步，在后端安装扩展，并确认源已出现；这与 Bot 是否在线是两回事 |
| 后端连接失败 | 从 Bot 所在机器或容器检查地址、Docker 网络、端口与认证；不要只在自己电脑的浏览器测试 |
| 401 / 403 | 核对认证模式和后端账号；插件面板的令牌不能替代后端密码 |
| `.env` 改了不生效 | 确认编辑的是 Bot 当前环境文件，检查 `data/suwayomi/config.json` 的面板覆盖值，并按原方式加载 |
| 章节没有更新 | 使用 `/漫画 章节 作品名称 --刷新`；默认 6 小时按需刷新，`-1` 每次从源刷新，`0` 不按时间刷新；后端无章节时仍会拉取 |
| 图片能阅读，打包文件发不出 | 文件上传要求 Bot 与 QQ 网关能读取同一个绝对路径；图片按字节发送，没有这个共享路径要求 |
| 合并转发不可用 | `forward` 仅用于 OneBot v11 群聊；检查网关支持与群内发送权限 |

更换后端机器但保留原数据库时，核对地址和认证即可；换成全新实例后，漫画 ID、章节 ID 和订阅记录不能直接通用。迁移时保留后端数据和插件数据目录，不要只改地址就沿用旧订阅。

本教程参考 [FFFold/AstrBot 版部署说明](https://github.com/FFFold/astrbot_plugin_suwayomi_server/blob/main/docs/setup.md)，按本项目的 NoneBot2 配置、Compose 与命令实现重新整理。
