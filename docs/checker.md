# 独立 Checker 步（Solver / Checker 分工）— 2026-11-16 迭代 10

## 一句话

生成（solver）只管"写出来"，核对（checker）只管"验对不对"——
checker 从原始 JD 和简历块重新推导一切，不信任生成器说的任何话。

## 为什么要分开

- 里程碑 4 的 `verify_grounding` 藏在生成器内部，只能抓一种错（引文造假），
  而且用的是生成器自己的要求清单——生成器漏掉的要求，它永远看不见。
- Google Antigravity `/teamwork` 的分工就是这个形状：producer 写候选方案，
  critic 找毛病，auditor 从干净上下文复核，最后由客观 checker（Lean 证明核查器、
  未改动的测试套件）决定哪个候选算数。"Try many, check every one."
- 分工成立的前提：**核对必须比生成便宜**，否则省下的错全被核对的钱吃掉。
  这里核对按 cheap 档记账（离线是纯代码，成本是下限；真实版会是一个小模型调用）。

## Checker 的 5 条规则（job_agent/checker.py）

| # | 规则 | 抓什么错 |
|---|------|---------|
| 1 | requirement_in_jd | 要点写了 JD 根本没要求的东西（简历真有也不行） |
| 2 | evidence_supports_requirement | 引用的块是真的、引文是真的，但块讲的不是这项要求（引错块） |
| 3 | evidence_verbatim | 引文是编的，不在被引用的块里（里程碑 4 的规则，改由 checker 拥有） |
| 4 | gap_is_real | 假缺口：说简历没证据，其实简历里有（幻觉的镜像——该写的要点没写出来） |
| 5 | no_orphan_requirements | 完整性：JD 里的每条要求必须是要点或缺口，静默漏检在这里被抓住 |

Checker 从 JD 原文（strong 档重抽）与简历块（重新检索）独立推导，
不读生成器抽到的要求清单——这是"独立"两个字的全部含义。

## 失败了怎么办：修一次，再验一次

- checker 返回结构化失败清单（check 名 + 哪一项 + 原因），`repair_materials`
  按清单确定性修复：删掉 JD 没要求的东西、重引正确的块、假缺口改成要点、
  漏掉的要求补成要点或缺口。
- 只修一次（MAX_REPAIRS=1），再验一次。还不过就挂 `needs_human=True`
  交人，绝不悄悄发出去，也绝不修第二次。
- 步数上限：带核对的流水线最坏 8 步（3 次级联抽取 + 生成 + 校验 +
  核对 + 修复 + 复验），`MAX_STEPS_CHECKED=8` 是硬上限——"只修一次"
  靠步数上限保证，不靠口头约定。

## Eval 怎么量（python3 -m job_agent.evaluate，checker 一节）

三部分，缺一不可：

1. **基线**：诚实的级联草稿过 checker——应该全过、0 次修复。
   一个对诚实草稿乱报警的 checker 是噪音，不是安全。
2. **对抗**：8 份手工造的坏草稿，每份只违反一条规则——
   检测率（抓没抓到）与修复率（修一次 + 复验能不能过）分开量：
   抓到修不好，终点还是人。其中 adv-06（2026-11-18 新增）是 adv-03
   的加强版：Java 要点引 education 块、引文逐字正确，所以 verbatim
   必须放行，只有 supports 规则必须报警——验的是"引文真 ≠ 证据对"。
   adv-07（2026-11-19 新增）是 adv-04 的第二例：把"REST API"写进
   gaps 当假缺口——简历 resume-skills 块明明写着，修复后它必须变回
   要点（从 resume-skills 块取证），而不是留在 gaps。
   adv-08（2026-11-20 新增）是第一例**状态冲突**：REST API 同时是
   要点和缺口——草稿自己跟自己打架。只允许 gap_is_real 报警，
   修复必须只删缺口、不许再补一个要点，否则要点就重复了。
3. **全流水线**：5 案跑 `generate_materials_checked`——
   checker_passed 率、用了几次修复、步数（≤8）、总成本。

## 试着跑

```bash
python3 -m job_agent.evaluate            # 看 checker 一节
python3 main.py "帮我核对申请材料"          # 走 agent loop 调 draft_materials_checked
python3 -c "from job_agent.tools import draft_materials_checked; print(draft_materials_checked(case_id='eval-m01'))"
python3 -m unittest discover -s tests -v
```

## 最小证据引文 + 修复幂等（2026-11-20 迭代 14）

- **最小引文**：修复新生成的要点，引文只贴提到该要求的那一句
  （`_minimal_quote`），而不是整块原文。逐字规则照样过——那一句
  仍是原文的子串；supports 规则根本不读引文（它查块的 keywords），
  所以引文变短既不会让它更容易过、也不会更难过——只影响人读到
  的证据清不清晰。找不到单句时回退到整块原文。
- **修复幂等**：修两次 = 修一次。adv-08 抓到的就是旧修复的非幂等
  bug：要点已存在时又补一个要点，草稿里出现重复。修复前先查
  "这条要求是不是已经有要点了"，有就只删缺口、什么都不补。
  和支付接口的 Idempotency-Key 是同一条道理：重试不可避免，
  重复的副作用必须无害。

**2026-11-21 迭代 15：生成器也用最小引文**——`_minimal_quote` 从
`checker.py` 搬到 `materials.py`（生成器也要用，反向 import 会循环），
生成器和修复器从此共用同一个函数、产出的要点一模一样（测试：
同一条 Kafka 要点，生成版 = 修复版）。实测：材料 eval 三档 recall
不变（70%/85%/100%）、grounded 100%、没有规则变红；每条合格要点
成本强档 $0.003027 → $0.00096（约 −68%），级联总成本 $0.050841 →
$0.015705。诚实注脚：离线账把引文同时算进生成步的输入和输出，
所以降幅被放大了；真接 API 时省的主要是输出 token，方向对、
数字别照搬。

**2026-11-22 迭代 16：引文再细一级——KWIC 片段**——句级引文仍带
无关并列项（Kafka 那句 108 字里混着 SQL Server、MySQL、S3……），
现在只留匹配词前后各 30 字、在 `, ` / `; ` / `: ` 处断开
（`_kwic_snippet`，QUOTE_WINDOW_CHARS=30）。Kafka 引文 108 字 →
"MySQL, NoSQL, S3, Kafka" 23 字；REST API 引文 105 字 →
"distributed systems, REST APIs, microservices" 45 字；Spark 引文
131 字 → "data pipelines using Spark, PySpark, Hadoop, APIs" 49 字。
两个护栏：① 片段仍是原文子串（逐字规则照样过），切口只落在
分隔符处、绝不断在词组中间（"ASP.NET" 不会丢下 " Core"）；
② 锚点优先落在词边界——"SQL" 锚在 "SQL Server" 而不是 "MySQL"
里面（`_anchor_pos`）。实测：材料 eval 三档 recall 不变
（70%/85%/100%）、grounded 100%、没有规则变红；强档每条合格要点
$0.00096 → $0.000627（约 −35%）、级联 $0.000924 → $0.000591、
checked pipeline 总成本 $0.017829 → $0.011812；checker 对抗检测
仍 8/8、修复 8/8。诚实注脚同上：离线账放大了降幅；另外片段会
丢掉句首的标签（如 "Data and messaging:"），读证据时要连 chunk id
一起看——标签丢了，来源没丢。

## 下一步预告

- 真接一次 Batch API（Anthropic Message Batches，需 API key），
  把规则打分换成模型打分、对比确定性版。
- 或者把 checker 换成小模型调用：同样的 5 条规则、让模型当 checker，
  量它的检测率与成本——那时"核对比生成便宜"就不再是假设，而是 eval 数字。
