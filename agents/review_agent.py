"""
review_agent: Reviews lecture content for completeness, quality, AND context fidelity.
Returns {"passed": bool, "feedback": str}

NOTE: subject_description was previously declared in worker.gen_outline but
this function didn't accept it — every outline generation raised TypeError.
"""
from utils import chat, parse_json
import json

SYSTEM = """你是严格的课程质量审核专家。
评审标准：
1. 内容是否完整覆盖当前课程主题
2. 章节结构是否合理、循序渐进
3. 是否有明显缺失的重要知识点
4. 学习目标是否明确可衡量
5. 【重点】内容是否严格锚定在给定的「学科 → 父课程 → 当前课程」上下文中
   - 是否存在偏离学科的泛化内容
   - 子课程内容是否真正属于父课程的范畴
   - 课程标题与实际内容是否一致

只返回 JSON：
{
  "passed": true/false,
  "feedback": "审核意见（如不通过，必须指出哪些内容偏离了学科上下文，应如何修正）"
}
不要有任何其他文字。
"""


async def run(
    course_name: str,
    lecture: dict,
    subject_name: str,
    subject_description: str | None = None,
    parent_course_name: str | None = None,
) -> dict:
    ctx = f"学科：{subject_name}"
    if subject_description:
        ctx += f"\n学科描述：{subject_description}"
    if parent_course_name:
        ctx += f"\n父课程：{parent_course_name}"
    ctx += f"\n当前课程：{course_name}"

    messages = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": (
                f"{ctx}\n\n讲义内容：\n"
                f"{json.dumps(lecture, ensure_ascii=False, indent=2)}"
            ),
        },
    ]
    raw = await chat(messages, temperature=0.2)
    return parse_json(raw)
