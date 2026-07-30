#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 MongoDB 读取【子分类(subcategory)】最后一级页面，拼成 courseDetail 链接，
追加写入现有 sitemap.xml。

链接格式（注意 & 已转义为 &amp;，这是 XML/sitemap 标准要求）：
  https://www.juzhiart.com/courseDetail?courseId={subject_id}&amp;chapterId={lecture_id}&amp;level=2

字段映射（串 4 张表）：
  subcategories._id          → 用来找它的 lecture
  subcategories.course_id    → courses._id
  courses.subject_id         → courseId
  lectures._id               → chapterId
    （条件：lectures.ref_id = sub._id 且 ref_type = "subcategory"）
  level 固定 = 2

行为：
  - 查不到对应 lecture 的 subcategory → 跳过，不生成链接
  - 在现有 sitemap 基础上【追加】，不覆盖、不新建
  - 自动去重（& 与 &amp; 归一化比较），可反复运行
  - 新链接加到 </urlset> 之前，lastmod 用今天
  - 运行前自动备份原文件为 sitemap.xml.bak

依赖：
  pip install pymongo
"""

import re
import sys
from datetime import date

from pymongo import MongoClient

# ─────────────────────────── 配置区 ───────────────────────────

MONGO_URI = "mongodb://localhost:27017"
MONGO_DB  = "course_gen_new"

SITEMAP_PATH = "sitemap.xml"
BASE_URL     = "https://www.juzhiart.com/courseDetail"
LASTMOD      = date.today().isoformat()   # 今天；也可手写 "2026-05-28"

# 只导出符合条件的子分类；{} 表示全部子分类
# 例：只要已生成纲要的 → {"status": {"$in": ["outline_done", "material_done"]}}
SUB_FILTER = {}

# ─────────────────────────── 逻辑 ───────────────────────────


def main():
    # 1. 读现有 sitemap
    try:
        with open(SITEMAP_PATH, encoding="utf-8") as f:
            xml = f.read()
    except FileNotFoundError:
        print(f"❌ 找不到 {SITEMAP_PATH}，请把脚本和 sitemap.xml 放同一目录")
        sys.exit(1)

    if "</urlset>" not in xml:
        print("❌ 现有文件里找不到 </urlset>，格式异常，已中止")
        sys.exit(1)

    existing = set(re.findall(r"<loc>(.*?)</loc>", xml))
    # 归一化：& 与 &amp; 视为同一条，保证去重正确
    existing_norm = {u.replace("&amp;", "&") for u in existing}
    print(f"现有 sitemap：{len(existing)} 条 URL")

    # 2. 连 MongoDB
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
    except Exception as e:
        print(f"❌ 连接 MongoDB 失败：{e}")
        print(f"   检查 MONGO_URI={MONGO_URI!r}，以及 mongod 是否在运行")
        sys.exit(1)

    db = client[MONGO_DB]

    # 3. 批量预加载，避免逐条查询（整体只 3 次查询）
    # 3a. 子分类
    subs = list(db.subcategories.find(
        SUB_FILTER, {"_id": 1, "course_id": 1}
    ))
    print(f"子分类(subcategories)：{len(subs)} 条")
    if not subs:
        print("⚠️  没读到任何子分类。确认库名/过滤条件，或该库是否有 expanded 的课程。")
        return

    # 3b. courses → 建 course_id(str) → subject_id 映射
    #     课程的 _id 在 subcategory 里以字符串形式存（course_id 是 str）
    course_ids = {str(s["course_id"]) for s in subs if s.get("course_id")}
    # course._id 可能是 ObjectId，也可能是 str，两种都查
    from bson import ObjectId
    oid_list = []
    for cid in course_ids:
        try:
            oid_list.append(ObjectId(cid))
        except Exception:
            pass
    courses = list(db.courses.find(
        {"$or": [
            {"_id": {"$in": oid_list}},
            {"_id": {"$in": list(course_ids)}},
        ]},
        {"_id": 1, "subject_id": 1},
    ))
    course_to_subject = {str(c["_id"]): str(c.get("subject_id", "")) for c in courses}
    print(f"关联到 courses：{len(courses)} 条")

    # 3c. lectures → 建 ref_id(sub._id) → lecture._id 映射
    #     只取 ref_type="subcategory" 的
    sub_id_strs = [str(s["_id"]) for s in subs]
    lectures = list(db.lectures.find(
        {"ref_type": "subcategory", "ref_id": {"$in": sub_id_strs}},
        {"_id": 1, "ref_id": 1},
    ))
    subref_to_lecture = {str(l["ref_id"]): str(l["_id"]) for l in lectures}
    print(f"关联到 lectures：{len(lectures)} 条")

    # 4. 拼链接
    new_urls = []
    skipped_no_lecture = 0
    skipped_no_subject = 0
    skipped_dup = 0
    seen = set()

    for s in subs:
        sub_id   = str(s["_id"])
        cid      = str(s.get("course_id", ""))

        lecture_id = subref_to_lecture.get(sub_id)
        if not lecture_id:
            skipped_no_lecture += 1     # 还没生成 lecture → 跳过
            continue

        subject_id = course_to_subject.get(cid)
        if not subject_id:
            skipped_no_subject += 1     # 追溯不到 subject_id → 跳过
            continue

        url = (f"{BASE_URL}?courseId={subject_id}"
               f"&amp;chapterId={lecture_id}&amp;level=2")
        url_norm = url.replace("&amp;", "&")
        if url_norm in existing_norm or url_norm in seen:
            skipped_dup += 1
            continue
        seen.add(url_norm)
        new_urls.append(url)

    if not new_urls:
        print("\n没有需要追加的新链接。文件未改动。")
        print(f"（缺 lecture 跳过 {skipped_no_lecture}，"
              f"缺 subject 跳过 {skipped_no_subject}，重复 {skipped_dup}）")
        return

    # 5. 组装并插入
    blocks = [
        f"<url>\n<loc>{u}</loc>\n<lastmod>{LASTMOD}</lastmod>\n</url>"
        for u in new_urls
    ]
    new_xml = xml.replace("</urlset>", "\n".join(blocks) + "\n</urlset>")

    # 6. 备份 + 写回
    with open(SITEMAP_PATH + ".bak", "w", encoding="utf-8") as f:
        f.write(xml)
    with open(SITEMAP_PATH, "w", encoding="utf-8") as f:
        f.write(new_xml)

    # 7. 报告
    print("\n────────── 完成 ──────────")
    print(f"新增追加：{len(new_urls)} 条")
    print(f"跳过（未生成lecture）：{skipped_no_lecture} 条")
    if skipped_no_subject:
        print(f"跳过（追溯不到subject）：{skipped_no_subject} 条")
    print(f"跳过（重复）：{skipped_dup} 条")
    print(f"最终总数：{len(existing) + len(new_urls)} 条")
    print(f"原文件已备份：{SITEMAP_PATH}.bak")
    print(f"lastmod：{LASTMOD}")


if __name__ == "__main__":
    main()