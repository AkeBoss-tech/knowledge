#!/usr/bin/env python3

from __future__ import annotations

import json
import math
import os
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "artifacts" / "demo"
GIF_PATH = OUT_DIR / "krail-terminal-demo.gif"
PNG_PREVIEW_PATH = OUT_DIR / "krail-terminal-demo-preview.png"

WIDTH = 1400
HEIGHT = 900
PADDING_X = 36
PADDING_Y = 34
HEADER_H = 54
LINE_GAP = 8
FONT_SIZE = 24
TITLE_FONT_SIZE = 21
BG = "#0b1020"
PANEL = "#111827"
TEXT = "#d9e1f2"
MUTED = "#93a4c3"
GREEN = "#9be564"
CYAN = "#7dd3fc"
YELLOW = "#facc15"
RED = "#fb7185"


@dataclass
class Step:
    display_command: str
    summary_lines: list[str]


def run_json(command: str) -> dict:
    proc = subprocess.run(
        ["bash", "-lc", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": "packages/rail-py"},
        check=True,
    )
    return json.loads(proc.stdout)


def wrap_lines(lines: list[str], width: int = 92) -> list[str]:
    wrapped: list[str] = []
    for line in lines:
        if not line:
            wrapped.append("")
            continue
        wrapped.extend(textwrap.wrap(line, width=width) or [""])
    return wrapped


def collect_steps() -> list[Step]:
    doctor = run_json(
        "python -m rail.cli --local --path examples/minimal-project doctor"
    )
    doctor_ok = sum(1 for check in doctor["checks"] if check["ok"])
    doctor_total = len(doctor["checks"])
    doctor_lines = [
        f"Workspace health: {doctor_ok}/{doctor_total} checks passing",
        next(check["detail"] for check in doctor["checks"] if check["name"] == "pack"),
        next(
            check["detail"]
            for check in doctor["checks"]
            if check["name"] == "knowledge_mode"
        ),
        next(
            check["detail"]
            for check in doctor["checks"]
            if check["name"] == "workflows"
        ),
    ]

    search = run_json(
        'python -m rail.cli --local --path examples/minimal-project search "employment index" --explain'
    )
    search_lines = ["Top local hits:"]
    for idx, hit in enumerate(search["hits"][:3], start=1):
        search_lines.append(f"{idx}. {hit['path']} (score {hit['score']})")
    search_lines.append(f"Search mode: {search['explain']['mode']}")

    think = run_json(
        'python -m rail.cli --local --path examples/minimal-project think "How does the synthetic employment index differ by region?"'
    )
    think_lines = [
        think["answer"],
        "Strongest citations:",
    ]
    for citation in think["citations"][:3]:
        think_lines.append(f"{citation['ref']} {citation['path']}")
    think_lines.append("Suggested next actions:")
    for action in think["suggested_next_actions"][:2]:
        think_lines.append(f"- {action}")

    subprocess.run(
        [
            "bash",
            "-lc",
            "python -m rail.cli --local --path examples/minimal-project graph build >/dev/null",
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": "packages/rail-py"},
        check=True,
    )
    graph_entities = run_json(
        "python -m rail.cli --local --path examples/minimal-project graph entities --type Dataset"
    )
    graph_lines = [
        f"Graph dataset entities: {graph_entities['count']}",
        *[
            f"- {entity['label']}"
            for entity in graph_entities["entities"][:3]
        ],
    ]

    workflow = run_json(
        "python -m rail.cli --local --path examples/minimal-project workflow run weekly_research_review --dry-run"
    )
    workflow_lines = [
        f"Workflow status: {workflow['status']}",
        f"Run id: {workflow['run_id']}",
        "Dry-run steps:",
    ]
    for step in workflow["steps"]:
        workflow_lines.append(f"- {step['id']} ({step['kind']})")

    return [
        Step("cd ~/Documents/CodingProjects/knowledge", []),
        Step("source .venv/bin/activate", ["Virtual environment activated."]),
        Step(
            "cd examples/minimal-project",
            ["Loaded the public synthetic KRAIL fixture."],
        ),
        Step(
            "krail --local doctor",
            doctor_lines,
        ),
        Step(
            'krail --local search "employment index" --explain',
            search_lines,
        ),
        Step(
            'krail --local think "How does the synthetic employment index differ by region?"',
            think_lines,
        ),
        Step(
            "krail --local graph entities --type Dataset",
            graph_lines,
        ),
        Step(
            "krail --local workflow run weekly_research_review --dry-run",
            workflow_lines,
        ),
    ]


def get_fonts() -> tuple[ImageFont.FreeTypeFont, ImageFont.FreeTypeFont]:
    mono_path = "/System/Library/Fonts/Menlo.ttc"
    return (
        ImageFont.truetype(mono_path, FONT_SIZE),
        ImageFont.truetype(mono_path, TITLE_FONT_SIZE),
    )


def frame_image(lines: list[tuple[str, str]], mono: ImageFont.FreeTypeFont, title_font):
    im = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(im)

    radius = 24
    panel_box = (24, 24, WIDTH - 24, HEIGHT - 24)
    draw.rounded_rectangle(panel_box, radius=radius, fill=PANEL)
    draw.rounded_rectangle((24, 24, WIDTH - 24, 24 + HEADER_H), radius=radius, fill="#0f172a")
    draw.rectangle((24, 24 + HEADER_H // 2, WIDTH - 24, 24 + HEADER_H), fill="#0f172a")

    for idx, color in enumerate(["#ff5f57", "#febc2e", "#28c840"]):
        x = 50 + idx * 26
        y = 51
        draw.ellipse((x, y, x + 14, y + 14), fill=color)

    draw.text((WIDTH // 2 - 160, 38), "KRAIL terminal demo", font=title_font, fill=MUTED)

    y = 24 + HEADER_H + PADDING_Y
    for text, color in lines:
        draw.text((24 + PADDING_X, y), text, font=mono, fill=color)
        bbox = draw.textbbox((24 + PADDING_X, y), text, font=mono)
        y = bbox[3] + LINE_GAP

    return im


def build_frames(steps: list[Step]) -> tuple[list[Image.Image], list[int]]:
    mono, title_font = get_fonts()
    prompt = "akash@knowledge % "
    frames: list[Image.Image] = []
    durations: list[int] = []
    transcript: list[tuple[str, str]] = [("KRAIL makes local agent workflows easy to demo.", MUTED), ("", TEXT)]

    def add_frame(ms: int = 90):
        visible = transcript[-24:]
        frames.append(frame_image(visible, mono, title_font))
        durations.append(ms)

    add_frame(900)

    for step in steps:
        command_line = prompt
        for ch in step.display_command:
            command_line += ch
            current = transcript + [(command_line, GREEN)]
            visible = current[-24:]
            frames.append(frame_image(visible, mono, title_font))
            durations.append(35)

        transcript.append((command_line, GREEN))
        add_frame(350)

        wrapped = wrap_lines(step.summary_lines)
        for line in wrapped:
            transcript.append((line, TEXT if not line.startswith("- ") and not line.endswith(":") else CYAN if line.endswith(":") else TEXT))
            add_frame(420 if line else 150)

        transcript.append(("", TEXT))
        add_frame(280)

    transcript.append(("Done. Local setup, search, think, graph, and workflow dry-run in one flow.", YELLOW))
    add_frame(1800)
    return frames, durations


def save_gif(frames: list[Image.Image], durations: list[int]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        GIF_PATH,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=False,
        disposal=2,
    )
    frames[-1].save(PNG_PREVIEW_PATH)


def main() -> None:
    steps = collect_steps()
    frames, durations = build_frames(steps)
    save_gif(frames, durations)
    print(GIF_PATH)
    print(PNG_PREVIEW_PATH)


if __name__ == "__main__":
    main()
