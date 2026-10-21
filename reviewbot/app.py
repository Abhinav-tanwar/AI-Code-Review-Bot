import asyncio
import hashlib
import hmac
import json
import os
import re
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .engine import review_diff
from .github import GitHub
from .models import ReviewRequest
from .store import Store

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


@lru_cache
def store():
    return Store(Path(os.getenv("DATA_DIR", str(ROOT / "data"))) / "reviews.sqlite3")


def perform(body: ReviewRequest):
    source = None
    if body.diff is not None:
        if body.repository or body.pull_number:
            raise ValueError("Choose a diff or a pull request, not both.")
        diff = body.diff
    elif body.repository and body.pull_number:
        source = GitHub().pull(body.repository, body.pull_number)
        diff = source.pop("diff")
    else:
        raise ValueError("Provide a unified diff or a repository and pull request number.")
    result = review_diff(diff)
    if source:
        result["source"] = source
        if source["omitted"]:
            result["review"]["limitations"].append(
                "Unavailable, binary, or deleted patches: " + ", ".join(source["omitted"])
            )
    result["id"] = store().save(result)
    return result


def process_job(job):
    try:
        github = GitHub()
        source = github.pull(job["repo"], job["number"])
        if source["head_sha"] != job["head"]:
            store().finish(job["id"], "superseded")
            return
        diff = source.pop("diff")
        result = review_diff(diff)
        result["source"] = source
        if source["omitted"]:
            result["review"]["limitations"].append(
                "Unavailable patches: " + ", ".join(source["omitted"])
            )
        key = store().save(result)
        if os.getenv("AUTO_PUBLISH", "false").lower() == "true":
            github.publish(source, result)
        store().finish(job["id"], "completed", key)
    except Exception as exc:
        # Only a sanitized error category is persisted. No prompts or credentials in logs.
        store().finish(job["id"], "failed", error=type(exc).__name__)


async def worker():
    while True:
        job = await run_in_threadpool(store().claim)
        if job:
            await run_in_threadpool(process_job, job)
        else:
            await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(worker())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="AI Code Review Bot", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def guard(request: Request, call_next):
    host = request.headers.get("host", "").split(":")[0].lower()
    allowed = {
        x.strip() for x in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",")
    }
    if host not in allowed:
        return JSONResponse({"detail": "Host not allowed."}, 400)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Cross-origin requests are not allowed."}, 403)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 160000:
                return JSONResponse({"detail": "Request exceeds 160 KB."}, 413)
        request._body = bytes(body)
    return await call_next(request)


def authorize(request: Request):
    token = os.getenv("API_TOKEN", "")
    provided = request.headers.get("authorization", "").removeprefix("Bearer ")
    if token:
        if not hmac.compare_digest(provided, token):
            raise HTTPException(401, "API access token required.")
    elif request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(403, "Remote API access requires API_TOKEN configuration.")


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
        "github_configured": bool(os.getenv("GITHUB_TOKEN")),
        "model": os.getenv("OPENAI_MODEL", "gpt-4.1"),
        "auto_publish": os.getenv("AUTO_PUBLISH", "false").lower() == "true",
    }


@app.post("/api/reviews", dependencies=[Depends(authorize)])
async def create_review(body: ReviewRequest):
    try:
        return await run_in_threadpool(perform, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        if getattr(exc, "code", None) in {"insufficient_quota", "credit_balance_exhausted"}:
            raise HTTPException(402, "OpenAI API credits are unavailable.") from exc
        raise HTTPException(
            502,
            "Review failed. Check provider access, billing, GitHub permissions, and network connectivity.",
        ) from exc


@app.get("/api/reviews", dependencies=[Depends(authorize)])
def history():
    return store().recent()


@app.get("/api/reviews/{review_id}", dependencies=[Depends(authorize)])
def get_review(review_id: str):
    result = store().get(review_id)
    if result is None:
        raise HTTPException(404, "Review not found.")
    return {**result, "id": review_id}


@app.post("/api/reviews/{review_id}/publish", dependencies=[Depends(authorize)])
def publish(review_id: str):
    result = store().get(review_id)
    if not result:
        raise HTTPException(404, "Review not found.")
    if not result.get("source"):
        raise HTTPException(422, "Only reviews fetched from GitHub can be published.")
    try:
        return GitHub().publish(result["source"], result)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            502, "GitHub could not accept this review. Check permissions and retry."
        ) from exc


@app.get("/api/jobs", dependencies=[Depends(authorize)])
def jobs():
    return store().jobs()


@app.post("/api/jobs/{job_id}/retry", dependencies=[Depends(authorize)])
def retry_job(job_id: str):
    if not store().retry(job_id):
        raise HTTPException(409, "Only failed jobs with fewer than three attempts can be retried.")
    return {"status": "queued"}


@app.post("/webhooks/github", status_code=202)
async def webhook(request: Request):
    secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(503, "Webhook is not configured.")
    body = await request.body()
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(request.headers.get("x-hub-signature-256", ""), expected):
        raise HTTPException(401, "Invalid webhook signature.")
    if request.headers.get("x-github-event") != "pull_request":
        return {"status": "ignored"}
    try:
        payload = json.loads(body)
        if payload.get("action") not in {"opened", "synchronize", "reopened", "ready_for_review"}:
            return {"status": "ignored"}
        pr = payload["pull_request"]
        repo = payload["repository"]["full_name"]
        number = pr["number"]
        head = pr["head"]["sha"]
        allowed = {x.strip().lower() for x in os.getenv("ALLOWED_REPOSITORIES", "").split(",")}
        if repo.lower() not in allowed:
            raise HTTPException(403, "Repository is not allowlisted.")
        if pr.get("draft") or pr.get("state") != "open":
            return {"status": "ignored"}
        if not isinstance(number, int) or number < 1 or not re.fullmatch("[a-fA-F0-9]{40}", head):
            raise ValueError()
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(422, "Invalid pull request event.") from exc
    key = store().enqueue(repo, number, head)
    return {"status": "queued", "job_id": key}


@app.get("/")
def home():
    return FileResponse(ROOT / "web/index.html")


@app.get("/api/example")
def example():
    return {"diff": (ROOT / "examples/payment.diff").read_text()}


app.mount("/assets", StaticFiles(directory=ROOT / "web"), name="assets")
