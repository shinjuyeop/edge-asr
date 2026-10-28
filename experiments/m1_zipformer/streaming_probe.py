import os
import time
import wave
from pathlib import Path

import numpy as np
import sherpa_onnx

MODEL = Path("models/sherpa-onnx-streaming-zipformer-korean-2024-06-16")
WAV = MODEL / "test_wavs/0.wav"

recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
    tokens=str(MODEL / "tokens.txt"),
    encoder=str(MODEL / "encoder-epoch-99-avg-1.int8.onnx"),
    decoder=str(MODEL / "decoder-epoch-99-avg-1.onnx"),
    joiner=str(MODEL / "joiner-epoch-99-avg-1.int8.onnx"),
    num_threads=int(os.getenv("ASR_THREADS", "2")),
    provider="cpu",
    sample_rate=16000,
    feature_dim=80,
)

with wave.open(str(WAV), "rb") as f:
    assert f.getframerate() == 16000
    assert f.getnchannels() == 1
    assert f.getsampwidth() == 2
    audio = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
    audio = audio.astype(np.float32) / 32768.0

stream = recognizer.create_stream()
chunk_size = 1600  # 100ms
start = time.perf_counter()
first_text_time = None
previous_text = ""
max_overrun_ms = 0.0

for pos in range(0, len(audio), chunk_size):
    end = min(pos + chunk_size, len(audio))

    # 실제 오디오 수집 속도 재현
    deadline = start + end / 16000
    time.sleep(max(0, deadline - time.perf_counter()))

    stream.accept_waveform(16000, audio[pos:end])

    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)

    now = time.perf_counter()
    max_overrun_ms = max(
        max_overrun_ms, max(0, now - deadline) * 1000
    )

    result = recognizer.get_result(stream)
    text = result.text if hasattr(result, "text") else str(result)

    if text and text != previous_text:
        elapsed = now - start
        if first_text_time is None:
            first_text_time = elapsed

        print(f"[{elapsed:.3f}s] {text}", flush=True)
        previous_text = text

# 마지막 오디오까지 디코딩
stream.accept_waveform(16000, np.zeros(10560, dtype=np.float32))
stream.input_finished()

while recognizer.is_ready(stream):
    recognizer.decode_stream(stream)

result = recognizer.get_result(stream)
final_text = result.text if hasattr(result, "text") else str(result)

duration = len(audio) / 16000
finished = time.perf_counter() - start

print("\n--- Summary ---")
print(f"Audio duration: {duration:.3f}s")
print(f"First partial: {first_text_time}")
print(f"Final result: {final_text}")
print(f"Final elapsed: {finished:.3f}s")
print(f"Finalization delay: {finished - duration:.3f}s")
print(f"Max processing overrun: {max_overrun_ms:.1f}ms")
