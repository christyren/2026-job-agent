# 正式作答日一页纸：Day-of 模式（2026-10-12）

代码：`job_agent/dayof.py`（`python3 -m job_agent.dayof` 一键打印）
来源运行单：`~/workspace/goals/1-sde2/files/stripe-hackerrank-dayof-2026-10-12.md`
铁律：**今天就是正式日。状态好时段再点 Start（午餐+休息后、留足缓冲），不卡死钟点、但不拖到明天。点 Start 后 60 分钟不可暂停。**

## 核心区分：两类检查，别混

- **代码能验的**（`run_preflight` 自动验）：精度 / 重复 / 空输入 / 时间盒覆盖 60 分钟——对着真实模板验，全绿才算数。
- **代码验不了的**（只能你亲手确认）：下面 7 项。程序不会假装替你验过——`can_start()` 会把没确认的逐项列出来。

## Start 门（两边都绿才 go）

- [ ] 代码 preflight `all_ok: True`
- [ ] `cat_fed_noon`：猫梦中午粮已加，避免中途打断
- [ ] `lunch_and_rest`：午餐少碳水、多蛋白质、吃熟悉的；餐后休息 20–30 分钟
- [ ] `chrome_clean`：Chrome 无痕、关第三方暗色/扩展、桌面只留考试窗口
- [ ] `phone_silent`：手机静音/勿扰
- [ ] `desk_ready`：水、充电线就位，60 分钟不被打断
- [ ] `faq_scanned`：点 Start 前先扫一遍邀请邮件里的 Candidate FAQ
- [ ] `handwritten`：全程手写，不外贴代码块

## 60 分钟时间盒（同备考包，到点进下一段）

0–5 读全部段落+格式 → 5–25 Part 1（parse/calc/output 分开）→ 25–45 后续段（只改对应一段）→ 45–55 边界（空/重复/精度/排序）→ 55–60 只修能说出名字的 bug。

## 交卷后（只做两件事）

1. 复盘只答两问：Part 1 留扩展口了吗？输入格式哪里读漏了？（写具体细节）
2. 其余顺延：系统设计 #9 正式课、LeetCode「堆」都顺延到下一个正常学习日，不断档、不在今天补。
