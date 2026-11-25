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

**2026-11-23 迭代 17：窗口自适应**——一个 30 字窗口对 345 字的长句太宽松（integration 引文仍有 49 字）、对 54 字的短句又太爱切（Java 引文曾被切掉 "TypeScript."）。现在窗口按句长分三档：≤80 字整句保留、81–150 字用 30、>150 字收紧到 20（`_adaptive_window`）。实测：Kafka / REST API / Spark 三条引文纹丝不动（句子都在 30 字档）；integration 引文 49 字 → "between Substrate and Item Query" 36 字；Java 引文恢复整句 "Programming: Java, C#, Python, JavaScript, TypeScript."。材料 eval 三档 recall 仍 70%/85%/100%、grounded 100%、checker 对抗仍 8/8 检测 + 8/8 修复；诚实账：强档每条合格要点 $0.000627 → $0.000649（约 +3.5%）——短句引文变长了，成本小涨，换的是可读性，这次不是省钱迭代。

**2026-11-24 迭代 18：窗口按锚点周围的密度调**——句长是粗信号：345 字的 integration 句是散文头 + 列表尾，104 字的 REST API 句通篇都是列表。所以窗口再收一档的条件不是"句长"，而是"锚点周围密不密"：锚点前后一个基础窗口内 ≥4 个分隔符（`, ` / `; ` / `: `）才算埋进列表里（`_neighborhood_separators`），中句 30→20、长句 20→15。先量再定的教训：整句数项数会误伤——integration 句整句 9 项、锚点周围 0 个，按整句数会把它收到 15 字、把引文里的 "Query" 切掉；按锚点周围数，它 stays 20、引文不动。实测：REST API 引文 45 字 → "REST APIs, microservices" 24 字、Spark 49 字 → "pipelines using Spark, PySpark, Hadoop" 38 字、CI/CD 44 字 → "DevOps: Linux, CI/CD, Docker" 28 字；Kafka 因分隔符断点位置不变、纹丝不动；integration、Java 引文不动。材料 eval 三档 recall 仍 70%/85%/100%、grounded 100%、checker 对抗仍 8/8 检测 + 8/8 修复；强档每条合格要点 $0.000649 → $0.000582（约 −10%）、级联 $0.000613 → $0.000545、checked pipeline 总成本 $0.012213 → $0.010994——这次是省钱迭代，省的是输出 token（离线账把引文同时算进输入和输出，降幅被放大，方向对、数字别照搬）。

## 下一步预告

- 真接一次 Batch API（Anthropic Message Batches，需 API key），
  把规则打分换成模型打分、对比确定性版。
- 或者把 checker 换成小模型调用：同样的 5 条规则、让模型当 checker，
  量它的检测率与成本——那时"核对比生成便宜"就不再是假设，而是 eval 数字。

**2026-11-25 迭代 19：阈值敏感度分析（DENSE_NEIGHBORHOOD_SEPARATORS 4 → 5）**——接 11/24 留的练习，先预测、再实测、最后才决定留不留。预测：REST API（邻域 4 个分隔符）与 Spark（4 个）不再算"埋进列表"，窗口 30→20 的收紧取消，引文变回 11/23 的样子（45 字 / 49 字）；Kafka（5）、CI/CD（5）、Docker（6）仍收紧，不动；integration（0）、Java（短句整句）不动；recall 与 checker eval 不变。实测全中：REST API 24 字 → "distributed systems, REST APIs, microservices" 45 字、Spark 38 字 → "data pipelines using Spark, PySpark, Hadoop, APIs" 49 字，其余四条纹丝不动；材料 eval 三档 recall 仍 70%/85%/100%、grounded 100%、checker 对抗仍 8/8 检测 + 8/8 修复。决定留 5 不回 4：4 档切掉的是真上下文（"distributed systems"、"data pipelines" 是信号不是列表填充），5 档只在锚点真被 5 项以上包围时才收——Kafka / CI/CD / Docker 的干净收益全保留。代价：强档每条合格要点 $0.000582 → $0.000606（约 +4%）、级联 $0.000545 → $0.000570、checked pipeline 总成本 $0.010994 → $0.011428，200 个岗位约多 $0.005，噪声级。4 个测试按新行为更新（钉旧引文的断言，不是代码 bug）。
