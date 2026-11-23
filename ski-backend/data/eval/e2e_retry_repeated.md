> 轮数：2；题集：e2e_challenge_set.yaml；SHA256：eead1f7756c7e8f0510c47c4ff087e975982018dce2387b77d904355a10d9fb5

# 漏引用重答重复实验

独立进程、唯一会话、每轮交替顺序。小样本观察，不代表统计显著或普遍语义准确率。

| 开关 | 通过/总题 | 降级 | 平均耗时 | 输入token | 输出token |
|---|---|---|---|---|---|
| off | 4/8 | 3 | 12.9s | 177292 | 837 |
| on | 7/8 | 0 | 14.8s | 188661 | 965 |

## 逐题重复通过

| 题目 | off | on |
|---|---|---|
| challenge_jiangjunshan_conflict | 0/2 | 1/2 |
| challenge_uncollected | 2/2 | 2/2 |
| challenge_wanlong_followup | 1/2 | 2/2 |
| challenge_wanlong_mixed | 1/2 | 2/2 |
