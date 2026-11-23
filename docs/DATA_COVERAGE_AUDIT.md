# 六雪场数据覆盖审计

审计/原文访问日期：2026-10-07。范围仅 `ski-backend/data/resorts/{wanlong,taiwu,fulong,yunding,beidahu,songhuahu}.yaml`；文件名与内部 id 全部一致。数字为实际 YAML 列表长度，不等于当季开放数量。未读取 private/，未写 MySQL/向量库，未修改解析器/前端/指导数据。

## 实际覆盖

| id | 雪道 | 索道/魔毯 | 价格项 | tips | videos | photos | by_level | media来源页 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| wanlong | 32 | 8 | 29 | 10 | 4 | 3 | 3 | 0 |
| taiwu | 22 | 0 | 0 | 0 | 0 | 0 | 0 | 1 |
| fulong | 33 | 0 | 14 | 0 | 0 | 0 | 0 | 0 |
| yunding | 39 | 5 | 28 | 0 | 0 | 0 | 0 | 0 |
| beidahu | 34 | 10 | 0 | 2 | 0 | 0 | 0 | 0 |
| songhuahu | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 1 |

表为本审计及并行主任务编辑完成时快照；太舞官方历史视频来源页由主任务补充为 media 1（只核验发布页文字，非播放内容）。万龙 4 视频 URL 全空：标题、时长、时间点不能视作已观看证据；3 照片仅对象键与自述授权，没有可访问 URL/文件验证，宜按样例待核验处理。松花湖新增 1 项是新闻配图**来源页**，不是查看过的实拍或当前雪况。

## 关键缺口（不补猜测）

- 万龙：官方总数 22 与既有明细 32、落差多口径保留；课程预约/租赁流程需当前官方文本，旧雪季价目不可当现价。
- 太舞：雪道有明细，但索道、价格、服务 tips/课程/装备领取退还均空；既有“太子城出站步行可达”未核实，不可承诺。
- 富龙：33 行含公园/复合难度；缺索道、课程/租赁流程、当前到达接驳及素材；14 项历史价格不可当现价。
- 云顶：39 行包含 7 地形公园，且“空中技巧”出现两行，保留来源行、不擅自去重；缺课程/租赁/预约/到达实用文本及素材。
- 北大湖：74 条宣传口径与 34 条表明细、13 索道与 10 条明细仍并列；价格空。营地仅历史介绍，未确认本雪季招生/价目。乘车页文字提取未得到时刻/价格，不推断图片内的信息。
- 松花湖：50 条/55 公里是 2024-2025 收官稿口径，不能补逐道、分难度、坡度、索道和实时价目；教学来源是 2023-2024 历史报道，需新公告。
- 六场共同缺口：可预约课程日期、教练/单板双板适配、装备尺码/押金/领还步骤、当前接驳预约方式、安全示范教学视频的真实内容证据。无原文证据就保持空缺。

## 原文访问证据与本次新增

| 来源 URL | 本次访问 | 可用证据 / 增加字段 |
|---|---|---|
| https://www.beidahu.com/ | 默认 UA 403；Mozilla UA HTTP 200，读取正文 | 74 条、13 缆车等既有口径仍在正文；未覆盖冲突或录入首页动态开放状态 |
| https://www.beidahu.com/study/1740576798586744832.html | utils.net.http_session + Mozilla UA HTTP 200，读取正文 | 原文“自2010年起”“7-16周岁”“14年”及滑雪技能教学/自然体验/户外探索；增加 tips 1（A，访问日期，历史且招生未核实） |
| https://www.beidahu.com/contact/3.html | HTTP 200，读取正文 | 读到乘车路线标题、400-770-6789；增加 tips 1（A，说明本次文本未得到班次，不宣称整页没有图片信息） |
| https://finance.sina.com.cn/jjxw/2023-11-02/doc-imztfnvn7920831.shtml | HTTP 200，读取正文 | 2023-11-02 吉林日报、记者刘姗姗署名；VSKI 官方小程序教学预约、V-PARK 初学者单双板分区教学；增加 songhuahu tips 1（B，显式23-24雪季，不保证现行） |
| https://www.vanke.com/news/data?newsid=8406&typeid=1 | HTTP 200，读取完整文章段落及日期/署名 | 原文发布时间2025-03-26，文末“图/万科松花湖度假区”；增加 media 1（不入 RAG）：kind/title/url/source/source_type/date/published_at/checked_at/verification/rights/notice，page_checked + link_only；**配图未观看，拍摄日期未知** |

新增 tips 均保留 source_type/source/date；date 是本次访问日期，不伪装文章发布日期。未新增当季价格或开放承诺，不改旧 facts/provenance 冲突。

## 候选与访问限制

以下仅候选，不是 verified：
- https://www.thaiwoo.com/ ：本执行 HTTP SSL EOF / 超时；并行主任务读取 https://www.thaiwoo.com/page/videos?lang=zh 成功，可继续按页面证据登记，不能推断播放内容。
- https://www.wlski.com/ ：本次 SSL EOF；不据搜索摘要追加事实。
- https://www.zjknews.com/news/shehui/zjkshehui/202602/04/478333.html ：搜索发现2026张家口指南，HTTP/HTTPS ProxyError，未读原文；可能补太舞/富龙/云顶服务。
- https://dendanskeskiskole.dk/content/thaiwoo-china ：搜索发现，SSL失败，未读原文，非当前官方课程证据。
- 北大湖首页指向 https://mp.weixin.qq.com/s/WjKxqZyO3GaCW9aL5V7PMg （25-26收官）及 https://mp.weixin.qq.com/s/q8Hop07V7UsEyLcS8xgO1g （2024赛事回顾）；仅发现链接，未读取正文/观看媒体。

未关闭 TLS 校验；连接失败不等于源站不存在。搜索摘要只定位 URL，不作为核验依据。已发布页面也不等于授予素材转载权，媒体保持来源链接而不下载复制。

## 离线验证

<你的 Python 环境>/python.exe -m rag.ingest --dry-run wanlong taiwu fulong yunding beidahu songhuahu 最终通过（exit 0）：366 chunks；万龙124 / 太舞31 / 富龙56 / 云顶81 / 北大湖61 / 松花湖13；tip 16、video 4、photo 3。工具明确报告未调用嵌入模型、未写入向量库。六 YAML 均经 yaml.safe_load 成功读取。

初次将新闻来源放 photos 导致缺 shot_at 的校验失败；已改为独立 media（前端来源链接，不作为 RAG 事实），未捏造拍摄日期，未改 parser，原真实 photos 语义保留。
