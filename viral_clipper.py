#!/usr/bin/env python3
"""
Gaming Stream Viral Clipper (Open-Source OpusClip Alternative)
==============================================================
100% Commercial-Safe Open-Source Stack:
  - yt-dlp (Unlicense)           : Live Chat JSONL, Most-Replayed Heatmap, & Stream-Slicing
  - FFmpeg (LGPL/GPL)            : 9:16 Vertical Gaming Layouts & Dynamic Karaoke Captions
  - faster-whisper (MIT)         : Fast CPU Word-Level Speech Transcription
  - OpenCV (Apache 2.0)          : Visual Action / Motion Intensity Scoring
  - Free OpenAI-Compatible LLMs  : GitHub Models / Groq / OpenRouter (optional hook & title AI)
  - rclone (MIT)                 : Optional Google Drive Upload / Download

Key Innovation for 4-10 Hour Gaming Streams on Free GitHub Actions:
  1. ZERO FULL-VIDEO DOWNLOAD: Downloads only the lightweight Live Chat JSONL + info.json
     heatmap first (<50 MB), scores the entire 8-10 hour timeline in seconds, and then
     downloads ONLY the top N 30-50 second clips (~30 MB each).
  2. CHAT-LAG COMPENSATION: Live chat always explodes 10-25 seconds AFTER the actual
     gameplay moment due to stream delay + viewer typing time. This engine shifts each
     clip window backward by `chat_lag_sec` + `pre_roll_sec` so every Short captures the
     build-up, the exact climax, and the streamer/chat reaction.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Configuration & Hype Lexicon (Tailored for Gaming / GTA 6 Livestreams)
# ---------------------------------------------------------------------------

HYPE_KEYWORDS = {
    "omg", "wtf", "no way", "clip", "clip it", "clipped", "insane", "crazy",
    "bro", "bruh", "lmao", "lmfao", "lol", "haha", "w", "ww", "www", "wwww",
    "goat", "sheesh", "fire", "lit", "holy", "yo", "yooo", "yoooo", "pog",
    "poggers", "let's go", "lets go", "gta", "gta6", "gta 6", "vice city",
    "police", "wasted", "busted", "explosion", "glitch", "bug", "secret",
    "easter egg", "what", "whaaat", "ain't no way", "aint no way", "dead",
    "cooked", "clutch", "god", "wild", "unbelievable", "epic", "legendary",
}

HYPE_EMOJIS = ("🔥", "😂", "💀", "😱", "🤯", "😭", "🚨", "👑", "⚡", "💥", "🐐", "👀")


@dataclass
class ClipCandidate:
    rank: int
    start_sec: float
    end_sec: float
    chat_peak_sec: float
    chat_score: float
    heatmap_score: float
    audio_score: float = 0.0
    motion_score: float = 0.0
    final_score: float = 0.0
    top_chat_samples: List[str] = None
    transcript: str = ""
    viral_title: str = ""
    hook_text: str = ""
    hashtags: List[str] = None
    output_video: str = ""

    def __post_init__(self):
        if self.top_chat_samples is None:
            self.top_chat_samples = []
        if self.hashtags is None:
            self.hashtags = ["#GTA6", "#Gaming", "#Shorts", "#Viral"]


# ---------------------------------------------------------------------------
# Utility Helpers
# ---------------------------------------------------------------------------

def run_cmd(cmd: List[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    """Run a shell command cleanly with readable logging."""
    print(f"[CMD] {' '.join(cmd)}", flush=True)
    return subprocess.run(
        cmd,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def format_timestamp(seconds: float) -> str:
    """Convert seconds to HH:MM:SS."""
    seconds = max(0, int(round(seconds)))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_ass_time(seconds: float) -> str:
    """Convert seconds to ASS subtitle timestamp format H:MM:SS.cs."""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def escape_ffmpeg_path(path: Path) -> str:
    """Escape a file path for FFmpeg's libass filter argument."""
    s = str(path.resolve()).replace("\\", "/")
    for ch in (":", "'", "=", ",", "[", "]"):
        s = s.replace(ch, "\\" + ch)
    return s


# ---------------------------------------------------------------------------
# Step 1: Lightweight Metadata & Live Chat Download (Zero Full Video Download)
# ---------------------------------------------------------------------------

def fetch_metadata_and_chat(
    youtube_url: str,
    work_dir: Path,
    cookies_file: Optional[str] = None,
) -> Tuple[Optional[Path], Optional[Path], float]:
    """
    Download ONLY info.json (for duration + Most Replayed heatmap) and live_chat.json
    without downloading the 15-30 GB video file.
    """
    meta_dir = work_dir / "metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(meta_dir / "stream.%(ext)s")

    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-info-json",
        "--write-subs",
        "--sub-langs", "live_chat",
        "--no-warnings",
        "-o", out_template,
    ]
    if cookies_file and Path(cookies_file).exists():
        cmd.extend(["--cookies", cookies_file])
    cmd.append(youtube_url)

    run_cmd(cmd, check=False)

    info_files = list(meta_dir.glob("*.info.json"))
    chat_files = list(meta_dir.glob("*.live_chat.json"))

    info_path = info_files[0] if info_files else None
    chat_path = chat_files[0] if chat_files else None

    duration_sec = 0.0
    if info_path and info_path.exists():
        try:
            with open(info_path, "r", encoding="utf-8") as f:
                info_data = json.load(f)
            duration_sec = float(info_data.get("duration") or 0.0)
            title = info_data.get("title", "Unknown Stream")
            license_str = info_data.get("license") or "Not specified in metadata"
            print(f"[INFO] Stream: {title}")
            print(f"[INFO] Duration: {format_timestamp(duration_sec)} ({duration_sec:.0f}s)")
            print(f"[INFO] License field: {license_str}")
        except Exception as exc:
            print(f"[WARN] Could not parse info.json: {exc}")

    return info_path, chat_path, duration_sec


# ---------------------------------------------------------------------------
# Step 2: Live Chat Mining + Stream-to-Chat Lag Compensation + Heatmap Fusion
# ---------------------------------------------------------------------------

def extract_chat_text(renderer: Dict[str, Any]) -> str:
    """Extract plain text and emote shortcuts from a YouTube live chat renderer."""
    runs = renderer.get("message", {}).get("runs", [])
    parts = []
    for run in runs:
        if "text" in run:
            parts.append(run["text"])
        elif "emoji" in run:
            shortcuts = run["emoji"].get("shortcuts", [])
            if shortcuts:
                parts.append(shortcuts[0])
            else:
                parts.append(run["emoji"].get("emojiId", ""))
    return "".join(parts).strip()


def message_hype_weight(text: str, is_paid: bool = False) -> float:
    """Assign a hype weight to a single chat message."""
    weight = 3.0 if is_paid else 1.0
    lower = text.lower()

    # Check hype keywords
    hits = sum(1 for kw in HYPE_KEYWORDS if kw in lower)
    if hits > 0:
        weight += min(2.5, hits * 0.8)

    # Check hype emojis
    emoji_hits = sum(text.count(e) for e in HYPE_EMOJIS)
    if emoji_hits > 0:
        weight += min(2.0, emoji_hits * 0.5)

    # ALL CAPS shouting boost (e.g. "NO WAYYYYYY")
    letters = [c for c in text if c.isalpha()]
    if len(letters) >= 5 and sum(1 for c in letters if c.isupper()) / len(letters) >= 0.75:
        weight += 0.7

    # Repeated exclamation/question marks
    if "!!!" in text or "???" in text:
        weight += 0.5

    return weight


def parse_live_chat_buckets(
    chat_path: Path,
    bucket_sec: int = 5,
) -> Tuple[Dict[int, float], Dict[int, List[str]], float]:
    """
    Stream-parse a YouTube live_chat.json (JSONL) file with low memory usage.
    Returns:
      - bucket_scores: {bucket_index: total_hype_score}
      - bucket_samples: {bucket_index: [sample messages]}
      - max_offset_sec: maximum observed timestamp in seconds
    """
    bucket_scores: Dict[int, float] = defaultdict(float)
    bucket_samples: Dict[int, List[str]] = defaultdict(list)
    max_offset_sec = 0.0
    total_msgs = 0

    with open(chat_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            replay = obj.get("replayChatItemAction")
            if not replay:
                continue

            offset_ms_raw = replay.get("videoOffsetTimeMsec")
            if offset_ms_raw is None:
                continue
            try:
                offset_sec = float(offset_ms_raw) / 1000.0
            except (ValueError, TypeError):
                continue

            if offset_sec < 0:
                continue
            if offset_sec > max_offset_sec:
                max_offset_sec = offset_sec

            actions = replay.get("actions", [])
            for act in actions:
                item = act.get("addChatItemAction", {}).get("item", {})
                renderer = item.get("liveChatTextMessageRenderer")
                is_paid = False
                if not renderer:
                    renderer = item.get("liveChatPaidMessageRenderer")
                    is_paid = renderer is not None
                if not renderer:
                    continue

                text = extract_chat_text(renderer)
                if not text:
                    continue

                total_msgs += 1
                b_idx = int(offset_sec // bucket_sec)
                w = message_hype_weight(text, is_paid=is_paid)
                bucket_scores[b_idx] += w

                if len(bucket_samples[b_idx]) < 5 and len(text) <= 120:
                    bucket_samples[b_idx].append(text)

    print(f"[CHAT] Parsed {total_msgs:,} chat messages across {len(bucket_scores):,} time buckets.")
    return bucket_scores, bucket_samples, max_offset_sec


def parse_heatmap_buckets(info_path: Optional[Path], bucket_sec: int = 5) -> Dict[int, float]:
    """Extract YouTube 'Most Replayed' heatmap scores from info.json if available."""
    heatmap_buckets: Dict[int, float] = defaultdict(float)
    if not info_path or not info_path.exists():
        return heatmap_buckets

    try:
        with open(info_path, "r", encoding="utf-8") as f:
            info = json.load(f)
        heatmap = info.get("heatmap") or []
        for entry in heatmap:
            start = float(entry.get("start_time", 0.0))
            end = float(entry.get("end_time", start + bucket_sec))
            val = float(entry.get("value", 0.0))
            b_start = int(start // bucket_sec)
            b_end = int(end // bucket_sec)
            for b in range(b_start, b_end + 1):
                heatmap_buckets[b] = max(heatmap_buckets[b], val)
        if heatmap:
            print(f"[HEATMAP] Loaded {len(heatmap)} YouTube Most Replayed segments.")
    except Exception as exc:
        print(f"[WARN] Could not parse heatmap: {exc}")

    return heatmap_buckets


def detect_viral_candidates(
    chat_buckets: Dict[int, float],
    chat_samples: Dict[int, List[str]],
    heatmap_buckets: Dict[int, float],
    duration_sec: float,
    num_clips: int = 5,
    clip_duration: int = 45,
    chat_lag_sec: float = 18.0,
    pre_roll_sec: float = 10.0,
    bucket_sec: int = 5,
    min_gap_sec: float = 90.0,
) -> List[ClipCandidate]:
    """
    Detect the top viral moments and compensate for live-stream-to-chat delay.

    How chat-lag compensation works:
      If viewers explode in chat at T = chat_peak_sec, the actual gameplay event
      happened ~`chat_lag_sec` seconds earlier (default 18s: stream latency + typing time).
      We add `pre_roll_sec` (default 10s) before the event for context/build-up:
          clip_start = max(0, chat_peak_sec - chat_lag_sec - pre_roll_sec)
          clip_end   = min(duration_sec, clip_start + clip_duration)
    """
    all_buckets = sorted(set(chat_buckets.keys()) | set(heatmap_buckets.keys()))

    # Fallback if neither chat nor heatmap is available (e.g., disabled chat replay)
    if not all_buckets:
        if duration_sec <= 0:
            return []
        print("[WARN] No live chat or heatmap found. Falling back to evenly spaced highlights.")
        step = max(clip_duration + 10, duration_sec / (num_clips + 1))
        candidates = []
        for i in range(num_clips):
            start = max(0.0, min(duration_sec - clip_duration, (i + 1) * step - clip_duration / 2))
            candidates.append(
                ClipCandidate(
                    rank=i + 1,
                    start_sec=round(start, 2),
                    end_sec=round(min(duration_sec, start + clip_duration), 2),
                    chat_peak_sec=round(start + clip_duration / 2, 2),
                    chat_score=0.0,
                    heatmap_score=0.0,
                    final_score=50.0,
                )
            )
        return candidates

    # Compute rolling 15-second (3-bucket) chat spike Z-scores to find genuine explosions
    chat_vals = [chat_buckets.get(b, 0.0) for b in all_buckets]
    mean_chat = sum(chat_vals) / max(1, len(chat_vals))
    var_chat = sum((v - mean_chat) ** 2 for v in chat_vals) / max(1, len(chat_vals))
    std_chat = math.sqrt(var_chat) if var_chat > 0 else 1.0

    scored_peaks: List[Tuple[float, int, float, float]] = []
    for b in all_buckets:
        # 3-bucket rolling window (captures sustained explosion rather than 1 random message)
        rolling_chat = (
            chat_buckets.get(b - 1, 0.0) * 0.5
            + chat_buckets.get(b, 0.0) * 1.0
            + chat_buckets.get(b + 1, 0.0) * 0.5
        ) / 2.0
        chat_z = max(0.0, (rolling_chat - mean_chat) / std_chat) if chat_buckets else 0.0
        chat_norm = min(100.0, chat_z * 20.0)

        # Heatmap is already in [0.0, 1.0]
        # Note: Heatmap is aligned to actual video playback time, whereas chat lags by chat_lag_sec!
        # So we check the heatmap bucket shifted BACK by chat_lag_sec to align them!
        lag_bucket_shift = int(round(chat_lag_sec / bucket_sec))
        hm_val = heatmap_buckets.get(max(0, b - lag_bucket_shift), 0.0) * 100.0

        if chat_buckets and heatmap_buckets:
            combined = 0.70 * chat_norm + 0.30 * hm_val
        elif chat_buckets:
            combined = chat_norm
        else:
            combined = hm_val

        scored_peaks.append((combined, b, chat_norm, hm_val))

    # Sort descending by combined score
    scored_peaks.sort(key=lambda x: x[0], reverse=True)

    # Select top non-overlapping candidates (we pick 2x num_clips to let audio/motion refine)
    selected: List[ClipCandidate] = []
    target_pool = max(num_clips, min(num_clips + 3, 12))

    for combined, b_idx, chat_norm, hm_val in scored_peaks:
        if len(selected) >= target_pool:
            break

        chat_peak_sec = b_idx * bucket_sec + (bucket_sec / 2.0)

        # Apply stream-to-chat delay compensation!
        if chat_buckets:
            clip_start = max(0.0, chat_peak_sec - chat_lag_sec - pre_roll_sec)
        else:
            # Heatmap is already video-aligned
            clip_start = max(0.0, chat_peak_sec - pre_roll_sec)

        clip_end = clip_start + clip_duration
        if duration_sec > 0 and clip_end > duration_sec:
            clip_end = duration_sec
            clip_start = max(0.0, clip_end - clip_duration)

        # Avoid overlapping with already chosen clips
        if any(abs(clip_start - c.start_sec) < min_gap_sec for c in selected):
            continue

        # Collect sample chat messages from around the peak
        samples = []
        for offset in (-1, 0, 1, 2):
            samples.extend(chat_samples.get(b_idx + offset, []))
        samples = samples[:6]

        selected.append(
            ClipCandidate(
                rank=len(selected) + 1,
                start_sec=round(clip_start, 2),
                end_sec=round(clip_end, 2),
                chat_peak_sec=round(chat_peak_sec, 2),
                chat_score=round(chat_norm, 1),
                heatmap_score=round(hm_val, 1),
                final_score=round(combined, 1),
                top_chat_samples=samples,
            )
        )

    print(f"[DETECT] Selected {len(selected)} candidate viral windows (chat lag offset: -{chat_lag_sec}s).")
    return selected


# ---------------------------------------------------------------------------
# Step 3: Targeted Segment Slicing (YouTube Stream-Slice OR Local/Drive File)
# ---------------------------------------------------------------------------

def slice_candidate_clip(
    candidate: ClipCandidate,
    clip_path: Path,
    youtube_url: Optional[str] = None,
    local_video: Optional[Path] = None,
    cookies_file: Optional[str] = None,
) -> bool:
    """
    Extract ONLY the 30-50 second segment — either from a local/Google Drive file
    using fast FFmpeg seeking, or directly from YouTube via yt-dlp --download-sections.
    """
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(5.0, candidate.end_sec - candidate.start_sec)

    if local_video and local_video.exists():
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{candidate.start_sec:.2f}",
            "-i", str(local_video),
            "-t", f"{duration:.2f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac", "-b:a", "128k",
            str(clip_path),
        ]
        res = run_cmd(cmd, check=False)
        return res.returncode == 0 and clip_path.exists()

    if youtube_url:
        section = f"*{candidate.start_sec:.1f}-{candidate.end_sec:.1f}"
        cmd = [
            "yt-dlp",
            "--download-sections", section,
            "--force-keyframes-at-cuts",
            "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
            "--merge-output-format", "mp4",
            "--no-warnings",
            "-o", str(clip_path),
        ]
        if cookies_file and Path(cookies_file).exists():
            cmd.extend(["--cookies", cookies_file])
        cmd.append(youtube_url)
        res = run_cmd(cmd, check=False)
        return res.returncode == 0 and clip_path.exists()

    return False


# ---------------------------------------------------------------------------
# Step 4: Audio Excitement & Visual Motion Scoring (OpenCV + FFmpeg)
# ---------------------------------------------------------------------------

def compute_audio_and_motion_scores(clip_path: Path) -> Tuple[float, float]:
    """
    Score a candidate clip's audio loudness excitement and visual frame motion.
    Returns (audio_score_0_100, motion_score_0_100).
    """
    # 1. Audio peak/RMS loudness via FFmpeg volumedetect
    audio_score = 50.0
    try:
        res = run_cmd(
            ["ffmpeg", "-i", str(clip_path), "-af", "volumedetect", "-f", "null", "-"],
            check=False,
            capture=True,
        )
        stderr = res.stderr or ""
        mean_match = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", stderr)
        max_match = re.search(r"max_volume:\s*([-\d.]+)\s*dB", stderr)
        if mean_match and max_match:
            mean_db = float(mean_match.group(1))
            max_db = float(max_match.group(1))
            # Dynamic punchiness + overall energy
            punch = max(0.0, max_db - mean_db)
            energy = max(0.0, min(100.0, (mean_db + 35.0) * 3.0 + punch * 1.5))
            audio_score = round(min(100.0, energy), 1)
    except Exception:
        pass

    # 2. Visual motion intensity via OpenCV (sampled at 2 FPS for speed)
    motion_score = 50.0
    try:
        import cv2
        import numpy as np

        cap = cv2.VideoCapture(str(clip_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, int(round(fps / 2.0)))
        prev_gray = None
        diffs = []
        frame_idx = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if frame_idx % step == 0:
                small = cv2.resize(frame, (160, 90))
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                if prev_gray is not None:
                    diff = cv2.absdiff(gray, prev_gray)
                    diffs.append(float(np.mean(diff)))
                prev_gray = gray
            frame_idx += 1
        cap.release()

        if diffs:
            avg_diff = sum(diffs) / len(diffs)
            # Typical gaming action avg_diff ranges 5..35
            motion_score = round(min(100.0, max(0.0, avg_diff * 3.2)), 1)
    except Exception:
        pass

    return audio_score, motion_score


# ---------------------------------------------------------------------------
# Step 5: Word-Level Transcription (faster-whisper, MIT License)
# ---------------------------------------------------------------------------

def transcribe_clip_words(
    clip_path: Path,
    model_size: str = "base",
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Transcribe speech with exact word-level timestamps using faster-whisper on CPU.
    Returns (full_transcript_text, list_of_word_dicts).
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("[WARN] faster-whisper not installed; skipping speech captions.")
        return "", []

    print(f"[WHISPER] Transcribing {clip_path.name} with model '{model_size}' (CPU int8)...")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(clip_path), word_timestamps=True, vad_filter=True)

    words: List[Dict[str, Any]] = []
    texts: List[str] = []
    for seg in segments:
        texts.append(seg.text.strip())
        if seg.words:
            for w in seg.words:
                clean = w.word.strip()
                if clean:
                    words.append({
                        "word": clean,
                        "start": float(w.start),
                        "end": float(w.end),
                    })

    return " ".join(texts).strip(), words


# ---------------------------------------------------------------------------
# Step 6: Free LLM Viral Title, Hook & Hashtag Generator (Optional)
# ---------------------------------------------------------------------------

def generate_viral_metadata(
    candidate: ClipCandidate,
    game_name: str = "GTA 6",
) -> None:
    """
    Use a free OpenAI-compatible LLM API (Groq, OpenRouter, or GitHub Models)
    to generate a viral Short title, 3-second hook banner, and hashtags.
    Falls back cleanly to a zero-API heuristic if no API key is configured.
    """
    provider = os.getenv("LLM_PROVIDER", "auto").lower()
    api_key = None
    endpoint = None
    model = None

    # Auto-detect whichever free API key the user set in GitHub Secrets
    if (provider in ("auto", "groq")) and os.getenv("GROQ_API_KEY"):
        api_key = os.getenv("GROQ_API_KEY")
        endpoint = "https://api.groq.com/openai/v1/chat/completions"
        model = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
    elif (provider in ("auto", "openrouter")) and os.getenv("OPENROUTER_API_KEY"):
        api_key = os.getenv("OPENROUTER_API_KEY")
        endpoint = "https://openrouter.ai/api/v1/chat/completions"
        model = os.getenv("LLM_MODEL", "openrouter/free")
    elif (provider in ("auto", "github")) and os.getenv("GITHUB_TOKEN"):
        api_key = os.getenv("GITHUB_TOKEN")
        endpoint = "https://models.inference.ai.azure.com/chat/completions"
        model = os.getenv("LLM_MODEL", "gpt-4o-mini")

    if not api_key or not endpoint:
        # Clean offline fallback (no API key required!)
        candidate.viral_title = f"INSANE {game_name} Moment That Broke Chat! #{candidate.rank}"
        candidate.hook_text = f"WAIT FOR IT... 🔥"
        candidate.hashtags = [f"#{game_name.replace(' ', '')}", "#Gaming", "#Shorts", "#Reels", "#Viral"]
        return

    prompt = (
        f"You are a viral YouTube Shorts & Facebook Reels editor for {game_name} gaming clips.\n"
        f"Given the streamer's transcript and live chat reactions below, return ONLY valid JSON with:\n"
        f'  "title": catchy under-65-char viral title,\n'
        f'  "hook": ultra-short 3-5 word uppercase screen hook (e.g. "CHAT LOST IT HERE 🔥"),\n'
        f'  "hashtags": list of 5 relevant hashtags.\n\n'
        f"Transcript: {candidate.transcript or '(gameplay action / no speech)'}\n"
        f"Live Chat Reactions: {', '.join(candidate.top_chat_samples[:5]) or '(hype spike)'}"
    )

    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 200,
    }).encode("utf-8")

    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        # Extract JSON block if wrapped in markdown fences
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group(0))
            candidate.viral_title = str(parsed.get("title", "")).strip()[:80]
            candidate.hook_text = str(parsed.get("hook", "")).strip()[:40]
            tags = parsed.get("hashtags")
            if isinstance(tags, list) and tags:
                candidate.hashtags = [str(t) for t in tags[:6]]
            return
    except Exception as exc:
        print(f"[WARN] Free LLM API call skipped/failed ({exc}); using offline title.")

    candidate.viral_title = f"CRAZY {game_name} Moment! (Chat Exploded) #{candidate.rank}"
    candidate.hook_text = "WAIT FOR THIS MOMENT 🔥"
    candidate.hashtags = [f"#{game_name.replace(' ', '')}", "#Gaming", "#Shorts", "#Reels"]


# ---------------------------------------------------------------------------
# Step 7: OpusClip-Style Animated Word-by-Word ASS Subtitles
# ---------------------------------------------------------------------------

def write_opus_style_ass(
    words: List[Dict[str, Any]],
    ass_path: Path,
    hook_text: str = "",
    words_per_chunk: int = 3,
) -> None:
    """
    Generate an Advanced SubStation Alpha (.ass) subtitle file with:
      - Optional 3-second top viral hook banner
      - Bold OpusClip-style 3-word chunks where the ACTIVE spoken word pops in bright yellow
      - Safe-zone vertical positioning (avoids YouTube Shorts / Reels UI buttons)
    """
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,DejaVu Sans,74,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,420,1
Style: Hook,DejaVu Sans,64,&H0000FFFF,&H00FFFFFF,&H00000000,&HB0000000,-1,0,0,0,100,100,1,0,3,6,0,8,60,60,180,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]

    if hook_text:
        clean_hook = hook_text.replace("\n", " ").replace("{", "").replace("}", "")
        lines.append(
            f"Dialogue: 1,{format_ass_time(0.2)},{format_ass_time(3.5)},Hook,,0,0,0,,{clean_hook}\n"
        )

    # Group words into punchy 3-word phrases and highlight the active word in yellow (&H0000FFFF)
    for i in range(0, len(words), words_per_chunk):
        chunk = words[i : i + words_per_chunk]
        if not chunk:
            continue
        for active_idx, active_word in enumerate(chunk):
            w_start = active_word["start"]
            w_end = active_word["end"]
            if active_idx + 1 < len(chunk):
                w_end = max(w_end, chunk[active_idx + 1]["start"])
            if w_end <= w_start:
                w_end = w_start + 0.15

            rendered_tokens = []
            for j, token in enumerate(chunk):
                txt = token["word"].upper().replace("{", "").replace("}", "")
                if j == active_idx:
                    # Bright Yellow + slight pop scale
                    rendered_tokens.append(r"{\c&H0000FFFF&\fscx112\fscy112}" + txt + r"{\r}")
                else:
                    rendered_tokens.append(txt)

            dialogue_text = " ".join(rendered_tokens)
            lines.append(
                f"Dialogue: 0,{format_ass_time(w_start)},{format_ass_time(w_end)},Caption,,0,0,0,,{dialogue_text}\n"
            )

    ass_path.write_text("".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Step 8: 9:16 Gaming Vertical Video Renderer (FFmpeg, Commercial-Safe)
# ---------------------------------------------------------------------------

def render_vertical_short(
    input_clip: Path,
    ass_path: Path,
    output_mp4: Path,
    layout: str = "blur_stack",
    facecam_corner: str = "bottom_right",
) -> bool:
    """
    Render a 1080x1920 (9:16) viral Short for YouTube Shorts & Facebook Reels.

    Layouts:
      - blur_stack    : Full 16:9 gameplay crisp in center + blurred vertical fill (preserves GTA 6 HUD!)
      - split_facecam : Top 35% zoomed streamer facecam + Bottom 65% centered gameplay
      - center_crop   : Full-bleed 9:16 zoomed gameplay crop
    """
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    ass_escaped = escape_ffmpeg_path(ass_path)

    if layout == "split_facecam":
        # Crop a 28% width x 32% height facecam region from the chosen corner
        corner_map = {
            "bottom_right": "iw*0.72:ih*0.68",
            "bottom_left": "0:ih*0.68",
            "top_right": "iw*0.72:0",
            "top_left": "0:0",
        }
        xy = corner_map.get(facecam_corner, "iw*0.72:ih*0.68")
        vf = (
            f"[0:v]crop=iw*0.28:ih*0.32:{xy},scale=1080:672:force_original_aspect_ratio=increase,"
            f"crop=1080:672[cam];"
            f"[0:v]scale=-2:1248,crop=1080:1248:(iw-1080)/2:0[game];"
            f"[cam][game]vstack=inputs=2,ass=filename='{ass_escaped}'[v]"
        )
    elif layout == "center_crop":
        vf = f"[0:v]scale=-2:1920,crop=1080:1920:(iw-1080)/2:0,ass=filename='{ass_escaped}'[v]"
    else:
        # Default: blur_stack (best for GTA 6 so minimap, HUD, and action stay visible)
        vf = (
            f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
            f"boxblur=20:5[bg];"
            f"[0:v]scale=1080:-2[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,ass=filename='{ass_escaped}'[v]"
        )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_clip),
        "-filter_complex", vf,
        "-map", "[v]",
        "-map", "0:a?",
        "-c:v", "libx264", "-preset", "medium", "-crf", "21",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
        "-movflags", "+faststart",
        str(output_mp4),
    ]
    res = run_cmd(cmd, check=False)
    return res.returncode == 0 and output_mp4.exists()


# ---------------------------------------------------------------------------
# Built-in Demo Self-Test Mode (Verifies Full Pipeline Offline)
# ---------------------------------------------------------------------------

def run_demo_self_test(out_dir: Path) -> None:
    """
    Generate a synthetic 40-second 16:9 test video + mock live_chat.json with a chat
    explosion at T=30s (representing an event at T=12s with 18s chat lag), run the
    entire detection + ASS caption + FFmpeg 9:16 vertical render pipeline, and verify!
    """
    print("[DEMO] Running offline self-test of the full Viral Clipper pipeline...")
    demo_dir = out_dir / "_demo_test"
    demo_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create a 40s 1280x720 synthetic gaming clip with tone audio
    test_video = demo_dir / "mock_stream.mp4"
    run_cmd([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=40",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=40",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
        str(test_video),
    ])

    # 2. Create mock YouTube live_chat.json with a huge chat explosion at T=30..34s
    mock_chat = demo_dir / "mock.live_chat.json"
    lines = []
    # Baseline chatter every 4 seconds
    for t in range(2, 39, 4):
        lines.append(json.dumps({
            "replayChatItemAction": {
                "videoOffsetTimeMsec": str(t * 1000),
                "actions": [{"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
                    "message": {"runs": [{"text": "nice driving in vice city"}]}
                }}}}]
            }
        }))
    # Chat explosion at T=30..33s (18s after the actual T=12s gameplay event!)
    hype_msgs = ["OMG NO WAYYYY 🔥🔥🔥", "WTF DID HE JUST DO 😂", "CLIP IT!!", "WWWWWWW", "GTA 6 IS INSANE 😱"]
    for i in range(25):
        t_ms = 30000 + (i * 150)
        lines.append(json.dumps({
            "replayChatItemAction": {
                "videoOffsetTimeMsec": str(t_ms),
                "actions": [{"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
                    "message": {"runs": [{"text": hype_msgs[i % len(hype_msgs)]}]}
                }}}}]
            }
        }))
    mock_chat.write_text("\n".join(lines), encoding="utf-8")

    # 3. Run chat mining & lag-compensation detection
    buckets, samples, _ = parse_live_chat_buckets(mock_chat, bucket_sec=5)
    candidates = detect_viral_candidates(
        chat_buckets=buckets,
        chat_samples=samples,
        heatmap_buckets={},
        duration_sec=40.0,
        num_clips=1,
        clip_duration=30,
        chat_lag_sec=18.0,
        pre_roll_sec=8.0,
    )
    assert len(candidates) == 1, "Expected 1 candidate clip"
    cand = candidates[0]
    print(f"[DEMO] Detected peak at {cand.chat_peak_sec}s -> shifted clip window: {cand.start_sec}s to {cand.end_sec}s")
    assert cand.start_sec <= 10.0, f"Lag compensation should shift start earlier, got {cand.start_sec}"

    # 4. Slice, score, generate ASS captions, and render 9:16 Short
    raw_slice = demo_dir / "slice_1.mp4"
    assert slice_candidate_clip(cand, raw_slice, local_video=test_video)
    audio_s, motion_s = compute_audio_and_motion_scores(raw_slice)
    print(f"[DEMO] Audio score: {audio_s}, Motion score: {motion_s}")

    mock_words = [
        {"word": "NO", "start": 1.0, "end": 1.3},
        {"word": "WAY", "start": 1.3, "end": 1.7},
        {"word": "CHAT", "start": 1.7, "end": 2.1},
        {"word": "GTA", "start": 2.2, "end": 2.6},
        {"word": "SIX", "start": 2.6, "end": 3.0},
        {"word": "IS", "start": 3.0, "end": 3.3},
        {"word": "WILD!", "start": 3.3, "end": 4.0},
    ]
    ass_file = demo_dir / "clip_1.ass"
    write_opus_style_ass(mock_words, ass_file, hook_text="WAIT FOR THIS GTA 6 MOMENT 🔥")

    final_short = demo_dir / "short_1_vertical.mp4"
    assert render_vertical_short(raw_slice, ass_file, final_short, layout="blur_stack")
    print(f"[DEMO] SUCCESS! Verified 9:16 Short rendered at {final_short} ({final_short.stat().st_size:,} bytes)")


# ---------------------------------------------------------------------------
# Main Pipeline Orchestrator
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Free Open-Source Gaming Viral Shorts Clipper (OpusClip Alternative)"
    )
    parser.add_argument("--url", type=str, help="YouTube livestream VOD URL (Creative Commons / licensed)")
    parser.add_argument("--video-path", type=str, help="Optional local/Google Drive downloaded video file")
    parser.add_argument("--out-dir", type=str, default="output_shorts", help="Output directory")
    parser.add_argument("--num-clips", type=int, default=5, help="Number of viral Shorts to generate")
    parser.add_argument("--clip-duration", type=int, default=45, help="Target clip length in seconds (30-50)")
    parser.add_argument(
        "--chat-lag",
        type=float,
        default=18.0,
        help="Stream-to-chat delay in seconds (shifts clip BEFORE chat explosion; default 18s)",
    )
    parser.add_argument("--pre-roll", type=float, default=10.0, help="Build-up seconds before the action")
    parser.add_argument(
        "--layout",
        type=str,
        default="blur_stack",
        choices=["blur_stack", "split_facecam", "center_crop"],
        help="9:16 vertical gaming layout",
    )
    parser.add_argument(
        "--facecam-corner",
        type=str,
        default="bottom_right",
        choices=["bottom_right", "bottom_left", "top_right", "top_left"],
        help="Where the streamer's facecam is located (used only for split_facecam layout)",
    )
    parser.add_argument("--whisper-model", type=str, default="base", help="faster-whisper model (tiny/base/small)")
    parser.add_argument("--game-name", type=str, default="GTA 6", help="Game title for titles & hashtags")
    parser.add_argument("--cookies", type=str, default=None, help="Optional path to YouTube cookies.txt")
    parser.add_argument("--demo-test", action="store_true", help="Run built-in offline verification self-test")

    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.demo_test:
        run_demo_self_test(out_dir)
        return

    if not args.url and not args.video_path:
        parser.error("Please provide --url (YouTube livestream link) and/or --video-path.")

    work_dir = out_dir / "_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Lightweight chat + metadata download
    info_path, chat_path, duration_sec = (None, None, 0.0)
    if args.url:
        info_path, chat_path, duration_sec = fetch_metadata_and_chat(args.url, work_dir, args.cookies)

    local_video = Path(args.video_path) if args.video_path else None
    if duration_sec <= 0 and local_video and local_video.exists():
        probe = run_cmd(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(local_video)],
            check=False,
            capture=True,
        )
        try:
            duration_sec = float((probe.stdout or "0").strip())
        except ValueError:
            duration_sec = 0.0

    # Step 2: Mine live chat + heatmap with stream-to-chat lag compensation
    chat_buckets: Dict[int, float] = {}
    chat_samples: Dict[int, List[str]] = {}
    if chat_path and chat_path.exists():
        chat_buckets, chat_samples, max_chat_sec = parse_live_chat_buckets(chat_path)
        if duration_sec <= 0:
            duration_sec = max_chat_sec + 30.0

    heatmap_buckets = parse_heatmap_buckets(info_path)

    candidates = detect_viral_candidates(
        chat_buckets=chat_buckets,
        chat_samples=chat_samples,
        heatmap_buckets=heatmap_buckets,
        duration_sec=duration_sec,
        num_clips=args.num_clips,
        clip_duration=args.clip_duration,
        chat_lag_sec=args.chat_lag,
        pre_roll_sec=args.pre_roll,
    )

    if not candidates:
        print("[ERROR] Could not determine any clip timestamps. Check the YouTube URL or video file.")
        sys.exit(1)

    # Step 3 & 4: Slice ONLY the top candidate segments & compute audio + motion scores
    sliced_candidates: List[Tuple[ClipCandidate, Path]] = []
    for idx, cand in enumerate(candidates, start=1):
        raw_clip = work_dir / f"raw_{idx:02d}.mp4"
        ok = slice_candidate_clip(
            cand,
            raw_clip,
            youtube_url=args.url,
            local_video=local_video,
            cookies_file=args.cookies,
        )
        if not ok:
            print(f"[WARN] Skipping candidate #{idx} (slice failed).")
            continue

        audio_s, motion_s = compute_audio_and_motion_scores(raw_clip)
        cand.audio_score = audio_s
        cand.motion_score = motion_s
        # Composite Viral Score: Chat/Heatmap (60%) + Audio Excitement (25%) + Visual Motion (15%)
        cand.final_score = round(0.60 * cand.final_score + 0.25 * audio_s + 0.15 * motion_s, 1)
        sliced_candidates.append((cand, raw_clip))

    # Re-rank by final composite score and keep top N
    sliced_candidates.sort(key=lambda pair: pair[0].final_score, reverse=True)
    top_clips = sliced_candidates[: args.num_clips]

    # Step 5, 6, 7, 8: Transcribe, generate viral title/hook, burn ASS captions, render 9:16 Shorts
    finished: List[ClipCandidate] = []
    for final_rank, (cand, raw_clip) in enumerate(top_clips, start=1):
        cand.rank = final_rank
        transcript, words = transcribe_clip_words(raw_clip, model_size=args.whisper_model)
        cand.transcript = transcript

        generate_viral_metadata(cand, game_name=args.game_name)

        ass_path = work_dir / f"short_{final_rank:02d}.ass"
        write_opus_style_ass(words, ass_path, hook_text=cand.hook_text)

        safe_slug = re.sub(r"[^a-zA-Z0-9]+", "_", cand.viral_title).strip("_")[:40]
        final_mp4 = out_dir / f"Short_{final_rank:02d}_{safe_slug}.mp4"

        ok = render_vertical_short(
            raw_clip,
            ass_path,
            final_mp4,
            layout=args.layout,
            facecam_corner=args.facecam_corner,
        )
        if ok:
            cand.output_video = final_mp4.name
            finished.append(cand)
            print(f"[DONE] Created {final_mp4.name} (Viral Score: {cand.final_score}/100)")

    # Save metadata JSON and ready-to-paste CSV for YouTube Shorts / Facebook Reels
    meta_json = out_dir / "shorts_metadata.json"
    meta_json.write_text(json.dumps([asdict(c) for c in finished], indent=2), encoding="utf-8")

    csv_path = out_dir / "upload_sheet.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Rank", "Filename", "Viral_Score", "Start_Time", "End_Time",
            "Chat_Peak_Time", "Title", "Hook_Banner", "Hashtags", "Transcript"
        ])
        for c in finished:
            writer.writerow([
                c.rank, c.output_video, c.final_score,
                format_timestamp(c.start_sec), format_timestamp(c.end_sec),
                format_timestamp(c.chat_peak_sec), c.viral_title,
                c.hook_text, " ".join(c.hashtags), c.transcript
            ])

    # Clean temporary work files so GitHub Artifact / Drive upload stays lean
    shutil.rmtree(work_dir, ignore_errors=True)
    print(f"\n[COMPLETE] Generated {len(finished)} viral 9:16 Shorts in: {out_dir.resolve()}")


if __name__ == "__main__":
    main()