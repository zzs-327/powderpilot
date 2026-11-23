"""环境自检：换机器 / 换解释器 / 报 ModuleNotFoundError 时先跑这个。

用法：
    python -m scripts.check_env

检查四件事：
    1. Python 版本与必需依赖是否齐全（缺哪个直接告诉你 pip 装什么）
    2. MySQL 能不能连、ski_db 有没有数据
    3. Ollama 在不在、嵌入模型有没有 pull
    4. .env 里的 API key 填没填
"""
from __future__ import annotations

import importlib.util
import sys


# 模块名 → pip 包名（两者不一致的要写清楚，否则照着装会装错）
REQUIRED = {
    'fastapi': 'fastapi',
    'uvicorn': 'uvicorn[standard]',
    'sqlalchemy': 'sqlalchemy',
    'pymysql': 'pymysql',
    'cryptography': 'cryptography',
    'pydantic': 'pydantic',
    'pydantic_settings': 'pydantic-settings',
    'jose': 'python-jose[cryptography]',
    'bcrypt': 'bcrypt',
    'yaml': 'PyYAML',
    'langchain': 'langchain',
    'langchain_openai': 'langchain-openai',
    'langgraph': 'langgraph',
    # 会话记忆用异步 SQLite checkpointer：langgraph 本体不带，漏装要等第一次对话才炸
    'langgraph.checkpoint.sqlite': 'langgraph-checkpoint-sqlite',
    'aiosqlite': 'aiosqlite',
    'langchain_text_splitters': 'langchain-text-splitters',
    'langchain_ollama': 'langchain-ollama',
    'langchain_chroma': 'langchain-chroma',
    'langchain_community': 'langchain-community',
    'chromadb': 'chromadb',
    'rank_bm25': 'rank-bm25',
}
# 可选依赖：不在核心 requirements.txt 里（装了更好，没装不影响主流程）
# pymupdf 是 AGPL-3.0 双许可，按许可合规挪出核心依赖，见 requirements-optional.txt
OPTIONAL = {'httpx': 'httpx（测试用）', 'minio': 'minio（可选）',
            'fastmcp': 'fastmcp（可选）', 'fitz': 'pymupdf（PDF 手册解析，可选 / AGPL-3.0）'}


def check_packages() -> list[str]:
    print(f'Python {sys.version.split()[0]}  ({sys.executable})')
    missing = []
    for module, package in REQUIRED.items():
        if importlib.util.find_spec(module) is None:
            missing.append(package)
    if missing:
        print(f'  ❌ 缺少 {len(missing)} 个必需依赖：')
        for package in missing:
            print(f'     - {package}')
        print('  安装：pip install ' + ' '.join(f'"{p}"' for p in missing))
    else:
        print(f'  ✅ 必需依赖齐全（{len(REQUIRED)} 个）')
    absent_optional = [name for name, label in OPTIONAL.items()
                       if importlib.util.find_spec(name) is None]
    if absent_optional:
        print(f'  （可选未装：{", ".join(absent_optional)}）')
    return missing


def check_database() -> None:
    try:
        from sqlalchemy import create_engine, text

        from config.config import settings
        # 用 echo=False 的临时连接，避免 DEBUG 模式下把 SQL 刷满屏幕
        engine = create_engine(settings.DATABASE_URL)
        with engine.connect() as conn:
            name = conn.execute(text('SELECT DATABASE()')).scalar()
            tables = [row[0] for row in conn.execute(text('SHOW TABLES'))]
        print(f'  ✅ MySQL 已连接，库={name}，表：{", ".join(tables) or "（空）"}')
        engine.dispose()
        if 'resort' not in tables:
            print('     提示：跑 python -m scripts.sync_to_mysql 建表并同步数据')
    except Exception as exc:
        print(f'  ❌ MySQL 连接失败：{type(exc).__name__}: {str(exc)[:120]}')
        print('     检查 .env 的 DATABASE_URL，以及 MySQL 服务是否启动')


def check_ollama() -> None:
    import urllib.request

    from config.config import settings
    from utils.net import url_opener
    try:
        # 走 url_opener：本机 Ollama 必须绕开系统代理，否则这里永远报"连不上"
        opener = url_opener(settings.OLLAMA_BASE_URL)
        with opener.open(f'{settings.OLLAMA_BASE_URL}/api/tags', timeout=5) as resp:
            import json
            models = [m['name'] for m in json.load(resp).get('models', [])]
        hit = any(settings.OLLAMA_EMBED_MODEL.split(':')[0] in m for m in models)
        if hit:
            print(f'  ✅ Ollama 在跑，嵌入模型已就位（{settings.OLLAMA_EMBED_MODEL}）')
        else:
            print(f'  ⚠️ Ollama 在跑，但没有 {settings.OLLAMA_EMBED_MODEL}')
            print(f'     执行：ollama pull {settings.OLLAMA_EMBED_MODEL}')
    except Exception as exc:
        print(f'  ❌ Ollama 连不上（{settings.OLLAMA_BASE_URL}）：{type(exc).__name__}')
        print('     先启动 Ollama 桌面端 / 服务')


def check_vector_store() -> None:
    """向量库自检：**库里有数据 ≠ 检索能用**

    为什么必须单独检（坑 #24 的教训）：那次的现象正是"rag.ingest 报入库完成 538 条，
    但任何新进程一查就报 `Error loading hnsw index`"。原来的自检只看依赖/MySQL/Ollama/key，
    这种"数据在、检索炸"的状态**一点都看不出来**，等到演示当天才发现就晚了。

    查三件事：集合在不在、条数、**真跑一次查询**（读一条已存向量当查询向量，
    维度天然对得上，也顺带验证了索引文件能不能加载）。
    """
    from config.config import settings
    try:
        import chromadb

        client = chromadb.PersistentClient(path=settings.CHROMA_DIR)
        collection = client.get_collection(settings.CHROMA_COLLECTION)
    except Exception as exc:
        print(f'  ❌ 打不开向量库集合 {settings.CHROMA_COLLECTION}：{type(exc).__name__}: {exc}')
        print('     先跑：python -m rag.ingest（需要 Ollama 在跑）')
        return

    count = collection.count()
    if not count:
        print('  ⚠️ 集合是空的：跑 python -m rag.ingest 灌入雪场档案')
        return

    try:
        probe = collection.get(limit=1, include=['embeddings'])
        vector = probe['embeddings'][0]
        hits = collection.query(query_embeddings=[vector], n_results=3)
        hit_count = len(hits['ids'][0])
    except Exception as exc:
        print(f'  ❌ 向量库有 {count} 条，但**查询失败**：{type(exc).__name__}: {exc}')
        print('     这正是坑 #24 的症状（索引文件损坏）。修复：python -m rag.ingest --rebuild')
        print('     ⚠️ 别把 ingest 里的 upsert 改回 delete(ids) + add_documents，那是扳机')
        return

    print(f'  ✅ 向量库可读可查：集合 {settings.CHROMA_COLLECTION}，{count} 条，'
          f'试查返回 {hit_count} 条')


def check_keys() -> None:
    from config.config import settings
    provider = settings.LLM_PROVIDER.lower()
    key = settings.DEEPSEEK_API_KEY if provider == 'deepseek' else settings.ZHIPU_API_KEY
    if key:
        # 只报"有没有填"，不打印 key 内容：日志/截图/录屏都会带出去（AGENTS.md 约定 8）
        print(f'  ✅ LLM provider={provider}，模型={settings.LLM_MODEL}，key 已填（{len(key)} 字符）')
    else:
        print(f'  ❌ LLM provider={provider} 但没填 key，对话接口会报错')


def main() -> None:
    # Windows 中文控制台默认 GBK，下面的 ✅/⚠️/❌ 会 UnicodeEncodeError 把自检崩在半路
    from utils.console import enable_utf8_console
    enable_utf8_console()
    print('=== 1. Python 与依赖 ===')
    missing = check_packages()
    if missing:
        print('\n依赖没装齐，后面的检查跳过。先按上面的命令装包，再重跑本脚本。')
        return
    print('=== 2. MySQL ===')
    check_database()
    print('=== 3. Ollama 嵌入模型 ===')
    check_ollama()
    print('=== 4. 向量库（Chroma）自检 ===')
    check_vector_store()
    print('=== 5. 大模型 key ===')
    check_keys()


if __name__ == '__main__':
    main()