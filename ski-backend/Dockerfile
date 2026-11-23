# PowderPilot 后端镜像
#
# 为什么是 python:3.12 而不是本机的 3.14：
#   chromadb / onnxruntime / cryptography 这些带二进制扩展的包，3.12 的 manylinux wheel
#   覆盖最全；3.14 太新，wheel 可能缺，镜像构建时容易退化成源码编译（慢且要 build-essential）。
#   本机跑 3.14 没问题，但**镜像里用稳定版本**是更省事的选择。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 依赖单独一层：requirements 不变就不会重复安装（构建缓存）
# 用国内镜像源：ECS 上直连 pypi.org 经常很慢
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
        -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY . .

# 运行期目录（compose 里挂卷）：容器重建也不丢向量库/会话记忆
RUN mkdir -p /app/chroma /app/logs /app/state

EXPOSE 8000

# 直接跑 uvicorn 而不是 python main.py：main.py 会按 DEBUG 开 reload（容器里要关掉）
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
