from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import httpx
import asyncio
import json
from sse_starlette.sse import EventSourceResponse
import aiohttp
import re
from dotenv import load_dotenv
import os

load_dotenv()

app = FastAPI()
templates = Jinja2Templates(directory="templates")

PR_STORE = {"initial_prs": [], "webhook_prs": []}
subscribers = []

LYZR_URL = os.getenv("LYZR_URL")
LYZR_API_KEY = os.getenv("LYZR_API_KEY")


async def model(data: dict) -> dict:
    patch_url = data.get("patch_url") or data.get("pull_request", {}).get("patch_url")
    message = ""

    if patch_url:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(patch_url, allow_redirects=True) as resp:
                    resp.raise_for_status()
                    patch_text = await resp.text()
                    if patch_text:
                        message = patch_text.strip().replace("\n", "\\n")
        except Exception as e:
            print("Error fetching patch:", e)

    payload = {
        "user_id": "d37028840@gmail.com",
        "agent_id": "692592fdc69ec8d9a078471e",
        "session_id": "692592fdc69ec8d9a078471e-b7v6b8jmjon",
        "message": message
    }

    headers = {"Content-Type": "application/json", "x-api-key": LYZR_API_KEY}
    model_output = {}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(LYZR_URL, json=payload, headers=headers) as resp:
                resp.raise_for_status()
                model_output = await resp.json()
    except Exception as e:
        print("Error calling Lyzr model:", e)

    if "pull_request" in data:
        return process_github_pr(model_output, data)
    else:
        return process_internal_pr(model_output, data)


def process_github_pr(model_json: dict, original: dict) -> dict:
    pr = original.get("pull_request", {})
    model_data = {}
    if model_json.get("response"):
        try:
            clean_json_str = re.sub(r"^```json\s*|\s*```$", "", model_json["response"].strip(), flags=re.MULTILINE)
            model_data = json.loads(clean_json_str)
        except Exception as e:
            print("Error parsing model response JSON:", e)
            model_data = {}

    result = {
        "id": pr.get("id"),
        "title": pr.get("title"),
        "user": pr.get("user", {}).get("login"),
        "status": "enhanced",
        "processed_at": "server",
    }

    if model_data:
        result.update({
            "security": model_data.get("security"),
            "readability": model_data.get("readability"),
            "logic": model_data.get("logic"),
            "performance": model_data.get("performance"),
        })

    return result


def process_internal_pr(model_json: dict, original: dict) -> dict:
    model_data = {}
    if model_json.get("response"):
        try:
            model_data = json.loads(model_json["response"])
        except Exception as e:
            print("Error parsing model response JSON:", e)
            model_data = {}

    result = {
        "id": original.get("id"),
        "title": original.get("title"),
        "user": original.get("user", {}).get("login"),
        "status": "enhanced",
        "processed_at": "server",
    }

    if model_data:
        result.update({
            "security": model_data.get("security"),
            "readability": model_data.get("readability"),
            "logic": model_data.get("logic"),
            "performance": model_data.get("performance"),
        })

    return result


async def event_stream():
    queue = asyncio.Queue()
    subscribers.append(queue)
    try:
        while True:
            data = await queue.get()
            yield {"event": "update", "data": json.dumps(data)}
    except asyncio.CancelledError:
        subscribers.remove(queue)


def broadcast(data):
    for q in subscribers:
        q.put_nowait(data)


@app.get("/stream")
async def stream():
    return EventSourceResponse(event_stream())


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "initial_prs": PR_STORE["initial_prs"], "webhook_prs": PR_STORE["webhook_prs"]},
    )


@app.get("/prs")
async def load_prs():
    url = "https://api.github.com/repos/DhruvVayugundla/testing/pulls"
    async with httpx.AsyncClient() as client:
        res = await client.get(url)
    raw_prs = res.json()
    processed = [await model(pr) for pr in raw_prs]
    PR_STORE["initial_prs"] = processed
    return {"count": len(processed), "prs": processed}


@app.post("/webhook")
async def webhook(payload: dict):
    processed = await model(payload)
    PR_STORE["webhook_prs"].append(processed)
    broadcast({"webhook_prs": PR_STORE["webhook_prs"]})
    return {"status": "received", "processed": processed}
