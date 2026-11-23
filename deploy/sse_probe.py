"""经 Nginx 反代验证 SSE 流式（部署后自查用）

为什么需要它：SSE 被 `proxy_buffering` 缓冲时的现象很有迷惑性——**回答不是打字的，
而是等十几秒后一次全冒出来**，看起来像"模型很慢"，其实是反代配置错了。
这个脚本把"事件到达时间"打出来，一眼就能分辨。

用法（在仓库根目录，**在 backend 容器里跑**，同 compose 网络才能用服务名 web）：

    docker compose --env-file .env.docker exec -T backend python - < deploy/sse_probe.py

判据：
    首块很快到（< 1s）+ 正文事件沿时间轴散开  → 没被缓冲，打字机效果成立
    所有事件时间点几乎相同、都挤在最后       → 被缓冲了，检查 proxy_buffering off

环境变量（可选）：`PROBE_BASE`（默认 http://web，即经 nginx）、
`PROBE_USER` / `PROBE_PASSWORD`（必须自行设置，仓库不提供默认口令）。
"""
import json
import os
import time
import urllib.request

BASE = os.environ.get('PROBE_BASE', 'http://web')
USER = os.environ.get('PROBE_USER', 'ski_demo')
PASSWORD = os.environ.get('PROBE_PASSWORD')
QUESTION = os.environ.get('PROBE_QUESTION', '万龙滑雪场有哪些高级道？')


def post(path, payload, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(BASE + path,
                                data=json.dumps(payload).encode('utf-8'),
                                headers=headers)
    return urllib.request.urlopen(req, timeout=300)


def main():
    if not USER or not PASSWORD:
        raise SystemExit('请设置环境变量 PROBE_USER / PROBE_PASSWORD，仓库不提供默认口令')
    token = json.load(post('/api/v1/user/login',
                           {'username': USER, 'password': PASSWORD}))['data']
    print(f'经 {BASE} 登录成功，token 长度 {len(token)}')

    t0 = time.time()
    resp = post('/api/v1/chat/stream', {'message': QUESTION}, token)
    marks = []
    for raw in resp:
        marks.append((time.time() - t0, raw.decode('utf-8', 'replace').strip()))
        if '[DONE]' in marks[-1] or len(marks) > 500:
            break

    if not marks:
        print('❌ 一个事件都没收到')
        return

    print('事件数:', len(marks))
    print('首块到达: %.2fs' % marks[0][0])
    print('前 6 块时间点:', ['%.2f' % m[0] for m in marks[:6]])
    print('末块到达: %.2fs' % marks[-1][0])

    spread = marks[-1][0] - marks[0][0]
    if marks[0][0] < 1.0 and spread > 1.0:
        print('✅ 结论：未被缓冲（首块 %.2fs 到，事件铺开 %.2fs）' % (marks[0][0], spread))
    else:
        print('⚠️ 结论：疑似被缓冲（首块 %.2fs，总铺开 %.2fs）——检查 proxy_buffering off'
              % (marks[0][0], spread))


if __name__ == '__main__':
    main()
