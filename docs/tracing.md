# Trace + 成本上限治理（2026-11-17）

## 为什么需要 trace

到昨天为止，每条流水线都把成本记在 `RunBudget` 里——但那份日志只活在
那一次调用的返回值里。半夜跑了一批任务、早上发现账单不对，你想问
「昨晚哪一步烧了钱、哪一步最贵」，手里没有任何东西能回答。
**可观测性（observability）**就是回答这个问题的能力：每次运行一个
trace_id，每步一个 span（名字 / 模型档位 / 输入输出 token / 成本 /
累计成本），trace 存下来供人审计，单次运行太贵就报警。
LangSmith（LangChain 的 tracing 平台）就是这个形状的参考实现。

## 三件套，对照 LangSmith

| LangSmith 的做法 | 本项目今天的做法 |
|---|---|
| 每次运行一个 trace，嵌套的 span | `RunTrace`：trace_id + 每步 name/tier/tokens/cost/cumulative |
| token / 成本按 run、按 project 聚合 | `trace_summary`：runs / 总成本 / 超上限次数 / 最贵的一次 |
| 单条 trace 成本超 $0.05 报警 | `TRACE_COST_ALERT_USD = 0.05` 报警线；`DEFAULT_MAX_RUN_COST_USD = 0.10` 是**硬上限**，不是报警 |

关键区别：LangSmith 只给**可见性**，不替你停；今天的上限是**硬停**——
超了就停、草稿不发、标待人工。

## 两个上限，别混

- **步数上限**（里程碑 1 的 `max_steps`）：防死循环。
- **成本上限**（今天的 `CostCappedBudget`）：防烧钱。一条循环步数没超、
  但每步都是强档，照样能把钱烧光——步数上限拦不住它，成本上限才拦得住。

默认上限 $0.10：正常一次带核对的生成约 $0.055，所以 $0.10 ≈ 2 倍健康
运行——诚实的工作能通过，真正出问题（修复循环、档位配错、级联失控）
才会被拦住。

## 超上限时发生什么

1. 触发那一步**先记进日志**，再抛 `BudgetExceeded`——trace 里能看到
   到底是哪一步越的线。
2. `run_traced_checked` 捕获它，trace 状态记为 `over_cap`，**不返回草稿**
   （半成品悄悄发出去比不发更糟），`needs_human=True`。
3. trace 追加写进 `data/traces.jsonl`（只追加、不改写），人可以事后翻。

## 运行方式

```bash
cd ~/workspace/projects/job-agent
python3 -m job_agent.evaluate            # 末段：trace eval 一节
python3 main.py "帮我追踪生成申请材料"      # 走 agent loop 调 draft_materials_traced
python3 -c "from job_agent.tools import draft_materials_traced; print(draft_materials_traced(case_id='eval-m01'))"
python3 -c "from job_agent.tools import draft_materials_traced; print(draft_materials_traced(source='summary'))"
python3 -m unittest discover -s tests -v
```

## 下一步预告

① 真接一次 Batch API（Anthropic Message Batches）把规则打分换成模型打分、
对比确定性版；② 把 checker 换成小模型调用：同样的 5 条规则、让模型当
checker，量它的检测率与成本——那时「核对比生成便宜」就不再是假设，
而是 eval 数字，trace 会把两边的成本如实记下来。
