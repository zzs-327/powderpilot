"""本机服务的 HTTP 工具：**绕开系统代理**。

为什么需要（2026-10-03 实测踩到）：
    Windows 上 `requests` 和 `urllib` 都不只看环境变量，还会去读注册表里的 WinINET
    代理设置（本机是 `127.0.0.1:65532`）。代理软件通常不转发 localhost，于是每一次
    本地 Ollama 调用都抛 ProxyError / ConnectionResetError(10054)：

        HTTPConnectionPool(host='127.0.0.1', port=65532): Max retries exceeded ...
        POST http://localhost:11434/api/embed

    现象极具误导性——`check_env` 报"Ollama 连不上"，`rag.ingest` / `evaluate` 重试三次后
    报"嵌入模型调用失败"，看日志像 Ollama 挂了，其实请求压根没到 Ollama。

规则：只对 localhost / 127.0.0.1 / ::1 / 0.0.0.0 关掉代理继承，
      远程地址（智谱、DeepSeek 等）照旧走系统代理——那边可能真的需要代理。
"""
from __future__ import annotations

from urllib.parse import urlparse

LOCAL_HOSTS = {'localhost', '127.0.0.1', '::1', '0.0.0.0'}


def is_local(url: str) -> bool:
    """URL 是否指向本机（服务在本机时不该走代理）。"""
    return (urlparse(url).hostname or '').lower() in LOCAL_HOSTS


def http_session(url: str):
    """返回 requests.Session；目标是本机时关闭代理继承（trust_env）。"""
    import requests

    session = requests.Session()
    if is_local(url):
        session.trust_env = False
    return session


def url_opener(url: str):
    """返回 urllib 的 opener；目标是本机时不带任何代理处理器。

    标准库路径也要单独处理：urlopen 同样会读系统代理，症状与 requests 一致。
    """
    import urllib.request

    if is_local(url):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()
