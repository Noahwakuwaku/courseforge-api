"""
exam_agent: Generates MCQ exam questions based on full learning material.
Requires the complete material (from material_agent) for deep, content-grounded questions.
"""
from utils import chat, parse_json
import json

SYSTEM = """你是专业的题库设计专家。
根据完整的课程学习材料，生成高质量的多选题考试。

要求：
- 生成 8-12 道题（覆盖所有章节，每章节至少 1 题）
- 每题 4 个选项（A/B/C/D）
- 题目类型混合：
  * 概念理解题（单选）
  * 应用分析题（可多选，correct_answers 含多个选项）
  * 代码分析题（如有代码内容）
- 题目必须基于材料中的具体内容，不得超出范围
- 难度分布：简单 30%、中等 50%、难 20%
- 选项设计要有干扰性，避免明显错误

只返回 JSON 数组：
[
  {
    "question": "题目（可包含代码片段）",
    "section_index": 0,
    "difficulty": "easy|medium|hard",
    "options": {"A": "选项A", "B": "选项B", "C": "选项C", "D": "选项D"},
    "correct_answers": ["A"],
    "explanation": "详细解析，说明为什么正确答案是对的，以及其他选项为什么错"
  }
]
不要有任何其他文字。
"""

async def run(course_name: str, material: dict) -> list[dict]:
    """
    material: the full material dict produced by material_agent
    """
    # Send full material — richer context → better questions
    messages = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": f"课程：{course_name}\n\n完整学习材料：\n{json.dumps(material, ensure_ascii=False, indent=2)}",
        },
    ]
    raw = await chat(messages, temperature=0.5)
    return parse_json(raw)
