# 模型对比报告（同一套题、同一套 Agent）

- 题目数：4 ｜ 总轮次：20
- 判定口径：**降级 = 用户看到的是拒答**；打回 = 引用校验没过一次；编号 = 回答里出现的 [Exxx] 个数（0 = 没标来源）

| 模型 | 平均耗时 | 降级 | 打回 | 平均编号数 | 平均字数 | 没调工具 |
|---|---|---|---|---|---|---|
| zhipu/glm-4.5-air | 6s | 0/4 | 0 | 2.5 | 347 | 0/4 |
| zhipu/glm-4.6v | 7s | 0/4 | 0 | 4.2 | 253 | 0/4 |
| zhipu/glm-4-flash | 13s | 0/4 | 2 | 3.8 | 151 | 0/4 |
| zhipu/glm-4.6 | 20s | 0/4 | 0 | 9.0 | 756 | 0/4 |
| zhipu/glm-5.3-flash | 28s | 0/4 | 0 | 11.2 | 550 | 0/4 |

## 逐题明细

| 模型 | 题目 | 耗时 | 工具 | 编号 | 打回 | 降级 | 字数 |
|---|---|---|---|---|---|---|---|
| zhipu/glm-4.5-air | 万龙有几条高级道 | 12s | get_resort_runs | 1 | 0 | 否 | 609 |
| zhipu/glm-4.5-air | 万龙雪票多少钱 | 6s | get_resort_price | 4 | 0 | 否 | 401 |
| zhipu/glm-4.5-air | 万龙的数据分别是几手的？ | 6s | search_resort_kb | 5 | 0 | 否 | 329 |
| zhipu/glm-4.5-air | 二世谷雪票多少钱 | 2s | get_resort_price | 0 | 0 | 否 | 49 |
| zhipu/glm-4.6v | 万龙有几条高级道 | 8s | get_resort_runs | 1 | 0 | 否 | 243 |
| zhipu/glm-4.6v | 万龙雪票多少钱 | 11s | get_resort_price | 11 | 0 | 否 | 425 |
| zhipu/glm-4.6v | 万龙的数据分别是几手的？ | 5s | search_resort_kb | 5 | 0 | 否 | 286 |
| zhipu/glm-4.6v | 二世谷雪票多少钱 | 2s | get_resort_price | 0 | 0 | 否 | 57 |
| zhipu/glm-4-flash | 万龙有几条高级道 | 5s | get_resort_runs | 1 | 0 | 否 | 21 |
| zhipu/glm-4-flash | 万龙雪票多少钱 | 24s | get_resort_price | 9 | 1 | 否 | 250 |
| zhipu/glm-4-flash | 万龙的数据分别是几手的？ | 21s | search_resort_kb | 5 | 1 | 否 | 256 |
| zhipu/glm-4-flash | 二世谷雪票多少钱 | 5s | get_resort_price | 0 | 0 | 否 | 76 |
| zhipu/glm-4.6 | 万龙有几条高级道 | 24s | get_resort_runs | 3 | 0 | 否 | 1056 |
| zhipu/glm-4.6 | 万龙雪票多少钱 | 26s | get_resort_price | 30 | 0 | 否 | 1221 |
| zhipu/glm-4.6 | 万龙的数据分别是几手的？ | 19s | search_resort_kb | 3 | 0 | 否 | 530 |
| zhipu/glm-4.6 | 二世谷雪票多少钱 | 12s | get_resort_price | 0 | 0 | 否 | 215 |
| zhipu/glm-5.3-flash | 万龙有几条高级道 | 39s | get_resort_runs | 20 | 0 | 否 | 738 |
| zhipu/glm-5.3-flash | 万龙雪票多少钱 | 22s | get_resort_price | 21 | 0 | 否 | 732 |
| zhipu/glm-5.3-flash | 万龙的数据分别是几手的？ | 28s | search_resort_kb | 4 | 0 | 否 | 429 |
| zhipu/glm-5.3-flash | 二世谷雪票多少钱 | 23s | get_resort_price/get_resort_price/get_resort_price | 0 | 0 | 否 | 302 |

