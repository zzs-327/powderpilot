"""私有仓库 → 公开快照导出（双仓工作流的核心）。

为什么需要：
    私有仓库里有**不能公开**的东西（private/交接说明含本机路径与数据库账号、
    个人求职文档、内部策略文档、运行期产物），而公开仓库（powderpilot）要拿去
    报名比赛、给评委和企业看。人工挑文件必然漏——漏一个就是不可逆事故。
    所以公开侧只接收"按 .publicignore 白名单导出的快照"，物理隔离。

做法（每一步都刻意选择最保守的）：
    1. 源文件集合 = git 已跟踪 + 未忽略的新文件（新写的治理文件也能进快照）
    2. 按 .publicignore 的 exclude 过滤；未匹配任何 exclude 的默认导出
    3. 文本文件按 sanitize 规则把本机路径/账号替换成占位符
    4. **不清空目标目录以外的东西**：每次导出前删掉整个导出目录，保证快照是"当前的"
    5. 导出后再扫一遍残留的本机路径（复用 pre_open_source_check 的模式），有残留就报错

用法（在 ski-backend 目录下）：
    python -m scripts.export_public                     # 导出到 <仓库根>/.public-export
    python -m scripts.export_public -o D:/tmp/powderpilot-public
    python -m scripts.export_public --yes               # 目标目录非空时直接覆盖

导出之后（推公开仓库）：
    cd <导出目录>
    git init && git add -A && git commit -m "chore: 首次开源快照"
    git remote add origin <powderpilot 仓库地址>
    git push -u origin main
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / '.public-export'
TEXT_SUFFIX = {'.py', '.md', '.txt', '.yml', '.yaml', '.json', '.ini', '.cfg', '.toml',
               '.vue', '.js', '.ts', '.mjs', '.html', '.css', '.scss', '.ps1', '.sh',
               '.example', '.gitignore', '.dockerignore', '.publicignore', '.editorconfig'}
BINARY_SUFFIX = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.pdf', '.zip', '.npy', '.db'}


def run_git(*args: str) -> str:
    result = subprocess.run(['git', *args], cwd=REPO_ROOT, capture_output=True, text=True)
    return result.stdout


def load_rules() -> tuple[list[str], list[tuple[re.Pattern, str]]]:
    """从 .publicignore 读 (exclude 列表, sanitize 规则列表)"""
    excludes: list[str] = []
    sanitize: list[tuple[re.Pattern, str]] = []
    path = REPO_ROOT / '.publicignore'
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('exclude '):
            excludes.append(line[len('exclude '):].strip())
        elif line.startswith('sanitize '):
            rule = line[len('sanitize '):]
            if '=>' not in rule:
                continue
            pattern, replacement = rule.split('=>', 1)
            try:
                sanitize.append((re.compile(pattern.strip()), replacement.strip()))
            except re.error as exc:                     # 规则写错就直接报出来，别静默跳过
                raise SystemExit(f'.publicignore 里的 sanitize 正则不合法：{pattern.strip()}（{exc}）')
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


def source_files(excludes: list[str]) -> list[str]:
    """git 已跟踪 + 未忽略的新文件（排除 .publicignore 命中的）"""
    out = run_git('ls-files', '--cached', '--others', '--exclude-standard')
    files = []
    for rel in out.splitlines():
        rel = rel.strip()
        if not rel or is_excluded(rel, excludes):
            continue
        files.append(rel)
    return files


def export(out_dir: Path, force: bool = False) -> int:
    excludes, sanitize = load_rules()
    rels = source_files(excludes)

    if out_dir.exists():
        if not force:
            # 默认就覆盖：导出目录是"生成物"，不是工作区；但先打印一句让人知道删了什么
            print(f'导出目录已存在，先清空：{out_dir}')
        # ⚠️ 保住 .git：公开仓库就在这个目录里长期维护（每次导出只是更新文件，
        #    然后 git add/commit/push）。如果连 .git 一起删，每次导出都要重新 init+推全量。
        keep_git = out_dir / '.git'
        backup_git = None
        if keep_git.exists():
            backup_git = out_dir.parent / '.public-export-git-backup'
            if backup_git.exists():
                shutil.rmtree(backup_git)
            shutil.move(str(keep_git), str(backup_git))
        shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True)
        if backup_git is not None:
            shutil.move(str(backup_git), str(out_dir / '.git'))
            print('已保留公开仓库的 .git（历史不会被导出清掉）')
    else:
        out_dir.mkdir(parents=True)

    copied = sanitized_files = total_replacements = 0
    for rel in rels:
        src = REPO_ROOT / rel
        dst = out_dir / rel
        if not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        suffix = src.suffix.lower()
        name = src.name.lower()

        if suffix in BINARY_SUFFIX:
            shutil.copy2(src, dst)
            copied += 1
            continue

        if suffix in TEXT_SUFFIX or name in TEXT_SUFFIX or src.suffix == '':
            text = src.read_text(encoding='utf-8', errors='ignore')
            if rel == '.publicignore':
                # 规则文件本身不脱敏：否则规则文本会被自己的规则改写成
                # `sanitize <你的用户名> => <你的用户名>`，公开出去反而看不懂
                # （用户名本来就会出现在仓库 URL 里，不算泄漏）
                dst.write_text(text, encoding='utf-8', newline='\n')
                copied += 1
                continue
            hits = 0
            for pattern, replacement in sanitize:
                text, count = pattern.subn(replacement, text)
                hits += count
            dst.write_text(text, encoding='utf-8', newline='\n')
            copied += 1
            if hits:
                sanitized_files += 1
                total_replacements += hits
                print(f'  脱敏 {rel}：{hits} 处')
        else:
            shutil.copy2(src, dst)
            copied += 1

    print(f'\n导出完成：{copied} 个文件 → {out_dir}')
    print(f'其中 {sanitized_files} 个文件做了脱敏，共替换 {total_replacements} 处')

    # 导出后自查：快照里不该再出现本机路径
    from scripts.pre_open_source_check import PATH_PATTERNS

    leftovers = []
    for path in out_dir.rglob('*'):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIX:
            continue
        text = path.read_text(encoding='utf-8', errors='ignore')
        for pattern, label in PATH_PATTERNS:
            found = pattern.search(text)
            if found:
                leftovers.append(f'{path.relative_to(out_dir)} {label}：{found.group(0)[:40]}')

    if leftovers:
        print('\n⚠️ 快照里仍有本机路径残留（sanitize 规则没覆盖到）：')
        for item in leftovers[:20]:
            print('  ' + item)
        return 1

    print('✅ 快照自查通过：没有本机路径残留')
    print('\n下一步（把快照推到公开仓库；首次需要先建空仓库并配远端）：\n'
          f'  cd "{out_dir}"\n'
          '  git add -A && git commit -m "chore: 同步公开快照"\n'
          '  git push          # 若已配好双 remote（Gitee + GitHub），一条命令两边都更新')
    return 0


def main() -> None:
    from utils.console import enable_utf8_console
    enable_utf8_console()

    parser = argparse.ArgumentParser(description='导出可公开的快照（按 .publicignore 过滤 + 脱敏）')
    parser.add_argument('-o', '--out', default=str(DEFAULT_OUT), help='导出目录')
    parser.add_argument('--yes', action='store_true', help='目标目录存在时不再提示（脚本化用）')
    args = parser.parse_args()

    out_dir = Path(args.out).resolve()
    if REPO_ROOT in out_dir.parents and out_dir.name != '.public-export':
        # 导进仓库内部的其他目录容易误提交，提示一句
        print(f'⚠️ 导出目录在仓库内部：{out_dir}（记得加进 .gitignore）')
    raise SystemExit(export(out_dir, force=True if args.yes else True))


if __name__ == '__main__':
    main()
