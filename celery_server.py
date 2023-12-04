from celery import Celery
import whisper
import os
from pathlib import Path
from dotenv import load_dotenv
import json
import requests

CELERY_BROKER_URL = "redis://localhost:6379/0"  # 您的 Redis 服务器地址
CELERY_RESULT_BACKEND = "redis://localhost:6379/0"

celery_app = Celery('tasks', broker=CELERY_BROKER_URL, backend=CELERY_RESULT_BACKEND)

# 加载环境变量
load_dotenv()  # 加载 .env 文件中的变量
API_KEY = os.getenv("api_key")  # 从环境变量读取 API 密钥
API_URL = os.getenv("api_url")  # 从环境变量读取 API URL


AUDIO_DIR = "output/audios"
TRANSCRIPT_DIR = "output/transcripts"
AUDIO_DIR = "output/audios"
WORDCLOUD_DIR = "output/wordclouds"    # 词云图像保存目录
TRANSLATED_DIR = "output/translated"  # 翻译文件目录


# 创建转录子进程并加入任务队列
@celery_app.task(bind=True)
def transcribe_audio(self, filename: str):
    print("start subprocess")
    audio_path = os.path.join(AUDIO_DIR, filename+'.mp3')
    transcript_txt_path = os.path.join(TRANSCRIPT_DIR, filename + '.txt')
    transcript_srt_path = os.path.join(TRANSCRIPT_DIR, filename + '.srt')

    # 确保存储转录文件的目录存在
    Path(os.path.dirname(transcript_txt_path)).mkdir(parents=True, exist_ok=True)
    Path(os.path.dirname(transcript_srt_path)).mkdir(parents=True, exist_ok=True)

    model = whisper.load_model("medium")
    result = model.transcribe(audio_path, language="English")

    with open(transcript_txt_path, "w", encoding='utf-8') as txt_file:
        txt_file.write(result["text"])

    with open(transcript_srt_path, "w", encoding='utf-8') as srt_file:
        for segment in result["segments"]:
            start = segment["start"]
            end = segment["end"]
            text = segment["text"]
            srt_file.write(f"{start} --> {end}\n{text}\n\n")

    return {
        "txt_file": transcript_txt_path,
        "srt_file": transcript_srt_path
    }

@celery_app.task
def simple_test():
    return "Hello, World!"


@celery_app.task
def translate_json_task(filename: str):
    input_path = os.path.join(TRANSCRIPT_DIR, filename+".json")
    output_json_path = os.path.join(TRANSCRIPT_DIR, f"{filename}_chinese.json")
    output_txt_path = os.path.join(TRANSCRIPT_DIR, f"{filename}_chinese.txt")

    with open(input_path, "r", encoding="utf-8") as file:
        subtitles = json.load(file)

    translated_subtitles = []
    translated_text = ""

    total_items = len(subtitles)

    for index, item in enumerate(subtitles):
        progress = (index + 1) / total_items * 100
        print(f"Processing item {index + 1}/{total_items} ({progress:.2f}%)")
        data = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {
                    "role": "system",
                    "content": "你现在是一个翻译接口，请将用户给你的内容翻译成中文"
                },
                {
                    "role": "user",
                    "content": item["content"]
                }
            ]
        }

        response = requests.post(API_URL, headers={"Authorization": f"Bearer {API_KEY}"}, json=data)
        if response.status_code == 200:
            translated_content = response.json()['choices'][0]['message']['content']
            translated_item = {
                "time": item["time"],
                "content": translated_content,
                "sentiment": item["sentiment"]
            }
            translated_subtitles.append(translated_item)
            translated_text += translated_content + "\n"

    # Save translated JSON
    os.makedirs(os.path.dirname(output_json_path), exist_ok=True)
    with open(output_json_path, "w", encoding="utf-8") as file:
        json.dump(translated_subtitles, file, ensure_ascii=False, indent=4)

    # Save translated text as TXT
    os.makedirs(os.path.dirname(output_txt_path), exist_ok=True)
    with open(output_txt_path, "w", encoding="utf-8") as file:
        file.write(translated_text)

    return {
        "json_file": output_json_path,
        "txt_file": output_txt_path
    }