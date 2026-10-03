# job-agent — 求职 workflow agent

一步一步搭的实作项目：搜岗抓取 → 岗位匹配评分 → 定制申请材料 → 进度跟踪。

## 里程碑
1. ✅ 搭骨架：Python 项目跑通 LLM API 调用（prompt / tool use 基础）— 2026-10-02
2. ⬜ 搜岗抓取：拉取职位列表并存本地
3. ⬜ 岗位匹配评分：LLM 按个人标准打分（structured output）
4. ⬜ 定制申请材料：按 JD 生成申请要点（RAG：结合简历）
5. ⬜ 进度跟踪：申请状态管理 + 每日汇总
6. ⬜ 进阶：planning / memory 优化 + 简单 eval

## 快速开始（离线 mock，无需 API key）
```bash
cd ~/workspace/projects/job-agent
python3 main.py
python3 main.py "处理 200 个岗位大概要花多少钱?"
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
- `data/preferences.json` — 求职标准（后续评分工具的数据来源）
