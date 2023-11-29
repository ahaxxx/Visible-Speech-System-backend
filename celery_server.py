from celery import Celery
import whisper
import os
from pathlib import Path

CELERY_BROKER_URL = "redis://localhost:6379/0"  # 您的 Redis 服务器地址
CELERY_RESULT_BACKEND = "redis://localhost:6379/0"

celery_app = Celery('tasks', broker=CELERY_BROKER_URL, backend=CELERY_RESULT_BACKEND)

AUDIO_DIR = "output/audios"
TRANSCRIPT_DIR = "output/transcripts"

# 创建转录子进程并加入任务队列
@celery_app.task(bind=True)
def transcribe_audio(self, filename: str):
    print("start subprocess")
    audio_path = os.path.join(AUDIO_DIR, filename+'.mp4.mp3')
    transcript_txt_path = os.path.join(TRANSCRIPT_DIR, filename + '.txt')
    transcript_srt_path = os.path.join(TRANSCRIPT_DIR, filename + '.srt')

    # 确保存储转录文件的目录存在
    Path(os.path.dirname(transcript_txt_path)).mkdir(parents=True, exist_ok=True)
    Path(os.path.dirname(transcript_srt_path)).mkdir(parents=True, exist_ok=True)

    model = whisper.load_model("tiny")
    result = model.transcribe(audio_path, language="Chinese")

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