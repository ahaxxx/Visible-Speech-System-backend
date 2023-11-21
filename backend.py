from fastapi import FastAPI, File, UploadFile, HTTPException
from pydantic import BaseModel
import shutil
import os
from uuid import uuid4
from datetime import datetime
import sqlite3
from typing import List
from fastapi.middleware.cors import CORSMiddleware

# 数据库文件位置
DATABASE_URL = "sqlite.db"

# 视频文件的存储目录
VIDEO_DIR = "videos"

# 确保视频存储目录存在
os.makedirs(VIDEO_DIR, exist_ok=True)

app = FastAPI()

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
