"""百度地图 MCP 接入层：把外部 MCP 工具变成"带证据信封"的项目能力。

对应计划书 v0.4 的外部数据源部分。MCP 文档见仓库根目录 MCP.md。

为什么不能把 MCP 工具直接塞给 Agent（两件事都是实测踩出来的）：

    1. **证据缺失会被自己的校验误杀**
       MCP 工具返回纯 JSON 文本，里面没有 [Ex] 证据头；而项目的引用校验要求
       "回答里的数字必须能在本轮证据里找到"。拿不到证据头 → 白名单为空 →
       走"调了工具却一无所获"分支 → 正常回答被打回重写、甚至降级成"暂无可靠来源"。
       实测：'崇礼明天多云，白天 22 度…过路费约 364 元' 会被判为无依据。

    2. **模型用不对原始工具**
       map_weather 要 6 位行政区划码（district_id），模型不知道崇礼区是 130709；
       地图类接口的参数体系是给开发者设计的，不是给模型设计的。

所以这一层做三件事：
    1. 参数适配：把"崇礼"这种人类说法翻译成接口要的 adcode / 经纬度
    2. 输出适配：把原始 JSON 摘成中文可读文本（顺带砍掉大半 token）
    3. 证据适配：包成 [E1] (A 官方｜百度地图 MCP｜数据更新时间) 的标准信封

对外只暴露 3 个语义清晰的工具（见 agent/tools.py），而不是 14 个地图接口。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import re

from config.config import settings
from config.exceptions import ServiceException
from agent.evidence import Evidence, format_block

# 懒加载并缓存的 MCP 工具表 {工具名: langchain 工具对象}
_mcp_tools: dict | None = None

# 行政区划解析结果缓存 {城市名: adcode}——同一个雪场会反复问天气，没必要每次查
_adcode_cache: dict[str, str] = {}


class McpEvidence:
    """证据适配：把 MCP 的原始返回包成项目标准证据信封"""

    # 天气接口的 uptime 是 14 位时间戳，如 "20260912145000"
    _UPTIME = re.compile(r'"uptime"\s*:\s*"(\d{14})"')
    # 兜底：返回里任何 YYYY-MM-DD
    _ISO_DATE = re.compile(r'"(\d{4}-\d{2}-\d{2})')
    # 数据版本号，也是溯源信息（如 data_version: "20260717"）
    _VERSION = re.compile(r'"data_version"\s*:\s*"(\d+)"')

    @staticmethod
    def stamp(text: str) -> str:
        """从返回内容里抠出"数据是什么时候的"

        为什么重要：天气是会过期的东西，取证时必须带时间。
        抠不到就用今天，绝不空着——空着等于没法判断新鲜度。
        """
        match = McpEvidence._UPTIME.search(text or '')
        if match:
            raw = match.group(1)
            return f'{raw[0:4]}-{raw[4:6]}-{raw[6:8]}'
        match = McpEvidence._ISO_DATE.search(text or '')
        if match:
            return match.group(1)
        return dt.date.today().isoformat()

    @staticmethod
    def version_note(text: str) -> str:
        """有数据版本号就带上，便于日后复核"""
        match = McpEvidence._VERSION.search(text or '')
        return f'（数据版本 {match.group(1)}）' if match else ''

    @staticmethod
    def wrap(content: str, *, source: str, date: str = '',
             doc_type: str = 'external') -> str:
        """包成标准证据信封，返回带 [E1] 编号的文本

        编号从 E1 开始即可：上层中间件 evidence_envelope 会按工具把它
        平移进专属编号段（map_* 系列在 E5xx），保证全轮唯一。
        """
        return format_block([Evidence(
            content=content,
            source_type='A',
            source=source,
            date=date or McpEvidence.stamp(content),
            doc_type=doc_type,
        )])


class BaiduMapMcp:
    """百度地图 MCP 客户端：懒加载工具、按名调用、参数与输出适配"""

    # ---------------- 连接与调用 ----------------
    @staticmethod
    async def tools() -> dict:
        """加载并缓存 MCP 工具表（首次调用会联网）"""
        global _mcp_tools
        if _mcp_tools is not None:
            return _mcp_tools

        if not settings.BAIDU_MAP_AK:
            raise ServiceException(
                '未配置 BAIDU_MAP_AK：请在 ski-backend/.env 里填写百度地图开放平台的 AK')

        # 懒加载 MCP 客户端，避免影响无关模块导入
        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient({
            'baidu_map': {
                'transport': 'http',
                'url': f'https://mcp.map.baidu.com/mcp?ak={settings.BAIDU_MAP_AK}',
            }
        })
        loaded = await client.get_tools()
        _mcp_tools = {tool.name: tool for tool in loaded}
        return _mcp_tools

    @staticmethod
    def _text_of(result) -> str:
        """把 MCP 工具返回值归一成纯文本

        ⚠️ 踩过的坑：MCP 工具返回的**不是字符串**，而是内容块列表
        [{'type': 'text', 'text': '...'}]。直接 str() 会拿到 Python repr，
        后面所有 JSON 解析都会失败——而且失败得很安静：
        行政区划解析不出来 → 天气查不了，但代码不报错，只是"什么都查不到"。
        """
        if isinstance(result, str):
            return result
        if isinstance(result, list):
            parts: list[str] = []
            for block in result:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and isinstance(block.get('text'), str):
                    parts.append(block['text'])
            return '\n'.join(parts)
        return str(result)

    @staticmethod
    async def call(name: str, arguments: dict) -> str:
        """调用一个 MCP 工具，返回纯文本

        失败时抛异常而不是返回错误字符串：中间件里挂了 ToolRetryMiddleware，
        网络抖动这类"重试就好"的故障交给它退避重试，重试耗尽后
        由它把错误变成一条工具消息交回模型，不会打断整轮。

        ⚠️ 必须有超时（2026-10-04 加）：实测"排行程"那题有一次调了
        `map_weather` / `map_district_search` 之后烧穿 recursion_limit=40
        —— 外部 HTTP 工具一旦慢/挂住，`ToolRetryMiddleware` 还会按指数退避重试，
        **时间与图步数一起被吃掉**，用户侧表现就是"回答卡住"。
        超时让"慢"变成"快速失败"，失败的处置交给重试/降级，别再拖住整轮。
        """
        timeout = settings.MCP_TIMEOUT_SECONDS
        # 首次调用要先去 MCP 服务器拉工具列表，这一步同样是外部 HTTP——
        # 实测（把超时压到 1ms）它也会超时，所以**必须单独兜住**，
        # 否则抛出去的是裸 TimeoutError、消息为空，上层只会看到一句无意义的报错。
        try:
            tools = await asyncio.wait_for(BaiduMapMcp.tools(), timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            raise ServiceException(
                f'连接百度地图 MCP 超时（超过 {timeout:g} 秒）：外部数据源暂时不可用，'
                '请基于已有资料回答，不要编造实时信息'
            )
        tool = tools.get(name)
        if tool is None:
            raise ServiceException(f'百度地图 MCP 未提供工具 {name}')
        try:
            result = await asyncio.wait_for(tool.ainvoke(arguments), timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            raise ServiceException(
                f'调用百度地图 MCP({name}) 超时（超过 {timeout:g} 秒）：'
                '该外部数据源暂时不可用，请基于已有资料回答，不要编造实时信息'
            )
        except Exception as exc:
            raise ServiceException(f'调用百度地图 MCP({name}) 失败：{exc}')
        return BaiduMapMcp._text_of(result)

    @staticmethod
    def _loads(text: str) -> dict:
        """解析 MCP 返回的 JSON；解析不了返回空 dict 由调用方兜底"""
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    # ---------------- 参数适配 ----------------
    @staticmethod
    async def resolve_adcode(city: str) -> str:
        """把"崇礼""张家口"这类说法解析成 6 位行政区划码

        为什么要这一步：天气接口只认 adcode，而模型不可能知道
        "崇礼区 = 130709"。这层翻译本来就该由代码做，不该指望模型记住。
        """
        key = (city or '').strip()
        if not key:
            return ''
        if key in _adcode_cache:
            return _adcode_cache[key]

        # 1.用户/模型多半说"崇礼""张家口"，而行政区划库里的正式名是"崇礼区""张家口市"，
        #   所以补几次后缀再试，别指望调用方写全称
        candidates = [key]
        if not key.endswith(('区', '市', '县', '州', '盟', '旗')):
            candidates += [f'{key}区', f'{key}市', f'{key}县']

        code = ''
        for candidate in candidates:
            raw = await BaiduMapMcp.call('map_district_search', {'keyword': candidate})
            data = BaiduMapMcp._loads(raw)
            districts = data.get('districts') or []
            for item in districts:
                name = str(item.get('name') or '')
                if candidate in name or name in candidate:
                    code = str(item.get('code') or '')
                    break
            if not code and districts:
                code = str(districts[0].get('code') or '')
            if code:
                break

        if code:
            _adcode_cache[key] = code
        return code

    # ---------------- 输出适配：天气 ----------------
    @staticmethod
    def format_weather(raw: str, days: int = 5) -> str:
        """把天气 JSON 摘成中文可读文本

        为什么不直接把原始 JSON 交给模型：
            原始返回带 6 类生活指数、20 多个空气污染物字段、每段导航路径的坐标串，
            既占 token 又干扰判断。摘出"决策真正要用的那几个数"更有效。
        """
        data = BaiduMapMcp._loads(raw)
        result = data.get('result') or {}
        if not result:
            return raw[:1500]          # 结构变了就退回原文，不要吞掉信息

        lines: list[str] = []
        # 1.地点
        location = result.get('location') or {}
        place = f"{location.get('province', '')}{location.get('city', '')}{location.get('name', '')}"
        lines.append(f'【{place}】天气预报（来源：百度地图 MCP·天气，底层为气象部门数据）')

        # 2.实时
        now = result.get('now') or {}
        if now:
            lines.append(
                f"实时：{now.get('text', '')}，气温 {now.get('temp', '?')}℃"
                f"（体感 {now.get('feels_like', '?')}℃），"
                f"湿度 {now.get('rh', '?')}%，{now.get('wind_dir', '')} {now.get('wind_class', '')}，"
                f"能见度 {now.get('vis', '?')} 米")
            if now.get('aqi') is not None:
                lines.append(f"空气质量：AQI {now.get('aqi')}（pm2.5 {now.get('pm25', '?')}）")
            uptime = str(now.get('uptime') or '')
            if len(uptime) == 14:
                lines.append(f"数据更新时间：{uptime[0:4]}-{uptime[4:6]}-{uptime[6:8]} "
                             f"{uptime[8:10]}:{uptime[10:12]}")

        # 3.预警——滑雪场景里这条最关键：霜冻/大风直接影响缆车是否停运
        alerts = result.get('alerts') or []
        if alerts:
            lines.append(f"⚠️ 气象预警 {len(alerts)} 条：")
            for item in alerts:
                lines.append(f"  - {item.get('title', '')}：{item.get('desc', '')}")
        else:
            lines.append('气象预警：无')

        # 4.逐日预报
        forecasts = (result.get('forecasts') or [])[:max(1, int(days or 5))]
        if forecasts:
            lines.append('逐日预报：')
            for day in forecasts:
                lines.append(
                    f"  - {day.get('date', '')} {day.get('week', '')}："
                    f"白天 {day.get('text_day', '')} {day.get('high', '?')}℃ / "
                    f"夜间 {day.get('text_night', '')} {day.get('low', '?')}℃，"
                    f"{day.get('wd_day', '')} {day.get('wc_day', '')}")
        return '\n'.join(lines)

    # ---------------- 输出适配：路线 ----------------
    @staticmethod
    def format_route(raw: str) -> str:
        """把路线 JSON 摘成中文可读文本"""
        data = BaiduMapMcp._loads(raw)
        result = data.get('result') or {}
        routes = result.get('routes') or []
        if not routes:
            return raw[:1200]

        lines: list[str] = ['驾车路线（来源：百度地图 MCP·路线规划，含实时路况）']
        # 只取第一条（最优路线），其余只报距离，避免把几十段导航全塞进上下文
        for index, route in enumerate(routes[:3], 1):
            distance_km = round(float(route.get('distance') or 0) / 1000, 1)
            duration_min = round(float(route.get('duration') or 0) / 60)
            hours, minutes = divmod(duration_min, 60)
            toll = route.get('toll')
            tag = '推荐路线' if index == 1 else f'备选路线{index - 1}'
            text = (f'{tag}：全程约 {distance_km} 公里，'
                    f'预计 {hours} 小时 {minutes} 分')
            if toll:
                text += f'，过路费约 {toll} 元'
            lines.append(text)

        steps = (routes[0].get('steps') or [])
        if steps:
            # 导航指令是逐段给的，同一条高速会被拆成几十段重复描述。
            # 这里只抽路径名并去重，既省 token 又更像人话。
            roads: list[str] = []
            for step in steps:
                for chunk in re.findall(r'<b>([^<]{2,20})</b>',
                                        str(step.get('instruction') or '')):
                    # 同一个 <b> 里常混着方向提示，如"京港澳高速,商都大道方向"、
                    # "G4,京港澳高速方向"。按逗号切开、取第一段像路名的，
                    # 再去掉结尾的"方向/出口/入口"，否则榜单里全是噪声。
                    picked = ''
                    for part in re.split(r'[,，]', chunk):
                        part = part.strip()
                        if re.search(r'(高速|公路|大道|路|桥|隧道|环|线|街)', part):
                            picked = re.sub(r'(方向|出口|入口)$', '', part)
                            break
                    if not picked or picked in ('起点', '终点') or picked in roads:
                        continue
                    roads.append(picked)

            # 长途自驾真正要看的是高速，不是市区里那几条辅路。
            # 实测：不加这层筛选时，前 10 条全被"中原路辅路、嵩山南路"这类
            # 市内路段占满，京港澳高速反而被挤出榜单。
            major = [r for r in roads if re.search(r'(高速|公路|国道|省道)', r)]
            if major:
                roads = major
            if roads:
                lines.append('主要途经：' + ' → '.join(roads[:8]))
        return '\n'.join(lines)

    # ---------------- 输出适配：周边 POI ----------------
    @staticmethod
    def format_places(raw: str, limit: int = 8) -> str:
        """把 POI 检索结果摘成中文可读文本"""
        data = BaiduMapMcp._loads(raw)
        results = data.get('results') or data.get('result') or []
        if isinstance(results, dict):
            results = results.get('results') or []
        if not results:
            return raw[:1200]

        lines: list[str] = ['周边地点（来源：百度地图 MCP·地点检索）']
        for item in results[:limit]:
            name = item.get('name', '')
            address = item.get('address', '')
            detail = item.get('detail_info') or {}
            text = f'  - {name}'
            if address:
                text += f'｜{address}'
            if detail.get('overall_rating'):
                text += f"｜评分 {detail['overall_rating']}"
            # ⚠️ 单位必须写全（"人均 167" → "人均 167 元"）：
            # 事实校验是按"数值+单位"配对比对的，证据里写"167"而回答写"167 元"
            # 会被判成对不上 → 误报 → 正常回答被打回甚至降级。这个坑真踩过。
            if detail.get('price'):
                text += f"｜人均 {detail['price']} 元"
            lines.append(text)
        return '\n'.join(lines)


# ---------------- 兼容旧调用（模块级别名） ----------------
call_mcp = BaiduMapMcp.call
resolve_adcode = BaiduMapMcp.resolve_adcode
