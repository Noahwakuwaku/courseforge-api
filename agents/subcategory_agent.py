"""
subcategory_agent: Decides whether a course needs subcategories, and if so,
generates names that are strictly scoped to the parent course + subject.
"""
from utils import chat, parse_json

SYSTEM = """你是课程结构设计专家。
给定一门课程名称和所属学科，判断该课程是否需要拆分为子分类（子课程）。

判断标准：
- 内容跨度大、层次分明、适合分阶段学习 → 需要拆分
- 内容集中、单一主题 → 无需拆分

【命名约束】
如果需要拆分，子课程名称必须：
- 明确反映其在父课程中的具体位置和内容
- 不得使用「第X部分」「概论」「导论」等模糊通用标题
- 名称中须体现学科专有术语（如「斯宾诺莎的实体概念」而非「关于实体的讨论」）
- 子课程之间要形成完整、互补的覆盖，不重叠

只返回 JSON，格式如下：
{
  "has_subcategories": true/false,
  "subcategories": ["子课程1", "子课程2"]
}
不要有任何其他文字。
"""


async def run(subject_name: str, course_name: str) -> dict:
    messages = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": f"学科：{subject_name}\n课程：{course_name}",
        },
    ]
    raw = await chat(messages, temperature=0.3)
    return parse_json(raw)
