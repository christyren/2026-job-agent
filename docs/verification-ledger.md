# 核实台账一页纸：Unknown 是正式状态（2026-10-13）

代码：`job_agent/verify.py`（`python3 -m job_agent.verify` 一键打印台账汇总）
数据：`data/verifications.json`（durable，每次从磁盘重读，和里程碑 5 同一规则）
铁律：**没有证据，就老老实实写 unknown；不许把「没看到」默认成「没发生」，也不许默认成「已经好了」。**

## 三个状态，别只用两个

- `unknown`：还没有证据。今天的 Stripe OA 提交就是它——Gmail 里没有确认邮件，只能说明「没证据」，不能说明交了、也不能说明过期了。
- `confirmed`：有证据说明「是」。必须同时写清 evidence（看到了什么）+ source（从哪看到的）。
- `refuted`：有证据说明「不是」。例如 Robinhood 今早的拒信，就是它还在推进这一说法的 refuted 证据。

## 离开 unknown 的门槛

- [ ] evidence 非空：写具体所见（页面文字 / 邮件时间 / 截图内容），不写「我记得」「应该是」
- [ ] source 非空：Gmail / HackerRank 邀约页 / 本机测试输出……说清出处
- [ ] 两项缺一，`record_evidence()` 直接报错，台账状态不变——猜测不入账

## 纠正怎么做

发现记错了：**追加一条纠正，不改旧记录**（history 只追加，和里程碑 5 一样）。纠正可以把状态打回 `unknown`，而且打回 unknown 不需要证据——怀疑永远不需要许可。

## 今天台账里有什么（种子 3 条）

1. `stripe-oa-submission` = **unknown**：等你在 HackerRank 邀约页亲眼看一眼，看到 Submitted 就带证据改 confirmed，看不到/已过期就带证据改 refuted。
2. `robinhood-backend-advancing` = refuted：今早 08:38 拒信已到，此线关闭。
3. `job-agent-tests-green-2026-10-13` = confirmed：今天动手前本机实测 97 个测试全绿。

## 读汇总的方法

`summary()` 会把 unknown 逐条列名——它们就是待办清单本身，不是脚注。`can_claim()` 只有 confirmed 才返回 True；在任何地方说「这件事搞定了」之前，先过这一关。
