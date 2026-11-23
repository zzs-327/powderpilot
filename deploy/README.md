# 部署说明（阿里云 ECS / 任意装了 Docker 的 Linux 机器）
#
# 目标：一条命令起 MySQL + Ollama + 后端，跑完初始化脚本就能对外提供 API。

## 0. 嵌入（Ollama）放哪：三种模式

本地开发时 Ollama 跑在 Windows 上（有 GPU）；ECS 上没有 GPU。所以按场景选：

| 模式 | 怎么起 | 嵌入在哪 | 适用 |
|---|---|---|---|
| **A. 本地 + GPU**（推荐开发/录视频） | `docker compose -f docker-compose.yml -f docker-compose.local.yml --env-file .env.docker up -d --build` | 宿主机原生 Ollama（**吃 GPU，最快**） | 日常开发、录演示视频 |
| **B. ECS 全容器**（证明可部署） | `docker compose --env-file .env.docker up -d --build` | 容器内 Ollama（**CPU**） | 公网演示、"一键部署"证据 |
| **C. 托管嵌入 API** | 改 `OLLAMA_BASE_URL` / 换 embedding 实现 | 第三方 API | 生产提速，不运维模型 |

**模式 A 的 Windows 前置**（不做这两步容器一定连不上）：

```powershell
# Ollama 默认只听 127.0.0.1，容器访问不到；让它听所有网卡后**重启 Ollama**
setx OLLAMA_HOST 0.0.0.0:11434
# 验证
curl http://127.0.0.1:11434/api/tags
```

防火墙只放行本机 Docker 网段即可，**别把 11434 开到公网**。

> 模式 B 的 CPU 性能：`dmeta-embedding-zh` 是 BERT 级小模型，538 条入库约几分钟、
> 单次查询几十到几百毫秒——**演示够用，但不是生产性能**。

## 1. 服务器准备

```bash
# 安装 docker 与 compose 插件（阿里云 ECS 已有 docker 的跳过）
docker --version && docker compose version

# 拉代码
git clone <你的仓库地址> powderpilot && cd powderpilot

# 配 compose 用的环境文件（不要提交 .env.docker）
cp .env.docker.example .env.docker
vi .env.docker      # 填 MYSQL_ROOT_PASSWORD / SECRET_KEY / ZHIPU_API_KEY
```

## 2. 起服务

```bash
docker compose --env-file .env.docker up -d --build
docker compose --env-file .env.docker ps
```

## 3. 初始化数据（建表 → 同步档案 → 拉模型 → 灌向量库 → 自检）

```bash
bash deploy/init.sh
```

最后一行应该看到 `scripts.check_env` 五段全绿，其中第 4 段是向量库自检：

```
=== 4. 向量库（Chroma）自检 ===
  ✅ 向量库可读可查：集合 ski_kb，538 条，试查返回 3 条
```

## 4. 验证

```bash
# 健康检查（服务器本机）
curl -s http://127.0.0.1:8000/ ; echo

# 命令行真跑一轮（会调模型，注意额度）
docker compose --env-file .env.docker exec -T backend \
    python -m scripts.chat_cli --debug
```

浏览器访问 `http://<ECS公网IP>:8000/docs`。

## 5. 安全清单（公网部署必看）

| 项 | 现状 | 该怎么做 |
|---|---|---|
| MySQL 3306 | compose 里**只绑 127.0.0.1** | ✅ 别改成 `3306:3306`；需要连库就用 SSH 隧道 |
| Ollama 11434 | 同样只绑 127.0.0.1 | ✅ 别暴露，否则任何人可白嫖你的算力 |
| 后端 8000 | 对外 | 用**安全组**限制来源 IP；正式对外挂 Nginx + HTTPS |
| `ALLOW_ORIGINS` | 默认 `*` | 部署时填前端域名（`allow_credentials` 与通配符互斥） |
| `DEBUG` | compose 里设 `False` | 保持 False，异常不许泄漏细节 |
| 密钥 | 全部走 `.env.docker` | 该文件已 gitignore；`SECRET_KEY` 用 `openssl rand -hex 32` 生成 |

## 6. 常用运维命令

```bash
# 看日志（后端日志同时落宿主机 ./ski-backend/logs/）
docker compose --env-file .env.docker logs -f backend

# 改了雪场档案后重新同步（YAML 是唯一真相源）
docker compose --env-file .env.docker exec -T backend python -m scripts.sync_to_mysql
docker compose --env-file .env.docker exec -T backend python -m rag.ingest

# 重建向量库（delete_collection 路径已实测安全，见 scripts/probe_chroma.py）
docker compose --env-file .env.docker exec -T backend python -m rag.ingest --rebuild

# 更新代码
git pull && docker compose --env-file .env.docker up -d --build backend

# 备份（向量库是可重建的派生数据，真正要备的是 MySQL 与 YAML）
docker compose --env-file .env.docker exec -T mysql \
    mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" ski_db > backup_$(date +%F).sql
```

> 数据分层决定了备份策略：**YAML 档案在 git 里，MySQL 是可从 YAML 重建的**，
> 所以真正的备份重点是 `mysql_data` 卷（用户、画像、会话相关的运行期数据）。

## 7. 部署自查脚本（ECS 实测用过，可复跑）

| 脚本 | 查什么 | 怎么跑 |
|---|---|---|
| `scripts/check_env.py` | 依赖 / MySQL / Ollama / **向量库可读可查** / model key | `docker compose --env-file .env.docker exec -T backend python -m scripts.check_env` |
| `deploy/sse_probe.py` | **经 nginx 反代后 SSE 有没有被缓冲**（被缓冲时"打字机"会变成"憋一会儿一次全出"） | `docker compose --env-file .env.docker exec -T backend python - < deploy/sse_probe.py` |
| `deploy/ops_snapshot.py` | 当前状态快照：MySQL 计数 / 向量库条数 / 会话记忆行数 | 上一条把文件名换成 `ops_snapshot.py` |
| `deploy/ops_restart_drill.sh` | **重启自愈**：stop/start、down/up 前后快照数字是否一致 | `bash deploy/ops_restart_drill.sh`（不带 `-v`，不会删卷） |

> 实测结论（2026-10-04，阿里云 ECS 2 核 3.6G）：SSE 首块 **0.01s** 到、正文铺开到 **14.5s**（没被缓冲）；
> `stop` 后 8000 关闭、`start` **9 秒**恢复；`down`+`up` 后 MySQL 12 雪场 / 219 雪道 / 98 价格、
> 向量库 538 条、会话记忆 161 checkpoints + 217 writes **全部不变**。

