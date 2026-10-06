# job-agent — 求职 workflow agent

一步一步搭的实作项目：搜岗抓取 → 岗位匹配评分 → 定制申请材料 → 进度跟踪。

## 里程碑
1. ✅ 搭骨架：Python 项目跑通 LLM API 调用（prompt / tool use 基础）— 2026-10-02
2. ✅ 搜岗抓取：拉取职位列表并存本地（去重 + 来源链接 + 抓取时间戳，幂等）— 2026-10-03
3. ✅ 岗位匹配评分：硬规则 → structured output 评分 → 置信度阈值转人工 + 10 条 eval 集 — 2026-10-04
4. ✅ 定制申请材料：按 JD 生成申请要点（RAG：结合简历）— 2026-10-05
5. ✅ 进度跟踪：申请状态管理 + 每日汇总（状态机 + durable store）— 2026-10-06
6. ⬜ 进阶：planning / memory 优化

## 快速开始（离线 mock，无需 API key）
```bash
cd ~/workspace/projects/job-agent
python3 main.py
python3 main.py "处理 200 个岗位大概要花多少钱?"
python3 main.py "帮我抓取岗位"
python3 main.py "帮我给岗位评分"
python3 main.py "帮我看今天的进度汇总"
python3 -m job_agent.evaluate
python3 -m unittest discover -s tests -v
```

## 真实 LLM 模式
```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...   # 用你自己的 key，不要发到聊天里
python3 main.py --real "我的求职标准是什么?"
```

## 结构
- `main.py` — 入口，选 mock / real
- `job_agent/agent.py` — agent loop（核心：循环调用 LLM 直到 end_turn，带 max_steps 保险）
- `job_agent/tools.py` — 工具函数 + 工具 schema + 分发器
- `job_agent/fetchers.py` — 抓取层（sample 离线样本 / Greenhouse 公开 API）
- `job_agent/jobs.py` — 标准化、指纹去重、本地存储（`data/jobs.json`）
- `job_agent/scoring.py` — 里程碑 3 评分：硬规则 → structured output（schema 校验）→ 置信度阈值转人工
- `job_agent/materials.py` — 里程碑 4 材料：RAG 检索简历 + grounding 校验 + model routing + RunBudget
- `job_agent/tracking.py` — 里程碑 5 跟踪：申请状态机（合法迁移）+ durable store（`data/applications.json`）+ 每日汇总
- `job_agent/evaluate.py` — 里程碑 3/4 eval：跑标注集算 accuracy / recall
- `data/eval_jobs.json` — 10 条人工标注的评分测试集（每条带 expected_verdict + 标注理由）
- `data/preferences.json` — 求职标准（后续评分工具的数据来源）
- `data/sample_jobs_raw.json` — 离线样本（含故意重复，练去重）
