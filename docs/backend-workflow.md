# 后端操作补充

启动、依赖和公开快照边界以仓库根目录 [README](../README.md) 为准。下文补充根 README 未展开、但原 `ski-backend/README.md` 中仍有用的操作说明。完整旧稿（含本机路径笔记和历史评测叙述）见 [archive/ski-backend-README.md](archive/ski-backend-README.md)。

## 数据流

结构化档案与检索库都应以 `ski-backend/data/resorts/` 下你有权使用的 YAML 为来源（本公开快照只带字段模板和虚构示例）：

```
data/resorts/*.yaml  ── scripts.sync_to_mysql ──▶  MySQL
        │
        └── rag.ingest ──▶ Chroma 向量库
```

不要在数据库里手改业务数据来“补档”。改档案后重新同步，并按根 README 的说明重建或更新向量库。

## 常用补充命令

在 `ski-backend/` 下：

```bash
# 只解析与体检，不调用嵌入模型
python -m rag.ingest --dry-run

# 需要本地 Ollama 嵌入模型时，先安装并确认模型可用，例如：
# ollama pull shaw/dmeta-embedding-zh
# ollama list

# 混合检索调试（需已有索引和档案；查询内容请换成你自己的数据）
python -m rag.retriever "你的问题" --debug

# 服务文档与健康检查（服务启动后）
# http://127.0.0.1:8000/docs
# http://127.0.0.1:8000/
```

Windows 控制台中文：脚本入口会把 stdout 切到 UTF-8（`utils/console.py`）。若第三方终端仍乱码，可临时设置 `PYTHONIOENCODING=utf-8`。

需要新建 MySQL 库时，使用你自己的库名和权限，例如 `CREATE DATABASE ... DEFAULT CHARACTER SET utf8mb4`。表结构迁移见 [DATABASE_MIGRATIONS.md](DATABASE_MIGRATIONS.md)，不要在未备份的现有库上直接 stamp。

更细的目录职责、中间件与历史评测过程保留在归档 README，其中的历史 hit 率和本机课程环境路径不是本公开快照的开箱结果。
