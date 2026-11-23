"""RAG 管线（本项目重点一）。

流程：parser → chunker → embedding → ingest → retriever
   文档(md/pdf) → 解析 → 中文分块 → Ollama 嵌入 → Chroma 入库
   用户提问 → 向量 + BM25 双路召回 → RRF 融合 → metadata 过滤 → 证据列表

对应计划书 v0.4 第四节。实现顺序（Day1）：
    1. parser.py      加载 data/resorts/*.yaml 与手册文本
    2. chunker.py     中文分块，补 metadata
    3. embedding.py   Ollama 嵌入（已实现懒加载工厂）
    4. ingest.py      入库脚本：python -m rag.ingest
    5. retriever.py   混合检索 + RRF + 过滤（Agent 工具的底层）
"""
