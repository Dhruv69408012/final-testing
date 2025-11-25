from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import httpx
import asyncio
import json
from sse_starlette.sse import EventSourceResponse

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# In-memory PR list
PR_STORE = {
    "initial_prs": [],
    "webhook_prs": []
}

# -----------------------------
#   CORE FUNCTIONS
# -----------------------------


async def model(data: dict) -> dict:
    """
    Call external model and merge model output with original PR data.
    """
    async with httpx.AsyncClient() as client:
        response = await client.post("https://httpbin.org/post", json=data)

    received_json = response.json().get("json", {})

    # Choose processor based on type of PR
    if "pull_request" in data:  # GitHub webhook
        return process_github_pr(received_json, data)

    else:  # internal PR structure
        return process_internal_pr(received_json, data)

def process_github_pr(model_json: dict, original: dict) -> dict:
    """
    Process PRs coming from GitHub webhook structure.
    """
    pr = original.get("pull_request", {})
    print(pr.keys())

    return {
        "id": pr.get("id"),
        "title": pr.get("title"),
        "user": pr.get("user", {}).get("login"),

        # model output fields
        "status": model_json.get("status", "enhanced"),
        "processed_at": model_json.get("processed_at", "server"),
    }

def process_internal_pr(model_json: dict, original: dict) -> dict:
    """
    Process PRs coming from internal DB / manually created objects.
    """
    return {
        "id": original.get("id"),
        "title": original.get("title"),
        "user": original.get("user").get("login"),

        # model output fields
        "status": model_json.get("status", "enhanced"),
        "processed_at": model_json.get("processed_at", "server"),
    }


# -----------------------------
#   SSE STREAM
# -----------------------------
subscribers = []


async def event_stream():
    """Generator for SSE stream updates."""
    queue = asyncio.Queue()
    subscribers.append(queue)

    try:
        while True:
            data = await queue.get()
            yield {"event": "update", "data": json.dumps(data)}
    except asyncio.CancelledError:
        subscribers.remove(queue)


def broadcast(data):
    """Send data to all subscribers."""
    for q in subscribers:
        q.put_nowait(data)


@app.get("/stream")
async def stream():
    return EventSourceResponse(event_stream())


# -----------------------------
#   ENDPOINTS
# -----------------------------

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "initial_prs": PR_STORE["initial_prs"],
            "webhook_prs": PR_STORE["webhook_prs"],
        },
    )


@app.get("/prs")
async def load_prs():
    """
    Load PRs from GitHub API.
    Process each PR using the model() function
    so that initial PRs and webhook PRs share the same structure.
    """
    url = "https://api.github.com/repos/DhruvVayugundla/testing/pulls"

    async with httpx.AsyncClient() as client:
        res = await client.get(url)

    raw_prs = res.json()

    processed = []
    for pr in raw_prs:
        normalized = await model(pr)   # <--- replace process_json()
        processed.append(normalized)

    PR_STORE["initial_prs"] = processed
    return {"count": len(processed), "prs": processed}


@app.post("/webhook")
async def webhook(payload: dict):
    """
    Receive incoming JSON.
    Pass it through model().
    Store in webhook PRs.
    Then broadcast update to HTML via SSE.
    """
    processed = await model(payload)  # <--- replace process_json()

    PR_STORE["webhook_prs"].append(processed)
    broadcast({"webhook_prs": PR_STORE["webhook_prs"]})

    return {"status": "received", "processed": processed}
