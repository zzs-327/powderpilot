"""开源前审查：把"发布前要检查的东西"变成一条可复现的命令。

为什么需要（而不是靠记忆）：
    开源是**不可逆**的——推上公开仓库后，历史会被爬虫、fork、镜像抓走，事后 force push
    也未必删干净。所以每次发布快照前必须机械地扫一遍，而不是"我觉得应该没问题"。

检查六项：
    1. 工作区密钥扫描（行级匹配，只看字面量，避免误报）
    2. git 历史里是否出现过 .env / *.key / *.pem / secret
    3. 将随开源公开的文件清单（按 .publicignore 排除后）+ 疑似内部/个人文档
    4. 导出集合里的本机绝对路径泄漏（C:\\Users\\... / D:\\... / conda3_envs）
    5. 依赖许可证（标出 AGPL/GPL 这类会传染的）
    6. 未跟踪文件提醒（git add -A 会把它们一起带进公开仓库）

⚠️ 第一版的教训（2026-10-03）：初版用"password=<非空白串>"去扫，把 5 处**代码**
误报成明文密码——
    `password=PwdUtil.hash_password(...)` / `password: Mapped[str] = mapped_column(...)`
    / `password=url.password` / `{ username, password }` / `:param hashed_password:`
误报会让这个脚本失去意义（真正的告警会被淹没），所以现在**逐行**匹配，只认：
带引号的字面量、或"看起来像密钥"的裸值（含数字、非全大写常量、非占位符），
并跳过注释与 docstring 行。

用法（在 ski-backend 目录下）：
    python -m scripts.pre_open_source_check
    python -m scripts.pre_open_source_check --quiet     # 只打印问题
"""
from __future__ import annotations

import argparse
import re
import subprocess
from importlib import metadata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEXT_SUFFIX = {'.py', '.md', '.txt', '.yml', '.yaml', '.json', '.ini', '.cfg',
               '.vue', '.js', '.ts', '.mjs', '.html', '.css', '.scss', '.toml', '.ps1', '.sh'}

# 新增的治理/许可证文件：未跟踪是正常的（提交后就没了），不该报到"未跟踪"里
GOVERNANCE_FILES = {'LICENSE', 'DATA_LICENSE.md', 'NOTICE', '.publicignore', 'CONTRIBUTING.md',
                    'ROADMAP.md', 'GOVERNANCE.md', 'SECURITY.md', 'CODE_OF_CONDUCT.md',
                    'CHANGELOG.md', 'DEPENDENCIES.md'}

# 占位符白名单：这些值出现在 .env.example / 文档里是正常的
SAFE_VALUES = {'你的密码', 'changeme', 'change_me', 'placeholder', 'example', 'your_password',
               'password', 'none', 'null', 'true', 'false', 'todo', 'xxx', '******',
               'ci-only-not-a-real-secret'}

SECRET_PATTERNS = [
    (re.compile(r'sk-[A-Za-z0-9]{16,}'), '疑似 API key（sk- 开头）'),
    (re.compile(r'AK=[A-Za-z0-9]{16,}'), '疑似百度 AK'),
    (re.compile(r"""(?i)(?<![A-Za-z_])(password|passwd|pwd)\s*[=:]\s*['"]([^'"\s]{6,})['"]"""),
     '疑似明文密码（字面量）'),
    (re.compile(r'(?i)(?<![A-Za-z_])(password|passwd|pwd)\s*[=:]\s*([A-Za-z0-9_\-]{8,})'),
     '疑似明文密码（裸值）'),
    (re.compile(r"""(?i)secret_key\s*[=:]\s*['"]?([A-Za-z0-9_\-]{12,})"""), '疑似 JWT secret'),
    (re.compile(r'mysql\+pymysql://[^:\s]+:((?!\$)[^@\s]{6,})@'), '连接串里带明文密码'),
]
PATH_PATTERNS = [
    (re.compile(r'[A-Z]:\\Users\\[^\\\s]+'), 'Windows 用户目录绝对路径'),
    (re.compile(r'D:\\conda3_envs'), '本机 conda 环境路径'),
    (re.compile(r'D:\\ollama'), '本机 Ollama 路径'),
]
INTERNAL_HINTS = ('交接', '秋招', '内部', 'private', '面试', '计划书')


def run_git(*args: str) -> str:
    result = subprocess.run(['git', *args], cwd=REPO_ROOT, capture_output=True, text=True)
    return result.stdout


def load_publicignore() -> tuple[list[str], list[str]]:
    """返回 (exclude 前缀列表, sanitize 规则原文)"""
    path = REPO_ROOT / '.publicignore'
    excludes: list[str] = []
    sanitize: list[str] = []
    if not path.exists():
        return excludes, sanitize
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.split('#')[0].strip()
        if not line:
            continue
        if line.startswith('exclude '):
            excludes.append(line[len('exclude '):].strip())
        elif line.startswith('sanitize '):
            sanitize.append(line[len('sanitize '):].strip())
    return excludes, sanitize


def is_excluded(rel: str, excludes: list[str]) -> bool:
    rel = '/' + rel.replace('\\', '/')
    for pattern in excludes:
        if pattern.endswith('/'):
            if rel.startswith(pattern.rstrip('/') + '/'):
                return True
        elif pattern.startswith('/*'):
            if Path(rel).name.endswith(pattern[1:]):
                return True
        elif rel == pattern:
            return True
    return False


def exported_files(excludes: list[str]) -> list[Path]:
    files = []
    for rel in run_git('ls-files').splitlines():
        rel = rel.strip()
        if not rel or is_excluded(rel, excludes):
            continue
        files.append(REPO_ROOT / rel)
    return files


def _is_noise_line(line: str) -> bool:
    """注释 / docstring / 参数说明行：里面的 password 不是密钥"""
    stripped = line.strip()
    if stripped.startswith(('#', '//', '*', ':', '"""', "'''")):
        return True
    return ':param' in stripped or ':return' in stripped


def _looks_like_secret(value: str) -> bool:
    """裸值是否像真密钥：含数字、不是全大写常量引用、不是已知占位符"""
    if value.lower() in SAFE_VALUES:
        return False
    if value.isupper():                     # DEMO_PASSWORD / MY_SECRET 之类是变量引用
        return False
    return any(ch.isdigit() for ch in value)


def _conn_password_is_safe(value: str) -> bool:
    """连接串里的口令是不是"明显不是真密钥"

    只放行两类（判据**故意写窄**）：
      1. CI 里给一次性测试容器设的口令（`ci-` / `test-` / `example-` 开头）——
         容器随流水线销毁，碰不到任何真实数据（见 .github/workflows/ci.yml）
      2. 变量引用（`${MYSQL_ROOT_PASSWORD}`）——正则已经排除了 `$`，这里再兜一次

    刻意**不用**"没有数字就不算密码"这种宽松判据：那会放过 `SuperSecret` 这类真口令。
    """
    lowered = (value or '').lower()
    return lowered.startswith(('ci-', 'test-', 'example-', '$'))


def check_secrets(files: list[Path]) -> list[str]:
    problems = []
    for path in files:
        if path.suffix.lower() not in TEXT_SUFFIX or not path.exists():
            continue
        for number, raw in enumerate(path.read_text(encoding='utf-8', errors='ignore')
                                     .splitlines(), start=1):
            if _is_noise_line(raw):
                continue
            for pattern, label in SECRET_PATTERNS:
                for match in pattern.finditer(raw):
                    value = match.group(match.lastindex) if match.lastindex else match.group(0)
                    if label == '连接串里带明文密码' and _conn_password_is_safe(value):
                        continue
                    if label in {'疑似明文密码（字面量）', '疑似明文密码（裸值）', '疑似 JWT secret',
                                 '连接串里带明文密码'}:
                        if value.lower() in SAFE_VALUES:
                            continue
                    if label == '疑似明文密码（裸值）' and not _looks_like_secret(value):
                        continue
                    problems.append(f'{path.relative_to(REPO_ROOT)}:{number} {label}：'
                                    f'{value[:24]}')
    return problems


def check_history() -> list[str]:
    names = run_git('log', '--all', '--full-history', '--name-only', '--pretty=format:')
    hits = set()
    for name in names.splitlines():
        name = name.strip()
        if not name:
            continue
        if name.endswith('.env') or name.endswith('.key') or name.endswith('.pem') \
                or 'secret' in name.lower():
            hits.add(name)
    return sorted(hits)


def check_untracked() -> list[str]:
    out = run_git('status', '--porcelain')
    items = []
    for line in out.splitlines():
        if not line.startswith('??'):
            continue
        name = line[3:].strip().strip('"')
        if Path(name).name in GOVERNANCE_FILES or name.endswith('pre_open_source_check.py'):
            continue
        items.append(name)
    return items


def check_paths(files: list[Path]) -> list[str]:
    problems = []
    for path in files:
        if path.suffix.lower() not in TEXT_SUFFIX or not path.exists():
            continue
        text = path.read_text(encoding='utf-8', errors='ignore')
        for pattern, label in PATH_PATTERNS:
            found = pattern.search(text)
            if found:
                problems.append(f'{path.relative_to(REPO_ROOT)} {label}：{found.group(0)[:40]}')
    return problems


def check_licenses() -> list[str]:
    req = REPO_ROOT / 'ski-backend' / 'requirements.txt'
    if not req.exists():
        return []
    flagged = []
    for line in req.read_text(encoding='utf-8').splitlines():
        line = line.split('#')[0].strip()
        if not line or line.startswith('-'):
            continue
        name = re.split(r'[<>=!\[;]', line)[0].strip()
        if not name:
            continue
        try:
            meta = metadata.metadata(name)
        except metadata.PackageNotFoundError:
            continue
        declared = (meta.get('License-Expression') or meta.get('License') or '')
        if re.search(r'AGPL|GPL', declared, re.I):
            flagged.append(f'{name}：{declared.strip()[:70]}')
    return flagged


def main() -> int:
    from utils.console import enable_utf8_console
    enable_utf8_console()

    parser = argparse.ArgumentParser(description='开源前审查')
    parser.add_argument('--quiet', action='store_true', help='只打印问题')
    args = parser.parse_args()

    def say(*text) -> None:
        if not args.quiet:
            print(*text)

    excludes, sanitize = load_publicignore()
    files = exported_files(excludes)
    problems = 0      # 阻断项：必须处理完才能公开
    warnings = 0      # 提示项：导出脚本会自动处理（如本机路径替换）

    say(f'仓库根目录：{REPO_ROOT}')
    say(f'将随开源公开的文件：{len(files)} 个（.publicignore：{len(excludes)} 条 exclude，'
        f'{len(sanitize)} 条 sanitize）')

    say('\n[1] 工作区密钥扫描（行级、只认字面量）')
    secrets = check_secrets(files)
    problems += len(secrets)
    if secrets:
        for item in secrets[:20]:
            print('  ⚠️ ' + item)
    else:
        say('  ✅ 无命中')

    say('\n[2] git 历史里的敏感文件')
    history = check_history()
    problems += len(history)
    if history:
        for item in history[:20]:
            print('  ⚠️ 历史里出现过：' + item)
    else:
        say('  ✅ 历史里从未提交过 .env / *.key / *.pem / secret')

    say('\n[3] 疑似内部/个人文档（这些会公开！）')
    internal = [p.relative_to(REPO_ROOT).as_posix() for p in files
                if any(hint in p.name for hint in INTERNAL_HINTS)]
    if internal:
        problems += len(internal)
        for item in internal[:20]:
            print('  ⚠️ ' + item)
    else:
        say('  ✅ 没有命中内部文档关键词')

    say('\n[4] 导出集合里的本机路径泄漏（**由导出脚本按 .publicignore 的 sanitize 规则替换，属可接受**）')
    paths = check_paths(files)
    warnings += len(paths)
    if paths:
        for item in paths[:20]:
            print('  · ' + item + ' → 导出时替换')
    else:
        say('  ✅ 没有本机绝对路径')

    say('\n[5] 依赖许可证风险（AGPL/GPL 会传染）')
    licenses = check_licenses()
    if licenses:
        problems += len(licenses)
        for item in licenses:
            print('  ⚠️ ' + item)
        print('  → 处置：移出核心依赖（requirements-optional.txt）或换等效库')
    else:
        say('  ✅ 核心依赖没有 AGPL/GPL')

    say('\n[6] 未跟踪文件（git add -A 会把它们带进公开仓库）')
    untracked = check_untracked()
    if untracked:
        for item in untracked[:20]:
            print('  ⚠️ ' + item)
    else:
        say('  ✅ 没有需要留意的未跟踪文件')

    print('\n' + '=' * 60)
    if problems:
        print(f'结论：❌ 有 {problems} 项阻断问题，先别公开'
              + (f'（另有 {warnings} 项导出时自动替换）' if warnings else ''))
    else:
        print('结论：✅ 可以公开'
              + (f'（有 {warnings} 项本机路径会在导出时替换为占位符）' if warnings else ''))
    print('=' * 60)
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
