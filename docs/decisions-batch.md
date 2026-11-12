# 批量类型化决策（Decisions API 形状）— 2026-11-12 一页清单

## 今天这一步是什么
把里程碑 6 的 planner 决定的**打包方式**换掉：以前一次只产一条决定，
今天按昨天（2026-11-11）Google 发布的 Gemini Decisions API 的形状，
把一整份今日计划打成一个**批量类型化决策**——一份共享输入 + 最多 1000 个
独立问题，一次前向传播全部打分。

## 三种问题类型（API 原名）
| 类型 | 问什么 | 返回什么 |
|---|---|---|
| `choice` | 这条申请今天先做什么？ | 8 个动作各一个分数，最高分者当选 |
| `noul` | 这条决定能自动执行吗？ | P(yes) = 该决定的 confidence |
| `score` | 这份计划整体有多可执行？ | 0–1 的 rubric 值 = 全部决定置信度的均值 |

## 关键点
- **答案是数字，不是文章**：选项只能从 8 个动作里选，分数直接能被代码
  排序、比较、设阈值——不用先把一段话解析回结构。
- **共享输入只计一次费**：2000 token 的共享上下文 + 每题 400 token；
  100 题一批约 $0.0042；同样 100 题各调一次 LLM，每次都重算共享上下文，
  贵得多。输出 token 永远是 0，不收费。
- **概率还是那把阈值尺**：`noul` 的 P(yes) < 0.7，和里程碑 6 的
  confidence < 0.7 是同一条护栏，换了个说法——低于它就交给人。
- **问题之间互相独立**：加一题、删一题不影响其他题的答案，所以 eval
  可以逐题验收（`data/eval_decisions.json`，3 条场景）。

## 跑一遍
```bash
cd ~/workspace/projects/job-agent
python3 -m job_agent.decisions        # 看今天真实库的 9 个问题 + 批量成本
python3 main.py "帮我看今天的类型化决策"   # 走 agent loop 调 typed_decisions
python3 -m job_agent.evaluate         # 末段看 decisions eval（3/3）
python3 -m unittest discover -s tests -v
```

## 注意
- 这一步仍是**确定性规则版**：分数来自 planner 的 confidence，不是真调
  Decisions API。真接 API 时只换打分那一步，batch 形状、校验、eval 都不用动。
- 价格是发布日（2026-11-11）口径：输入 $0.10/1M、输出 $0；引用前请重核。
- Planner 只提议、不执行：batch 里 4 条决定今天全标需人工——
  Snowflake 仍被长期 memory 暂停，压到 0.50 转 needs_review。
