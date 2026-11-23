"""来源可信度分级的离线验证：把"分级有没有说错话"变成可复现的断言。

用法（在 ski-backend 目录下）：
    python -m scripts.test_provenance        # 不需要 API key、不需要 MySQL

为什么要单独一个脚本（而不是并进 test_middleware）：
    "数据可信度分级"是这个项目"反照骗"命题的地基，但它的实现横跨**五处**：
        YAML 档案 → rag/parser.py 解析 → MySQL 列 → Chroma/Evidence 渲染 → 工具话术与提示词
    任何一处漂移，同一份数据在不同地方就会显示成不同的可信度——
    而"我们说自己几手数据"这件事一旦自相矛盾，整个卖点就没了。

它盯三类事：
    1. 档位表在 rag/parser.py 与 agent/evidence.py 两处**逐字一致**（防漂移）
    2. 逐字段印证记录解析正确：一致的、**有分歧的**、我方原档案没有的字段，一条都不能丢
    3. 话术按档位分档：二手转引不许说"官方公布"，多源印证不许说"官方发布"
"""
import sys
from pathlib import Path

# 允许直接以脚本方式运行（python scripts/test_provenance.py）
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from agent.evidence import DATA_STATUS_LABEL as EVIDENCE_LABELS
from agent.tools import (_raw_difficulty_lines, _run_total_lines,
                         _total_sentence)
from config.config import settings
from rag.parser import (CORROBORATION_AGREEMENT, DATA_STATUS_LABEL, DATA_STATUS_VALUES,
                        ResortParser,
                        ResortProvenance, weakest_status)

PASSED = 0
FAILED = 0


def check(name: str, ok: bool, detail: str = '') -> None:
    """打印一条断言结果"""
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f'  ✅ {name}')
    else:
        FAILED += 1
        print(f'  ❌ {name}  {detail}')


def sample_archive() -> dict:
    """一份最小可用的雪场档案：雪道有多源印证、价格只有单一转引

    这正是真实情况的形状（万龙）：**同一座雪场里，不同数据类型是不同档的**。
    用一个雪场级档位去表达它，必然有一类说错。
    """
    return {
        'id': 'demo',
        'name': '示例雪场',
        'region': '某地',
        'provenance': {
            'data_status': 'transcribed',
            'retrieved_at': '2026-09-12',
            'method': '第三方票务聚合页转引',
            'primary_source': '某聚合站《雪道参数》',
            'stated_source': '示例雪场度假天堂（页面自述）',
            'caveat': '官网已失效被抢注，未能官网直采',
            'corroboration': [
                {
                    'field': '雪道总数',
                    'claim': '共 32 条（初级 4 / 中级 9 / 高级 19）',
                    'source': '百度百科「示例雪场」词条',
                    'url': 'https://baike.baidu.com/item/demo',
                    'retrieved_at': '2026-09-12',
                    'stated_source': '词条未标注原始出处',
                    'independence': '两处来源都可能转引自同一篇宣传稿，不构成独立来源',
                    'agreement': 'consistent',
                    'note': '数字一致。',
                },
                {
                    'field': '雪道总数（另一说法）',
                    'claim': '某旅行社页面称 28 条',
                    'source': '某旅行社页面',
                    'retrieved_at': '2026-09-12',
                    'agreement': 'conflict',
                    'independence': '商业推广页，口径不明',
                    'note': '与上面两个来源都不一致，本库因此不对总数下断言。',
                },
                {
                    'field': '垂直落差',
                    'claim': '554 米',
                    'source': '百度百科「示例雪场」词条',
                    'retrieved_at': '2026-09-12',
                    'agreement': 'new_field',
                    'checked_by': '',
                },
            ],
        },
        'source_defaults': {
            'overview': {'source_type': 'A', 'source': '转引自某聚合站', 'date': '2026-09-11'},
            # 雪道有独立来源印证 → 单独提档；价格没有 → 继承雪场默认档
            'run': {'source_type': 'A', 'source': '转引自某聚合站', 'data_status': 'corroborated'},
            'price': {'source_type': 'A', 'source': '转引自某票务页'},
            'provenance': {'source_type': 'C', 'source': '本库来源登记'},
        },
    }


# ---------------- 1. 档位表两处必须逐字一致 ----------------

def test_label_sync():
    print('\n[1] 档位表一致性（parser 与 evidence 两处）')
    check('两处档位表逐字一致', DATA_STATUS_LABEL == EVIDENCE_LABELS,
          f'parser={sorted(DATA_STATUS_LABEL)} evidence={sorted(EVIDENCE_LABELS)}')
    check('含 corroborated 档', 'corroborated' in DATA_STATUS_LABEL)
    check('合法取值列表与档位表同源',
          set(DATA_STATUS_VALUES) == set(DATA_STATUS_LABEL))
    check('旧档位仍在（兼容历史档案）',
          {'verified', 'transcribed', 'sample'} <= set(DATA_STATUS_LABEL))


# ---------------- 2. 保守取值：宁可少说不可多说 ----------------

def test_weakest():
    print('\n[2] 雪场级口径取最保守档（宁可少说，不可多说）')
    check('印证 + 转引 -> 转引',
          weakest_status(['corroborated', 'transcribed']) == 'transcribed')
    check('官方 + 印证 -> 印证',
          weakest_status(['verified', 'corroborated']) == 'corroborated')
    check('混进未知值不参与比较',
          weakest_status(['corroborated', 'unknown']) == 'corroborated')
    check('全是未知值 -> sample', weakest_status(['??', '']) == 'sample')
    check('空集合 -> sample', weakest_status([]) == 'sample')


# ---------------- 3. 可信度按数据类型分开 ----------------

def test_status_map():
    print('\n[3] 可信度按数据类型生效（不是一刀切一个雪场档）')
    prov = ResortProvenance.resolve(sample_archive())
    check('雪道被单独提档为 corroborated',
          ResortProvenance.status_of(prov, 'run') == 'corroborated',
          ResortProvenance.status_of(prov, 'run'))
    check('价格继承雪场默认档 transcribed',
          ResortProvenance.status_of(prov, 'price') == 'transcribed',
          ResortProvenance.status_of(prov, 'price'))
    check('未声明的类型也继承默认档',
          ResortProvenance.status_of(prov, 'lift') == 'transcribed')
    check('未知类型不炸，退回默认档',
          ResortProvenance.status_of(prov, 'nope') == 'transcribed')
    check('status_map 反映的是各类实际档位',
          prov['status_map'].get('run') == 'corroborated'
          and prov['status_map'].get('price') == 'transcribed',
          str(prov['status_map']))
    # 非法档位要兜底，不能把非法值透传到证据里
    bad = sample_archive()
    bad['source_defaults']['run']['data_status'] = 'official-ish'
    prov_bad = ResortProvenance.resolve(bad)
    check('非法档位兜底成雪场默认档',
          ResortProvenance.status_of(prov_bad, 'run') == 'transcribed',
          ResortProvenance.status_of(prov_bad, 'run'))


# ---------------- 4. 印证记录：一致、分歧、新字段，一条都不能丢 ----------------

def test_corroboration():
    print('\n[4] 逐字段印证记录（分歧同样是一等公民）')
    prov = ResortProvenance.resolve(sample_archive())
    entries = prov['corroborations']
    check('三条记录都解析出来了', len(entries) == 3, str(len(entries)))

    by_field = {e['field']: e for e in entries}
    check('一致记录被标成"一致"',
          by_field['雪道总数']['agreement_label'] == CORROBORATION_AGREEMENT['consistent'])
    check('分歧记录被标成"有分歧"',
          by_field['雪道总数（另一说法）']['agreement_label']
          == CORROBORATION_AGREEMENT['conflict'])
    check('我方原档案没有的字段单独标记',
          by_field['垂直落差']['agreement_label']
          == CORROBORATION_AGREEMENT['new_field'])
    check('印证的出处与链接被保留',
          by_field['雪道总数']['url'].startswith('https://')
          and '百度百科' in by_field['雪道总数']['source'])
    check('印证来源自述的原始出处被保留（用来判断是否同源）',
          by_field['雪道总数']['stated_source'] == '词条未标注原始出处')
    check('没复核过的记录如实空着 checked_by',
          by_field['垂直落差']['checked_by'] == '')

    # 非法 agreement 不能透传，兜底成 consistent（宁可保守）
    bad = sample_archive()
    bad['provenance']['corroboration'][0]['agreement'] = 'whatever'
    check('非法 agreement 兜底成 consistent',
          ResortProvenance.resolve(bad)['corroborations'][0]['agreement'] == 'consistent')

    caveats = ' '.join(prov['caveats'])
    check('原文出处与官网失效说明仍在', '官网已失效被抢注' in caveats)
    # 各类档位不一致时，**第一句就按类型说清**——实测模型会把笼统句概括成"一律二手"
    first = prov['caveats'][0]
    check('不一致时第一句就按类型列清（概览 vs 雪道）',
          '各类数据的可信度不一致' in first and '概览' in first and '雪道' in first,
          first[:110])
    check('并明确禁止"整体"式概括',
          '整体都是二手的' in first and '整体都是官方直采的' in first)
    check('该档"别写成官方发布"的措辞仍在',
          any('官方发布' in item for item in prov['caveats']))
    # 元信息（来源登记）不参与"这雪场数据几手的"——踩过两次：模型会把它自己的标签
    # 套到数据上（万龙"一律二手"、北大湖"官网直采被说成二手转引"）
    check('元信息不进 status_map',
          'provenance' not in prov['status_map'], str(prov['status_map']))
    # 注意：允许出现"…见「数据来源登记」"这种**指路**话术，不许出现"来源登记 = 某档"这种**定档**
    check('可信度清单里不给"来源登记"定档',
          '来源登记 = ' not in caveats and '来源登记=' not in caveats,
          next((c for c in prov['caveats'] if '来源登记' in c), '')[:90])
    # 档位一致时不该啰嗦地逐类型罗列（丝绸之路那种：全 transcribed）
    uniform = sample_archive()
    uniform['source_defaults']['run']['data_status'] = 'transcribed'
    check('档位一致时不逐类型罗列，只用笼统句式',
          '各类数据的可信度不一致' not in ' '.join(
              ResortProvenance.resolve(uniform)['caveats']))
    # 印证明细**不塞进概览块**：它可能很长，会把概览顶过阈值（→ 触发挂死的分块器）
    check('印证明细不塞进概览块', '交叉印证｜' not in caveats)

    # 印证记录转成"给模型看的提醒"：生成器同时供来源登记块与库内 note 使用，这里直接查它
    record = ' '.join(ResortProvenance.corroboration_caveats(entries))
    check('分歧写进了给模型看的提醒', '有分歧' in record)
    check('独立性说明写进了提醒（交叉印证 ≠ 独立）', '不构成独立来源' in record)
    check('新字段被说明成"由印证来源补充"', '由印证来源补充' in record)
    check('每条记录都带上印证来源与检索日期',
          '百度百科' in record and '检索于 2026-09-12' in record)


# ---------------- 5. 话术按档位分档 ----------------

def test_wording():
    print('\n[5] 工具话术按档位分档（不许替数据抬高身份）')
    base = {'resort_name': '示例雪场', 'summary_total': 32,
            'summary_counts': {'初级': 4, '中级': 9, '高级': 19}}

    def sentence(status: str) -> str:
        return _total_sentence({**base, 'data_status': status})

    check('verified 才可以说"官方公布"', '官方公布' in sentence('verified'))
    for status in ('corroborated', 'transcribed', 'sample', ''):
        text = sentence(status)
        check(f'{status or "（未标注）"} 不许出现"官方公布"', '官方公布' not in text, text[:40])
    check('corroborated 说明"多个来源相互印证"',
          '相互印证' in sentence('corroborated'))
    check('transcribed 说明"二手转引"', '二手转引' in sentence('transcribed'))
    check('sample 说明"示例占位（未核实）"', '示例占位' in sentence('sample'))
    check('未标注档位时用最保守说法',
          '收录的雪道资料' in sentence('') and '官方' not in sentence(''))

    # 两个口径打架时：**必须把分歧说出来**，不许挑一个报
    conflict = ' '.join(_run_total_lines({**base, 'official_total': 22,
                                          'data_status': 'transcribed'}))
    check('两个口径不一致时同时给出两个数', '22' in conflict and '32' in conflict, conflict[:80])
    check('明确要求把分歧讲给用户',
          '两个口径' in conflict and '不要只报一个数' in conflict)
    check('差额被算出来', '相差 10 条' in conflict, conflict[:80])
    # 差额方向不同，给的推测也必须不同（万龙官网更窄 / 北大湖官网更宽）
    check('官网数更少时说"口径更窄"', '更窄' in conflict)
    wider = ' '.join(_run_total_lines({**base, 'official_total': 74,
                                       'data_status': 'verified'}))
    check('官网数更多时说"口径更宽"', '更宽' in wider and '74' in wider, wider[:80])
    check('两个口径一致时不啰嗦，退回单口径句式',
          len(_run_total_lines({**base, 'official_total': 32,
                                'data_status': 'transcribed'})) == 1)
    check('没有官网口径时也退回单口径句式',
          len(_run_total_lines({**base, 'official_total': None,
                                'data_status': 'transcribed'})) == 1)
    check('两个口径都没有时不输出任何条数断言',
          _run_total_lines({**base, 'official_total': None, 'summary_total': None}) == [])

    # 来源用复合分级（北大湖）时，必须把"归并动作"说出来，否则用户拿官网一对就以为我们在编
    merged = ' '.join(_raw_difficulty_lines({'raw_counts': {'初级道': 3, '初中级道': 4,
                                                           '中级道': 16, '中高级道': 5,
                                                           '高级道': 5, '连接道': 1}}))
    check('复合分级时给出"来源原始级别"清单',
          '来源原始级别' in merged and '中高级道 5 条' in merged, merged[:90])
    check('并说明归并与口径差异', '归并' in merged and '两个口径' in merged)
    check('标准三级时不啰嗦',
          _raw_difficulty_lines({'raw_counts': {'初级': 4, '中级': 9, '高级': 19}}) == [])

    # 只有总数、没有分难度条数时（松花湖），总数不能丢；也不能把"来源的分难度合计"
    # 说成"本库已收录明细"（将军山：明细其实是 0 条）
    only_total = ' '.join(_run_total_lines({'resort_name': '某雪场', 'official_total': 50,
                                            'summary_total': None, 'detail_total': 0}))
    check('只有总数时也要把总数说出来', '50 条' in only_total and '未公布各难度条数' in only_total,
          only_total[:90])
    check('只有总数时明确"不能给分级数字"', '不能给分级数字' in only_total)
    jjs = ' '.join(_run_total_lines({'resort_name': '某雪场', 'official_total': 61,
                                     'summary_total': 48,
                                     'summary_counts': {'初级': 15, '中级': 18, '高级': 15},
                                     'detail_total': 0}))
    check('两个口径打架时区分"来源声明"与"本库明细"',
          '收录明细 0 条' in jjs and '分难度合计为 48 条' in jjs, jjs[:120])
    check('措辞不把百科/第三方说成"官网"', '官网称' not in jjs)


# ---------------- 6. 真实档案体检 ----------------

def test_real_archives():
    print('\n[6] 真实档案体检（data/resorts/*.yaml）')
    files = sorted(Path(settings.RESORT_DIR).glob('*.yaml'))
    files = [f for f in files if not f.name.startswith('_')]
    check('找到雪场档案', len(files) > 0, str(len(files)))

    for path in files:
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        prov = ResortProvenance.resolve(data)
        name = data.get('name', path.stem)

        statuses = set(prov['status_map'].values()) | {prov['data_status']}
        check(f'{name}：所有档位都是合法值',
              statuses <= set(DATA_STATUS_VALUES), str(sorted(statuses)))
        if prov['data_status'] == 'corroborated':
            check(f'{name}：标了多源印证就必须有印证记录',
                  len(prov['corroborations']) > 0)
        for entry in prov['corroborations']:
            check(f'{name}：印证记录「{entry["field"]}」有出处',
                  bool(entry['source']), entry['field'])
        print(f'      · {name}：雪场默认档={prov["data_status"]}｜'
              f'分类档位={prov["status_map"]}｜印证记录 {len(prov["corroborations"])} 条')

        # 生成的文档必须短于分块阈值：一超过就会去构造 langchain_text_splitters，
        # 而那个依赖在本机导入即挂死（docs/TROUBLESHOOTING.md #10），表现为"入库无输出地卡住"
        docs = ResortParser.parse_resort(path.stem)
        longest = max(docs, key=lambda d: len(d.page_content))
        check(f'{name}：所有文档都短于分块阈值（否则会触发挂死的分块器）',
              len(longest.page_content) < settings.RAG_CHUNK_SIZE,
              f'最长 {len(longest.page_content)} 字 / 阈值 {settings.RAG_CHUNK_SIZE}'
              f'（{longest.metadata.get("chunk_id")}）')


# ---------------- 7. 收录范围与未收录名单的一致性 ----------------

def test_coverage_consistency():
    """已收录的雪场不能留在"未收录名单"里，未收录的也不能漏登记

    为什么单列一节（2026-09-13 实测踩到）：这一轮扩雪场时，
    我把"长城岭"从名单里删了，**但并没有真的收录它**（聚合站没有它的参数页）——
    结果是守卫不再拦长城岭，用户问它可能拿到别家雪场的数据。
    而且这种错误**不会报错**，只会静默地把拒答变成误召回。
    """
    print('[7] 收录范围一致性')
    import yaml as _yaml
    from pathlib import Path as _Path
    from config.config import settings as _settings
    from rag.parser import ResortRegistry

    covered = set(ResortRegistry.list_resort_ids())
    covered_names = set(ResortRegistry.name_map())   # 只算**已收录**的名字（别把未收录名单也算进来）
    path = (_Path(_settings.RESORT_DIR).parent / 'uncovered_resorts.yaml').resolve()
    if not path.is_file():
        print('  跳过未收录名单一致性检查：未提供 uncovered_resorts.yaml')
        return
    listed = _yaml.safe_load(path.read_text(encoding='utf-8'))
    missing = [item['name'] for item in (listed or {}).get('resorts') or []
               if item['name'] in covered_names]
    check('未收录名单里没有"其实已收录"的雪场', not missing, str(missing))

    # 反向：名单里的雪场必须确实没有档案
    archive_ids = {p.stem for p in _Path(_settings.RESORT_DIR).glob('*.yaml')
                   if not p.stem.startswith('_')}
    check('档案数与已收录雪场数一致', len(archive_ids) == len(covered),
          f'{sorted(archive_ids)} vs {covered}')
    print(f'      · 已收录 {len(covered)} 座：{sorted(covered)}')
    print(f'      · 未收录名单 {len((listed or {}).get("resorts") or [])} 座（守卫据此拒答）')


def run_suite() -> tuple[int, int]:
    """跑完全部断言，返回 (通过数, 失败数)。

    给 CLI 和 pytest 共用（tests/test_offline_suites.py）：这里**不 sys.exit**，
    否则 pytest 只会看到 SystemExit，而不是一条能定位的失败用例。
    """
    from utils.console import enable_utf8_console
    enable_utf8_console()      # pytest 直接调这里，不经过 main()——控制台编码要自己设
    test_label_sync()
    test_weakest()
    test_status_map()
    test_corroboration()
    test_wording()
    test_real_archives()
    test_coverage_consistency()
    return PASSED, FAILED


def main() -> None:
    from utils.console import enable_utf8_console
    enable_utf8_console()
    print('=' * 64)
    print('数据可信度分级 · 离线验证（不需要 API key / 不需要 MySQL）')
    print('=' * 64)
    _, failed = run_suite()
    print()
    print('=' * 64)
    print(f'通过 {PASSED} 项，失败 {FAILED} 项')
    print('=' * 64)
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
