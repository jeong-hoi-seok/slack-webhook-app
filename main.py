import os
import re
import logging
import pathlib
import yaml
from fastapi import FastAPI, Request, HTTPException
from slack_sdk import WebClient
from dotenv import load_dotenv
import store

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_BASE = pathlib.Path(__file__).parent

_token = os.getenv("SLACK_BOT_TOKEN")
CHANNEL = os.getenv("SLACK_CHANNEL_ID")
GITLAB_WEBHOOK_TOKEN = os.getenv("GITLAB_WEBHOOK_TOKEN")

if not _token or not CHANNEL:
    raise RuntimeError("SLACK_BOT_TOKEN and SLACK_CHANNEL_ID must be set")

client = WebClient(token=_token)
app = FastAPI()

with open(_BASE / "users.yml", "r") as f:
    USER_MAP: dict = yaml.safe_load(f)["gitlab_to_slack"]

_TEMPLATE_SECTION_RE = re.compile(r"^##\s*2[\.\s]", re.MULTILINE)


def mention(username: str) -> str:
    uid = USER_MAP.get(username)
    return f"<@{uid}>" if uid else f"`{username}`"


def truncate(text: str, limit: int = 100) -> str:
    if not text:
        return ""
    text = text.strip().replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "..."


def clean_description(text: str, limit: int = 100) -> str:
    if not text:
        return ""
    match = _TEMPLATE_SECTION_RE.search(text)
    if match:
        text = text[:match.start()]

    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if line.startswith("- [ ]") or line.startswith("- [x]"):
            continue
        if line.startswith("- "):
            line = line[2:]
        lines.append(line)

    if not lines:
        return ""
    if len(lines) == 1:
        line = lines[0]
        return line if len(line) <= limit else line[:limit] + "..."
    return "\n".join(f"• {l}" for l in lines)


def mr_key(project_id, mr_iid) -> str:
    return f"{project_id}_{mr_iid}"


def post_main(text: str, title: str, url: str, description: str, color: str = "#36a64f", branch_info: str = "") -> str:
    res = client.chat_postMessage(
        channel=CHANNEL,
        text=text,
        attachments=[
            {
                "color": color,
                "title": title,
                "title_link": url,
                "text": clean_description(description),
                "footer": branch_info,
            }
        ],
    )
    return res["ts"]


def post_thread(thread_ts: str, text: str, color: str = "", title: str = "", url: str = "", description: str = ""):
    attachment = {"color": color} if color else {}
    if title:
        attachment["title"] = title
        attachment["title_link"] = url
        attachment["text"] = truncate(description, 80)
    elif color:
        attachment["text"] = text

    if attachment:
        client.chat_postMessage(channel=CHANNEL, thread_ts=thread_ts, text=text if not title else " ", attachments=[attachment])
    else:
        client.chat_postMessage(channel=CHANNEL, thread_ts=thread_ts, text=text)


@app.post("/gitlab-webhook")
async def gitlab_webhook(request: Request):
    if GITLAB_WEBHOOK_TOKEN:
        token = request.headers.get("X-Gitlab-Token")
        if token != GITLAB_WEBHOOK_TOKEN:
            raise HTTPException(status_code=401, detail="Unauthorized")

    data = await request.json()
    kind = data.get("object_kind")

    try:
        return await _handle(data, kind)
    except Exception as e:
        logger.error("Webhook handling failed: %s", e)
        return {"status": "error", "detail": str(e)}


async def _handle(data: dict, kind: str):
    # ── 파이프라인 이벤트 ──────────────────────────────────────────
    if kind == "pipeline":
        status = data.get("object_attributes", {}).get("status")
        if status != "failed":
            return {"status": "ignored"}

        mr_data = data.get("merge_request") or {}
        project_id = data.get("project", {}).get("id")
        mr_iid = mr_data.get("iid")
        ref = data.get("object_attributes", {}).get("ref", "")
        pipeline_id = data.get("object_attributes", {}).get("id")
        project_url = data.get("project", {}).get("web_url", "")
        pipeline_url = f"{project_url}/-/pipelines/{pipeline_id}" if project_url and pipeline_id else ""

        key = mr_key(project_id, mr_iid) if mr_iid else None
        thread_ts = store.get(key) if key else None

        link = f" — <{pipeline_url}|파이프라인 보기>" if pipeline_url else ""
        msg = f":x: 파이프라인 실패 (`{ref}`){link}"

        if thread_ts:
            post_thread(thread_ts, msg)
        else:
            client.chat_postMessage(channel=CHANNEL, text=msg)
        return {"status": "ok"}

    # ── 댓글/리뷰 이벤트 ──────────────────────────────────────────
    if kind == "note":
        attrs = data.get("object_attributes", {})
        if attrs.get("noteable_type") != "MergeRequest":
            return {"status": "ignored"}

        commenter = data.get("user", {}).get("username", "")
        mr_data = data.get("merge_request", {})
        mr_iid = mr_data.get("iid")
        project_id = data.get("project_id") or data.get("project", {}).get("id")
        mr_title = mr_data.get("title", "(제목 없음)")
        mr_url = mr_data.get("url", "")
        note = truncate(attrs.get("note", ""), 80)

        mr_author = mr_data.get("author", {}).get("username", "") if isinstance(mr_data.get("author"), dict) else ""
        target = mention(mr_author) if mr_author and mr_author != commenter else ""

        key = mr_key(project_id, mr_iid)
        thread_ts = store.get(key)

        header = f"{mention(commenter)} 댓글" + (f" → {target}" if target else "")
        if thread_ts:
            post_thread(thread_ts, header, color="#e8a838", title=mr_title, url=mr_url, description=note)
        else:
            client.chat_postMessage(channel=CHANNEL, text=header, attachments=[{"color": "#e8a838", "title": mr_title, "title_link": mr_url, "text": truncate(note, 80)}])
        return {"status": "ok"}

    # ── MR 이벤트 ─────────────────────────────────────────────────
    if kind != "merge_request":
        return {"status": "ignored"}

    attrs = data.get("object_attributes")
    if not attrs:
        return {"status": "ignored"}

    action = attrs.get("action", "open")
    title = attrs.get("title", "(제목 없음)")
    url = attrs.get("url", "")
    description = attrs.get("description", "")
    source_branch = attrs.get("source_branch", "")
    target_branch = attrs.get("target_branch", "")
    branch_info = f"{source_branch} → {target_branch}" if source_branch and target_branch else ""
    project_id = data.get("project", {}).get("id")
    mr_iid = attrs.get("iid")
    actor = data.get("user", {}).get("username", "")
    key = mr_key(project_id, mr_iid)

    if action == "open":
        assignees = data.get("assignees", [])
        reviewers = data.get("reviewers", [])

        if reviewers:
            r_mentions = " ".join(mention(r["username"]) for r in reviewers)
            text = f"{r_mentions} 리뷰 요청"
        else:
            text = " "

        assignee_names = ", ".join(a["username"] for a in assignees)
        footer = branch_info
        if assignee_names:
            footer = f"{footer} | 담당자: {assignee_names}" if footer else f"담당자: {assignee_names}"

        ts = post_main(text=text, title=title, url=url, description=description, branch_info=footer)
        store.set(key, ts)

    else:
        thread_ts = store.get(key)

        if action == "update":
            reviewers = data.get("reviewers", [])
            if not reviewers:
                return {"status": "ignored"}
            mentions = " ".join(mention(r["username"]) for r in reviewers)
            msg = f"{mentions} 리뷰 요청"
            if thread_ts:
                post_thread(thread_ts, msg)
            else:
                client.chat_postMessage(channel=CHANNEL, text=msg)

        elif action == "approved":
            msg = f":white_check_mark: {mention(actor)} 승인했어요!"
            if thread_ts:
                post_thread(thread_ts, msg)
            else:
                client.chat_postMessage(channel=CHANNEL, text=msg)

        elif action == "close":
            if thread_ts:
                post_thread(thread_ts, "closed", color="#e01e5a", title=title, url=url, description="MR이 닫혔어요.")
            else:
                client.chat_postMessage(channel=CHANNEL, text="closed", attachments=[{"color": "#e01e5a", "title": title, "title_link": url, "text": "MR이 닫혔어요."}])

        elif action == "reopen":
            if thread_ts:
                post_thread(thread_ts, "reopened", color="#36a64f", title=title, url=url, description="MR이 다시 열렸어요.")
            else:
                client.chat_postMessage(channel=CHANNEL, text="reopened", attachments=[{"color": "#36a64f", "title": title, "title_link": url, "text": "MR이 다시 열렸어요."}])

        elif action == "merge":
            mr_author = attrs.get("author_username", "")
            target = mention(mr_author) if mr_author else ""
            msg = f":rocket: MR 머지 되었습니다!" + (f" {target}" if target else "")
            if thread_ts:
                post_thread(thread_ts, msg)
            else:
                client.chat_postMessage(channel=CHANNEL, text=msg)

        else:
            return {"status": "ignored"}

    return {"status": "ok"}


@app.get("/health")
def health():
    return {"status": "ok"}
