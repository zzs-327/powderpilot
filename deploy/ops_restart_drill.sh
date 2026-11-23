#!/usr/bin/env bash
# 重启自愈演练：stop/start + down/up，对比前后数据有没有丢
#
# 为什么值得跑：档位 A 是"演示完就 stop，面试前再 start"，
# 而"停了以后起不来 / 起来数据少了一半"这种事，**演示当天发现就晚了**。
#
# 用法（在仓库根目录、已 up 过的情况下）：
#     bash deploy/ops_restart_drill.sh
#
# 判据：第 5 步快照的数字必须与第 1 步**完全一致**。
# 只做 stop/start/down/up，**不带 -v**，所以卷不会被删。
set -u
cd "$(dirname "$0")/.." || exit 1
C="docker compose --env-file .env.docker"
WAIT_SECONDS=${WAIT_SECONDS:-90}

wait_up() {
    local waited=0
    local code=000
    while [ "$waited" -lt "$WAIT_SECONDS" ]; do
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8000/ || true)
        [ "$code" = "200" ] && break
        sleep 3
        waited=$((waited + 3))
    done
    echo "8000 => $code（等待 ${waited}s）"
}

echo '===== 1. 重启前快照 ====='
$C exec -T backend python - < deploy/ops_snapshot.py

echo
echo '===== 2. stop（录完屏要做的动作）====='
$C stop
sleep 3
curl -s -o /dev/null -w 'stop 之后 8000 => %{http_code}（000 = 已停）\n' --max-time 6 http://127.0.0.1:8000/

echo
echo '===== 3. start ====='
$C start
wait_up

echo
echo '===== 4. down + up（模拟整机重启 / 彻底重建容器）====='
$C --profile web down
echo '--- down 后还有 powderpilot 容器吗 ---'
docker ps -a --format '{{.Names}}' | grep powderpilot || echo '（没有了）'
echo '--- 数据卷还在吗 ---'
docker volume ls --format '{{.Name}}' | grep powderpilot || echo '!! 卷不见了'
echo '--- 绑定挂载的向量库 ---'
ls -la ski-backend/chroma/

$C --profile web up -d
wait_up

echo
echo '===== 5. 重启后快照（数字应与第 1 步一致）====='
$C ps --format '{{.Service}}|{{.Status}}'
$C exec -T backend python - < deploy/ops_snapshot.py
