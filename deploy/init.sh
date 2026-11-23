#!/usr/bin/env bash
# ============================================================
# PowderPilot 首次部署初始化（在仓库根目录执行）
#
#   bash deploy/init.sh
#
# 做四件事：
#   1. 建表 + 种子账号（init_db.py）
#   2. YAML 雪场档案 → MySQL（幂等）
#   3. 拉 Ollama 嵌入模型（CPU 机器上首次较慢）
#   4. 雪场档案 → Chroma 向量库（幂等 upsert）
# 最后跑一次 check_env + 向量库自检，全绿才算部署成功。
#
# 前置：已 `docker compose --env-file .env.docker up -d --build`
# ============================================================
set -euo pipefail

COMPOSE="docker compose --env-file .env.docker"
EMBED_MODEL="${OLLAMA_EMBED_MODEL:-shaw/dmeta-embedding-zh:latest}"

echo "==> 0/5 容器状态"
$COMPOSE ps

echo "==> 1/5 建表 + 种子账号"
$COMPOSE exec -T backend python init_db.py

echo "==> 2/5 雪场档案 → MySQL（幂等，可重复跑）"
$COMPOSE exec -T backend python -m scripts.sync_to_mysql

echo "==> 3/5 拉嵌入模型：$EMBED_MODEL（CPU 机器上第一次要几分钟）"
$COMPOSE exec -T ollama ollama pull "$EMBED_MODEL"

echo "==> 4/5 雪场档案 → 向量库（幂等 upsert；不要改成 delete+add，见 scripts/probe_chroma.py）"
$COMPOSE exec -T backend python -m rag.ingest --dry-run
$COMPOSE exec -T backend python -m rag.ingest

echo "==> 5/5 自检（依赖 / MySQL / Ollama / 向量库可读可查 / key）"
$COMPOSE exec -T backend python -m scripts.check_env

echo
echo "完成。接口文档： http://<服务器IP>:8000/docs"
echo "命令行验证： $COMPOSE exec -T backend python -m scripts.chat_cli --debug"
