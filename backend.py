import jieba
import requests
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
import json
from textblob import TextBlob
from wordcloud import WordCloud

from celery_server import transcribe_audio, simple_test, translate_json_task
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv


# 加载环境变量
load_dotenv()  # 加载 .env 文件中的变量
API_KEY = os.getenv("api_key")  # 从环境变量读取 API 密钥
API_URL = os.getenv("api_url")  # 从环境变量读取 API URL


# 数据库文件位置
DATABASE_URL = "sqlite.db"

# 视频文件的存储目录
VIDEO_DIR = "output/videos"
AUDIO_DIR = "output/audios"
TRANSCRIPT_DIR = "output/transcripts"  # 转录文件将被保存的目录
WORDCLOUD_DIR = "output/wordclouds"    # 词云图像保存目录
TRANSLATED_DIR = "output/translated"  # 翻译文件目录

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
    wordcloud_path = os.path.join(WORDCLOUD_DIR, filename + "_wordcloud.json")

    if os.path.exists(wordcloud_path):
        os.remove(wordcloud_path)
        return {"message": "Wordcloud json deleted successfully"}
    else:
        raise HTTPException(status_code=404, detail="Wordcloud json not found")


# 情感分析
def analyze_sentiment(text):
    analysis = TextBlob(text)
    return 'positive' if analysis.sentiment.polarity > 0 else 'negative' if analysis.sentiment.polarity < 0 else 'neutral'

@app.get("/analyze-sentiment/{filename}")
async def analyze_subtitle(filename: str):
    file_path = os.path.join(TRANSCRIPT_DIR, filename + ".srt")

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Subtitle file not found")

    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            lines = file.readlines()

        subtitles = []
        for i in range(0, len(lines), 3):  # 三行一循环
            if i + 1 < len(lines):
                time = lines[i].strip()
                content = lines[i + 1].strip()

                subtitle = {
                    'time': time,
                    'content': content,
                    'sentiment': analyze_sentiment(content)
                }
                subtitles.append(subtitle)

        # 保存 JSON 数据到文件
        save_path = os.path.join(TRANSCRIPT_DIR, f"{filename}.json")
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, 'w', encoding='utf-8') as json_file:
            json.dump(subtitles, json_file, ensure_ascii=False, indent=4)

        return subtitles
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/get-audio/{filename}")
async def get_audio(filename: str):
    file_path = os.path.join(AUDIO_DIR, filename+".mp3")

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Audio file not found")

    return FileResponse(file_path)


@app.post("/gpt-request")
async def gpt_request(prompt: str, text: str):
    try:
        data = {
            "model": "gpt-4-1106-preview",  # 或其他适用的 GPT 模型
            "messages": [
                {
                    "role": "system",
                    "content": prompt
                },
                {
                    "role": "user",
                    "content": text
                }
            ]
        }

        response = requests.post(API_URL, headers={"Authorization": f"Bearer {API_KEY}"}, json=data)
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"API request failed with status code {response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/translate-json/{filename}")
async def translate_json(filename: str):
    try:
        task = translate_json_task.delay(filename)  # 启动后台任务
        return {"task_id": task.id}  # 返回任务 ID，用于稍后查询任务状态
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/summarize-text/{filename}")
async def summarize_text(filename: str):
    file_path = os.path.join(TRANSCRIPT_DIR, filename+".txt")

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=file_path+"TXT file not found")

    try:
        with open(file_path, "r", encoding="utf-8") as file:
            text = file.read()

        # 构建 GPT 请求数据
        data = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {
                    "role": "system",
                    "content": "你现在是一个文本总结接口，请使用中文总结这段文字:"
                },
                {
                    "role": "user",
                    "content": text
                }
            ]
        }

        response = requests.post(API_URL, headers={"Authorization": f"Bearer {API_KEY}"}, json=data)
        if response.status_code == 200:
            summary = response.json()['choices'][0]['message']['content']
            return {"summary": summary}
        else:
            raise Exception(f"API request failed with status code {response.status_code}")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/evaluate-speech/{filename}")
async def evaluate_speech(filename: str):
    file_path = os.path.join(TRANSCRIPT_DIR, filename+".txt")

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="TXT file not found")

    try:
        with open(file_path, "r", encoding="utf-8") as file:
            text = file.read()

        # 构建 GPT 请求数据
        prompt = """
        我们的评分标准包括以下几个维度：
        内容分析：研究演讲的主题，信息的详细程度，以及其传递的核心信息和观点。也可以考察其是否涉及特定的政治、社会或文化议题。
        语言风格：分析演讲者使用的语言特点，如词汇的选择、句式结构、修辞手法（比如比喻、反问、排比）等，以及这些元素如何影响信息的传达。
        逻辑结构：评估演讲的组织结构，包括论点的提出、论据的支持、结论的得出，以及这些元素如何协同工作以加强演讲的说服力。
        情感表达：分析演讲中的情感色彩，例如热情、愤怒、同情等，以及这些情感如何与听众的共鸣和反应相联系。
        目标受众：考虑演讲是如何针对特定听众群体的需求和期望进行定制的，包括使用的语言、内容的选择以及呈现方式。
        文化和历史背景：考虑演讲在特定的文化和历史背景下的意义，以及它如何反映或影响当时的社会环境。
        批判性分析：从批判性角度审视演讲的内容，包括其可能的偏见、误导性陈述或遗漏的重要视角。
        
        您需要在每个维度上进行0到10分的打分，并给出您的中文评语
        
        返回格式为json，示例如下
        
        {
            “内容分析”:{
                score:,
                comments:
            },
            “语言风格”:{
                score:,
                comments:
            },
            “逻辑结构”:{
                score:,
                comments:
            },
            “情感表达”:{
                score:,
                comments:
            },
            “目标受众”:{
                score:,
                comments:
            },
            “文化和历史背景”:{
                score:,
                comments:
            },
            “批判性分析”:{
                score:,
                comments:
            },		
        }
        """

        data = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {
                    "role": "system",
                    "content": prompt
                },
                {
                    "role": "user",
                    "content": text
                }
            ]
        }

        response = requests.post(API_URL, headers={"Authorization": f"Bearer {API_KEY}"}, json=data)
        if response.status_code == 200:
            evaluation_str = response.json()['choices'][0]['message']['content']
            try:
                # 尝试将字符串解析为 JSON 对象
                evaluation_json = json.loads(evaluation_str)
            except json.JSONDecodeError:
                raise Exception("Failed to parse the evaluation string into JSON")

            return {"evaluation": evaluation_json}
        else:
            raise Exception(f"API request failed with status code {response.status_code}")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 生成单个词频
@app.post("/generate-freq/{filename}")
async def generate_wordcloud(filename: str):
    transcript_path = os.path.join(TRANSCRIPT_DIR, filename + ".txt")
    freq_path = os.path.join(WORDCLOUD_DIR, filename + "_freq.json")

    if not os.path.exists(transcript_path):
        raise HTTPException(status_code=404, detail="Transcript file not found")

    try:
        # 如果词频文件存在，则直接返回该文件
        if os.path.exists(freq_path):
            with open(freq_path, "r", encoding="utf-8") as freq_file:
                freq_data = json.load(freq_file)
            return freq_data

        # 否则，处理文本，计算词频
        with open(transcript_path, "r", encoding="utf-8") as file:
            text = file.read()

        # 使用jieba进行分词
        words = jieba.cut(text)
        word_freq = {}
        for word in words:
            if word not in word_freq:
                word_freq[word] = 0
            word_freq[word] += 1

        # 保存词频数据为JSON文件
        os.makedirs(os.path.dirname(freq_path), exist_ok=True)
        with open(freq_path, "w", encoding="utf-8") as freq_file:
            json.dump(word_freq, freq_file, ensure_ascii=False)

        return {"message": "Word frequency data generated successfully", "frequency_file": freq_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 请求单个词频数据
@app.get("/get-freq/{filename}")
async def get_wordcloud(filename: str):
    freq_path = os.path.join(WORDCLOUD_DIR, filename + "_freq.json")

    if not os.path.exists(freq_path):
        raise HTTPException(status_code=404, detail="Frequency data file not found")

    try:
        with open(freq_path, "r", encoding="utf-8") as file:
            freq_data = json.load(file)
        return freq_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))