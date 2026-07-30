"""
arq worker — replaces Celery entirely.

Startup:
    arq tasks.worker.WorkerSettings

All tasks are native async functions. arq stores job state/result in Redis
using the same key pattern as before, so the router's /tasks/{job_id} polling
endpoint works without changes (we adapt the result format to match).

Concurrency knobs (see config.py):
    settings.WORKER_MAX_JOBS    — concurrent tasks per worker process
    settings.LLM_MAX_CONCURRENCY — concurrent LLM calls across all tasks
                                  (cap is per-process; multiply across procs)

For more throughput, raise WORKER_MAX_JOBS and/or run multiple worker
processes:
    arq tasks.worker.WorkerSettings   # process 1
    arq tasks.worker.WorkerSettings   # process 2
    ...
Each will pull from the same Redis queue.
"""
from arq.connections import RedisSettings
from config import settings


# ── Parse REDIS_URL → arq RedisSettings ───────────────────────────────────────
def _redis_settings() -> RedisSettings:
    url = settings.REDIS_URL  # e.g. redis://localhost:6379/0
    url = url.replace("redis://", "")
    host_port, *db_part = url.split("/")
    host, *port_part = host_port.split(":")
    port = int(port_part[0]) if port_part else 6379
    db   = int(db_part[0])   if db_part   else 0
    return RedisSettings(host=host, port=port, database=db)

REDIS = _redis_settings()


# ── Shared logic (unchanged) ───────────────────────────────────────────────────
async def _recover_context(db, ref_id, ref_type):
    from bson import ObjectId
    if ref_type == "course":
        course  = await db.courses.find_one({"_id": ObjectId(ref_id)})
        if not course: return "未知学科", "", None
        subject = await db.subjects.find_one({"_id": ObjectId(course["subject_id"])})
        if not subject: return "未知学科", "", None
        return subject["name"], subject.get("description", ""), None
    if ref_type == "subcategory":
        sub     = await db.subcategories.find_one({"_id": ObjectId(ref_id)})
        if not sub: return "未知学科", "", None
        course  = await db.courses.find_one({"_id": ObjectId(sub["course_id"])})
        if not course: return "未知学科", "", None
        subject = await db.subjects.find_one({"_id": ObjectId(course["subject_id"])})
        if not subject: return "未知学科", "", None
        return subject["name"], subject.get("description", ""), course["name"]
    return "未知学科", "", None


# ── arq task functions (must be async, first arg is ctx) ──────────────────────
async def gen_outline(ctx, course_name, subject_name, subject_description,
                      parent_course_name, ref_id, ref_type):
    from agents import content_agent, review_agent
    from models import upsert_lecture, update_course, get_db
    from config import settings as cfg
    from bson import ObjectId

    feedback = None
    outline  = None
    for _ in range(cfg.MAX_REVIEW_RETRIES):
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


async def gen_material(ctx, lecture_id: str):
    from agents import material_agent
    from models import upsert_material, get_db
    from bson import ObjectId

    db      = get_db()
    lec_doc = await db.lectures.find_one({"_id": ObjectId(lecture_id)})
    if not lec_doc:
        raise ValueError(_missing_msg("Lecture", lecture_id, "lectures"))

    outline  = lec_doc["content"]
    ref_id   = lec_doc.get("ref_id")
    ref_type = lec_doc.get("ref_type")
    subject_name, subject_description, parent_course_name = \
        await _recover_context(db, ref_id, ref_type)

    mat         = await material_agent.run(
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


async def gen_exam(ctx, material_id: str):
    from agents import exam_agent
    from models import upsert_exam, get_db
    from bson import ObjectId

    db      = get_db()
    mat_doc = await db.materials.find_one({"_id": ObjectId(material_id)})
    if not mat_doc:
        raise ValueError(_missing_msg("Material", material_id, "materials"))

    mat       = mat_doc["material"]
    questions = await exam_agent.run(mat.get("course_title", "课程"), mat)
    exam_id   = await upsert_exam(material_id, questions)
    return {"exam_id": exam_id, "questions": questions}


async def gen_expand_course(
    ctx,
    subject_name: str,
    course_id: str,
    course_name: str | None = None,   # NEW: passed by the router so the worker
                                       # doesn't need to read the course doc
                                       # before doing the LLM call.
):
    """Decide subcategories for ONE course.

    The router passes `course_name` directly so the LLM call no longer depends
    on the worker being able to look the course up in Mongo. We still verify
    the course exists *before writing back* — without that check, a worker
    pointed at the wrong DB would silently insert orphan subcategories into
    that DB. Better to fail loudly with a diagnostic that points at the cause.
    """
    from agents import subcategory_agent
    from models import save_subcategories, update_course, get_db
    from bson import ObjectId

    # Run the LLM call FIRST (doesn't need DB). If course_name is missing for
    # any reason, fall back to a DB lookup for the name only.
    name = course_name
    if not name:
        db = get_db()
        course_doc = await db.courses.find_one(
            {"_id": ObjectId(course_id)}, {"name": 1}
        )
        if not course_doc:
            raise ValueError(_missing_msg("Course", course_id, "courses"))
        name = course_doc["name"]

    result   = await subcategory_agent.run(subject_name, name)
    has_subs = result["has_subcategories"]
    update   = {"has_subcategories": has_subs}
    sub_ids: list[str] = []

    # Now verify the course still exists in the DB we're about to write to.
    # If it doesn't, we'd be creating orphan subcategories — better to fail.
    db = get_db()
    course = await db.courses.find_one({"_id": ObjectId(course_id)})
    if not course:
        raise ValueError(_missing_msg("Course", course_id, "courses"))

    if has_subs and result.get("subcategories"):
        sub_ids = await save_subcategories(course_id, result["subcategories"])
        update["status"] = "expanded"
    await update_course(course_id, update)
    return {
        "course_id":         course_id,
        "course_name":       name,
        "has_subcategories": has_subs,
        "subcategory_ids":   sub_ids,
    }


# ── Diagnostic helpers ─────────────────────────────────────────────────────────
def _missing_msg(label: str, _id: str, collection: str) -> str:
    """Build an error that tells the user WHY a worker can't find a doc that
    the API server just enqueued for it.

    The single most common cause is API and worker processes connected to
    different MongoDB databases — usually because pydantic-settings reads
    `.env` from the current working directory, and the two processes were
    started from different directories. Stale Redis jobs from a previous
    run are a distant second.
    """
    return (
        f"{label} {_id} not found in collection '{collection}' of database "
        f"'{settings.MONGO_DB}' (MONGO_URI={settings.MONGO_URI!r}).\n"
        f"Most likely cause: the API server and this worker are connected to "
        f"different MongoDB instances/databases. Check that both processes "
        f"load the same .env file (pydantic-settings reads .env from CWD).\n"
        f"Other causes: stale Redis jobs from before you reset Mongo "
        f"(try `redis-cli FLUSHDB`), or the parent record was deleted "
        f"between enqueue and run."
    )


# ── Worker lifecycle hooks ─────────────────────────────────────────────────────
async def on_startup(ctx):
    """Build indexes once per worker process startup, and print a banner so
    misconfiguration shows up at boot rather than as a confusing per-job error.
    """
    import logging
    from models import ensure_indexes, get_db

    log = logging.getLogger("arq.worker")

    # Banner — first thing the user sees when starting the worker. If the
    # MONGO_DB shown here doesn't match what their API uses, "X not found"
    # errors are explained immediately.
    log.info(
        "course-gen worker booting | MONGO_URI=%s MONGO_DB=%s REDIS_URL=%s "
        "WORKER_MAX_JOBS=%d LLM_MAX_CONCURRENCY=%d",
        settings.MONGO_URI, settings.MONGO_DB, settings.REDIS_URL,
        settings.WORKER_MAX_JOBS, settings.LLM_MAX_CONCURRENCY,
    )

    db = get_db()
    # Quick connectivity / data sanity check. If counts here look wildly
    # different from what the user expects (e.g. 0 when they just created
    # a subject), it's the "wrong DB" symptom.
    try:
        n_subj    = await db.subjects.estimated_document_count()
        n_courses = await db.courses.estimated_document_count()
        log.info(
            "mongo connection ok — db '%s' has %d subjects, %d courses",
            settings.MONGO_DB, n_subj, n_courses,
        )
    except Exception as e:
        log.error("mongo connection check failed: %s", e)

    await ensure_indexes()


# ── Worker settings ────────────────────────────────────────────────────────────
class WorkerSettings:
    functions      = [gen_outline, gen_material, gen_exam, gen_expand_course]
    redis_settings = REDIS
    max_jobs       = settings.WORKER_MAX_JOBS    # was hardcoded 10
    job_timeout    = 600                          # 10 min max per task
    keep_result    = 3600 * 24                    # keep results 24h
    on_startup     = on_startup
