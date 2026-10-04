"""A minimal agent loop: Prompt -> (Tool Use)* -> Final Answer.

Agent Loop (remember this shape — almost every agent framework is this):
  1. Send messages + tool schemas to the LLM
  2. If stop_reason == "tool_use": execute the tool(s), append tool_result, go to 1
  3. If stop_reason == "end_turn": return the final text
  4. Always cap iterations (max_steps) so a buggy loop cannot run/charge forever
"""
from __future__ import annotations

import os
import re

from .tools import TOOL_SCHEMAS, dispatch_tool

SYSTEM_PROMPT = """You are a job-search assistant for a backend Software Engineer II candidate.
Use tools when you need facts (preferences, cost) instead of guessing.
Be concise. Never submit an application — a human approves every submission."""

MODEL = os.getenv("JOB_AGENT_MODEL", "claude-sonnet-4-5")


def _mock_decide(user_text: str):
    """Offline stand-in for the LLM's *decision* (so you can learn the loop with no API key).

    A real LLM reads the tool descriptions and picks a tool + arguments by itself.
    Here we fake that decision with simple keyword/number matching.
    Returns (tool_name, args) or (None, None) for a direct answer.
    """
    text = user_text.lower()
    numbers = [int(n) for n in re.findall(r"\d+", text)]
    if "fetch" in text or "抓" in user_text or "搜岗" in user_text or "拉取" in user_text:
        return "fetch_jobs", {"source": "sample"}
    if "score" in text or "eval" in text or "评分" in user_text or "匹配" in user_text:
        return "score_jobs", {"source": "eval"}
    if "cost" in text or "成本" in user_text or "多少钱" in user_text:
        return "estimate_cost", {"num_jobs": numbers[0] if numbers else 100}
    if "preference" in text or "偏好" in user_text or "标准" in user_text or "criteria" in text:
        return "get_preferences", {}
    return None, None


def run_mock(user_text: str, max_steps: int = 5) -> str:
    """Run the same loop shape as the real agent, but with the mock decision-maker."""
    print(f"[user] {user_text}")
    for step in range(1, max_steps + 1):
        tool_name, args = _mock_decide(user_text) if step == 1 else (None, None)
        if tool_name is None:
            answer = "（mock）这是直接回答：没有需要查的事实时，LLM 不调用工具，直接回复。"
            print(f"[agent step {step}] stop_reason=end_turn\n[answer] {answer}")
            return answer
        print(f"[agent step {step}] stop_reason=tool_use -> {tool_name}({args})")
        result = dispatch_tool(tool_name, args)
        print(f"[tool_result] {result}")
        # In the real loop this result goes back to the LLM, which then writes the final answer.
        answer = f"（mock）工具 {tool_name} 返回：{result}"
        print(f"[agent step {step + 1}] stop_reason=end_turn\n[answer] {answer}")
        return answer
    raise RuntimeError("max_steps exceeded — loop guard triggered")


def run_real(user_text: str, max_steps: int = 5) -> str:
    """Real LLM path (Anthropic Messages API). Requires: pip install anthropic + ANTHROPIC_API_KEY."""
    import anthropic  # imported lazily so mock mode needs zero dependencies

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    messages = [{"role": "user", "content": user_text}]

    for step in range(1, max_steps + 1):
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            messages=messages,
        )
        print(f"[agent step {step}] stop_reason={response.stop_reason}")
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            return "".join(b.text for b in response.content if b.type == "text")

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    print(f"  -> {block.name}({block.input})")
                    result = dispatch_tool(block.name, dict(block.input))
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": str(result)}
                    )
            messages.append({"role": "user", "content": tool_results})
            continue

        raise RuntimeError(f"unexpected stop_reason: {response.stop_reason}")
    raise RuntimeError("max_steps exceeded — loop guard triggered")
