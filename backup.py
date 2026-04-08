#!/usr/bin/env python3
"""
backup.py
Fetches all Claude.ai conversations + projects,
formats as markdown, pushes to GitHub.
Auto-refreshes sessionKey on 401.
"""
import base64
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO", "m3h3di/claude-vault-backups")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
BACKUP_FOLDER = os.getenv("BACKUP_FOLDER", "backups")
STATE_FILE = f"{BACKUP_FOLDER}/.state.json"
CLAUDE_BASE = "https://claude.ai/api"
GH_BASE = "https://api.github.com"


# -- Session -----------------------------------------------------------------

def claude_headers():
    return {
        "cookie": f"sessionKey={os.getenv('CLAUDE_SESSION')}",
        "content-type": "application/json",
        "user-agent": "Mozilla/5.0",
    }


def refresh_session():
    try:
        from auto_cookie import refresh_session_key

        os.environ["CLAUDE_SESSION"] = refresh_session_key()
        log.info("Session refreshed automatically.")
    except Exception as e:
        log.error(f"Session refresh failed: {e}")
        raise


def claude_get(path: str):
    url = f"{CLAUDE_BASE}/{path}"
    r = requests.get(url, headers=claude_headers(), timeout=15)
    if r.status_code == 401:
        log.warning("Session expired - refreshing...")
        refresh_session()
        r = requests.get(url, headers=claude_headers(), timeout=15)
    r.raise_for_status()
    return r.json()


# -- Claude API --------------------------------------------------------------

def get_org_id():
    data = claude_get("organizations")
    return (data if isinstance(data, list) else [data])[0]["uuid"]


def get_conversations(org_id):
    data = claude_get(f"organizations/{org_id}/chat_conversations")
    return data if isinstance(data, list) else data.get("conversations", [])


def get_conversation(org_id, conv_id):
    return claude_get(f"organizations/{org_id}/chat_conversations/{conv_id}")


def get_projects(org_id):
    try:
        data = claude_get(f"organizations/{org_id}/projects")
        return data if isinstance(data, list) else data.get("projects", [])
    except Exception as e:
        log.warning(f"Could not fetch projects: {e}")
        return []


def get_project_conversations(org_id, project_id):
    try:
        data = claude_get(f"organizations/{org_id}/projects/{project_id}/conversations")
        return data if isinstance(data, list) else data.get("conversations", [])
    except Exception as e:
        log.warning(f"Could not fetch project conversations: {e}")
        return []


def get_project_docs(org_id, project_id):
    try:
        data = claude_get(f"organizations/{org_id}/projects/{project_id}/docs")
        return data if isinstance(data, list) else data.get("documents", [])
    except Exception as e:
        log.warning(f"Could not fetch project docs: {e}")
        return []


# -- Formatters --------------------------------------------------------------

def slugify(text):
    return re.sub(r"[^\w-]", "_", text.strip())[:50]


def format_conversation(conv, project_name=None):
    title = conv.get("name") or "Untitled"
    messages = conv.get("chat_messages", [])
    now = datetime.now(timezone.utc).isoformat()
    lines = [
        f"# {title}",
        "",
        f"**Backed up:** {now}",
        f"**Created:** {conv.get('created_at', 'unknown')}",
    ]
    if project_name:
        lines.append(f"**Project:** {project_name}")
    lines += [f"**Messages:** {len(messages)}", "", "---", ""]
    for msg in messages:
        role = msg.get("sender", "unknown")
        content = msg.get("text") or ""
        if not content and isinstance(msg.get("content"), list):
            parts = []
            for block in msg["content"]:
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    for inner in block.get("content", []):
                        if inner.get("type") == "text":
                            parts.append(
                                f"**[Artifact]**\n```\n{inner['text']}\n```"
                            )
            content = "\n\n".join(parts)
        lines += [
            "## You" if role == "human" else "## Claude",
            "",
            content.strip(),
            "",
            "---",
            "",
        ]
    return "\n".join(lines)


def format_project_index(project, docs, conv_count):
    now = datetime.now(timezone.utc).isoformat()
    name = project.get("name", "Untitled project")
    lines = [
        f"# Project: {name}",
        "",
        f"**Backed up:** {now}",
        f"**Created:** {project.get('created_at', 'unknown')}",
        f"**Conversations:** {conv_count}",
        f"**Knowledge docs:** {len(docs)}",
        "",
    ]
    desc = project.get("description", "")
    instr = project.get("prompt_template") or project.get("instructions", "")
    if desc:
        lines += ["## Description", "", desc, ""]
    if instr:
        lines += ["## Project instructions", "", instr, ""]
    if docs:
        lines += ["## Knowledge base documents", ""]
        for doc in docs:
            lines += [
                f"### {doc.get('filename') or doc.get('name', 'Untitled')}",
                "",
                (doc.get("content") or doc.get("text", "")).strip(),
                "",
            ]
    return "\n".join(lines)


# -- GitHub ------------------------------------------------------------------

def gh_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }


def push_file(path, content, message):
    url = f"{GH_BASE}/repos/{GITHUB_REPO}/contents/{path}"
    encoded = base64.b64encode(content.encode()).decode()
    chk = requests.get(url, headers=gh_headers(), timeout=10)
    payload = {"message": message, "content": encoded, "branch": GITHUB_BRANCH}
    if chk.status_code == 200:
        payload["sha"] = chk.json().get("sha")
    r = requests.put(url, headers=gh_headers(), json=payload, timeout=15)
    r.raise_for_status()
    return r.json()["content"]["html_url"]


def get_file_content(path):
    url = f"{GH_BASE}/repos/{GITHUB_REPO}/contents/{path}"
    r = requests.get(
        url,
        headers=gh_headers(),
        params={"ref": GITHUB_BRANCH},
        timeout=15,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    payload = r.json()
    return base64.b64decode(payload["content"]).decode()


def load_state():
    raw = get_file_content(STATE_FILE)
    if not raw:
        return {"version": 1, "items": {}}
    try:
        state = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("State file is invalid JSON. Rebuilding state from scratch.")
        return {"version": 1, "items": {}}
    if not isinstance(state, dict):
        return {"version": 1, "items": {}}
    items = state.get("items")
    if not isinstance(items, dict):
        state["items"] = {}
    state.setdefault("version", 1)
    return state


def save_state(state, ts):
    push_file(
        STATE_FILE,
        json.dumps(state, indent=2, sort_keys=True) + "\n",
        f"update backup state @ {ts}",
    )


def content_hash(markdown):
    normalized = re.sub(
        r"^\*\*Backed up:\*\* .*$",
        "**Backed up:** <normalized>",
        markdown,
        flags=re.MULTILINE,
    )
    return hashlib.sha256(normalized.encode()).hexdigest()


def maybe_snapshot(state, item_key, path, content, message, ts):
    digest = content_hash(content)
    previous = state["items"].get(item_key, {})
    if previous.get("hash") == digest:
        log.info(f"  = unchanged {item_key}")
        return False
    push_file(path, content, message)
    state["items"][item_key] = {
        "hash": digest,
        "last_path": path,
        "updated_at": ts,
    }
    return True


# -- Main --------------------------------------------------------------------

def validate_config():
    required = {
        "CLAUDE_SESSION": os.getenv("CLAUDE_SESSION"),
        "GITHUB_TOKEN": GITHUB_TOKEN,
        "GITHUB_REPO": GITHUB_REPO,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")


def run_backup():
    validate_config()
    log.info("--- Backup run started ---")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    ok = fail = skipped = 0
    state = load_state()
    state_changed = False

    try:
        org_id = get_org_id()
    except Exception as e:
        log.error(f"Could not get org ID: {e}")
        raise

    # Standalone conversations
    try:
        for meta in get_conversations(org_id):
            conv_id = meta.get("uuid") or meta.get("id")
            if not conv_id:
                continue
            try:
                conv = get_conversation(org_id, conv_id)
                name = slugify(conv.get("name") or conv_id)
                content = format_conversation(conv)
                changed = maybe_snapshot(
                    state,
                    f"chats/{conv_id}",
                    f"{BACKUP_FOLDER}/chats/{name}_{ts}.md",
                    content,
                    f"backup chat: {name} @ {ts}",
                    ts,
                )
                if changed:
                    log.info(f"  + chats/{name}")
                    ok += 1
                    state_changed = True
                else:
                    skipped += 1
            except Exception as e:
                log.error(f"  x {conv_id}: {e}")
                fail += 1
    except Exception as e:
        log.error(f"Failed fetching conversations: {e}")

    # Projects
    for proj in get_projects(org_id):
        proj_id = proj.get("uuid") or proj.get("id")
        proj_name = proj.get("name", proj_id)
        proj_slug = slugify(proj_name)
        proj_convs = get_project_conversations(org_id, proj_id)
        proj_docs = get_project_docs(org_id, proj_id)
        try:
            changed = maybe_snapshot(
                state,
                f"projects/{proj_id}/index",
                f"{BACKUP_FOLDER}/projects/{proj_slug}/index_{ts}.md",
                format_project_index(proj, proj_docs, len(proj_convs)),
                f"backup project index: {proj_name} @ {ts}",
                ts,
            )
            if changed:
                log.info(f"  + projects/{proj_slug}/index")
                ok += 1
                state_changed = True
            else:
                skipped += 1
        except Exception as e:
            log.error(f"  x index {proj_name}: {e}")
            fail += 1
        for meta in proj_convs:
            conv_id = meta.get("uuid") or meta.get("id")
            if not conv_id:
                continue
            try:
                conv = get_conversation(org_id, conv_id)
                name = slugify(conv.get("name") or conv_id)
                changed = maybe_snapshot(
                    state,
                    f"projects/{proj_id}/conversations/{conv_id}",
                    f"{BACKUP_FOLDER}/projects/{proj_slug}/{name}_{ts}.md",
                    format_conversation(conv, project_name=proj_name),
                    f"backup: {proj_name}/{name} @ {ts}",
                    ts,
                )
                if changed:
                    log.info(f"    + projects/{proj_slug}/{name}")
                    ok += 1
                    state_changed = True
                else:
                    skipped += 1
            except Exception as e:
                log.error(f"    x {conv_id}: {e}")
                fail += 1

    if state_changed:
        save_state(state, ts)

    log.info(f"--- Done: {ok} pushed, {skipped} skipped, {fail} failed ---")
    return {"ok": ok, "skipped": skipped, "failed": fail}


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    result = run_backup()
    if result["failed"] > 0:
        raise SystemExit(1)
