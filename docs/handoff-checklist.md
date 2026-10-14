# 出发前快照清单（2026-10-14）

一页就够，回来那天照着走。

## 出发前（今天）
- [ ] 跑 `python3 -m job_agent.handoff`，生成 `data/handoff_snapshot.json`
- [ ] 看输出：unknown 有几条、needs_review 有几条，心里有数即可
- [ ] 不因为要出发就把 unknown 改掉——没证据就是没证据

## 快照里有什么
- 申请汇总：总数 + 按状态计数 + needs_review / stale / 面试中名单
- 核实台账：总数 + 按状态计数 + unknown 逐条列名
- 长期 memory：暂停公司名单 + 条数
- 回来先做：按 unknown → needs_review → stale → 面试中 排好的清单

## 回来第一天（只做这四步，不开新投递）
1. 先读快照，再读三个原文件（applications / verifications / agent_memory）
2. 先核实 unknown：看邮箱 / 邀约页，有证据才入台账
3. 再清 needs_review，然后看 stale 还值不值得跟
4. 快照和原文件对不上时，先记下差异、问人，不要直接覆盖任何一边

## 一句话原则
快照是「10 月 14 日那天我们相信什么」的照片，不是新的真相；
真相一直在三个原文件里，快照只负责让你不靠回忆续上。
