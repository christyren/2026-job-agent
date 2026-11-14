# 路由三档：cheap / mid / strong（一页清单）

## 三档是什么

| 档位 | 代表模型（示例） | 输入 / 输出价（每百万 token） | 抽取时认什么 |
|---|---|---|---|
| cheap | Haiku 级小模型 | $1.00 / $5.00 | 只认原词直命中 |
| **mid** | **Gemini 4 Hydrogen（快执行档）** | **$1.50 / $7.50** | 直命中 + 3 条最常见说法 |
| strong | Opus 级大模型 | $5.00 / $25.00 | 直命中 + 全部 9 条同义词 |

Hydrogen（Google，2026-11-12 发布）：1M 上下文全程同价、比 Gemini 3.8 Flash
快约 2.5 倍，发布当天就进了 Cursor（CursorBench 54.3%，Flash 是 39.6%）
和 Devin；Antigravity 里由 Orchestrator 在 Carbon（规划）和 Hydrogen
（执行）之间动态切换。来源：Google 官方博客
https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-gemini-4-hydrogen/

## 三档 eval（5 条案例、20 个要求，2026-11-14 本机实测）

| 档位 | recall | gap_accuracy | grounded | 总成本 | 每条合格要点 |
|---|---|---|---|---|---|
| cheap | 70%（14/20） | 100% | 100% | $0.0323 | $0.002936 |
| mid | 85%（17/20） | 100% | 100% | $0.0411 | $0.002939 |
| strong | 100%（20/20） | 100% | 100% | $0.0515 | $0.003027 |

## 今天最值得记的三条

1. **中档的赌注赢了一半**：只加 3 条最常见说法（event streaming→Kafka、
   api design→REST API、relational database→SQL），recall 就从 70% 涨到
   85%——JD 里最常见的换说法就这几种。但 eval-m05（stream processing /
   decoupled services / containerised，全是罕见说法）中档照样 0/3，
   只有强档全认。**中档不是"便宜版强档"，是"常见情况够用档"。**
2. **每要点成本三档几乎一样**（$0.00294 / $0.00294 / $0.00303）：因为
   成本大头在生成步（永远是强档），抽取档换谁都动不了总账——和
   10/05 的结论是同一条：抽取步别省错地方，**按每个成功产出算成本，
   不要只看单价**。
3. **gap_accuracy 100% 也可能在漏**：eval-m05 被便宜档和中档整条漏掉，
   gaps 却是空的（漏掉的要求根本进不了 gaps），gap_accuracy 仍是 100%。
   只有 recall 能看见静默漏检——这就是为什么 eval 要同时报两个数。

## 怎么用

```bash
python3 -m job_agent.evaluate            # 三档对比在 materials eval 一节
python3 main.py "帮我生成申请材料"          # 走 agent loop 跑三档对比
python3 -c "from job_agent.tools import draft_materials; \
  print(draft_materials(case_id='eval-m05', extract_tier='mid'))"
# 单份草稿指定档位：cheap / mid / strong，默认 cheap
```

默认路由不变：抽取仍走 cheap、生成仍走 strong；mid 是可选项，
不是新默认。什么时候用 mid：批量跑一堆 JD、先求覆盖面时；
什么时候必须 strong：JD 写得绕、或这份申请很重要时。

## 下一步可选

级联（cascade）：先用 cheap 抽，抽到的要求太少（比如少于 2 条）
再自动升级到 mid / strong——只在"看起来抽少了"时才花强档的钱。
