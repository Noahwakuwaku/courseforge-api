"""
content_agent: Generates lecture outline for a course/subcategory.

CONTEXT ANCHORING: subject_name, subject_description and parent_course_name
are injected into every call so the model never drifts away from the
intended domain. For example:
  subject="斯宾诺莎伦理学", parent="第二部分：心灵与意志"
  → the outline must stay strictly inside that scope.

NOTE: subject_description was previously declared in worker.gen_outline but
this function didn't accept it — every outline generation raised TypeError.
"""
from utils import chat, parse_json

SYSTEM = """你是一位经验丰富的大学讲师，擅长设计严谨、系统的课程讲义。

【重要约束】
你生成的所有内容必须严格锚定到用户给定的「学科 → 父课程 → 当前课程」三级上下文中：
- 不得引入与该学科无关的其他领域内容
- 章节标题、知识点、学习目标必须明确体现当前课程在整个学科体系中的定位
- 如果是子课程，内容必须是父课程的有机组成部分，而非独立展开

只返回 JSON，格式：
{
  "title": "课程标题（须与输入的课程名一致）",
  "description": "课程简介（2-3句话，需说明本课程在整个学科体系中的位置）",
  "sections": [
    {
      "title": "章节标题",
      "content": "章节内容概述（3-5句话）",
      "key_points": ["知识点1", "知识点2"]
    }
  ],
  "prerequisites": ["前置知识1"],
  "learning_outcomes": ["学习目标1", "学习目标2"]
}
不要有任何其他文字。
"""


def _build_context(
    subject_name: str,
    subject_description: str | None,
    parent_course_name: str | None,
    course_name: str,
) -> str:
    lines = [f"学科：{subject_name}"]
    if subject_description:
        lines.append(f"学科描述：{subject_description}")
    if parent_course_name:
        lines.append(f"父课程：{parent_course_name}")
    lines.append(f"当前课程：{course_name}")
    return "\n".join(lines)


async def run(
    course_name: str,
    subject_name: str,
    subject_description: str | None = None,
    parent_course_name: str | None = None,
    feedback: str | None = None,
) -> dict:
    user_msg = _build_context(
        subject_name, subject_description, parent_course_name, course_name
    )
    if feedback:
        user_msg += (
            "\n\n审核反馈（请根据此修改，但必须保持对学科上下文的严格锚定）：\n"
            f"{feedback}"
        )

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user_msg},
    ]
    raw = await chat(messages, temperature=0.6)
    return parse_json(raw)
