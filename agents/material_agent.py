"""
material_agent: Expands a lecture outline into deep, learnable material.

Design goals:
- Tech / applied courses (SQL, programming, DevOps, web, networking, mobile):
  code-first, hands-on, no academic flourishes
- Theoretical / mathematical courses (math, ML theory, algorithms, physics):
  derivations with intuition first, then code where applicable
- Humanities courses (philosophy, literature, history): close reading of
  primary texts and argument reconstruction — used ONLY when the subject
  actually is humanities, not as a fallback for anything we can't classify

The previous version had two prompt bugs:
  1. STEM keyword set was too narrow. Anything not explicitly STEM (e.g. SQL,
     web dev) fell through to the humanities prompt, which then mandated
     "原典为据" — so a SQL tutorial got written as if it were a Spinoza
     commentary, complete with invented references to Donald Rubin.
  2. The "上一节我们学习了..." connector rule was unconditional, so the
     first section of every course began with a callback to a section that
     didn't exist.

Both are fixed below. The output document shape is unchanged.
"""
from utils import chat, parse_json
import json


# ── Discipline classifier (3-way, broader keyword sets) ──────────────────────
#
# Order matters in _classify: humanities is checked first (most specific),
# then theoretical, then tech. The default fallback is `tech` rather than
# humanities — for an unrecognized subject, code-grounded prose is much
# safer than philosophical prose.

_TECH_KEYWORDS = {
    # programming languages
    "python", "java", "javascript", "typescript", "golang", " go ", "rust",
    "kotlin", "swift", "ruby", "php", "scala", "c++", "c#", ".net", "node",
    "node.js", "nodejs",
    # databases
    "sql", "mysql", "postgres", "postgresql", "mongodb", "mongo", "redis",
    "sqlite", "oracle", "数据库", "关系型", "nosql", "elasticsearch",
    "clickhouse", "kafka",
    # frontend
    "html", "css", "react", "vue", "angular", "svelte", "tailwind", "前端",
    "前端开发", "ui", "ux",
    # backend / api
    "后端", "django", "flask", "fastapi", "spring", "spring boot", "express",
    "nestjs", "rails", "api", "rest", "restful", "graphql", "grpc",
    # devops / cloud
    "docker", "kubernetes", "k8s", "aws", "gcp", "azure", "ci/cd", "jenkins",
    "github actions", "运维", "部署", "云计算", "微服务", "serverless",
    # OS / shell / tools
    "linux", "unix", "shell", "bash", "操作系统", "git", "github", "gitlab",
    # networking / web
    "http", "https", "websocket", "tcp", "udp", "网络协议", "计算机网络",
    # mobile
    "android", "ios", "flutter", "react native", "移动开发",
    # data engineering
    "spark", "hadoop", "airflow", "etl", "flink", "数据工程", "数据仓库",
    # security
    "网络安全", "渗透", "信息安全", "ssl", "tls", "owasp",
    # practical markers in Chinese course titles
    "实战", "实践", "入门", "实用", "应用", "开发", "工程",
}

_THEORETICAL_KEYWORDS = {
    # mathematics
    "数学", "微积分", "高等数学", "线性代数", "概率", "概率论", "数理统计",
    "矩阵", "向量", "微分", "积分", "导数", "梯度", "凸优化", "推断",
    "偏微分方程", "拓扑", "图论",
    # ML / AI theory
    "机器学习", "深度学习", "神经网络", "强化学习", "贝叶斯", "transformer",
    "损失函数", "信息论", "regression", "classification", "clustering",
    "bayesian", "gradient", "derivative", "integral",
    # CS theory
    "算法", "数据结构", "计算复杂度", "形式语言", "自动机", "可计算性",
    # natural sciences
    "物理", "量子", "力学", "热力学", "电磁学", "化学", "生物物理",
    # statistics
    "假设检验", "回归分析", "方差分析",
}

_HUMANITIES_KEYWORDS = {
    "哲学", "伦理学", "形而上学", "认识论", "现象学", "美学", "逻辑学",
    "斯宾诺莎", "黑格尔", "康德", "尼采", "海德格尔", "维特根斯坦",
    "亚里士多德", "柏拉图", "笛卡尔", "休谟",
    "文学", "诗歌", "小说", "戏剧", "比较文学",
    "历史", "思想史", "中世纪", "近代史", "古代史",
    "宗教", "神学", "佛学", "道学", "儒学",
    "艺术史", "音乐学",
    "kant", "hegel", "nietzsche", "heidegger", "wittgenstein", "ethics",
    "metaphysics",
}


def _classify(subject_name: str,
              subject_description: str,
              course_name: str) -> str:
    """Return 'humanities' | 'theoretical' | 'tech'.

    Also considers subject_description because the same name can mean very
    different things — '数据科学' as 'Python + SQL practical' is tech, but
    '数据科学' as 'statistical foundations' is theoretical.
    """
    text = " ".join([subject_name, subject_description or "", course_name]).lower()
    if any(kw in text for kw in _HUMANITIES_KEYWORDS):
        return "humanities"
    if any(kw in text for kw in _THEORETICAL_KEYWORDS):
        return "theoretical"
    if any(kw in text for kw in _TECH_KEYWORDS):
        return "tech"
    # Default to tech for unknown subjects — code-grounded prose fails more
    # gracefully than philosophical prose when the model has to write about
    # something it isn't sure about.
    return "tech"


# ── Shared rules (appended to every prompt) ───────────────────────────────────
#
# Two things every prompt must enforce regardless of discipline:
#   1. Section opening is conditional on index (first vs subsequent)
#   2. No academic / philosophical flourishes on technical content

_COMMON_TAIL = """
【章节开头规则（重要）】
- 第一节(index === 0)：开头必须是「本课程要解决什么 / 本节先讲什么」式的
  课程导言，不允许写"上一节我们学习了..."、"承接上节内容..."等任何回顾。
  第一节没有上一节。
- 后续节(index >= 1)：可以用 1 句话(20 字以内)简短回顾上一节作为衔接，
  然后立即进入本节内容。不要每节都强行套用三段式衔接公式。

【禁止的写作风格 — 严格遵守】
- 不出现"宏大叙事"、"认识论层面"、"奠基仪式"、"系统性叙事"、
  "深层结构"等空洞学术修辞
- 不引用与课程主题无关的学者或著作；如不确定某个引用是否真实存在，宁可不引
- 不在技术教程里写哲学化导论。例如 SQL 课程的开头不应该是
  "在探索性数据分析的宏大叙事中，数据预处理并非单纯的清洁工工作"
  而应该是 "实际数据里几乎总有缺失值。本节学如何识别它们，以及对应的处理方式。"

【输出 JSON 格式 — 严格遵守】
{
  "course_title": "课程标题",
  "sections": [
    {
      "index": 0,
      "title": "章节标题",
      "body": "完整 Markdown 正文（≥1000字）",
      "summary": "本节3个核心要点，每点一句话"
    }
  ]
}
只返回 JSON，不要有任何其他文字。
"""


# ── Tech / applied prompt ─────────────────────────────────────────────────────
SYSTEM_TECH = """你是一位经验丰富的工程师兼技术教程作者。你写的教程要让读者能直接上手实践，
不是论文，不是哲学随笔。

【写作风格】
1. **直奔主题**。开头一两句话说明"本节要解决什么问题、读完能做到什么"。
2. **代码先行**。每个概念用真实可运行的代码或命令片段展示——SQL 教程给 SQL 语句、
   Python 教程给 Python 片段、Docker 教程给 Dockerfile 和 shell 命令。
   先给可运行例子，再做解释，而不是先讲一大段抽象理论。
3. **用多个例子代替反复解释**。同一个概念不要换三种说法重复，直接展示三个
   不同场景的具体用法。
4. **结果要明确**。代码后用注释或独立块标明实际运行得到什么
   （SELECT 返回的行、print 的输出、报错文本）。
5. **讲清常见陷阱**。"新手最容易踩的坑"、"X vs Y 怎么选"这类对比要明确。

【格式】
- 代码块：```language ... ```
- 行内代码：`code`
- 提示/警告：> 开头
- 章节小标题：### 用于子标题
""" + _COMMON_TAIL


# ── Theoretical / mathematical prompt (was "STEM") ───────────────────────────
SYSTEM_THEORETICAL = """你是一位顶级理工科教材作者，擅长写出既严谨又实用的学习材料。

【写作风格】
1. **先直觉后公式**：每个概念先一句话说清"它解决什么问题"，再给数学定义。
2. **推导完整**：公式不能从天而降，中间步骤不能跳跃。
3. **代码是一等公民**：核心算法/概念给可运行 Python 代码：
   - 详细行注释（解释每行在做什么，不是简单复述代码）
   - 使用真实数据或固定随机种子（np.random.seed(42)）
   - 打印中间结果并用注释解释含义
   - 代码后跟一段"输出解读"
4. **例题精讲**：每节至少一道完整例题：
   - 【题目】具体数字/场景
   - 【思路分析】先想什么、为什么
   - 【详细求解】每步计算都写出来
   - 【结论与启发】这道题告诉我们什么
5. **禁止**：把同一概念用不同的话重复三遍；只给伪代码而不给可运行实现。

【格式】
- 行内公式：$...$
- 独立公式：$$...$$
- 代码块：```python ... ```
- 提示/注意：> 开头
""" + _COMMON_TAIL


# ── Humanities prompt (used ONLY for genuinely humanities subjects) ──────────
SYSTEM_HUMANITIES = """你是一位顶级人文学科教材作者，擅长把抽象理论变成清晰有层次的学习材料。

【写作风格】
1. **原典/史料为据**：
   - 如果学科有核心文本（如《伦理学》《存在与时间》），引用具体段落并注明出处
     (例：Ethics, Part I, Prop. 7)
   - 如果是综合性的历史/思想课，引用一手史料或权威研究，不做笼统概括
   - 切勿编造不存在的引用——不确定的引用宁可不引
2. **论证重建**：把原著/历史事件的逻辑链显式化（前提→结论），逐步拆解
3. **概念辨析**：关键术语给出
   - 作者的原始定义（引原文）
   - 与日常用法的区别
   - 与同时代或对立学说同名概念的对比
4. **历史语境**：适当补充写作/事件背景，说明在思想史或历史进程中的位置
5. **思考题**：每节末尾 1 道开放性思考题

【格式】
- 关键术语保留原文 + 中文解释：Substantia（实体）
- 引用格式：> 原文 (出处)
""" + _COMMON_TAIL


# ── Dispatch ──────────────────────────────────────────────────────────────────
_SYSTEM_BY_KIND = {
    "tech":        SYSTEM_TECH,
    "theoretical": SYSTEM_THEORETICAL,
    "humanities":  SYSTEM_HUMANITIES,
}

_HINT_BY_KIND = {
    "tech":
        "\n【学科类型】技术/实操类 — 必须代码先行、直奔主题，不做学术化包装。",
    "theoretical":
        "\n【学科类型】理工/理论类 — 必须含完整推导与可运行代码，每节≥1000字。",
    "humanities":
        "\n【学科类型】人文/理论类 — 必须基于真实原典/史料，显式重建论证结构。",
}


async def run(
    outline: dict,
    subject_name: str,
    subject_description: str = "",
    parent_course_name: str | None = None,
) -> dict:
    kind   = _classify(subject_name, subject_description, outline.get("title", ""))
    system = _SYSTEM_BY_KIND[kind]
    hint   = _HINT_BY_KIND[kind]

    anchor_lines = ["【学科锚点】", f"学科名称：{subject_name}"]
    if subject_description:
        anchor_lines.append(f"学科描述：{subject_description}")
    if parent_course_name:
        anchor_lines.append(f"父课程：{parent_course_name}")
    anchor_lines.append(f"当前课程：{outline.get('title', '')}")

    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                "\n".join(anchor_lines)
                + hint
                + "\n\n【课程纲要（严格按此展开每一节，禁止缩减章节数量）】\n"
                + json.dumps(outline, ensure_ascii=False, indent=2)
            ),
        },
    ]
    raw = await chat(messages, temperature=0.4)
    return parse_json(raw)
