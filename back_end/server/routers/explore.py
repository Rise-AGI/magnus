# back_end/server/routers/explore.py
import logging
import base64
import io
import re
import secrets
import asyncio
import threading
import shutil
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any, AsyncGenerator, cast
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form, BackgroundTasks, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from openai import OpenAI

from .. import database
from .. import models
from ..schemas import (
    ExplorerSessionCreate,
    ExplorerSessionResponse,
    ExplorerSessionWithMessages,
    ExplorerMessageCreate,
    ExplorerMessageResponse,
    PagedExplorerSessionResponse,
)
from .._magnus_config import magnus_config, admin_open_ids
from .._resource_manager import _parse_size_string
from .auth import get_current_user


logger = logging.getLogger(__name__)
router = APIRouter()

explorer_config = magnus_config["server"]["explorer"]
llm_client = OpenAI(
    api_key=explorer_config["api_key"],
    base_url=explorer_config["base_url"],
)

magnus_root = magnus_config["server"]["root"]
sessions_workspace = f"{magnus_root}/workspace/sessions"

IMAGE_PATTERN = re.compile(r'\[图片: ([^\]]+)\]\(file://([^)]+)\)')

_active_generations: Dict[str, Dict[str, Any]] = {}


def generate_session_title(
    user_message: str,
) -> str:
    small_model = explorer_config["small_fast_model_name"]
    clean_message = IMAGE_PATTERN.sub("[图片]", user_message)[:500]

    try:
        response = llm_client.chat.completions.create(
            model=small_model,
            messages=[{
                "role": "user",
                "content": f"请用简短的中文（10字以内）总结这段对话的主题，直接输出标题，不要任何解释：\n\n{clean_message}",
            }],
        )
        title = response.choices[0].message.content
        assert title is not None
        title = title.strip()
        return title[:50] if title else "New Session"
    except Exception as e:
        logger.error(f"Title generation error: {e}")
        first_line = user_message.split('\n')[0][:50]
        return IMAGE_PATTERN.sub("[图片]", first_line) or "New Session"


def get_session_files_dir(session_id: str) -> Path:
    path = Path(sessions_workspace) / session_id / "files"
    path.mkdir(parents=True, exist_ok=True)
    return path


def image_to_base64_url(file_path: str) -> Optional[str]:
    try:
        with open(file_path, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")
        ext = file_path.split(".")[-1].lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
        return f"data:{mime.get(ext, 'image/png')};base64,{data}"
    except Exception as e:
        logger.error(f"Failed to read image {file_path}: {e}")
        return None


def understand_image_with_vlm(
    image_path: str,
    filename: str,
    context_messages: List[Dict[str, Any]],
    current_text: str,
) -> str:
    visual_model = explorer_config["visual_model_name"]
    image_url = image_to_base64_url(image_path)
    if not image_url:
        return f"[图片 {filename} 无法读取]"

    context_summary = ""
    if context_messages:
        recent = context_messages[-6:]
        context_parts = []
        for msg in recent:
            role = "用户" if msg["role"] == "user" else "助手"
            content = msg["content"][:500]
            content = IMAGE_PATTERN.sub("[图片]", content)
            context_parts.append(f"{role}: {content}")
        context_summary = "\n".join(context_parts)

    current_text_clean = IMAGE_PATTERN.sub("", current_text).strip()

    prompt = f"""请根据对话上下文，理解并描述这张图片。

对话上下文：
{context_summary}

用户当前消息（除图片外）：{current_text_clean if current_text_clean else "（用户仅发送了图片）"}

请结合上下文，描述图片内容以及它与用户问题的关联。如果用户在询问图片中的具体内容，请直接回答。"""

    try:
        response = llm_client.chat.completions.create(
            model=visual_model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }],
        )
        return response.choices[0].message.content or f"[图片 {filename}]"
    except Exception as e:
        logger.error(f"VLM error: {e}")
        return f"[图片 {filename} 解析失败: {e}]"


def process_images_in_content(
    content: str,
    context_messages: List[Dict[str, Any]],
) -> str:
    matches = list(IMAGE_PATTERN.finditer(content))
    if not matches:
        return content

    result = content
    for match in reversed(matches):
        filename = match.group(1)
        file_path = match.group(2)

        understanding = understand_image_with_vlm(file_path, filename, context_messages, content)

        replacement = f"\n\n---\n🖼️ {filename}\n---\n{understanding}\n"
        result = result[:match.start()] + replacement + result[match.end():]

    return result


def extract_text_from_pdf(file_bytes: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(file_bytes))
    text_parts = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            text_parts.append(text)
    return "\n\n".join(text_parts)


def extract_text_from_docx(file_bytes: bytes) -> str:
    from docx import Document
    doc = Document(io.BytesIO(file_bytes))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def extract_text_from_txt(file_bytes: bytes) -> str:
    for encoding in ["utf-8", "gbk", "gb2312", "latin-1"]:
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return file_bytes.decode("utf-8", errors="replace")


@router.post("/explorer/sessions/{session_id}/upload")
async def upload_file(
    session_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> Dict[str, Any]:
    session = db.query(models.ExplorerSession).filter(
        models.ExplorerSession.id == session_id,
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.user_id != current_user.id and current_user.feishu_open_id not in admin_open_ids:
        raise HTTPException(status_code=403, detail="Access denied")

    content = await file.read()
    filename = file.filename or "unknown"
    content_type = file.content_type or ""

    _raw = magnus_config["server"]["file_custody"]["max_file_size"]
    if _raw is not None and len(content) > _parse_size_string(_raw):
        raise HTTPException(status_code=413, detail=f"File too large (limit: {_raw})")

    file_id = secrets.token_hex(8)
    ext = filename.split(".")[-1].lower() if "." in filename else ""
    saved_filename = f"{file_id}.{ext}" if ext else file_id

    files_dir = get_session_files_dir(session_id)
    file_path = files_dir / saved_filename

    with open(file_path, "wb") as f:
        f.write(content)

    logger.info(f"File saved: {file_path}")

    if content_type.startswith("image/") or ext in ["png", "jpg", "jpeg", "gif", "webp"]:
        return {
            "type": "image",
            "filename": filename,
            "file_id": file_id,
            "path": str(file_path),
        }

    if ext == "pdf" or content_type == "application/pdf":
        try:
            text = extract_text_from_pdf(content)
            return {"type": "text", "filename": filename, "file_id": file_id, "path": str(file_path), "content": text}
        except Exception as e:
            logger.error(f"PDF extraction error: {e}")
            raise HTTPException(status_code=400, detail=f"Failed to extract PDF: {e}")

    if ext == "docx" or content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        try:
            text = extract_text_from_docx(content)
            return {"type": "text", "filename": filename, "file_id": file_id, "path": str(file_path), "content": text}
        except Exception as e:
            logger.error(f"DOCX extraction error: {e}")
            raise HTTPException(status_code=400, detail=f"Failed to extract DOCX: {e}")

    if ext == "txt" or content_type.startswith("text/"):
        text = extract_text_from_txt(content)
        return {"type": "text", "filename": filename, "file_id": file_id, "path": str(file_path), "content": text}

    raise HTTPException(status_code=400, detail=f"Unsupported file type: {filename}")


@router.get("/explorer/files/{session_id}/{file_name}")
async def get_file(
    session_id: str,
    file_name: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    from fastapi.responses import FileResponse

    session = db.query(models.ExplorerSession).filter(
        models.ExplorerSession.id == session_id,
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    is_owner = session.user_id == current_user.id
    is_admin = current_user.feishu_open_id in admin_open_ids
    if not is_owner and not is_admin and not session.is_shared:
        raise HTTPException(status_code=403, detail="Access denied")

    allowed_dir = (Path(sessions_workspace) / session_id / "files").resolve()
    file_path = (allowed_dir / file_name).resolve()
    if not file_path.is_relative_to(allowed_dir):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(file_path)


@router.post("/explorer/sessions", response_model=ExplorerSessionResponse)
def create_session(
    data: ExplorerSessionCreate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> models.ExplorerSession:
    session = models.ExplorerSession(
        user_id=current_user.id,
        title=data.title or "New Session",
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@router.get("/explorer/sessions", response_model=PagedExplorerSessionResponse)
def list_sessions(
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> Dict[str, Any]:
    query = db.query(models.ExplorerSession).filter(
        models.ExplorerSession.user_id == current_user.id
    )
    total = query.count()
    items = query.order_by(models.ExplorerSession.updated_at.desc()).offset(skip).limit(limit).all()
    return {"total": total, "items": items}


@router.get("/explorer/sessions/{session_id}", response_model=ExplorerSessionWithMessages)
def get_session(
    session_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> models.ExplorerSession:
    session = db.query(models.ExplorerSession).filter(
        models.ExplorerSession.id == session_id,
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    is_owner = session.user_id == current_user.id
    is_admin = current_user.feishu_open_id in admin_open_ids
    if not is_owner and not is_admin and not session.is_shared:
        raise HTTPException(status_code=403, detail="Access denied")

    return session


@router.post("/explorer/sessions/{session_id}/share", response_model=ExplorerSessionResponse)
def share_session(
    session_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> models.ExplorerSession:
    session = db.query(models.ExplorerSession).filter(
        models.ExplorerSession.id == session_id,
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.user_id != current_user.id and current_user.feishu_open_id not in admin_open_ids:
        raise HTTPException(status_code=403, detail="Access denied")

    session.is_shared = True
    db.commit()
    db.refresh(session)
    return session


@router.post("/explorer/sessions/{session_id}/unshare", response_model=ExplorerSessionResponse)
def unshare_session(
    session_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> models.ExplorerSession:
    session = db.query(models.ExplorerSession).filter(
        models.ExplorerSession.id == session_id,
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.user_id != current_user.id and current_user.feishu_open_id not in admin_open_ids:
        raise HTTPException(status_code=403, detail="Access denied")

    session.is_shared = False
    db.commit()
    db.refresh(session)
    return session


@router.delete("/explorer/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    session_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> None:
    session = db.query(models.ExplorerSession).filter(
        models.ExplorerSession.id == session_id,
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.user_id != current_user.id and current_user.feishu_open_id not in admin_open_ids:
        raise HTTPException(status_code=403, detail="Access denied")

    db.delete(session)
    db.commit()

    session_dir = Path(sessions_workspace) / session_id
    if session_dir.exists():
        shutil.rmtree(session_dir)
        logger.info(f"Deleted session directory: {session_dir}")


@router.patch("/explorer/sessions/{session_id}", response_model=ExplorerSessionResponse)
def update_session(
    session_id: str,
    data: ExplorerSessionCreate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> models.ExplorerSession:
    session = db.query(models.ExplorerSession).filter(
        models.ExplorerSession.id == session_id,
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.user_id != current_user.id and current_user.feishu_open_id not in admin_open_ids:
        raise HTTPException(status_code=403, detail="Access denied")

    if data.title:
        session.title = data.title

    session.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(session)
    return session


def _convert_audio_to_mp3(audio_bytes: bytes) -> bytes:
    """内存管道转码，不落盘。要求系统已安装 ffmpeg。"""
    result = subprocess.run(
        [
            "ffmpeg", "-i", "pipe:0",
            "-f", "mp3", "-acodec", "libmp3lame",
            "-ab", "64k", "-ac", "1",
            "pipe:1",
        ],
        input=audio_bytes,
        capture_output=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 转码失败: {result.stderr.decode(errors='replace')[:200]}")
    return result.stdout


@router.post("/explorer/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    context: str = Form(""),
    current_user: models.User = Depends(get_current_user),
) -> Dict[str, str]:
    audio_bytes = await file.read()
    # 统一转为 mp3：单声道 64kbps，体积小且中转站能正确解析时长
    mp3_bytes = _convert_audio_to_mp3(audio_bytes)
    prompt = context[:900] if context else ""

    try:
        transcription = llm_client.audio.transcriptions.create(
            model=str(explorer_config["stt_model_name"]),
            file=("audio.mp3", mp3_bytes),
            prompt=prompt,
        )
        return {"text": transcription.text}
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")


@router.post("/explorer/sessions/{session_id}/chat")
async def chat(
    session_id: str,
    data: ExplorerMessageCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> StreamingResponse:
    session = db.query(models.ExplorerSession).filter(
        models.ExplorerSession.id == session_id,
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.user_id != current_user.id and current_user.feishu_open_id not in admin_open_ids:
        raise HTTPException(status_code=403, detail="Access denied")

    if data.truncate_before is not None:
        messages_to_delete = session.messages[data.truncate_before:]
        for msg in messages_to_delete:
            db.delete(msg)
        db.flush()
        db.expire(session, ["messages"])

    user_message = models.ExplorerMessage(
        session_id=session_id,
        role="user",
        content=data.content,
    )
    db.add(user_message)


    context_messages = [
        {"role": msg.role, "content": msg.content}
        for msg in session.messages
    ]


    processed_content = process_images_in_content(data.content, context_messages)


    messages = context_messages.copy()
    messages.append({"role": "user", "content": processed_content})


    is_first_message = len(session.messages) == 0
    session.updated_at = datetime.now(timezone.utc)
    db.commit()


    if is_first_message:
        background_tasks.add_task(
            _update_session_title_background,
            session_id,
            data.content,
        )


    generation_id = secrets.token_hex(8)
    _active_generations[generation_id] = {
        "chunks": [],
        "done": False,
        "error": None,
    }


    thread = threading.Thread(
        target=_run_generation_sync,
        args=(generation_id, session_id, messages),
        daemon=False,
    )
    thread.start()


    async def stream_response() -> AsyncGenerator[str, None]:
        last_index = 0

        while True:
            gen_state = _active_generations.get(generation_id)
            if not gen_state:
                break

            chunks = gen_state["chunks"]
            while last_index < len(chunks):
                yield chunks[last_index]
                last_index += 1

            if gen_state["done"]:
                break

            await asyncio.sleep(0.02)

        _active_generations.pop(generation_id, None)

    return StreamingResponse(
        stream_response(),
        media_type="text/plain; charset=utf-8",
    )


def _update_session_title_background(
    session_id: str,
    user_message: str,
) -> None:
    title = generate_session_title(user_message)
    with database.SessionLocal() as db:
        session = db.query(models.ExplorerSession).filter(
            models.ExplorerSession.id == session_id
        ).first()
        if session:
            session.title = title
            db.commit()


def _run_generation_sync(
    generation_id: str,
    session_id: str,
    messages: List[Dict[str, Any]],
) -> None:
    gen_state = _active_generations.get(generation_id)
    if not gen_state:
        return

    full_thinking = ""
    full_response = ""
    in_thinking = False

    try:
        stream = llm_client.chat.completions.create(
            model=str(explorer_config["model_name"]),
            messages=cast(Any, messages),
            stream=True,
            extra_body={"enable_thinking": True},
        )

        for chunk in stream:
            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta
            reasoning_content = getattr(delta, "reasoning_content", None)

            if reasoning_content:
                if not in_thinking:
                    in_thinking = True
                    gen_state["chunks"].append("<think>")
                full_thinking += reasoning_content
                gen_state["chunks"].append(reasoning_content)

            if delta.content:
                if in_thinking:
                    in_thinking = False
                    gen_state["chunks"].append("</think>")
                full_response += delta.content
                gen_state["chunks"].append(delta.content)

    except Exception as e:
        logger.error(f"Chat error: {e}")
        error_msg = f"\n\n[Error: {str(e)}]"
        full_response += error_msg
        gen_state["chunks"].append(error_msg)


    with database.SessionLocal() as save_db:
        save_content = full_response
        if full_thinking:
            save_content = f"<think>{full_thinking}</think>\n\n{full_response}"

        assistant_message = models.ExplorerMessage(
            session_id=session_id,
            role="assistant",
            content=save_content,
        )
        save_db.add(assistant_message)
        save_db.commit()

    gen_state["done"] = True
