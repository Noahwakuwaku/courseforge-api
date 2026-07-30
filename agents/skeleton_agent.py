"""
skeleton_agent: Given a subject name, identify the subject precisely and
generate a structured course list.

Returns: {"description": str, "courses": list[str]}
  description — precise academic characterisation used as anchor context
                for ALL downstream agents.
"""
from utils import chat, parse_json

SYSTEM = """你是一位资深教育课程设计专家。
用户会给你一个学科名称，你需要：
1. 先精确识别这门学科的真实含义（作者、时代、核心论题等），生成一段简短但准确的学科描述
2. 然后列出该学科完整学习体系所需的课程列表

【学科描述要求】
- 明确指出该学科/著作的作者、年代、核心命题
- 不超过 80 字
- 例：「斯宾诺莎1677年著作《伦理学》，以几何学公理化方法论证上帝（自然）、心灵与情感理论，最终指向理性自由的伦理学体系」

【课程列表要求】
- 数量在 5-12 门之间
- 按学习顺序排列（先基础后进阶）
- 课程名称必须体现该学科的专有术语，不得使用通用标题

只返回 JSON，不要有任何其他文字：
{
  "description": "学科精确描述",
  "courses": ["课程名1", "课程名2", ...]
}
"""


async def run(subject_name: str) -> dict:
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"学科：{subject_name}"},
    ]
    raw = await chat(messages, temperature=0.5)
    result = parse_json(raw)

    # Backward compat: model may still return a plain list
    if isinstance(result, list):
        return {"description": subject_name, "courses": result}
    return result
