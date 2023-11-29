import srt
import whisper
import datetime

# 音频转录函数
def transcribe_to_srt_and_txt(audio_path, language="Chinese"):
    model = whisper.load_model("base")
    result = model.transcribe(audio_path, language=language)

    segments = []
    for segment in result["segments"]:
        start = datetime.timedelta(seconds=segment["start"])
        end = datetime.timedelta(seconds=segment["end"])
        text = segment["text"]
        segments.append(srt.Subtitle(index=len(segments)+1, start=start, end=end, content=text))

    srt_content = srt.compose(segments)
    with open(f"{audio_path}.srt", "w", encoding="utf-8") as f:
        f.write(srt_content)

    txt_content = "\n".join([segment["text"] for segment in result["segments"]])
    with open(f"{audio_path}.txt", "w", encoding="utf-8") as f:
        f.write(txt_content)