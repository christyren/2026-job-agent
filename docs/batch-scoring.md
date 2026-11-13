# 批量评分：一批 choice 题打完一整批岗位

> 2026-11-13 迭代。把里程碑 3 的「逐岗调一次 LLM」改成一批
> choice 题（Decisions API 形状），复用昨天的批量决策。

## 一句话

每条岗位变成一个 `choice` 问题：选项是 `match / no_match / needs_review`
三个 verdict，当选者 = 流水线的**最终结论**（硬规则 + 阈值都走完之后），
分数 = 置信度；另加一个 batch 级 `score` 题 = 全部岗位置信度的均值。

## 为什么值得做

| | 逐岗调一次 | 一批 choice 题 |
|---|---|---|
| 共享输入（你的标准 + 评分规则，约 2000 token） | 每岗重算一次 | **整批只算一次** |
| 10 岗成本（Decisions 定价 $0.10/1M 输入，输出 $0） | $0.0024 | **$0.00064，省约 3.75 倍** |
| 100 岗成本 | $0.024 | **$0.0042，省约 5.7 倍** |

规律：批越大，共享输入被摊得越薄，省得越多。这就是昨天
「共享输入只计一次费」用在评分上的版本。

## 三个关键点

1. **问的是最终结论，不是第一直觉**。评分器可能倾向 `match`，
   但置信度 < 0.7 时阈值已把它改写成 `needs_review`——choice 题的
   当选者必须是改写后的那个。否则这批题会悄悄自动通过流水线
   本来拒绝下结论的岗位。eval-06 就是这个考题（CloudNine：倾向
   match、置信度 0.65 → 最终 needs_review）。
2. **当选者必须是最高分**。其余两个 verdict 平分剩下的
   (1 − confidence)。我们的评分器置信度最低 0.45，剩下的最多
   0.275，不会超过当选者；如果将来某个评分器返回 confidence < 1/3，
   校验会直接拒绝建批——护栏在工作，不是 bug。
3. **批量只改包装和价格，不改结论**。eval 的不变量：
   批量 verdict 必须和逐岗 `score_job` 的 verdict 逐条相同。
   变了就是 bug，不是优化。

## 真实世界的对应物

三家厂商的 Batch API 都是同一个交易：**拿等待时间换半价**——
OpenAI Batch API、Anthropic Message Batches、Gemini Batch API，
输入输出一律 5 折，结果在 24 小时内返回（Anthropic 多数 1 小时内）。
适合能等的活： nightly 扫一遍新岗位、离线跑 eval、批量打标签。
不适合： recruiter 正在等你回的那一次评分。

参考：Batch API 三家对比（2026-10-29 核对各家官方价）——
https://aiganak.com/insights/batch-api-openai-anthropic-gemini/

## 运行方式

```bash
cd ~/workspace/projects/job-agent
python3 -m job_agent.batch_scoring   # 看 10 条 eval 岗位的一批 11 个问题 + 成本对照
python3 main.py "帮我批量评分"        # 走 agent loop 调 score_jobs_batch
python3 -m job_agent.evaluate        # 末段看 batch scoring eval（10/10）
python3 -m unittest discover -s tests -v
```

## 文件

- `job_agent/batch_scoring.py` — `result_to_choice_scores` /
  `job_to_choice_question` / `score_jobs_batch` / `build_batch_from_source`
- `job_agent/evaluate.py` — 新增 `run_batch_scoring_eval()`
- `job_agent/tools.py` — 第 9 个工具 `score_jobs_batch`
- `tests/test_batch_scoring.py` — 16 个新测试
