# WeChat Agent

[English](README.md) | [中文](README.zh-CN.md)

[Quick Demo](#quick-demo) · [功能介绍](#核心功能) · [技术概览](#技术概览) · [接入真实数据](#部署与配置) · [模型配置](#模型配置) · [开始使用](#开始使用)

**把微信聊天记录变成可检索、可追问、可持续跟进的个人知识库。**

WeChat Agent 帮你从分散的私聊和群聊中找回信息：回顾过去的讨论，
用自然语言跨会话提问，也可以设置周期任务，持续整理你关心的动态。

群里分享的实习机会、朋友私聊中的准备建议，不必再分别翻找。
你可以把它们放在一个问题里检索、汇总，并展开原文核对。
对于需要持续关注的信息，则交给定时任务跟进。

## Quick Demo

**[观看 77 秒完整演示](docs/demo/wechat-agent-demo.mp4)**

[![点击观看：浏览聊天、跨会话提问、展开证据、定时任务和增量索引](docs/images/demo-poster.jpg)](docs/demo/wechat-agent-demo.mp4)

浏览聊天 → 跨会话提问 → 展开原始证据 → 创建每日任务 → 查看执行结果
→ 导入 10 条新消息并增量更新索引。

视频录自真实应用，数据全部虚构，问答与任务结果来自真实模型调用。
中文界面、英文说明字幕，无音轨；较长的模型等待已剪去并标注。
本次展示本地关键词检索，未启用可选的 Embedding 和重排。

### 运行虚构数据工作区

**不需要安装微信、提取数据库密钥，也不需要私人聊天记录。**
使用 Python 3.9+ 即可体验聊天浏览与本地索引：

```bash
git clone https://github.com/Fongkai777/wechat-agent.git
cd wechat-agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m wechat_agent.demo init --root .demo/quick
python -m wechat_agent.demo index --root .demo/quick
python -m wechat_agent.demo serve --root .demo/quick
```

访问 [localhost:8787](http://127.0.0.1:8787)。生成新的问答或任务结果时，
需要在 Demo 的「模型配置」中填写自己的服务凭据，相关调用可能产生费用。
仅观看视频无需任何安装。端口已被占用时，请先确认并停止自己已有的实例，勿重复启动。
Demo 不会回退读取真实账号数据。

[示例与录制说明](docs/DEMO.md) · [接入自己的微信记录](#部署与配置)

*页面截图与视频均使用虚构示例数据。*

## 核心功能

### 跨会话问答

用自然语言查询聊天记录，关联不同会话中的信息，并通过多轮对话继续追问。
回答附带可展开的来源，展示会话、发送者、时间和原文。
问答历史自动保存，切换页面或关闭网页不会中断后台请求。

例如：“最近有哪些实习机会？”“朋友之前给了我哪些面试准备建议？”

![带原文引用的聊天问答](docs/images/qa-citations.png)

### 周期任务助手

填写任务内容，设置执行周期与检索范围。助手自主选择只读检索工具，
在指定时间窗内查找消息，将发现和建议保存为每次执行的结果。
任务支持立即执行、暂停，以及查看历史结果。

你可以用它检查尚未回复的私聊、追踪新增实习信息，或整理朋友推荐的餐厅。
助手只提供信息和建议，不会代替你发送微信消息。

![任务结果与执行记录](docs/images/task-live.png)

### 聊天浏览与数据准备

以熟悉的聊天布局浏览私聊和群聊，搜索联系人，按需加载历史消息。
支持从已配置的本地微信数据自动或手动同步，展示支持的本地媒体，
并保存语音转写结果。

语音转文字、全文索引和语义索引可以依次执行。
新消息增量追加，转写变化更新对应记录及受影响的语义块。
RAG 配置页集中展示索引状态、检索调试和定时更新设置。

![聊天内容浏览](docs/images/chats.png)

<details>
<summary>索引准备与检索配置</summary>

![RAG 配置](docs/images/rag.png)

</details>

## 技术概览

系统基于同一份本地聊天数据，提供两条执行路径：
**面向问答的混合检索**，以及**面向周期任务的工具调用检索**。

```mermaid
flowchart LR
    A[本地微信快照 / 示例数据] --> B[消息解析与去重]
    B --> C[(本地聊天数据)]
    V[语音转写] --> I[增量索引]
    C --> I
    I --> K[关键词索引]
    I --> E[Embedding 索引]
    Q[问题与查询规划] --> K
    Q --> E
    K --> R[RRF 融合与可选重排]
    E --> R
    R --> G[问答模型与对话历史]
    G --> O[结构化回答与来源校验]
    T[任务与执行周期及检索范围] --> H[任务模型]
    H <--> S[只读检索与上下文工具]
    C --> S
    V --> S
    H --> P[保存发现与建议]
```

- **混合检索**：规则驱动的查询规划、语义与关键词多路召回、联系人关联软信号、
  RRF 融合，以及可选的 LLM 重排。
- **可核查的回答**：模型返回结论和来源编号，代码校验引用，
  并从检索记录中生成会话、发送者和时间等来源信息。
- **任务执行**：本地调度器驱动模型调用工具，读取已同步消息。
  任务检索与问答向量索引是独立路径。
- **模型独立配置**：语音转写、聊天问答、任务助手、Embedding 和重排分别配置。

| 模块 | 技术 |
|---|---|
| 后端与调度 | Python 3.9+、本地 HTTP 服务、后台任务 |
| 存储与检索 | SQLite、FTS5/LIKE、进程内向量相似度计算 |
| 模型接入 | OpenAI 兼容 API、JSON Schema 结构化回答、工具调用 |
| 前端 | HTML、CSS、JavaScript |
| 本地数据处理 | PyCryptodome、Zstandard |

实现细节和代码入口见[架构说明](docs/ARCHITECTURE.md)。

## 部署与配置

以下流程是在 **macOS + 微信 4.x** 上部署完整应用。
需要你自己的已登录微信账号、本地已同步的聊天记录、Python 3.9+ 和
Apple Command Line Tools。数据库密钥提取受微信与 macOS 版本影响，
并不保证所有版本都能成功。这里采用本机部署，不提供 Docker 或公网部署方案：
密钥提取需要本地客户端，网页服务也没有内置登录鉴权。

### 1. 安装项目

如果尚未安装 Command Line Tools，先运行 `xcode-select --install` 并完成安装。
然后执行：

```bash
git clone https://github.com/Fongkai777/wechat-agent.git
cd wechat-agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

后续命令均在仓库根目录、已激活的虚拟环境中执行。
如果需要调用云端模型转写微信语音，再安装本地 SILK 解码依赖：

```bash
python -m pip install 'pilk>=0.2'
```

运行网页不需要安装 Node.js 或编译前端。

### 2. 准备微信数据

找到包含 `db_storage/` 和通常与其同级的 `msg/` 的账号目录。
macOS 上常见位置为：

```text
~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<账号目录>/
```

请使用自己实际的账号目录，不同客户端版本的路径可能不同。
复制前先退出微信，避免复制过程中数据库仍在变化。
将**整个** `db_storage` 复制到项目根目录，保留其中已有的 `-wal`、`-shm` 文件；
需要图片、视频等本地媒体时，再复制同级的 `msg`。
不要覆盖或修改微信原始目录。

```text
wechat-agent/
  db_storage/
    message/message_0.db
    contact/contact.db
    session/session.db
    ...
  msg/                    # 可选媒体目录，体积可能较大
```

检查数据库副本：

```bash
python -m wechat_agent inspect
```

### 3. 获取数据库密钥并解密

**数据库密钥与模型 API Key 是两回事。**
程序从 `all_keys.json` 读取与数据库匹配的密钥；模型 API Key 不能用于解密微信。

重新打开微信，登录与数据库副本对应的账号。
项目内置的 LLDB 扫描脚本会尝试从运行中的客户端提取密钥，
并使用数据库副本验证是否匹配。请在本机 Terminal 中运行：

```bash
bash scripts/key_scan_python_lldb.sh
```

脚本会请求管理员权限，使用 Command Line Tools 自带的 Python，
而非项目虚拟环境或 Conda Python。成功后，项目根目录会生成 `all_keys.json`。
打开聊天、联系人和搜索页面可促使更多数据库被加载，
这不代表每个联系人都使用独立密钥。

遇到无法附加进程、找到 0 个密钥或 LLDB 导入错误时，
请查看[密钥提取与排错说明](docs/KEY_EXTRACTION.md)。
文件访问权限和进程调试权限不同；不要把关闭 SIP 或重新签名微信当作默认安装步骤，
这些操作会影响系统安全或应用完整性。

`all_keys.json` 属于敏感数据，只应保存在本机。提取后执行：

```bash
python -m wechat_agent doctor --keys all_keys.json
python -m wechat_agent decrypt --keys all_keys.json
```

解密副本写入 `decrypted/`，不会修改原始数据库。
继续前检查失败和跳过的数据库，尤其是消息库、联系人库和会话库。
仅生成一个没有匹配密钥的 JSON 文件不算提取成功。

### 4. 配置账号并启动

复制后的 `db_storage/` 路径不再包含账号 ID，需要显式告诉程序哪些消息是你发出的。
在项目根目录创建 `web_cache/`，新建本机配置 `web_cache/source.json`：

```json
{
  "db_storage": "db_storage",
  "media_root": "msg",
  "account": "YOUR_WECHAT_ID"
}
```

将 `YOUR_WECHAT_ID` 替换为数据库中的发送者 ID，不是昵称或备注。
对于形如 `wxid_example_ab12` 的原始账号文件夹，账号 ID 为 `wxid_example`，
不含最后的目录后缀；仅在原始目录符合该形式时这样提取。
也可用启动参数 `--account` 覆盖此配置。缺少正确账号 ID 会影响气泡方向和待回复判断。

启动服务：

```bash
bash scripts/start_web.sh
```

访问 [http://127.0.0.1:8787](http://127.0.0.1:8787)。
保持终端运行，按 `Ctrl+C` 停止服务。
默认展示 2023-01-01 以来的消息，不会删除更早的原始数据。
需要修改起始日期时，可给启动脚本传入 `--since 2020-01-01` 等参数。

#### 持续同步（可选）

**需要持续同步新消息时**，应将数据源指向微信实时账号目录，而不是静态副本。
修改上面的本机配置 `web_cache/source.json`，使用自己的绝对路径和账号 ID：

```json
{
  "db_storage": "/absolute/path/to/account-folder/db_storage",
  "media_root": "/absolute/path/to/account-folder/msg",
  "account": "YOUR_WECHAT_ID"
}
```

项目根目录仍需保留与该账号匹配的 `all_keys.json`。
如果 macOS 阻止读取微信容器，请为启动服务的终端应用授予“完全磁盘访问权限”。
修改路径或权限后重启：

```bash
bash scripts/restart_web.command
```

该脚本会停止 **8787** 上的监听进程，务必确认端口属于本项目，并先等待正在执行的任务结束。
服务每 60 秒检查一次数据库变化，也可以点击“同步最新消息”。
复制出来的文件夹不会自行更新；同步消息也不会自动更新 RAG 索引或调用模型。

## 模型配置

### 服务与密钥

打开**模型配置**页，为需要使用的功能填写服务 Base URL、模型名称和 API Key。
使用 OpenAI 时，按[官方 API 配置指南](https://developers.openai.com/api/docs/quickstart)
在服务商控制台创建密钥，本项目对应的 Base URL 为 `https://api.openai.com/v1`。
使用其他兼容服务时，填写对应服务商的密钥、模型 ID 和接口地址。
Base URL 不要额外拼接 `/chat/completions`。

| 配置框 | 用途 | 服务需要支持的能力 |
|---|---|---|
| 语音转文字 | 将本地语音转成文本 | 音频转写接口 |
| 聊天问答 | 生成带来源引用的回答 | Chat Completions + 严格 JSON Schema |
| 任务助手 | 执行定时或手动任务 | Chat Completions + 工具调用 + 严格 JSON Schema |
| Embedding | 建立和查询语义索引 | Embeddings 接口 |
| Rerank | 对检索结果重排 | Chat Completions；当前使用 LLM 重排 |

### 默认模型与保存位置

代码默认分别使用 `gpt-4o-mini-transcribe`、问答和任务的 `gpt-5-mini`、
`text-embedding-3-small`，以及重排的 `gpt-5-nano`。
这些是配置默认值，不代表你的账号一定可用；请按服务商实际支持的模型与接口能力填写。

保存配置后再准备索引。Embedding 和重排的地址、密钥留空时可继承问答配置。
问答、任务、语音使用独立配置框，也可从服务进程的环境变量读取密钥
（默认变量名为 `OPENAI_API_KEY`）。
网页保存的设置位于本机 `web_cache/llm_config.json`，请保护好该目录。
不使用 Embedding 或重排时可关闭对应开关。云端请求会传输相关内容，并可能产生费用。

## 开始使用

### 准备索引

进入 **RAG 配置**，点击“一键准备检索”，按顺序执行：

```text
语音转文字 → 增量全文索引 → 增量语义索引
```

有本地语音时先配置转写服务，已转写内容会复用。
也可以分别更新全文索引与语义索引；语义索引需要启用并配置 Embedding 服务。

### 问答、任务与聊天浏览

| 页面 | 使用方法 |
|---|---|
| 聊天内容 | 选择联系人或群聊，浏览历史、查看支持的媒体、同步新消息 |
| 聊天问答 | 新建对话，输入问题，连续追问，展开来源核对原文 |
| 任务助手 | 填写任务内容，设置每 N 小时／天执行和最近 N 天／周／月的检索范围，保存或立即执行 |
| RAG 配置 | 更新索引、调试检索、设置自动准备检索的周期 |
| 模型配置 | 分别修改各功能的服务商、凭据、模型与参数 |

任务工具直接读取已同步聊天及保存的语音转写，不要求先建立问答向量索引。
执行结果和历史会保存，但不会发送微信消息。
周期任务和定时索引更新都要求服务在线、电脑保持唤醒。

## 日常维护

新消息通常只需增量更新，不必每次全量重建。
本机配置、问答和任务历史、索引保存在 `web_cache/`，
解密数据库在 `decrypted/`，升级代码时不要把这些目录当作临时文件删除。
V2 图片可能需要额外的 `image_aes_key`；缺失的媒体或音频无法仅靠消息元数据恢复。

### 安装自检与排错

没有微信数据和密钥时，也可运行以下自检，验证安装、示例导入、增量索引、网页资源和本地接口：

```bash
python scripts/smoke_test.py
```

自检仅使用临时生成数据，不调用云端模型；临时服务会自动关闭，不占用 8787。
交互式示例见[示例运行说明](docs/DEMO.md)，更多运行细节见[使用指南](docs/USAGE.zh-CN.md)。

| 现象 | 优先检查 |
|---|---|
| 找不到数据库或匹配密钥为 0 | 数据目录、登录账号、提取权限及客户端版本 |
| 消息全部出现在对方一侧 | `web_cache/source.json` 的 `account` 是否为自己的发送者 ID |
| 没有最新消息 | 数据源是否为实时目录，而不是旧副本；终端是否有文件访问权限 |
| 端口 8787 已占用 | 确认已有服务，不要重复启动或停止不相关进程 |
| 模型请求失败 | 对应配置框的 Base URL、密钥、模型能力、网络及服务商额度 |

自检通过不代表所有微信版本都能提取密钥，也不验证你的云端模型权限。

可选的[回答质量检查](docs/ANSWER_QUALITY.md)覆盖正确性、完整性、引用支持度、延迟与费用。
它使用少量虚构对话和模型评审，不代表生产环境准确率。

## 已知限制与计划

- 只能读取本地已有的历史和媒体；媒体解析取决于格式、密钥及缓存是否可用。
- 对话历史参与回答生成；检索侧对模糊追问的查询改写仍在计划中。
- 来源校验让回答可追溯，但不能保证每个结论都得到引用原文的支持。
- 定时任务需要服务运行且电脑处于唤醒状态；较大的检索时间窗会增加处理时间与模型用量。
- 后续重点：多轮查询改写、更丰富的检索评测、引用支持度检查，以及大规模聊天数据的性能优化。

## 隐私与致谢

项目原创代码采用 [MIT License](LICENSE)，第三方材料保留各自许可，
详见[第三方声明](THIRD_PARTY_NOTICES.md)。

聊天数据存储在本地；使用云端模型时，相关文本或音频会发送给配置的服务商，
因此本项目并非纯离线应用。请仅处理你有权访问的数据。
私人数据库、密钥和缓存不提交到 Git。服务面向本机使用，不适合直接暴露到公网。

提取与解析参考了
[wechat-db-decrypt-macos](https://github.com/Thearas/wechat-db-decrypt-macos)、
[wechat-chat-history-mac](https://github.com/BIBOYANG425/wechat-chat-history-mac)、
[wechat-suite](https://github.com/raclen/wechat-suite)。
再分发上游代码前请确认许可证。本项目不是腾讯或微信官方产品。
