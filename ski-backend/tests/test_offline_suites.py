"""把三个确定性离线验证套件接进 pytest。

它们本来就是"机制验证"脚本（假模型 / 假 agent / 纯解析，不花钱、不出网），
以前只能手工跑、容易忘记；接进 pytest 后 `python -m pytest` 一次全包。

脚本本身仍是可独立运行的 CLI（python -m scripts.test_middleware 等），
这里复用的是它们新暴露的 run_suite()：返回 (通过数, 失败数)，不 sys.exit——
否则 pytest 会把"有断言失败"报成 SystemExit，而不是一条能定位的失败用例。
"""
import pytest

import asyncio

from scripts import test_chat_stream, test_middleware, test_provenance


pytestmark = pytest.mark.usefixtures("test_registry")

def test_provenance_suite():
    """数据可信度分级：档位表两处逐字一致 / 印证记录解析 / 话术分档。"""
    passed, failed = test_provenance.run_suite()
    assert failed == 0, f'可信度分级断言失败 {failed} 项（通过 {passed}）'
    assert passed > 0


@pytest.mark.local_data('resorts/wanlong.yaml', 'resorts/beidahu.yaml', 'uncovered_resorts.yaml')
def test_middleware_suite(requires_db):
    """引用校验中间件：编造数字、引用不存在的编号、跨轮回看、裁剪兜底。

    ⚠️ 这一套**需要 MySQL**（2026-10-03 实测）：它用假模型驱动**真实 Agent 图**，
    工具会真的查库（`get_resort_price` → MySQL）。库不可用时它会抛
    `OperationalError: Access denied`，看着像项目坏了，其实只是环境没准备好。
    所以这里接 requires_db：没库就跳过——"环境缺失"和"断言失败"是两件事。
    """
    passed, failed = asyncio.run(test_middleware.run_suite())
    assert failed == 0, f'中间件断言失败 {failed} 项（通过 {passed}）'
    assert passed > 0


def test_lookup_recovery_suite():
    """漏调工具时执行本轮补查；不依赖数据库和外部模型。"""
    passed, failed = asyncio.run(test_middleware.run_lookup_recovery_suite())
    assert failed == 0, f'雪场补查断言失败 {failed} 项（通过 {passed}）'
    assert passed > 0


def test_stream_protocol_suite():
    """流式事件协议：正常结束 / 引用打回发 reset / 内部前缀不外露。

    注意：这个脚本会把 `AgentService.aget_agent` 换成假 agent（模块级打桩），
    所以它跑完之后别在同一个进程里再测真实的对话路径。
    """
    assert test_chat_stream.main() == 0
