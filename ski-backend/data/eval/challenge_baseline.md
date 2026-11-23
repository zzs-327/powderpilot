# 检索评估报告

> 生成时间：2026-10-06 17:11:45
> 语料：resorts/

| 指标 | 数值 |
|---|---|
| 检索用例数 | 6 |
| hit@1 | 5/6 = 83% |
| hit@3 | 5/6 = 83% |
| hit@5 | 5/6 = 83% |
| MRR | 0.833 |
| 拒答正确率 | 2/2 = 100% |

## 明细

| id | 类型 | 问题 | 结果 | top1 摘要 |
|---|---|---|---|---|
| challenge:count_disagreement | retrieval | 将军山到底61条还是48条雪道，为什么不一样 | 命中@1  | 将军山国际滑雪度假区雪道总览：档案来源口径总数 61 条雪道；分级统计合计 48 |
| challenge:location_count | retrieval | 万龙在哪里，一共有几条雪道 | 命中@1  | 万龙滑雪场雪道总览：档案来源口径总数 22 条雪道；分级统计合计 32 条——初 |
| challenge:price_count | retrieval | 万龙有几条高级道，平日雪票多少钱 | 未命中（top5）  | 万龙滑雪场2天雪票·自带板·平日·分时段优惠最低价：599 元/人/2天（季末平 |
| challenge:deposit | retrieval | 万龙租雪具押金多少钱，怎么退押金 | 命中@1  | 万龙滑雪场避坑：租雪具要交押金：含雪具 2000 元、含全套 2500 元；归还 |
| challenge:park_count | retrieval | 多乐美地为什么总数9条，分级只有7条 | 命中@1  | 多乐美地滑雪场雪道总览：档案来源口径总数 9 条雪道；分级统计合计 7 条——初 |
| challenge:unknown_price | refusal | 南山滑雪场平日雪票多少钱 | ✅ 正确拒答  |  |
| challenge:unknown_vs_known | refusal | 二世谷和万龙哪个雪票更便宜 | ✅ 正确拒答  |  |
| challenge:run_location | retrieval | 万龙金龙道在哪里，有多长 | 命中@1  | 万龙滑雪场的雪道「金龙道」：难度高级（老手），长度约 1900 米，最高海拔约  |