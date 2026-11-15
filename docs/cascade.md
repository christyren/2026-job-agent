# 级联抽取（Cascade）— 先便宜档试，抽少了再升级

## 一句话

不再事先选定抽取档位：先用 cheap 抽，抽到的要求少于 4 条就升级到 mid，
还少于 4 条就升级到 strong。强档是最后防线，所以 recall 能追上强档；
简单的 JD 在 cheap 就停了，所以平均成本低于一直用强档。

## 三档 vs 级联（5 条 eval 案例，20 个要求）

| 方式 | recall | grounded | 总成本 | 每要点成本 |
|---|---|---|---|---|
| cheap（固定） | 70% | 100% | $0.0323 | $0.002936 |
| mid（固定） | 85% | 100% | $0.0411 | $0.002939 |
| strong（固定） | 100% | 100% | $0.0515 | $0.003027 |
| **级联（min=4）** | **100%** | **100%** | **$0.0508** | **$0.002991** |

## 逐案升级记录

| 案例 | 走了哪几档 | 停在哪档 | 说明 |
|---|---|---|---|
| eval-m04（直命中 5 条） | cheap | cheap | 简单 JD，一档就够 |
| eval-m01（常见说法） | cheap → mid | mid | mid 补上 Kafka / REST API |
| eval-m03（常见说法） | cheap → mid | mid | mid 补上 SQL |
| eval-m02（全缺口） | cheap → mid → strong | strong | 3 条都直命中，但不到 4 条，照样升到顶 |
| eval-m05（罕见说法） | cheap → mid → strong | strong | 只有强档认得 stream processing 等说法 |

5 案里 4 案升级——这套 eval 是故意挑难的（换说法），所以级联总成本
接近强档。省钱发生在简单的 JD 上（m04 只付 cheap 的抽取钱），不在这里。

## 三条结论

1. **触发条件是启发式，不是证明**：阈值 min=3 时 recall 只有 85%——
   eval-m01 抽到 3 条直命中就"看起来够了"，级联停住，Kafka / REST API
   被静默漏掉。min=4 时 recall 100%。阈值本身是参数，要用 eval 量，
   不能凭感觉定。
2. **升级永远不丢要求**：三档的同义词表是嵌套的（cheap ⊂ mid ⊂ strong），
   后一档的结果一定包含前一档的。这是级联能成立的前提，测试里钉住了。
3. **每次尝试都要记账**：升级两次就付三次抽取的钱。预算日志里每次尝试
   各记各档的价格（2026-11-14 修过的 bug：别再把非 cheap 档都按强档记）。
   最坏 3 次抽取 + 生成 + 校验 = 5 步，仍在 6 步上限内。

## 运行方式

```bash
python3 -m job_agent.evaluate            # cascade eval 一节
python3 main.py "帮我用级联抽取生成材料"    # 走 agent loop 调 draft_materials_cascade
python3 -c "from job_agent.tools import draft_materials_cascade; print(draft_materials_cascade(case_id='eval-m05'))"
```

## 下一步

checker 步：生成之后再加一个独立核对（对照 JD 逐条验要点），走昨天
Google /teamwork 提到的 solver / checker 分工——抽取负责"找全"，
checker 负责"验对"，两步互相独立。
