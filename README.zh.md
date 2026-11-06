# acestream-suite

🇬🇧 [English](README.md) · 🇪🇸 [Español](README.es.md) · 🇫🇷 [Français](README.fr.md) · 🇮🇹 [Italiano](README.it.md) · 🇩🇪 [Deutsch](README.de.md) · 🇷🇺 [Русский](README.ru.md) · 🇨🇳 **中文** · 🇯🇵 [日本語](README.ja.md)

<p align="center"><img src="assets/logo-3d.png" alt="acestream-suite" width="560"></p>

**起源。** 本应用的想法与基础来自 **AceList** 项目（<https://github.com/fabiomb/AceList>）：一个本地
AceStream 链接目录。本套件是在该想法之上扩展出的自有组件，`LICENSE` 记录了再分发时这意味着什么。

一个面向 **AceStream** 引擎的自托管 Web 前端。它维护一份 `acestream://` 链接目录 —— 频道及其
Content ID、针对引擎的可用性检查、每个频道抓取的一帧画面、分类、语言与国家 —— 并在这个目录之上
增加了四样东西：

- **节目指南（EPG）层**：XMLTV 源、频道与指南之间的映射、保存的节目时间窗、位于 `/epg.xml` 的
  XMLTV 文档，以及携带该指南的 M3U 播放列表；
- **搜索页**，检索引擎自带的索引（`acesearch`），从而可以找到并导入索引已知、但目录中还没有的
  频道；
- **HLS 桥接**，把一个引擎流转换成一份清单，让家里多个播放器可以同时观看；
- **脚本**，把目录发布为播放列表，供那些需要 URL 而不是网页的播放器使用（VLC、Kodi、
  Dispatcharr/Jellyfin Live TV、智能电视）。

`acestream-suite` 是本产品。**AceStream** 是它赖以播放的引擎：该引擎是第三方软件，保留它自己的
名字。

> **没有身份验证。** 本应用面向私有网络。任何能访问该端口的人都可以浏览目录并启动流。不要把它
> 发布到互联网上。

## 快速开始（Docker）

要求：装有 Compose v2 的 Docker，以及一个容器可通过 HTTP 访问的 AceStream 引擎（本套件不负责启动
引擎）。

```bash
git clone https://github.com/yerbajo/acestream-suite.git
cd acestream-suite
cp .env.example .env
$EDITOR .env                      # ACESTREAM_SUITE_ENGINE_URL, ACESTREAM_SUITE_ALLOWED_HOSTS
mkdir -p data && sudo chown 1000:1000 data
docker compose up -d --build
curl -fsS http://localhost:8080/health
```

然后打开 **`http://<host>:8080`** —— `.env` 里的 `ACESTREAM_SUITE_PORT` 是宿主机端口（除非你改动，
否则是 `8080`；应用在容器内始终监听 8000）。HLS 桥接发布 **8084**。

两个跳过就会出问题的细节：

- **`data/` 必须存在，并且 uid 1000 可写。** 缺失的绑定源会被 Docker 创建成 root 所有的空目录，
  而应用以 `"1000:1000"` 运行，因此无法写入数据库。
- **部署取值来自 `.env`，而不是 compose 文件。** `docker-compose.yml` 对每个因部署而异的取值都做了
  插值（`${ACESTREAM_SUITE_ENGINE_URL:-…}` 等），所以 `cp .env.example .env` 再加一次编辑就够了，
  compose 文件本身永远不必改动。`.env` 中未写出的项会回退到 compose 里写好的安全默认值。引擎
  URL、其超时、搜索 URL 与检查并发数同时也是应用 **Ajustes** 页面里的*已保存设置*，而已保存的值
  优先于环境变量 —— 因此写错的 `.env` 可以在界面里修正，无需重新构建。

## 镜像在本仓库本地构建

`docker compose up -d --build` 会**构建**镜像；除了基础镜像 `python:3.12-slim` 和发行版软件包之外，
不会拉取任何东西。应用源码就在这里：`app/`（FastAPI 应用）、`migrations/`（Alembic）和
`pyproject.toml`，它们被复制进镜像并以可编辑模式安装，因为应用是相对源码树读取其模板、静态文件和
迁移的。

镜像中安装了 `ffmpeg`，因为截图和可用性检查是通过它读取直播流的。

更新：

```bash
git pull
docker compose build acestream-suite && docker compose up -d acestream-suite
```

指明服务名很重要：同一个 compose 文件里还有 `acestream-suite-hls` 桥接服务，裸的
`docker compose up -d` 会把它也一起重建。

## 配置

应用从环境变量读取每一个变量。主名称是 `ACESTREAM_SUITE_*`；旧的 `ACELIST_*` 名称仍会作为一个版本
的兼容回退被读取，因此尚未改名的部署可以继续工作（两者同时设置时新名称优先）。

| 变量 | 默认值 | 作用 |
|---|---|---|
| `ACESTREAM_SUITE_ENGINE_URL` | `http://127.0.0.1:6878` | 引擎 HTTP API 的基础 URL。同时也是已保存设置。 |
| `ACESTREAM_SUITE_ENGINE_TIMEOUT` | `15` | 在一次调用被判定失败之前等待引擎的秒数。 |
| `ACESTREAM_SUITE_SEARCH_URL` | 空 = 引擎 URL | `acesearch` 的基础 URL，即搜索页读取的索引（`/search.m3u`）。同时也是已保存设置。 |
| `ACESTREAM_SUITE_ALLOWED_HOSTS` | 空 | 逗号分隔的 `host` / `host:port` 值，会在 `Host` 头以及写操作的 `Origin` 中被接受。若不含你用来浏览的地址，每个请求都会是 `400`，每次写入都会是 `403`。 |
| `ACESTREAM_SUITE_PLAYBACK_MODE` | `vlc` | `vlc` 启动流并打开本地播放器（桌面行为）。`url` 启动流并把链接交给页面 —— 容器需要的模式，因为它没有播放器。 |
| `ACESTREAM_SUITE_STREAM_PARAM` | `id` | 在引擎上启动流的查询参数：Windows 3.1.x 版本用 `id`，Linux 3.2.x 版本用 `infohash`（它对 `id` 会返回 `failed to load content`）。 |
| `ACESTREAM_SUITE_CHECK_CONCURRENCY` | `2` | 同时进行的可用性检查数。每一次都是一次实时引擎会话，因此这也是引擎上的负载。 |
| `ACESTREAM_SUITE_SCREENSHOT_TIMEOUT` | `30` | 允许 ffmpeg 抓帧的秒数。 |
| `ACESTREAM_SUITE_FFMPEG_PATH` | 空 = `PATH` 中的 `ffmpeg` | 用于截图的二进制文件。 |
| `ACESTREAM_SUITE_VLC_PATH` | 空 = 常规目录，然后是 `PATH` | 仅在 `vlc` 播放模式下读取。 |
| `ACESTREAM_SUITE_DATA_DIR` | 镜像中为 `/data`，其他情况为操作系统的应用数据目录 | `acestream-suite.db` 与 `screenshots/` 所在的位置。 |
| `ACESTREAM_SUITE_PORT` | `8080` | 在宿主机上发布的端口。应用在容器内始终监听 `8000`。 |
| `ACESTREAM_SUITE_EPG_SOURCES` | `[]` | XMLTV 源，形式为 URL 的 JSON 列表。空是受支持的状态：没有指南目录也能工作。 |
| `ACESTREAM_SUITE_EPG_WINDOW_HOURS` | `72` | 指南在*当前*时刻之后保留多远；每次刷新运行时都会清理更早的节目。 |
| `ACESTREAM_SUITE_EPG_TIMEOUT_SECONDS` | `120` | 刷新的每源超时。一个始终不应答的源只会牺牲一个源，而不是整次运行。 |
| `ACESTREAM_SUITE_EPG_MAX_MB` | `64` | 大于此值的源会被拒绝，而不是塞满容器的内存。 |
| `ACESTREAM_SUITE_EPG_GRACE_SECONDS` | `300` | 刚结束的节目额外保留的秒数，供时钟落后的播放器使用。 |
| `ACESTREAM_SUITE_EPG_ACTOR` | 空 = 操作系统用户 | 被记录为某次手动频道↔指南决定作者的名称。 |

**`.env` 能设置其中哪些，由 compose 文件决定。** 它插值 `ENGINE_URL`、`ALLOWED_HOSTS`、
`PLAYBACK_MODE`、`STREAM_PARAM`、`CHECK_CONCURRENCY`、`PORT`、`EPG_SOURCES`、
`EPG_WINDOW_HOURS` 以及桥接的 `HLS_ALIASES` —— 正是 `.env.example` 附带的那些名字。其余变量应用
会读取，但 compose 不会传入：它们要么是 **Ajustes** 中的已保存设置（引擎超时、搜索 URL、检查
超时、最小 peers、截图超时、ffmpeg 路径、VLC 路径），要么是你自己在 compose 的 `environment:`
里添加的值（数据目录、指南的超时与大小上限、作者名）。

两点说明：

- 指南多久刷新一次不是配置项：它是 `scripts/acestream-suite-guide-refresh.cron` 中的计划（04:07
  和 16:07），容器里没有任何东西读取间隔值。要改频率就改那一行。
- 数据库文件是 `acestream-suite.db`，位于应用所指向的数据目录内。

## 数据与备份

整个应用状态就是一个目录，在 compose 文件中挂载到 `/data` 并绑定到 `./data`：

```
data/
  acestream-suite.db   SQLite: catalogue, check history, guide tables, settings
  screenshots/    one captured frame per channel
```

要备份，请在应用没有写入它的时候复制该目录：

```bash
docker compose stop acestream-suite
tar czf "acestream-suite-data-$(date +%F).tar.gz" data
docker compose start acestream-suite
```

从运行中的容器里取得的副本可能会撞上 SQLite 文件的写入中途。`acestream-suite-prune` 脚本（见下文）
在删除任何东西之前，正是取这样一份副本，外加整个目录的一份可重新导入的导出。

恢复：停止应用，替换 `data/`，再启动。入口脚本会在启动过程中应用数据库迁移，因此来自旧版本的备份
会被升级，而不是被拒绝。

## 源码的组织方式

| 路径 | 是什么 |
|---|---|
| `app/` | 本 Web 应用：FastAPI 路由、模板、静态文件、引擎客户端、指南与搜索。 |
| `migrations/` + `alembic.ini` | 数据库模式，由 `entrypoint.sh` 在容器启动时应用。 |
| `tests/` | 应用的测试套件（pytest，约 640 个测试，无网络）：在仓库根目录运行 `pytest -q`。 |
| `scripts/` | 可选的主机任务：播放列表生成器、比赛日导入器、清理工具、它们的 `.cron` 文件与 `crons-install.sh`。 |
| `assets/` | 镜像复制进应用静态目录的徽标与 favicon。 |

来源，以及这对你意味着什么，见 `LICENSE`。

## HLS 桥接

3.2.x 系列的引擎不提供 HLS 清单，而若干客户端（智能电视、Dispatcharr、Jellyfin Live TV）需要一份。
`acestream-suite-hls` —— 同一 compose 文件里的第二个服务，使用同一镜像 —— 在不重新编码的情况下
把引擎的 MPEG-TS 重新封装成 2 秒分段并提供：

```
http://<host>:8084/<infohash>/index.m3u8
```

每个频道一个 `ffmpeg`，最多四个会话，最后一次请求之后 45 秒关闭会话，分段窗口放在 2 GB 的 tmpfs
中，桥接放弃某个频道时释放对应的引擎会话。当 tmpfs 的可用空间低于 `HLS_MIN_FREE_MB` 时，它会关闭
占用字节最多的会话：tmpfs 被写满会一次性破坏所有会话，而不只是一个。`python3 hls-bridge.py
--self-test` 用假引擎和假 ffmpeg 离线检查这些决策。

桥接是针对一项实测到的引擎限制的变通方案，而不是偏好：引擎对每个内容只服务一个读取者，所以让多个
播放器直接指向它，会让它们争抢同一个频道。

## 家用脚本（可选）

运行应用不需要 `scripts/` 中的任何东西。它们是作者使用的发布层，依赖每个文件顶部的那些路径，默认
指向该部署自己的主机（应用为 `http://127.0.0.1:8083`，播放列表为 `/srv/www/varios`）。每个路径和
URL 都是一个参数，对某些因主机而变的项还是一个环境变量 —— 请先读你要安装的那个脚本的文件头。

| 脚本 | 写入内容 |
|---|---|
| `acestream-suite-playlist.py` | Web 目录中的五个 M3U 文件：`acestream-suite.m3u`（全部）、`deportes.m3u`、`espana.m3u`、`hls.m3u`（含一个 `Campeones` 文件夹），以及 `direct.m3u`（引擎原生的 MPEG-TS）。只包含应用已验证为存活的频道，每个显示名一条记录，除 `direct.m3u` 之外每个 URL 都经过 HLS 桥接。仅在内容变化时写入。 |
| `acestream-suite-guide-playlist.py` | `acestream-suite-guide.m3u`：同一份目录，头部带 `url-tvg`，并把指南频道 id 作为 `tvg-id`，只针对拥有该项的频道。 |
| `acestream-suite-prune.py` | 一次性清理：等到每个频道都检查过，备份到 `/backup/…/<stamp>/`（播放列表、可重新导入的目录导出、数据库副本、删除清单），然后通过应用自己的 API 删除最后一次检查不是存活的频道。它会把自身标记为已完成；`--undo-guard` 让它再次运行。 |
| `crons-install.sh` | 目标主机上 root 的 crontab，由 `scripts/*.cron` 生成。`--dry-run` 打印，`--diff` 与实时 crontab 比较，`--install` 备份后写入。它的 `JOBS` 数组还带有来自其他技术栈的任务 —— 安装前请先读它。 |

它们都接受 `--dry-run`；两个播放列表脚本还接受 `--self-test`，在无网络、无文件的情况下运行它们的
检查。播放列表任务需要一个对外提供该目录的 Web 服务器；`acestream-suite-guide-refresh.cron` 是指南
刷新，它在容器内运行它（`docker exec acestream-suite python3 -m app.cli epg-refresh`）。

## 文档

- 八种语言：页面顶部的语言切换器可跳转到本文档的英语、西班牙语、法语、意大利语、德语、俄语和日语版本。
- `LICENSE` —— 此处撰写的作品采用 MIT 许可，以及应用代码的来源。
- 内联文档：每个模块都带有 docstring，说明它决定什么以及为什么；`python -m app.cli --help` 和
  每个脚本的 `--help` 记录了它们自己的选项。

## 许可与第三方声明

此处撰写的作品 —— 部署文件、脚本、文档、徽标与应用代码 —— 采用 MIT 许可；见 `LICENSE`。同一文件
也记录了想法与基础的来源：**AceList**（<https://github.com/fabiomb/AceList>），它并未发布自己的
许可证，以及这对再分发意味着什么。它还说明了徽标中的 AceStream 标识来自何处。再分发由此构建的
镜像之前请先读它。该镜像还安装了第三方软件：`python:3.12-slim`、Debian 软件包（`ffmpeg`、
`curl`），以及 `pyproject.toml` 中的 Python 依赖，各自遵循其自己的许可证；`htmx`（BSD-2-Clause）
与 Lucide 图标笔画（ISC）以随源码内置的形式放在 `app/web/`。

## 此处是如何部署的

**本仓库是套件，不是引擎。** AceStream 引擎是你要指向的依赖：任何其 HTTP API 可被容器访问的引擎都
可以，而作者自己的引擎运行在哪里 —— 另一台机器、VPN 内部、代理之后 —— 都在本仓库之外，与本仓库
无关。套件只假设 `ACESTREAM_SUITE_ENGINE_URL` 中的 URL 会应答。

本仓库确实描述的是一个规模很小、很普通的部署：一台主机运行 `docker compose up -d`，它的取值放在
`.env`，一个 Web 服务器或反向代理对外提供播放列表脚本写入的目录，以及 —— 如果你希望列表和指南按
计划刷新 —— 上面的 cron 任务。compose 文件是接口，上面的环境变量是旋钮，`scripts/` 下的脚本是可
选的助手，你可以阅读它们、手动运行或安排计划。
