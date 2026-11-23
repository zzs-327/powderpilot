"""嵌入模型：Ollama 本地部署，零 API 成本。

模型：shaw/dmeta-embedding-zh（中文效果好，RTX 4060 可跑）
准备：ollama pull shaw/dmeta-embedding-zh

注意：入库与检索必须用同一个嵌入模型，中途换模型要重建集合。

--------------------------------------------------------------------------
为什么不用 langchain_ollama.OllamaEmbeddings 直接调（2026-09-13 踩到的坑）

    原来 `embed_documents(538 条)` 是**一次性**发给 Ollama 的。实测这条路上
    Ollama 的 runner（llama-server）会中途猝死：
        Post "http://127.0.0.1:<port>/tokenize": connection refused
    而且**失败得很安静**——调用方拿到的是异常，但那时已经有一部分向量生在路上了。
    同一天里，向量库出现"写进去却再也读不出来"（HNSW 索引加载失败），
    排查了半天数据（id 唯一、无空内容、元数据齐全）都没问题，
    最后锁定的嫌疑就是"批量嵌入中途失败 → 落进索引的是残缺数据"。

    所以这里换成一个**自己控制的薄封装**，做三件事：
      1. 分批：每批 EMBED_BATCH_SIZE 条，单批失败不影响已完成的批次
      2. 重试：指数退避重试 EMBED_MAX_RETRIES 次（runner 猝死后重启需要几秒）
      3. **自检**：条数对得上、每条非空、**所有向量维度一致**——不一致就直接抛错。
         这一条是底线：宁可入库失败重跑，也不要把坏向量写进索引
         （写进去就是"库看着有数据、检索永远报错"）。
--------------------------------------------------------------------------
"""
from __future__ import annotations

import logging
import time

from langchain_core.embeddings import Embeddings

from config.config import settings
from utils.log import LogUtil

logger = LogUtil.get_logger('embedding')

# 懒加载并缓存的嵌入模型实例
_embedding = None


class OllamaEmbeddingsWithRetry(Embeddings):
    """嵌入调用薄封装：分批 + 重试 + 维度自检，支持两种协议

    - `provider='ollama'`：Ollama 原生 `/api/embed`（本地部署，零成本）
    - `provider='openai'`：任何 OpenAI 兼容的 `/embeddings`（托管服务，部署到
      小内存服务器时用它替代 Ollama，省 1~2GB 内存和全部 CPU）

    只实现 LangChain 需要的两个方法（embed_documents / embed_query），
    因此可以直接塞给 `Chroma(embedding_function=...)`。
    """

    def __init__(self, model: str, base_url: str,
                 batch_size: int = 32, max_retries: int = 3,
                 provider: str = 'ollama', api_key: str = '') -> None:
        self.model = model
        self.base_url = base_url.rstrip('/')
        self.batch_size = max(1, batch_size)
        self.max_retries = max(1, max_retries)
        self.provider = (provider or 'ollama').lower()
        self.api_key = api_key
        self._dim: int | None = None       # 第一次成功后记住维度，后面每次都要对得上
        self._http = None                  # 懒加载的 requests.Session（见 _session）

    # ---------------- 内部 ----------------
    def _session(self):
        """返回复用的 Session；**本地 Ollama 要绕开系统代理**（见 utils/net.py）。

        症状（2026-10-03 踩到）：Windows 上 requests 会读注册表里的系统代理
        （本机 127.0.0.1:65532），代理不转发 localhost → 每次嵌入都 ProxyError +
        ConnectionReset(10054)，看起来像 Ollama 挂了，其实请求没到 Ollama。

        托管服务（provider=openai）不关代理：那边的远程调用**可能真的需要**代理。
        """
        from utils.net import http_session, is_local

        if self._http is None:
            self._http = http_session(self.base_url)
            if is_local(self.base_url):
                logger.debug('本地 Ollama（%s）：已关闭 requests 代理继承', self.base_url)
        return self._http

    def _post(self, inputs: list[str]) -> list[list[float]]:
        """带重试地请求一次嵌入接口（两种协议在下面分支处理）

        Ollama 的 keep_alive 是必须的：不给的话模型可能被卸载，下一次请求又要重新加载，
        在批量入库这种"连续几百次调用"的场景里特别容易撞上 runner 重启。
        """
        is_ollama = self.provider == 'ollama'
        if is_ollama:
            url = f'{self.base_url}/api/embed'
            payload = {'model': self.model, 'input': inputs, 'keep_alive': '30m'}
            headers = {}
        else:
            url = f'{self.base_url}/embeddings'
            payload = {'model': self.model, 'input': inputs}
            headers = {'Authorization': f'Bearer {self.api_key}'}

        session = self._session()
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = session.post(url, json=payload, headers=headers, timeout=180)
                response.raise_for_status()
                body = response.json()
                if is_ollama:
                    vectors = body.get('embeddings') or []
                else:
                    # OpenAI 兼容：{"data": [{"embedding": [...], "index": 0}, ...]}
                    # 按 index 排序，别假设服务端一定按顺序返回
                    items = sorted(body.get('data') or [],
                                   key=lambda item: item.get('index', 0))
                    vectors = [item.get('embedding') or [] for item in items]
                if len(vectors) != len(inputs):
                    raise ValueError(f'嵌入服务返回 {len(vectors)} 条向量，'
                                     f'但请求了 {len(inputs)} 条')
                return vectors
            except Exception as exc:                      # noqa: BLE001
                last_error = exc
                wait = 2 ** attempt                              # 2s / 4s / 8s
                logger.warning('嵌入失败（第 %s/%s 次）：%s；%s 秒后重试',
                               attempt, self.max_retries, exc, wait)
                if attempt < self.max_retries:
                    time.sleep(wait)
        raise RuntimeError(f'嵌入模型调用失败（已重试 {self.max_retries} 次）：{last_error}')

    def _check(self, vectors: list[list[float]], count: int) -> list[list[float]]:
        """入库前的最后一道闸：条数、非空、维度一致

        为什么必须在这里拦：坏向量一旦写进向量库，现象是"库里有数据、检索永远报错"，
        排查成本极高（这次花了一个多小时）。**宁可这里报错重跑。**
        """
        if len(vectors) != count:
            raise ValueError(f'向量条数不对：拿到 {len(vectors)}，期望 {count}')
        dims = {len(v) for v in vectors}
        if 0 in dims:
            raise ValueError('存在空向量（模型返回了空数组），拒绝写入向量库')
        if len(dims) > 1:
            raise ValueError(f'同一次调用返回了不同维度的向量：{sorted(dims)}，拒绝写入向量库')
        dim = dims.pop()
        if self._dim is None:
            self._dim = dim
            logger.info('嵌入维度确定：%s（模型 %s）', dim, self.model)
        elif dim != self._dim:
            raise ValueError(f'向量维度变了：之前 {self._dim}，这次 {dim}；'
                             '换过嵌入模型就必须重建集合')
        return vectors

    # ---------------- LangChain 接口 ----------------
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量嵌入：**分批**发送，单批重试，最后统一自检"""
        cleaned = [(text or '').strip() or '（空文本）' for text in texts]
        out: list[list[float]] = []
        for start in range(0, len(cleaned), self.batch_size):
            batch = cleaned[start:start + self.batch_size]
            vectors = self._post(batch)
            out.extend(self._check(vectors, len(batch)))
            if start and start % (self.batch_size * 4) == 0:
                logger.info('嵌入进度：%s/%s', start, len(cleaned))
        return out

    def embed_query(self, text: str) -> list[float]:
        """单条查询嵌入（检索路径）：同样走重试与自检"""
        vectors = self._post([text or '（空查询）'])
        return self._check(vectors, 1)[0]


class EmbeddingFactory:
    """嵌入模型工厂：**本地 Ollama 或任何 OpenAI 兼容的 /embeddings 服务**

    为什么要做成可切换（2026-10-03，部署到 2 核 / 3.6G 的 ECS 时）：
        Ollama 在服务器上要吃掉 1~2GB 内存 + 全部 CPU，而那台机器只剩 1.7GB 可用；
        查询时每条问题都要嵌入，CPU 版会明显拖慢响应。
        换成托管嵌入（阿里云百炼 / 硅基流动 / 智谱等，只要是 OpenAI 兼容接口）
        就只需一个环境变量，不再依赖 Ollama：
            EMBED_PROVIDER=openai
            EMBED_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
            EMBED_MODEL=text-embedding-v4
            EMBED_API_KEY=sk-xxx

    ⚠️ 换嵌入模型 = 向量维度与语义空间都变了：**必须重建向量库并重跑评估**
       （见 AGENTS.md 约定 5；`_check` 里的维度自检会把旧库当场拦下来）。
    """

    @staticmethod
    def create():
        """构造/缓存嵌入模型；配置缺失时在构造处就报错，别等到检索时才炸"""
        global _embedding
        if _embedding is not None:
            return _embedding

        provider = (settings.EMBED_PROVIDER or 'ollama').strip().lower()
        if provider in ('openai', 'openai_compatible', 'compatible'):
            model = settings.EMBED_MODEL
            base_url = settings.EMBED_BASE_URL
            if not (model and base_url and settings.EMBED_API_KEY):
                raise RuntimeError(
                    'EMBED_PROVIDER=openai 需要同时配置 EMBED_BASE_URL / '
                    'EMBED_MODEL / EMBED_API_KEY（见 .env.docker.example）')
            logger.info('初始化嵌入模型（托管）：%s（%s，批大小 %s）',
                        model, base_url, settings.EMBED_BATCH_SIZE)
            _embedding = OllamaEmbeddingsWithRetry(
                model=model, base_url=base_url,
                batch_size=settings.EMBED_BATCH_SIZE,
                max_retries=settings.EMBED_MAX_RETRIES,
                provider='openai', api_key=settings.EMBED_API_KEY,
            )
            return _embedding

        logger.info('初始化嵌入模型（本地 Ollama）：%s（%s，批大小 %s）',
                    settings.OLLAMA_EMBED_MODEL, settings.OLLAMA_BASE_URL,
                    settings.EMBED_BATCH_SIZE)
        _embedding = OllamaEmbeddingsWithRetry(
            model=settings.OLLAMA_EMBED_MODEL,
            base_url=settings.OLLAMA_BASE_URL,
            batch_size=settings.EMBED_BATCH_SIZE,
            max_retries=settings.EMBED_MAX_RETRIES,
            provider='ollama',
        )
        return _embedding


class OllamaEmbeddingFactory(EmbeddingFactory):
    """旧名字，保留兼容（历史代码与本仓库文档都还在用这个名字）"""


# ---------------- 兼容旧调用 ----------------
get_embedding = EmbeddingFactory.create
