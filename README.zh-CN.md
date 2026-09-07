# WeChat Agent

本地微信聊天记录解析与 RAG 问答助手，用来查看、检索和分析你自己的 macOS WeChat 4.x 聊天数据库。

它目前支持：

- 解密并读取你自己导出的 WeChat `db_storage` 数据库。
- 用接近微信的方式浏览原始聊天内容。
- 展示文本、链接、转发聊天记录、图片、视频、表情包和语音占位信息。
- 对本地语音做 OpenAI 语音转文字，并把转写结果缓存起来。
- 建立全文索引和语义索引，用于聊天记录问答。
- 在 RAG 检索页查看 Query Planner、召回分路、融合排序、入模片段和索引状态。
- 在网页里配置问答、Embedding、Rerank 和语音转写模型。

> 注意：这个项目只适合处理你自己有权访问的聊天数据。`all_keys.json`、解密数据库、网页缓存、转写结果和原始媒体文件都不应该提交到 Git。

## 数据目录

项目默认期望根目录下有一份复制出来的微信数据库目录：

```text
db_storage/
  message/message_0.db
  contact/contact.db
  session/session.db
  ...
```

这些数据库是 SQLCipher 加密的。你还需要从自己已登录、正在运行的 WeChat 客户端里提取数据库密钥，保存为 `all_keys.json`，之后才能解密和浏览聊天记录。

## 快速开始

安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

检查数据库：

```bash
python3 -m wechat_agent inspect
```

拿到 `all_keys.json` 后解密数据库：

```bash
python3 -m wechat_agent decrypt --keys all_keys.json
```

解密过程会把加密的 SQLite `*.db-wal` 合并到解密副本里，这对最近下载但还没 checkpoint 到主 `.db` 文件里的语音和媒体记录很重要。

启动本地网页：

```bash
bash scripts/start_web.sh
```

然后打开：

```text
http://127.0.0.1:8787
```

默认只展示 2023 年以来的记录。这个时间过滤只影响网页展示和索引，不会删除或改写原始微信数据库。

如果想换端口：

```bash
WECHAT_AGENT_PORT=8899 bash scripts/start_web.sh
```

如果想读取原始微信容器目录，也可以用环境变量指定，不需要把路径写进代码：

```bash
WECHAT_AGENT_DB_STORAGE="/path/to/db_storage" bash scripts/start_web.sh
```

也可以直接指定参数：

```bash
python3 -m wechat_agent.web --db-storage db_storage --since 2023-01-01 --port 8787
```

如果直接读取微信容器目录时被 macOS 权限拦住，请从 Terminal.app 启动，并给 Terminal 开启 Full Disk Access。

## 网页功能

网页目前分成四个主要 tab：

- 原始聊天内容：浏览联系人、群聊和消息流。
- 聊天问答：像聊天界面一样对所有聊天记录提问，支持多轮对话和历史会话管理。
- RAG 检索：展示检索链路，包括查询改写、全文召回、语义召回、RRF 融合、Rerank、入模片段和索引更新状态。
- 大模型配置：配置语音转文字、问答模型、Embedding 模型和 Rerank 模型。

RAG 检索不会把“联系人命中”当作硬过滤。主检索会走全库，联系人只作为软召回和排序信号，避免因为关键词撞上联系人备注而漏掉真正相关的聊天内容。

## 媒体说明

聊天浏览器会从 `db_storage` 同级的 `msg/` 目录读取媒体文件。

- 视频：本地存在匹配的 `.mp4` 或缩略图时会展示。
- 图片：本地 `.dat` 文件本身已经是浏览器可读的 JPEG、PNG 或 WebP 时会展示。
- 微信内置小表情：例如 `[Sob]`、`[Whimper]` 会保留为文字，便于后续情绪分析。
- 表情包：会解析 `type=47` 的 XML，优先找本地缓存，找不到时尝试使用消息里的 CDN 预览地址。
- V2 加密图片：需要额外的 `image_aes_key`，可用 `WECHAT_AGENT_IMAGE_AES_KEY` 和 `WECHAT_AGENT_IMAGE_XOR_KEY` 传入，或写入本地 `config.json`。
- 语音：需要本地音频 blob、SILK 解码能力和语音模型配置。网页可以批量转写语音，并跳过已转写的内容。

## RAG 索引

构建命令行聊天索引：

```bash
python3 -m wechat_agent index
```

网页里的 RAG tab 维护两类索引：

- 全文候选库：SQLite + FTS，用于关键词召回和精确补漏。
- 语义索引：把聊天记录切成语义片段，调用 Embedding API 生成向量，用于语义召回。

索引支持增量更新：如果只是消息库追加新记录，系统会扫描新增消息并补写索引；如果账号、时间范围或关键文件结构发生变化，才需要全量重建。

## 导出聊天

按用户名或群聊 id 导出单个聊天：

```bash
python3 -m wechat_agent export --chat 1234567890@chatroom --name "My Group"
```

输出会写到 `exports/`，解密副本会写到 `decrypted/`。这两个目录默认被 `.gitignore` 忽略。

## 密钥提取

详见 [docs/KEY_EXTRACTION.md](docs/KEY_EXTRACTION.md)。

简短说：macOS WeChat 4.x 的数据库密钥不在 `db_storage` 文件夹里，而是在已登录的 WeChat 客户端进程内。你需要从自己的运行中客户端提取密钥，生成 `all_keys.json`，本项目才能用它解密本地数据库。

`all_keys.json` 可以解密你的微信聊天数据，请务必只保存在本机，不要提交到 GitHub、云盘或公开聊天里。

## 参考项目

- <https://github.com/Thearas/wechat-db-decrypt-macos>
- <https://github.com/BIBOYANG425/wechat-chat-history-mac>
- <https://github.com/raclen/wechat-suite>
