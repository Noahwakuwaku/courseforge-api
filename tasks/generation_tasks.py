"""
Celery tasks for all generation operations.
Each task runs in a worker process with its own asyncio event loop.

Task naming convention:  gen_{what}
"""
import asyncio
from celery import Task
from .celery_app import celery_app

# ── Helper: run async code inside a sync Celery task ──────────────────────────
def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Shared async logic (imported at task runtime to avoid import-time DB init) ─
async def _outline_flow(course_name, subject_name, subject_description, parent_course_name,
                        ref_id, ref_type):
    from agents import content_agent, review_agent
    from models import upsert_lecture, update_course, get_db
    from config import settings
    from bson import ObjectId

    feedback = None
    for _ in range(settings.MAX_REVIEW_RETRIES):
        outline = await content_agent.run(
            course_name=course_name,
            subject_name=subject_name,
            subject_description=subject_description,
            parent_course_name=parent_course_name,
            feedback=feedback,
        )
        review = await review_agent.run(
            course_name=course_name,
            lecture=outline,
            subject_name=subject_name,
            subject_description=subject_description,
            parent_course_name=parent_course_name,
        )
        if review["passed"]:
            break
        feedback = review["feedback"]

    lecture_id = await upsert_lecture(ref_id, ref_type, outline)

    db = get_db()
    if ref_type == "course":
        await update_course(ref_id, {"status": "outline_done"})
    else:
        await db.subcategories.update_one(
            {"_id": ObjectId(ref_id)}, {"$set": {"status": "outline_done"}}
        )
    return {"lecture_id": lecture_id, "content": outline}


async def _material_flow(lecture_id: str):
    from agents import material_agent
    from models import upsert_material, get_material, get_db
    from bson import ObjectId

    db = get_db()
    lec_doc = await db.lectures.find_one({"_id": ObjectId(lecture_id)})
    if not lec_doc:
        raise ValueError(f"Lecture {lecture_id} not found")

    outline  = lec_doc["content"]
    ref_id   = lec_doc.get("ref_id")
    ref_type = lec_doc.get("ref_type")

    # Recover context from DB chain
    subject_name, subject_description, parent_course_name = \
        await _recover_context(db, ref_id, ref_type)

    mat = await material_agent.run(
        outline=outline,
        subject_name=subject_name,
        subject_description=subject_description,
        parent_course_name=parent_course_name,
    )
    material_id = await upsert_material(lecture_id, mat)
    await db.lectures.update_one(
        {"_id": ObjectId(lecture_id)},
        {"$set": {"status": "material_done", "material_id": material_id}},
    )
    return {"material_id": material_id, "material": mat}


async def _exam_flow(material_id: str):
    from agents import exam_agent
    from models import upsert_exam, get_db
    from bson import ObjectId

    db = get_db()
    mat_doc = await db.materials.find_one({"_id": ObjectId(material_id)})
    if not mat_doc:
        raise ValueError(f"Material {material_id} not found")

    mat = mat_doc["material"]
    questions  = await exam_agent.run(mat.get("course_title", "课程"), mat)
    exam_id    = await upsert_exam(material_id, questions)
    return {"exam_id": exam_id, "questions": questions}


async def _recover_context(db, ref_id, ref_type):
    """Returns (subject_name, subject_description, parent_course_name|None)."""
    from bson import ObjectId

    if ref_type == "course":
        course = await db.courses.find_one({"_id": ObjectId(ref_id)})
        if not course:
            return "未知学科", "", None
        subject = await db.subjects.find_one({"_id": ObjectId(course["subject_id"])})
        if not subject:
            return "未知学科", "", None
        return subject["name"], subject.get("description", ""), None

    if ref_type == "subcategory":
        sub = await db.subcategories.find_one({"_id": ObjectId(ref_id)})
        if not sub:
            return "未知学科", "", None
        course = await db.courses.find_one({"_id": ObjectId(sub["course_id"])})
        if not course:
            return "未知学科", "", None
        subject = await db.subjects.find_one({"_id": ObjectId(course["subject_id"])})
        if not subject:
            return "未知学科", "", None
        return subject["name"], subject.get("description", ""), course["name"]

    return "未知学科", "", None


# ── Celery tasks ───────────────────────────────────────────────────────────────

@celery_app.task(bind=True, name="gen_outline")
def gen_outline(self, course_name, subject_name, subject_description,
                parent_course_name, ref_id, ref_type):
    try:
        return _run(_outline_flow(
            course_name, subject_name, subject_description,
            parent_course_name, ref_id, ref_type,
        ))
    except Exception as exc:
        self.update_state(state="FAILURE", meta={"error": str(exc)})
        raise


@celery_app.task(bind=True, name="gen_material")
def gen_material(self, lecture_id: str):
    try:
        return _run(_material_flow(lecture_id))
    except Exception as exc:
        self.update_state(state="FAILURE", meta={"error": str(exc)})
        raise


@celery_app.task(bind=True, name="gen_exam")
def gen_exam(self, material_id: str):
    try:
        return _run(_exam_flow(material_id))
    except Exception as exc:
        self.update_state(state="FAILURE", meta={"error": str(exc)})
        raise
