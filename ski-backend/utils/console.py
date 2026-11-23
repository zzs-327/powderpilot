"""控制台编码工具：让脚本在 Windows 中文控制台下也能正常打印。

问题（2026-10-03 实测）：Windows 控制台默认代码页是 GBK，脚本里的 ✅/⚠️/❌
不是 GBK 字符，`print` 会直接抛 UnicodeEncodeError 把整个脚本崩在半路——
现象是"自检跑到一半就报 Traceback"，很容易被当成环境缺依赖。

用法：在脚本入口（`if __name__ == '__main__':` 里第一行）调用一次即可：

    from utils.console import enable_utf8_console
    enable_utf8_console()

sys.stdout 不存在 reconfigure 时（被重定向成别的对象）静默跳过，不影响脚本执行。
"""
from __future__ import annotations

import sys


def enable_utf8_console() -> None:
    """把 Windows 控制台代码页与 stdout / stderr 都切成 UTF-8。

    两步缺一不可（都实测过）：
      1. SetConsoleOutputCP(65001)：控制台按自己的代码页解码我们写出的字节，
         只改 Python 侧的话，936(GBK) 的终端收到 UTF-8 字节会把中文显示成乱码；
      2. reconfigure(encoding='utf-8', errors='replace')：
         否则 ✅/⚠️/❌ 这类字符在 cp936 下直接 UnicodeEncodeError 崩掉脚本；
         errors='replace' 是保险丝——宁可显示成 ?，也不要死在打印上。

    输出被重定向（管道 / 文件）时第 1 步无效也无害，第 2 步保证调用方拿到的
    始终是 UTF-8 字节，管道按 UTF-8 解码即可（本仓库的脚本与 CI 都这样读）。
    """
    _set_console_code_page()
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding='utf-8', errors='replace')
        except (ValueError, OSError):
            # 流已被关闭或不是可重配置的文本流：不影响主流程，忽略
            pass


def _set_console_code_page() -> None:
    """Windows 控制台代码页 → 65001（UTF-8）；非 Windows / 无控制台时静默跳过。"""
    if sys.platform != 'win32':
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
    except Exception:                                        # noqa: BLE001
        pass
