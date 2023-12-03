import jieba
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from moviepy.editor import VideoFileClip
import shutil
import os
from uuid import uuid4
from datetime import datetime
import sqlite3
from typing import List
from fastapi.middleware.cors import CORSMiddleware
from wordcloud import WordCloud
from celery_server import transcribe_audio,simple_test
from fastapi.staticfiles import StaticFiles

# 数据库文件位置
DATABASE_URL = "sqlite.db"

# 视频文件的存储目录
VIDEO_DIR = "output/videos"
AUDIO_DIR = "output/audios"
TRANSCRIPT_DIR = "output/transcripts"  # 转录文件将被保存的目录
WORDCLOUD_DIR = "output/wordclouds"    # 词云图像保存目录


# 确保音视频存储目录存在
os.makedirs(VIDEO_DIR, exist_ok=True)
os.makedirs(AUDIO_DIR, exist_ok=True)

app = FastAPI()
app.mount("/static", StaticFiles(directory="output/wordclouds"), name="static")


# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080"],  # 允许的源列表
    allow_credentials=True,
    allow_methods=["*"],  # 允许的方法
    allow_headers=["*"],  # 允许的头
)

# 数据库连接
def get_db_connection():
    conn = sqlite3.connect(DATABASE_URL)
    conn.row_factory = sqlite3.Row
    return conn

# 创建数据库表
def create_videos_table():
    conn = get_db_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            size REAL NOT NULL,
            upload_time TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

create_videos_table()

# 视频模型
class Video(BaseModel):
    id: int
    filename: str
    size: float
    upload_time: datetime

# 保存视频信息到数据库
def save_video_info(filename, size, upload_time):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO videos (filename, size, upload_time) VALUES (?, ?, ?)",
                   (filename, size, upload_time))
    conn.commit()
    conn.close()

# 上传视频接口
@app.post("/upload-video/")
async def upload_video(file: UploadFile = File(...)):
    unique_filename = f"{uuid4()}-{file.filename}"
    file_location = os.path.join(VIDEO_DIR, unique_filename)

    with open(file_location, "wb") as file_object:
        shutil.copyfileobj(file.file, file_object)

    file_size = os.path.getsize(file_location)
    save_video_info(filename=unique_filename, size=file_size, upload_time=datetime.now().isoformat())

    return {"filename": unique_filename}

# 获取所有视频接口
@app.get("/videos/", response_model=List[Video])
def list_videos():
    conn = get_db_connection()
    videos = conn.execute("SELECT * FROM videos").fetchall()
    conn.close()
    return [{"id": video["id"], "filename": video["filename"], "size": video["size"], "upload_time": video["upload_time"]} for video in videos]

# 删除视频接口
@app.delete("/delete-video/{video_id}")
def delete_video(video_id: int):
    conn = get_db_connection()
    video = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()

    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    os.remove(os.path.join(VIDEO_DIR, video["filename"]))
    conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
    conn.commit()
    conn.close()
    return {"status": "Video deleted"}

@app.get("/videos/{filename}")
async def get_video(filename: str):
    video_path = os.path.join(VIDEO_DIR, filename)
    if not os.path.isfile(video_path):
        raise HTTPException(status_code=404, detail="Video not found")

    return FileResponse(video_path)

# 提取视频中的音频接口 请求这个接口需要对文件名url编码
@app.post("/extract-audio/{filename}")
async def extract_audio(filename: str):
    video_path = os.path.join(VIDEO_DIR, filename+".mp4")
    if not os.path.isfile(video_path):
        raise HTTPException(status_code=404, detail="Video not found")

    audio_path = os.path.join(AUDIO_DIR, filename + '.mp3')
    try:
        video_clip = VideoFileClip(video_path)
        audio_clip = video_clip.audio
        audio_clip.write_audiofile(audio_path)
        audio_clip.close()
        video_clip.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"audio_file": audio_path}

# 通过whisper将音频转录为文本 请求这个接口需要对文件名url编码
@app.post("/transcribe-audio/{filename}")
async def transcribe_audio_request(filename: str):
    try:
        task = transcribe_audio.delay(filename)
        return {"task_id": task.id}
    except Exception as e:
        return {"error": str(e)}

@app.get("/get-task-status/{task_id}")
async def get_task_status(task_id: str):
    task = transcribe_audio.AsyncResult(task_id)
    if task.state == 'PENDING':
        # 任务正在等待处理或未找到
        return {"status": "pending"}
    elif task.state != 'FAILURE':
        # 任务正在运行或已完成
        return {"status": task.state, "result": task.result}
    else:
        # 任务失败
        return {"status": "failure", "error": str(task.info)}


@app.post("/test-celery")
async def test_celery():
    try:
        task = simple_test.delay()
        return {"task_id": task.id}
    except Exception as e:
        return {"error": str(e)}


# 生成词云接口
@app.post("/generate-wordcloud/{filename}")
async def generate_wordcloud(filename: str):
    transcript_path = os.path.join(TRANSCRIPT_DIR, filename + ".txt")
    wordcloud_path = os.path.join(WORDCLOUD_DIR, filename + "_wordcloud.png")

    if not os.path.exists(transcript_path):
        raise HTTPException(status_code=404, detail="Transcript file not found")

    try:
        with open(transcript_path, "r", encoding="utf-8") as file:
            text = file.read()

        text = " ".join(jieba.cut(text))
        wordcloud = WordCloud(
            font_path='C:/Windows/Fonts/simhei.ttf',  # 根据实际情况调整字体路径
            background_color='white',
            width=800,
            height=600,
            margin=2
        ).generate(text)

        os.makedirs(os.path.dirname(wordcloud_path), exist_ok=True)
        wordcloud.to_file(wordcloud_path)

        return {"message": "Wordcloud generated successfully", "wordcloud_file": wordcloud_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 请求单个词云图片
@app.get("/get-wordcloud/{filename}")
async def get_wordcloud(filename: str):
    wordcloud_path = f"output/wordclouds/{filename}_wordcloud.png"

    if os.path.exists(wordcloud_path):
        return FileResponse(wordcloud_path)
    else:
        raise HTTPException(status_code=404, detail="Wordcloud image not found")

# 删除指定的创建词云文件
@app.delete("/delete-wordcloud/{filename}")
async def delete_wordcloud(filename: str):
    wordcloud_path = os.path.join(WORDCLOUD_DIR, filename + "_wordcloud.png")

    if os.path.exists(wordcloud_path):
        os.remove(wordcloud_path)
        return {"message": "Wordcloud image deleted successfully"}
    else:
        raise HTTPException(status_code=404, detail="Wordcloud image not found")