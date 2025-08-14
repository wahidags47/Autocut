import os, tempfile, subprocess, zipfile
import yt_dlp
import cv2
import mediapipe as mp
from scenedetect import SceneManager, open_video
from scenedetect.detectors import ContentDetector
from faster_whisper import WhisperModel
from keybert import KeyBERT
from flask import Flask, request, send_from_directory, render_template_string

app = Flask(__name__)

HTML_FORM = """
<!DOCTYPE html>
<html>
<head>
<title>YouTube Highlight Cutter</title>
</head>
<body>
    <h2>YouTube Highlight Cutter</h2>
    <form action="/process" method="post">
        <label>Link YouTube:</label><br>
        <input type="text" name="url" style="width:400px"><br><br>
        <button type="submit">Proses</button>
    </form>
</body>
</html>
"""

@app.route("/", methods=["GET"])
def index():
    return render_template_string(HTML_FORM)

@app.route("/process", methods=["POST"])
def process_video():
    url = request.form.get("url")
    if not url:
        return "URL YouTube tidak diberikan!", 400

    workdir = tempfile.mkdtemp()
    video_path = os.path.join(workdir, "video.mp4")
    output_dir = os.path.join(workdir, "clips")
    os.makedirs(output_dir, exist_ok=True)

    # 1. Download video
    ydl_opts = {"outtmpl": video_path, "format": "mp4", "quiet": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    # 2. Scene detection
    video = open_video(video_path)
    scene_manager = SceneManager()
    scene_manager.add_detector(ContentDetector(threshold=30.0))
    scene_manager.detect_scenes(video)
    scene_list = scene_manager.get_scene_list()

    # 3. Facial tracking
    mp_face_detection = mp.solutions.face_detection
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    face_scenes = []
    with mp_face_detection.FaceDetection(min_detection_confidence=0.5) as face_detector:
        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret: break
            results = face_detector.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            if results.detections:
                time_sec = frame_idx / fps
                face_scenes.append(time_sec)
            frame_idx += 1
    cap.release()

    # 4. Transcribe & keywords
    whisper_model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _ = whisper_model.transcribe(video_path)
    full_text = " ".join([seg.text for seg in segments])
    kw_model = KeyBERT()
    keywords = kw_model.extract_keywords(full_text, keyphrase_ngram_range=(1,2), stop_words='english', top_n=5)

    # 5. Gabungkan scene & face times
    interesting_times = set()
    for start, end in scene_list:
        interesting_times.add((start.get_seconds(), end.get_seconds()))
    for t in face_scenes:
        interesting_times.add((max(0, t-1), t+1))

    # 6. Export clips
    for i, (start, end) in enumerate(sorted(list(interesting_times)), start=1):
        out_path = os.path.join(output_dir, f"clip_{i}.mp4")
        subprocess.run([
            "ffmpeg", "-i", video_path,
            "-ss", str(start), "-to", str(end),
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
            "-c:v", "libx264", "-crf", "28", "-preset", "fast",
            "-c:a", "aac", "-b:a", "128k", out_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)

    # 7. Compress > 40MB
    for file in os.listdir(output_dir):
        path = os.path.join(output_dir, file)
        if os.path.getsize(path) > 40 * 1024 * 1024:
            subprocess.run([
                "ffmpeg", "-i", path, "-b:v", "1M", "-c:a", "aac",
                os.path.join(output_dir, f"compressed_{file}")
            ], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
            os.remove(path)

    # 8. ZIP hasil
    zip_path = os.path.join("static", "highlights.zip")
    os.makedirs("static", exist_ok=True)
    with zipfile.ZipFile(zip_path, "w") as zipf:
        for file in os.listdir(output_dir):
            zipf.write(os.path.join(output_dir, file), file)

    return f"""
    <h3>Proses selesai!</h3>
    <p>Kata kunci: {keywords}</p>
    <a href="/static/highlights.zip">Download ZIP</a>
    """

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
