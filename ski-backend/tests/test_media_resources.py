import pytest

from pathlib import Path
import yaml
from service import media_service


def test_media_catalog_filters_unverified_and_unsafe(tmp_path, monkeypatch):
    monkeypatch.setattr(media_service, 'RESORT_DIR', tmp_path)
    base = dict(title='视频栏目', kind='video', url='https://resort.example/videos',
                source='官网', checked_at='2026-10-07', verification='page_checked',
                rights='link_only', notice='未观看视频，不代表实时雪况')
    bad_url = {**base, 'url': 'javascript:alert(1)'}
    unchecked = {**base, 'verification': 'candidate'}
    (tmp_path / 'test.yaml').write_text(yaml.safe_dump({'media': [bad_url, unchecked, base, base]}), encoding='utf-8')
    result = media_service.media_for_resorts(['../escape', 'test'])
    assert len(result) == 1
    assert result[0].resort_id == 'test'
    assert result[0].verification == 'page_checked'
    assert result[0].published_at == '未注明'


def test_media_empty_without_curated_items():
    assert media_service.media_for_resorts(['unknown']) == []


@pytest.mark.local_data('resorts/taiwu.yaml', 'resorts/songhuahu.yaml', 'graph/graph.json')
def test_real_media_catalog_attaches_to_resort_guidance():
    from langchain_core.messages import HumanMessage
    from service.guidance_service import build_guidance
    for question, kind in [('太舞有哪些雪道', 'video'), ('松花湖有哪些雪道', 'photo')]:
        guidance = build_guidance({'messages': [HumanMessage(content=question)]})
        assert guidance['media']
        item = guidance['media'][0]
        assert item['kind'] == kind
        assert item['verification'] == 'page_checked'
        assert item['rights'] == 'link_only'
        assert '未' in item['notice']


def _base(**kwargs):
    return dict(title='原页', kind='video', url='https://example.com/page',
                source='官网', checked_at='2026-10-07', verification='page_checked',
                rights='link_only', notice='非实时', **kwargs)


def test_detail_all_chat_three_and_cross_resort_dedup(tmp_path, monkeypatch):
    monkeypatch.setattr(media_service, 'RESORT_DIR', tmp_path)
    items = [{**_base(), 'url': f'https://example.com/page/{i}'} for i in range(6)]
    for name in ('test', 'other'):
        (tmp_path / f'{name}.yaml').write_text(yaml.safe_dump({'media': items}), encoding='utf-8')
    assert len(media_service.media_for_resort('test')) == 6
    assert len(media_service.media_for_resorts(['test', 'other'])) == 3


def test_chat_media_follows_the_question(tmp_path, monkeypatch):
    monkeypatch.setattr(media_service, 'RESORT_DIR', tmp_path)
    items = [
        {**_base(), 'title': '官方岗梁道雾凇', 'source': '官方账号', 'url': 'https://example.com/1'},
        {**_base(), 'title': 'D9 一镜到底', 'source': '个人作者，非官方', 'url': 'https://example.com/2',
         'notice': 'D9 是中高级道，新手不要照着滑'},
        {**_base(), 'title': '星光大道一镜到底', 'url': 'https://example.com/3'},
        {**_base(), 'title': '夜场青云大道', 'url': 'https://example.com/4'},
        {**_base(), 'kind': 'map', 'title': '雪道图', 'url': 'https://example.com/5'},
    ]
    runs = [{'name': 'H6岗梁道'}, {'name': 'D9'}, {'name': '星光大道'}, {'name': 'D14青云大道'}]
    (tmp_path / 'test.yaml').write_text(yaml.safe_dump({'media': items, 'runs': runs}, allow_unicode=True),
                                        encoding='utf-8')
    titles = lambda q, **kw: [r.title for r in media_service.media_for_resorts(['test'], query=q, **kw)]
    assert titles('第一次去新手滑哪条')[0] == '星光大道一镜到底'
    assert 'D9 一镜到底' not in titles('第一次去新手滑哪条')
    assert titles('夜场能滑什么')[0] == '夜场青云大道'
    assert titles('D9难不难')[0] == 'D9 一镜到底'
    assert titles('青云大道怎么样')[0] == '夜场青云大道'
    assert titles('有雪道图吗')[0] == '雪道图'
    assert titles('哪里看雾凇')[0] == '官方岗梁道雾凇'
    assert [item.kind for item in media_service.media_for_query('test', '有什么推荐视频')] == ['video', 'video', 'video', 'video']
    assert media_service.media_for_query('test', '有雪道图吗')[0].kind == 'map'
    assert media_service.media_for_query('test', '雪票多少钱') == []
    assert titles('') == ['官方岗梁道雾凇', 'D9 一镜到底', '星光大道一镜到底']


def test_malformed_yaml_shapes_do_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(media_service, 'RESORT_DIR', tmp_path)
    for text in ('[', '- scalar', 'media: null', 'media: string', 'media: {}',
                 'media: [null, 123, string, []]', '!!python/object:bad {}'):
        (tmp_path / 'test.yaml').write_text(text, encoding='utf-8')
        assert media_service.media_for_resort('test') == []
    assert media_service.media_for_resort('../escape') == []
    assert media_service.media_for_resort(None) == []


def test_schema_display_gates_and_video_metadata():
    from schemas.guidance import MediaResource
    for rights, verification in [('link_only', 'content_checked'), ('authorized', 'page_checked')]:
        item = MediaResource.model_validate({**_base(), 'resort_id': 'test',
            'rights': rights, 'verification': verification,
            'image_url': 'https://example.com/image.jpg', 'recommendation': '适合初学者'})
        assert item.image_url is None
        if verification == 'page_checked':
            assert item.recommendation is None
    valid = {**_base(), 'resort_id': 'test', 'rights': 'authorized',
             'verification': 'content_checked', 'image_url': 'https://example.com/image.jpg',
             'category': 'cover', 'credit': '摄影师', 'author': '教练',
             'board': 'ski', 'level': 'beginner', 'topic': '转弯', 'duration': '3:20',
             'recommendation': '已人工观看'}
    item = MediaResource.model_validate(valid)
    assert str(item.image_url) == valid['image_url']
    assert item.recommendation == '已人工观看'
    assert MediaResource.model_validate({**valid, 'image_url': valid['url']}).image_url is None
    assert MediaResource.model_validate({**valid, 'kind': 'photo'}).recommendation is None


def test_unsafe_urls_and_candidates_are_filtered(tmp_path, monkeypatch):
    monkeypatch.setattr(media_service, 'RESORT_DIR', tmp_path)
    bad = ['http://example.com/a', 'javascript:alert(1)', 'https://user:pass@example.com/a',
           'https://example.com/a b', 'https://example.com/\\bad', 'https://example.com/a\n']
    items = [{**_base(), 'url': url} for url in bad]
    items += [{**_base(), 'verification': 'candidate'},
              {**_base(), 'image_url': 'http://example.com/image.jpg'}, _base()]
    (tmp_path / 'test.yaml').write_text(yaml.safe_dump({'media': items}), encoding='utf-8')
    assert len(media_service.media_for_resort('test')) == 1
