"""
FastAPI router — uses arq for async task dispatch.

arq jobs are enqueued via `await queue.enqueue_job(...)` and polled via:
  * GET  /tasks/{job_id}         — single task (unchanged, kept for compat)
  * POST /tasks/batch            — bulk poll (NEW, primary path for batch UI)

Batch endpoint matters because under high-concurrency generation the UI may
have 30+ in-flight tasks; one-at-a-time polling burns RPS for no reason.
"""
import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from bson import ObjectId
from arq import create_pool
from arq.jobs import Job, JobStatus

from tasks.worker import REDIS, gen_outline, gen_material, gen_exam, gen_expand_course
from agents import skeleton_agent
import models

router = APIRouter()


# ── Redis pool (shared across requests) ───────────────────────────────────────
_arq_pool = None

async def get_queue():
    global _arq_pool
    if _arq_pool is None:
        _arq_pool = await create_pool(REDIS)
    return _arq_pool


# ── Schemas ───────────────────────────────────────────────────────────────────
class CreateSubjectReq(BaseModel):
    name: str

class GenerateContentReq(BaseModel):
    subject_name: str

class BatchTaskReq(BaseModel):
    task_ids: list[str]


# ── Task status: single (kept for compat) ─────────────────────────────────────
async def _resolve_task(queue, job_id: str) -> dict:
    """Map arq state to the wire format used by the frontend."""
    job    = Job(job_id, queue)
    status = await job.status()

    if status == JobStatus.not_found:
        return {"task_id": job_id, "state": "NOT_FOUND", "result": None}
    if status in (JobStatus.deferred, JobStatus.queued):
        return {"task_id": job_id, "state": "PENDING",  "result": None}
    if status == JobStatus.in_progress:
        return {"task_id": job_id, "state": "STARTED",  "result": None}
    if status == JobStatus.complete:
        info = await job.result_info()
        if info and info.success:
            return {"task_id": job_id, "state": "SUCCESS", "result": info.result}
        err = str(info.result) if info else "unknown error"
        return {"task_id": job_id, "state": "FAILURE", "result": {"error": err}}

    return {"task_id": job_id, "state": str(status), "result": None}


@router.get("/tasks/{job_id}")
async def get_task_status(job_id: str):
    """Poll after any generate call. Kept for backward compat — prefer
    POST /tasks/batch when polling more than one task."""
    queue = await get_queue()
    return await _resolve_task(queue, job_id)


@router.post("/tasks/batch")
async def get_tasks_batch(req: BatchTaskReq):
    """Poll multiple tasks in one request.

    Frontend uses this to drive the whole "batch generate N items" UI from
    a single polling loop instead of N parallel polls. Response shape:

        {
          "tasks": {
            "<task_id>": {"task_id": ..., "state": ..., "result": ...},
            ...
          }
        }
    """
    queue   = await get_queue()
    ids     = req.task_ids or []
    results = await asyncio.gather(*[_resolve_task(queue, tid) for tid in ids])
    return {"tasks": {r["task_id"]: r for r in results}}


# ── 1. Skeleton (sync — fast) ─────────────────────────────────────────────────
@router.post("/subjects")
async def create_subject(req: CreateSubjectReq):
    result = await skeleton_agent.run(req.name)
    if isinstance(result, list):
        description = req.name
        courses     = result
    else:
        description = result.get("description", req.name)
        courses     = result.get("courses", [])

    subject_id = await models.create_subject(req.name, description)
    course_ids = await models.save_courses(subject_id, courses)
    await models.get_db().subjects.update_one(
        {"_id": ObjectId(subject_id)}, {"$set": {"status": "skeleton_done"}}
    )
    return {"subject_id": subject_id, "description": description,
            "courses": courses, "course_ids": course_ids}


@router.get("/subjects/{subject_id}/courses")
async def list_courses(subject_id: str):
    courses = await models.get_courses(subject_id)
    for c in courses:
        if c.get("has_subcategories"):
            c["subcategories"] = await models.get_subcategories(c["_id"])
    return courses


@router.get("/subjects")
async def list_subjects():
    return await models.list_subjects()


@router.get("/subjects/{subject_id}/snapshot")
async def get_subject_snapshot(subject_id: str):
    subject = await models.get_subject(subject_id)
    if not subject:
        raise HTTPException(404, "Subject not found")

    courses = await models.get_courses(subject_id)
    for c in courses:
        if c.get("has_subcategories"):
            c["subcategories"] = await models.get_subcategories(c["_id"])

    outlines: dict[str, Any] = {}
    materials: dict[str, Any] = {}
    exams: dict[str, Any]     = {}

    async def _load_leaf(ref_id, ref_type):
        lec = await models.get_lecture(ref_id, ref_type)
        if not lec: return
        key = f"{ref_type}:{ref_id}"
        lid = str(lec["_id"])
        outlines[key] = {"lectureId": lid, **lec.get("content", {})}
        mat = await models.get_material(lid)
        if mat:
            mid = str(mat["_id"])
            materials[lid] = {"materialId": mid, **mat.get("material", {})}
            exam = await models.get_exam_by_material(mid)
            if exam:
                exams[mid] = exam.get("questions", [])

    tasks = []
    for c in courses:
        if c.get("has_subcategories") is False or c.get("has_subcategories") is None:
            tasks.append(_load_leaf(c["_id"], "course"))
        if c.get("subcategories"):
            for sub in c["subcategories"]:
                tasks.append(_load_leaf(sub["_id"], "subcategory"))
    await asyncio.gather(*tasks)

    return {"subject": subject, "courses": courses,
            "outlines": outlines, "materials": materials, "exams": exams}


# ── 2. Expand subcategories ───────────────────────────────────────────────────
#
# v1 ran all LLM calls inline on the FastAPI process via asyncio.gather.
# That blocks the API thread for the duration AND bypasses the worker's
# concurrency/retry/semaphore logic. We now provide TWO endpoints:
#
#   POST /subjects/{id}/expand        — sync (legacy, small N is fine)
#   POST /subjects/{id}/expand/async  — enqueue arq jobs, return task_ids
#
# The frontend uses the async variant + batch polling for snappy UX.

async def _expand_course_inline(subject_name: str, course: dict) -> dict:
    """Inline path (legacy /expand) — still uses arq's chat() so it gets
    the retry + LLM semaphore for free."""
    from agents import subcategory_agent
    result   = await subcategory_agent.run(subject_name, course["name"])
    has_subs = result["has_subcategories"]
    update   = {"has_subcategories": has_subs}
    sub_ids  = []
    if has_subs and result.get("subcategories"):
        sub_ids = await models.save_subcategories(course["_id"], result["subcategories"])
        update["status"] = "expanded"
    await models.update_course(course["_id"], update)
    return {"course_id": course["_id"], "course_name": course["name"],
            "has_subcategories": has_subs, "subcategory_ids": sub_ids}


@router.post("/subjects/{subject_id}/expand")
async def expand_subject(subject_id: str):
    subject = await models.get_subject(subject_id)
    if not subject:
        raise HTTPException(404, "Subject not found")
    courses = await models.get_courses(subject_id)
    results = await asyncio.gather(
        *[_expand_course_inline(subject["name"], c) for c in courses]
    )
    return {"expanded": results}


@router.post("/subjects/{subject_id}/expand/async")
async def expand_subject_async(subject_id: str):
    """Queue each course's expansion as a separate arq job. Frontend gets
    task_ids and batch-polls them — UI updates per-course as they finish.

    We pass the course name in the job args (rather than just its id) so the
    worker doesn't need to round-trip Mongo just to read the name. This also
    makes the path robust against config mismatches (e.g. worker connected
    to a different DB than the API): the LLM call can still run.
    """
    subject = await models.get_subject(subject_id)
    if not subject:
        raise HTTPException(404, "Subject not found")
    courses = await models.get_courses(subject_id)
    queue   = await get_queue()
    jobs    = await asyncio.gather(*[
        queue.enqueue_job(
            "gen_expand_course",
            subject["name"],
            c["_id"],
            c["name"],          # NEW: course name in args, no lookup needed
        )
        for c in courses
    ])
    return {
        "tasks": [
            {"task_id": j.job_id, "course_id": c["_id"], "course_name": c["name"]}
            for j, c in zip(jobs, courses)
        ]
    }


# ── 3. Generate outline → arq task ───────────────────────────────────────────
@router.post("/courses/{course_id}/content")
async def generate_course_content(course_id: str, req: GenerateContentReq):
    course = await _get_course_or_404(course_id)
    sn, sd = await _get_subject_context(course["subject_id"])
    queue  = await get_queue()
    job    = await queue.enqueue_job(
        "gen_outline", course["name"], sn, sd, None, course_id, "course"
    )
    return {"task_id": job.job_id, "course_id": course_id}

@router.post("/courses/{course_id}/regenerate")
async def regenerate_course_content(course_id: str, req: GenerateContentReq):
    return await generate_course_content(course_id, req)


@router.post("/subcategories/{sub_id}/content")
async def generate_sub_content(sub_id: str, req: GenerateContentReq):
    sub                 = await _get_sub_or_404(sub_id)
    parent_name, sn, sd = await _get_sub_context(sub["course_id"])
    queue               = await get_queue()
    job                 = await queue.enqueue_job(
        "gen_outline", sub["name"], sn, sd, parent_name, sub_id, "subcategory"
    )
    return {"task_id": job.job_id, "sub_id": sub_id}

@router.post("/subcategories/{sub_id}/regenerate")
async def regenerate_sub_content(sub_id: str, req: GenerateContentReq):
    return await generate_sub_content(sub_id, req)


# ── 4. Get outline ────────────────────────────────────────────────────────────
@router.get("/lectures/{ref_type}/{ref_id}")
async def get_lecture(ref_type: str, ref_id: str):
    lecture = await models.get_lecture(ref_id, ref_type)
    if not lecture:
        raise HTTPException(404, "Lecture not found")
    return lecture


# ── 5. Generate material → arq task ──────────────────────────────────────────
@router.post("/lectures/{lecture_id}/material")
async def generate_material(lecture_id: str):
    lec = await models.get_db().lectures.find_one({"_id": ObjectId(lecture_id)})
    if not lec:
        raise HTTPException(404, "Lecture not found")
    queue = await get_queue()
    job   = await queue.enqueue_job("gen_material", lecture_id)
    return {"task_id": job.job_id, "lecture_id": lecture_id}

@router.post("/lectures/{lecture_id}/material/regenerate")
async def regenerate_material(lecture_id: str):
    return await generate_material(lecture_id)

@router.get("/lectures/{lecture_id}/material")
async def get_material(lecture_id: str):
    mat = await models.get_material(lecture_id)
    if not mat:
        raise HTTPException(404, "Material not found")
    return mat


# ── 6. Exam → arq task ────────────────────────────────────────────────────────
@router.post("/materials/{material_id}/exam")
async def generate_exam(material_id: str):
    mat = await models.get_db().materials.find_one({"_id": ObjectId(material_id)})
    if not mat:
        raise HTTPException(404, "Material not found")
    queue = await get_queue()
    job   = await queue.enqueue_job("gen_exam", material_id)
    return {"task_id": job.job_id, "material_id": material_id}

@router.get("/materials/{material_id}/exam")
async def get_exam(material_id: str):
    exam = await models.get_exam_by_material(material_id)
    if not exam:
        raise HTTPException(404, "Exam not found")
    return exam


# ── 7. Delete ─────────────────────────────────────────────────────────────────
@router.delete("/subjects/{subject_id}")
async def delete_subject(subject_id: str):
    if not await models.get_subject(subject_id):
        raise HTTPException(404, "Subject not found")
    await models.delete_subject_cascade(subject_id)
    return {"deleted": subject_id}

@router.delete("/subcategories/{sub_id}")
async def delete_subcategory(sub_id: str):
    db  = models.get_db()
    doc = await db.subcategories.find_one({"_id": ObjectId(sub_id)})
    if not doc:
        raise HTTPException(404, "Subcategory not found")
    await models.delete_subcategory_cascade(sub_id)
    return {"deleted": sub_id, "course_id": doc.get("course_id")}


# ── Helpers ───────────────────────────────────────────────────────────────────
async def _get_subject_context(subject_id: str) -> tuple[str, str]:
    db  = models.get_db()
    doc = await db.subjects.find_one({"_id": ObjectId(subject_id)})
    if not doc: return "未知学科", ""
    return doc["name"], doc.get("description", "")

async def _get_sub_context(course_id: str) -> tuple[str, str, str]:
    db     = models.get_db()
    course = await db.courses.find_one({"_id": ObjectId(course_id)})
    if not course: return "", "未知学科", ""
    sn, sd = await _get_subject_context(course["subject_id"])
    return course["name"], sn, sd

async def _get_course_or_404(course_id: str) -> dict:
    db  = models.get_db()
    doc = await db.courses.find_one({"_id": ObjectId(course_id)})
    if not doc: raise HTTPException(404, "Course not found")
    doc["_id"] = str(doc["_id"])
    return doc

async def _get_sub_or_404(sub_id: str) -> dict:
    db  = models.get_db()
    doc = await db.subcategories.find_one({"_id": ObjectId(sub_id)})
    if not doc: raise HTTPException(404, "Subcategory not found")
    doc["_id"] = str(doc["_id"])
    return doc

@router.delete("/courses/{course_id}")
async def delete_course(course_id: str):
    db  = models.get_db()
    doc = await db.courses.find_one({"_id": ObjectId(course_id)})
    if not doc:
        raise HTTPException(404, "Course not found")
    await models.delete_course_cascade(course_id)
    return {"deleted": course_id, "subject_id": doc.get("subject_id")}