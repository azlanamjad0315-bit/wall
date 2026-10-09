FROM python:3.12-slim
# ffmpeg is needed to turn Piper's .wav into WhatsApp's ogg/opus voice-note format
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# piper is optional: uncomment for 100% offline TTS (~120 MB with onnxruntime)
# RUN pip install --no-cache-dir piper-tts==1.2.0
COPY . .
RUN useradd -m app && mkdir -p /media /models && chown -R app:app /media /models /app
USER app
EXPOSE 8090
CMD ["uvicorn","voicenote:app","--host","0.0.0.0","--port","8090"]
