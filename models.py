"""
Collections:
  subjects        – top-level subject (e.g. 数据科学)
  courses         – courses under a subject (e.g. 微积分)
  subcategories   – sub-courses under a course (e.g. 多元微积分)
  lectures        – lecture outline/skeleton (from content_agent)
  materials       – full learnable content per lecture (from material_agent)
  exams           – MCQ exams generated from material

Document shapes are UNCHANGED from v1. Only addition: ensure_indexes()
creates compound indexes on the hot read paths so concurrent generation
doesn't hammer the collections with full scans.
"""
from bson import ObjectId
from utils import get_db

# ── subjects ───────────────────────────────────────────────────────────────────
async def create_subject(name: str, description: str = "") -> str:
    db = get_db()
    result = await db.subjects.insert_one({
        "name": name,
        "description": description,   # precise academic characterisation
        "status": "pending",
    })
    return str(result.inserted_id)

async def get_subject(subject_id: str) -> dict | None:
    db = get_db()
    doc = await db.subjects.find_one({"_id": ObjectId(subject_id)})
    if doc:
        doc["_id"] = str(doc["_id"])
    return doc

async def list_subjects() -> list[dict]:
    """Return all subjects sorted newest-first."""
    db = get_db()
    cursor = db.subjects.find({}, sort=[("_id", -1)])
    return [{**d, "_id": str(d["_id"])} async for d in cursor]

# ── courses ────────────────────────────────────────────────────────────────────
async def save_courses(subject_id: str, courses: list[str]) -> list[str]:
    db = get_db()
    docs = [
        {
            "subject_id": subject_id,
            "name": c,
            "has_subcategories": None,   # null = not yet evaluated
            "status": "pending",
        }
        for c in courses
    ]
    result = await db.courses.insert_many(docs)
    return [str(i) for i in result.inserted_ids]

async def get_courses(subject_id: str) -> list[dict]:
    db = get_db()
    cursor = db.courses.find({"subject_id": subject_id})
    return [
        {**d, "_id": str(d["_id"])}
        async for d in cursor
    ]

async def update_course(course_id: str, update: dict):
    db = get_db()
    await db.courses.update_one({"_id": ObjectId(course_id)}, {"$set": update})

# ── subcategories ──────────────────────────────────────────────────────────────
async def save_subcategories(course_id: str, subs: list[str]) -> list[str]:
    db = get_db()
    docs = [
        {
            "course_id": course_id,
            "name": s,
            "status": "pending",
        }
        for s in subs
    ]
    result = await db.subcategories.insert_many(docs)
    return [str(i) for i in result.inserted_ids]

async def get_subcategories(course_id: str) -> list[dict]:
    db = get_db()
    cursor = db.subcategories.find({"course_id": course_id})
    return [
        {**d, "_id": str(d["_id"])}
        async for d in cursor
    ]

# ── lectures ───────────────────────────────────────────────────────────────────
async def upsert_lecture(ref_id: str, ref_type: str, content: dict) -> str:
    """ref_type: 'course' | 'subcategory'"""
    db = get_db()
    result = await db.lectures.find_one_and_update(
        {"ref_id": ref_id, "ref_type": ref_type},
        {"$set": {"content": content, "status": "done"}},
        upsert=True,
        return_document=True,
    )
    return str(result["_id"])

async def get_lecture(ref_id: str, ref_type: str) -> dict | None:
    db = get_db()
    doc = await db.lectures.find_one({"ref_id": ref_id, "ref_type": ref_type})
    if doc:
        doc["_id"] = str(doc["_id"])
    return doc

# ── materials ──────────────────────────────────────────────────────────────────
async def upsert_material(lecture_id: str, material: dict) -> str:
    """Store full learning material linked to a lecture."""
    db = get_db()
    result = await db.materials.find_one_and_update(
        {"lecture_id": lecture_id},
        {"$set": {"material": material, "status": "done"}},
        upsert=True,
        return_document=True,
    )
    return str(result["_id"])

async def get_material(lecture_id: str) -> dict | None:
    db = get_db()
    doc = await db.materials.find_one({"lecture_id": lecture_id})
    if doc:
        doc["_id"] = str(doc["_id"])
    return doc

# ── exams ──────────────────────────────────────────────────────────────────────
async def upsert_exam(material_id: str, questions: list[dict]) -> str:
    """Exam is keyed by material_id (generated from full material)."""
    db = get_db()
    result = await db.exams.find_one_and_update(
        {"material_id": material_id},
        {"$set": {"questions": questions}},
        upsert=True,
        return_document=True,
    )
    return str(result["_id"])

async def get_exam_by_material(material_id: str) -> dict | None:
    db = get_db()
    doc = await db.exams.find_one({"material_id": material_id})
    if doc:
        doc["_id"] = str(doc["_id"])
    return doc


# ── cascade deletes ────────────────────────────────────────────────────────────
async def delete_subject_cascade(subject_id: str):
    """Delete a subject and ALL its descendant data."""
    db = get_db()
    courses = await get_courses(subject_id)

    for c in courses:
        await _delete_course_data(db, c["_id"])
        # subcategories
        subs = await get_subcategories(c["_id"])
        for s in subs:
            await _delete_sub_data(db, s["_id"])
        await db.subcategories.delete_many({"course_id": c["_id"]})

    await db.courses.delete_many({"subject_id": subject_id})
    await db.subjects.delete_one({"_id": ObjectId(subject_id)})


async def delete_subcategory_cascade(sub_id: str):
    """Delete a single subcategory and its lecture/material/exam."""
    db = get_db()
    await _delete_sub_data(db, sub_id)
    sub = await db.subcategories.find_one({"_id": ObjectId(sub_id)})
    course_id = sub["course_id"] if sub else None
    await db.subcategories.delete_one({"_id": ObjectId(sub_id)})

    # If no more subcategories under the parent course, reset has_subcategories
    if course_id:
        remaining = await db.subcategories.count_documents({"course_id": course_id})
        if remaining == 0:
            await db.courses.update_one(
                {"_id": ObjectId(course_id)},
                {"$set": {"has_subcategories": False, "status": "pending"}},
            )


async def _delete_course_data(db, course_id: str):
    """Remove lecture/material/exam for a course (not the course doc itself)."""
    lec = await db.lectures.find_one({"ref_id": course_id, "ref_type": "course"})
    if lec:
        await _delete_lecture_chain(db, str(lec["_id"]))
    await db.lectures.delete_one({"ref_id": course_id, "ref_type": "course"})


async def _delete_sub_data(db, sub_id: str):
    """Remove lecture/material/exam for a subcategory."""
    lec = await db.lectures.find_one({"ref_id": sub_id, "ref_type": "subcategory"})
    if lec:
        await _delete_lecture_chain(db, str(lec["_id"]))
    await db.lectures.delete_one({"ref_id": sub_id, "ref_type": "subcategory"})


async def _delete_lecture_chain(db, lecture_id: str):
    """Given a lecture _id string, delete its material and exam."""
    mat = await db.materials.find_one({"lecture_id": lecture_id})
    if mat:
        await db.exams.delete_many({"material_id": str(mat["_id"])})
        await db.materials.delete_one({"_id": mat["_id"]})


# ── indexes ────────────────────────────────────────────────────────────────────
async def ensure_indexes():
    """Create compound indexes on hot read paths.

    Without these, every concurrent task does a full collection scan to look
    up its lecture/material/exam — fine at small scale, brutal at 30+
    concurrent generations.

    Safe to call multiple times: create_index is idempotent on the same key.
    """
    db = get_db()
    # courses by subject (sidebar load, snapshot)
    await db.courses.create_index("subject_id")
    # subcategories by parent course
    await db.subcategories.create_index("course_id")
    # lectures keyed by (ref_id, ref_type) — every upsert_lecture / get_lecture
    await db.lectures.create_index(
        [("ref_id", 1), ("ref_type", 1)], unique=True
    )
    # materials keyed by lecture
    await db.materials.create_index("lecture_id", unique=True)
    # exams keyed by material
    await db.exams.create_index("material_id", unique=True)

async def delete_course_cascade(course_id: str):
    """Delete a course and ALL its descendants (subcategories + their
    lectures/materials/exams, and the course's own lecture chain if it
    has one because it had no subcategories)."""
    db = get_db()

    # 1. Delete the course's own lecture/material/exam (if no subcategories)
    await _delete_course_data(db, course_id)

    # 2. Delete every subcategory's lecture/material/exam
    subs = await get_subcategories(course_id)
    for s in subs:
        await _delete_sub_data(db, s["_id"])

    # 3. Delete the subcategory docs themselves
    await db.subcategories.delete_many({"course_id": course_id})

    # 4. Delete the course doc itself
    await db.courses.delete_one({"_id": ObjectId(course_id)})