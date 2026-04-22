"""Generate a production overview PDF for any MV project at any pipeline stage.

The doc adapts to whatever state the storyboard is in:
- After `comfy_mv_plan`: cover + song + scene structure + pipeline reference
- After `comfy_mv_set_brief`: adds creative brief section
- After element generation: adds world elements (with reference thumbnails)
- After `comfy_mv_set_shots`: adds shot list (no thumbnails until images made)
- After image/clip generation: shot list gets start-frame thumbnails
- After `comfy_mv_stitch`: final video stats + production notes

Entry point: build(project_name, output_filename=None) -> Path.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from fpdf import FPDF


# ── Styling ──────────────────────────────────────────────────────────────
AOL_BLUE = (30, 144, 255)
AOL_GOLD = (255, 215, 0)
INK = (20, 20, 28)
MUTE = (110, 110, 120)
HAIRLINE = (220, 220, 226)
PAGE_W = 216  # US letter mm
PAGE_H = 279

# Color swatches for the scene timeline (cycles if more than 7 scenes)
SCENE_COLORS = [
    (30, 144, 255),   # AOL blue — intro
    (255, 107, 0),    # orange — verse 1
    (255, 215, 0),    # gold — chorus
    (100, 180, 255),  # cyan — verse 2
    (200, 100, 255),  # purple — bridge
    (255, 60, 100),   # red/pink — drop
    (80, 80, 100),    # dusk — outro
]

# System TTF fonts on macOS. fpdf2's built-in Helvetica is latin-1 only, so
# anything fancy (em-dashes, curly quotes) blows up. Arial works everywhere.
_FONT_CANDIDATES = {
    "Body": {
        "": ["/System/Library/Fonts/Supplemental/Arial.ttf"],
        "B": ["/System/Library/Fonts/Supplemental/Arial Bold.ttf"],
        "I": ["/System/Library/Fonts/Supplemental/Arial Italic.ttf"],
        "BI": ["/System/Library/Fonts/Supplemental/Arial Bold Italic.ttf"],
    },
    "Mono": {
        "": ["/System/Library/Fonts/Supplemental/Courier New.ttf"],
        "B": ["/System/Library/Fonts/Supplemental/Courier New Bold.ttf"],
    },
}


def _probe_duration(path) -> float | None:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except Exception:
        return None


def _size_mb(path) -> float | None:
    try:
        return Path(path).stat().st_size / (1024 * 1024)
    except Exception:
        return None


def _shot_file_stem(scene_id: int, shot_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", str(shot_id))
    return f"scene_{scene_id:03d}_shot_{safe}"


class ProductionPDF(FPDF):
    """FPDF with a house style — Arial body, Courier mono, AOL-blue accents.

    Falls back to built-in Helvetica if the macOS TTF fonts aren't available,
    which keeps things limping on non-Mac systems (latin-1 characters only).
    """

    def __init__(self, title: str):
        super().__init__(format="letter", unit="mm")
        self.set_auto_page_break(auto=True, margin=18)
        self.set_margins(15, 15, 15)
        self._footer_title = title
        self._unicode_ok = self._load_fonts()
        # When fonts fail, fall back to Helvetica and silently strip non-latin1
        self._body = "Body" if self._unicode_ok else "Helvetica"
        self._mono = "Mono" if self._unicode_ok else "Courier"

    def _load_fonts(self) -> bool:
        try:
            for family, styles in _FONT_CANDIDATES.items():
                for style, paths in styles.items():
                    for p in paths:
                        if Path(p).exists():
                            self.add_font(family, style, p)
                            break
                    else:
                        return False
            return True
        except Exception:
            return False

    def _safe(self, text: str) -> str:
        """Downgrade common typographic chars when running on Helvetica."""
        if self._unicode_ok:
            return text
        replacements = {
            "—": "-",  # em-dash
            "–": "-",  # en-dash
            "‘": "'", "’": "'",  # curly singles
            "“": '"', "”": '"',  # curly doubles
            "…": "...",  # ellipsis
            "·": "-",  # middle dot
        }
        for k, v in replacements.items():
            text = text.replace(k, v)
        return text.encode("latin-1", errors="replace").decode("latin-1")

    def footer(self):
        if self.page_no() == 1:
            return
        self.set_y(-12)
        self.set_font(self._body, size=8)
        self.set_text_color(*MUTE)
        self.cell(0, 6, self._safe(f"{self._footer_title}   ·   p. {self.page_no()}"),
                  align="C")

    # ── Typography helpers ──────────────────────────────────────────────
    def h1(self, text: str):
        self.set_font(self._body, "B", 26)
        self.set_text_color(*INK)
        self.cell(0, 12, self._safe(text), new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*AOL_BLUE)
        self.set_line_width(1.0)
        x, y = self.get_x(), self.get_y()
        self.line(x, y, x + 40, y)
        self.ln(6)

    def h2(self, text: str):
        self.ln(2)
        self.set_font(self._body, "B", 15)
        self.set_text_color(*AOL_BLUE)
        self.cell(0, 8, self._safe(text), new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(*INK)
        self.ln(1)

    def h3(self, text: str):
        self.ln(1)
        self.set_font(self._body, "B", 11)
        self.set_text_color(*INK)
        self.cell(0, 6, self._safe(text), new_x="LMARGIN", new_y="NEXT")

    def body(self, text: str, size: float = 10):
        self.set_font(self._body, size=size)
        self.set_text_color(*INK)
        self.multi_cell(0, 5.2, self._safe(text), new_x="LMARGIN", new_y="NEXT")

    def muted(self, text: str, size: float = 9):
        self.set_font(self._body, "I", size)
        self.set_text_color(*MUTE)
        self.multi_cell(0, 4.8, self._safe(text), new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(*INK)

    def hairline(self, width=None):
        self.set_draw_color(*HAIRLINE)
        self.set_line_width(0.2)
        x = self.get_x()
        y = self.get_y()
        w = width if width is not None else (PAGE_W - 2 * 15)
        self.line(x, y, x + w, y)
        self.ln(2)

    def kv(self, key: str, value: str):
        self.set_font(self._body, "B", 10)
        self.cell(34, 5.4, self._safe(key))
        self.set_font(self._body, size=10)
        self.multi_cell(0, 5.4, self._safe(value), new_x="LMARGIN", new_y="NEXT")


# ── Stage detection ─────────────────────────────────────────────────────


def _detect_stage(data: dict, project_dir: Path) -> dict:
    """Figure out which sections of the doc to include given the current state."""
    brief = data.get("brief") or {}
    has_brief = any(brief.get(k) for k in (
        "narrative", "mood", "visual_style", "color_palette", "camera_style",
    ))
    elements = data.get("elements") or []
    has_elements = bool(elements)
    has_element_refs = any(
        any(Path(p).exists() for p in (el.get("reference_images") or []))
        for el in elements
    )
    scenes = data.get("scenes") or []
    total_shots = sum(len(s.get("shots") or []) for s in scenes)
    has_shots = total_shots > 0
    has_shot_images = any(
        (project_dir / "scenes" / f"{_shot_file_stem(s['id'], sh['id'])}.png").exists()
        for s in scenes
        for sh in (s.get("shots") or [])
    )
    finals = list(project_dir.glob("*_Music_Video.mp4"))
    final_video = max(finals, key=lambda p: p.stat().st_mtime) if finals else None

    return {
        "has_brief": has_brief,
        "has_elements": has_elements,
        "has_element_refs": has_element_refs,
        "has_shots": has_shots,
        "has_shot_images": has_shot_images,
        "final_video": final_video,
    }


# ── Section renderers ──────────────────────────────────────────────────


def _render_cover(pdf: ProductionPDF, data: dict, project_dir: Path, stage: dict):
    pdf.add_page()
    pdf.set_fill_color(*INK)
    pdf.rect(0, 0, PAGE_W, PAGE_H, "F")

    # Hero image — prefer first shot of scene 0, then any scene image, else plain
    hero = None
    scenes = data.get("scenes") or []
    for s in scenes:
        for sh in s.get("shots") or []:
            p = project_dir / "scenes" / f"{_shot_file_stem(s['id'], sh['id'])}.png"
            if p.exists():
                hero = p
                break
        if hero:
            break
        # Also try scene-level image (pre-shot pipeline)
        legacy = project_dir / "scenes" / f"scene_{s['id']:03d}.png"
        if legacy.exists():
            hero = legacy
            break
    if hero is None:
        # Any scene image
        for p in sorted((project_dir / "scenes").glob("*.png")) if (project_dir / "scenes").exists() else []:
            hero = p
            break

    if hero:
        try:
            pdf.image(str(hero), x=0, y=40, w=PAGE_W, h=115)
        except Exception:
            pass

    title = (data.get("title") or project_dir.name).upper()
    song_dur = data.get("duration") or _probe_duration(data.get("audio_path"))
    total_shots = sum(len(s.get("shots") or []) for s in scenes)
    lipsync = sum(
        1 for s in scenes for sh in (s.get("shots") or []) if sh.get("type") == "lipsync"
    )
    broll = total_shots - lipsync

    pdf.set_text_color(255, 255, 255)
    pdf.set_xy(15, 170)
    pdf.set_font(pdf._body, "B", 42)
    pdf.cell(0, 18, pdf._safe(title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(15)
    pdf.set_font(pdf._body, size=14)
    pdf.set_text_color(*AOL_GOLD)
    pdf.cell(0, 8, "Production Overview", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(15)
    pdf.set_text_color(180, 200, 230)
    pdf.set_font(pdf._body, "I", 11)

    stage_label = "plan" if not stage["has_brief"] else \
        "brief" if not stage["has_shots"] else \
        "shot-planned" if not stage["has_shot_images"] else \
        "images" if not stage["final_video"] else "final"
    pdf.cell(0, 6, pdf._safe(f"Snapshot taken at pipeline stage: {stage_label}."),
             new_x="LMARGIN", new_y="NEXT")

    pdf.set_xy(15, 235)
    pdf.set_text_color(150, 160, 180)
    pdf.set_font(pdf._body, size=9)
    stats = [
        ("SONG", f"{song_dur:.1f}s" if song_dur else "—"),
        ("SCENES", f"{len(scenes)}"),
        ("SHOTS", f"{total_shots}  ({lipsync} lipsync, {broll} broll)" if total_shots else "— (not set)"),
    ]
    if stage["final_video"]:
        fd = _probe_duration(stage["final_video"])
        fs = _size_mb(stage["final_video"])
        stats.append(("FINAL", f"{fd:.1f}s, {fs:.1f} MB" if fd else "rendered"))
    for k, v in stats:
        pdf.set_x(15)
        pdf.set_font(pdf._body, "B", 9)
        pdf.set_text_color(*AOL_GOLD)
        pdf.cell(28, 5, pdf._safe(k))
        pdf.set_font(pdf._body, size=9)
        pdf.set_text_color(220, 225, 240)
        pdf.cell(0, 5, pdf._safe(v), new_x="LMARGIN", new_y="NEXT")


def _render_contents(pdf: ProductionPDF, stage: dict):
    pdf.add_page()
    pdf.h1("Contents")
    sections = [
        "The song & lyrics",
        "Creative brief" if stage["has_brief"] else None,
        "World elements" if stage["has_elements"] else None,
        "Scene structure",
        "Shot list" if stage["has_shots"] else None,
        "Technical pipeline",
        "Production notes & lessons" if stage["final_video"] else None,
        "File inventory",
    ]
    sections = [s for s in sections if s]
    pdf.set_font(pdf._body, size=10.5)
    for i, item in enumerate(sections, 1):
        pdf.cell(8, 6, f"{i}.")
        pdf.cell(0, 6, pdf._safe(item), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.muted(
        f"Current pipeline stage: {'final' if stage['final_video'] else 'in-progress'}. "
        "Sections that don't have data yet are skipped — regenerate this doc at "
        "any time to reflect the current storyboard state."
    )


def _render_song(pdf: ProductionPDF, data: dict):
    pdf.add_page()
    pdf.h1("The song")
    audio = data.get("audio_path") or "(audio path not recorded)"
    pdf.kv("File", audio)
    dur = data.get("duration") or _probe_duration(audio)
    if dur:
        pdf.kv("Duration", f"{dur:.2f}s  (~{int(dur // 60)}:{int(dur % 60):02d})")
    pdf.kv("Width", str(data.get("width", 1280)))
    pdf.kv("Height", str(data.get("height", 720)))
    pdf.ln(2)

    pdf.h2("Lyrics (with beat-aligned scene boundaries)")
    full_lyrics = data.get("full_lyrics")
    if not full_lyrics:
        scenes = data.get("scenes") or []
        full_lyrics = "\n".join(
            f"[{s['start']:.1f}s - {s['end']:.1f}s]  {s.get('text', '')}"
            for s in scenes
        )
    pdf.set_font(pdf._mono, size=8.5)
    pdf.set_text_color(*INK)
    pdf.multi_cell(0, 4.2, pdf._safe(full_lyrics), new_x="LMARGIN", new_y="NEXT")


def _render_brief(pdf: ProductionPDF, data: dict):
    brief = data.get("brief") or {}
    pdf.add_page()
    pdf.h1("Creative brief")
    pdf.muted(
        "The shared contract between Claude and the user. Set via "
        "comfy_mv_set_brief. Edit freely and regenerate scenes to match."
    )
    pdf.ln(2)
    for label, key in [
        ("Narrative", "narrative"),
        ("Mood", "mood"),
        ("Visual style", "visual_style"),
        ("Color palette", "color_palette"),
        ("Camera style", "camera_style"),
        ("Global style (applied to every generation)", "global_style"),
        ("Notes", "notes"),
    ]:
        text = brief.get(key)
        if not text:
            continue
        pdf.h3(label)
        pdf.body(text)
        pdf.ln(1)


def _render_elements(pdf: ProductionPDF, data: dict):
    elements = {e["id"]: e for e in data.get("elements") or []}
    if not elements:
        return
    scenes = data.get("scenes") or []
    # Map element_id → scene ids where it appears
    element_scenes = {eid: set() for eid in elements}
    for s in scenes:
        for ref in s.get("element_refs") or []:
            rid = ref["element_id"] if isinstance(ref, dict) else ref
            if rid in element_scenes:
                element_scenes[rid].add(s["id"])
        for sh in s.get("shots") or []:
            for ref in sh.get("element_refs") or []:
                rid = ref["element_id"] if isinstance(ref, dict) else ref
                if rid in element_scenes:
                    element_scenes[rid].add(s["id"])

    pdf.add_page()
    pdf.h1("World elements")
    pdf.muted(
        "The 5W system — every visual component that recurs across scenes. "
        "Each element gets a locked reference image that composes into shots "
        "via qwen-edit-Nref so character / location / prop identity stays "
        "consistent across cuts."
    )
    pdf.ln(2)

    category_order = ["who", "where", "what", "when", "why"]
    category_labels = {
        "who": "WHO — characters",
        "where": "WHERE — locations",
        "what": "WHAT — props",
        "when": "WHEN — time / atmosphere",
        "why": "WHY — mood / theme",
    }
    grouped = {c: [] for c in category_order}
    for eid, el in elements.items():
        cat = el.get("category") or "why"
        grouped.setdefault(cat, []).append((eid, el))

    for cat in category_order:
        items = grouped.get(cat) or []
        if not items:
            continue
        pdf.h2(category_labels[cat])
        for eid, el in items:
            if pdf.get_y() > PAGE_H - 65:
                pdf.add_page()

            start_y = pdf.get_y()
            ref_imgs = el.get("reference_images") or el.get("source_images") or []
            img_path = next((p for p in ref_imgs if Path(p).exists()), None)
            if img_path:
                try:
                    pdf.image(img_path, x=15, y=start_y, w=55, h=32)
                except Exception:
                    pass
            else:
                pdf.set_fill_color(245, 245, 250)
                pdf.rect(15, start_y, 55, 32, "F")
                pdf.set_xy(15, start_y + 13)
                pdf.set_font(pdf._body, "I", 8)
                pdf.set_text_color(*MUTE)
                pdf.cell(55, 6, pdf._safe("(no reference yet)"), align="C")
                pdf.set_text_color(*INK)

            pdf.set_xy(75, start_y)
            pdf.set_font(pdf._body, "B", 12)
            pdf.set_text_color(*INK)
            pdf.cell(0, 6, pdf._safe(el.get("name") or eid),
                     new_x="LMARGIN", new_y="NEXT")

            pdf.set_x(75)
            pdf.set_font(pdf._body, "I", 8.5)
            pdf.set_text_color(*MUTE)
            scene_list = sorted(element_scenes.get(eid) or set())
            meta = f"id: {eid}   ·   scenes: {', '.join(str(n) for n in scene_list) or '—'}"
            pdf.cell(0, 4.5, pdf._safe(meta), new_x="LMARGIN", new_y="NEXT")

            pdf.set_x(75)
            pdf.set_font(pdf._body, size=9)
            pdf.set_text_color(*INK)
            pdf.multi_cell(PAGE_W - 75 - 15, 4.6,
                           pdf._safe(el.get("description") or ""),
                           new_x="LMARGIN", new_y="NEXT")

            pdf.set_y(max(pdf.get_y(), start_y + 36))
            pdf.ln(2)
            pdf.hairline()
            pdf.ln(1)
        pdf.ln(1)


def _render_scenes(pdf: ProductionPDF, data: dict):
    scenes = data.get("scenes") or []
    if not scenes:
        return
    pdf.add_page()
    pdf.h1("Scene structure")
    pdf.muted(
        f"{len(scenes)} tent-pole scenes keyed to the song's sections. "
        "Durations are beat-aligned (librosa during plan). For hyperactive "
        "songs, each scene decomposes into multiple shots (next section)."
    )
    pdf.ln(3)

    pdf.set_fill_color(*AOL_BLUE)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font(pdf._body, "B", 9)
    widths = [10, 30, 22, 22, 18, 82]
    for w, h in zip(widths, ["#", "Section", "Start", "End", "Dur", "Lyric text"]):
        pdf.cell(w, 7, h, border=0, fill=True)
    pdf.ln()

    pdf.set_text_color(*INK)
    pdf.set_font(pdf._body, size=9)
    for s in scenes:
        start, end = float(s["start"]), float(s["end"])
        text = (s.get("text") or "").strip()
        if len(text) > 55:
            text = text[:52] + "..."
        row_y = pdf.get_y()
        pdf.cell(widths[0], 6, str(s["id"]))
        pdf.cell(widths[1], 6, pdf._safe(s.get("segment_type") or ""))
        pdf.cell(widths[2], 6, f"{start:.1f}s")
        pdf.cell(widths[3], 6, f"{end:.1f}s")
        pdf.cell(widths[4], 6, f"{end - start:.1f}s")
        pdf.cell(widths[5], 6, pdf._safe(text))
        pdf.ln()
        pdf.set_draw_color(*HAIRLINE)
        pdf.line(15, row_y + 6, 15 + sum(widths), row_y + 6)
    pdf.ln(3)

    pdf.h2("Timeline")
    total = sum(float(s["end"]) - float(s["start"]) for s in scenes) or 1.0
    strip_w = PAGE_W - 30
    strip_h = 10
    strip_x = 15
    strip_y = pdf.get_y()
    cx = strip_x
    for i, s in enumerate(scenes):
        w = strip_w * ((float(s["end"]) - float(s["start"])) / total)
        pdf.set_fill_color(*SCENE_COLORS[i % len(SCENE_COLORS)])
        pdf.rect(cx, strip_y, w, strip_h, "F")
        pdf.set_xy(cx, strip_y + strip_h + 1)
        pdf.set_font(pdf._body, "B", 7)
        pdf.set_text_color(*INK)
        pdf.cell(w, 3, pdf._safe((s.get("segment_type") or "")[:10]))
        cx += w
    pdf.set_y(strip_y + strip_h + 6)


def _render_shots(pdf: ProductionPDF, data: dict, project_dir: Path):
    scenes = data.get("scenes") or []
    if not any(s.get("shots") for s in scenes):
        return

    elements = {e["id"]: e for e in data.get("elements") or []}
    pdf.add_page()
    pdf.h1("Shot list")
    pdf.muted(
        "Every shot planned for this project. Lipsync shots consume a slice of "
        "the scene's audio and run through ltx23-a2v (performer lip-synced to "
        "that audio window); broll shots are silent wan22-i2v cutaways. The "
        "thumbnail (if present) is the generated start frame that got "
        "animated into the final clip."
    )
    pdf.ln(3)

    running_t = 0.0
    scene_color = {s["id"]: SCENE_COLORS[i % len(SCENE_COLORS)]
                   for i, s in enumerate(scenes)}

    for s in scenes:
        scene_shots = s.get("shots") or []
        if not scene_shots:
            continue

        if pdf.get_y() > PAGE_H - 40:
            pdf.add_page()
        pdf.ln(2)
        pdf.set_fill_color(*scene_color.get(s["id"], AOL_BLUE))
        pdf.set_text_color(255, 255, 255)
        pdf.set_font(pdf._body, "B", 12)
        pdf.cell(
            0, 8,
            pdf._safe(f"  Scene {s['id']}: {(s.get('segment_type') or '').upper()}   "
                      f"({s['start']:.1f}s - {s['end']:.1f}s,  "
                      f"{float(s['end']) - float(s['start']):.1f}s)"),
            fill=True, new_x="LMARGIN", new_y="NEXT",
        )
        pdf.set_text_color(*INK)
        pdf.ln(1)

        for shot in scene_shots:
            dur = float(shot.get("duration", 0))
            shot_start, shot_end = running_t, running_t + dur
            running_t = shot_end

            if pdf.get_y() > PAGE_H - 60:
                pdf.add_page()

            stem = _shot_file_stem(s["id"], shot["id"])
            img_path = project_dir / "scenes" / f"{stem}.png"
            start_y = pdf.get_y()

            if img_path.exists():
                try:
                    pdf.image(str(img_path), x=15, y=start_y, w=70, h=39)
                except Exception:
                    pass
            else:
                pdf.set_fill_color(240, 240, 245)
                pdf.rect(15, start_y, 70, 39, "F")
                pdf.set_xy(15, start_y + 17)
                pdf.set_font(pdf._body, "I", 8)
                pdf.set_text_color(*MUTE)
                pdf.cell(70, 6, pdf._safe("(image not generated yet)"), align="C")
                pdf.set_text_color(*INK)

            col_x = 90
            col_w = PAGE_W - col_x - 15
            pdf.set_xy(col_x, start_y)
            pdf.set_font(pdf._body, "B", 11)
            pdf.cell(
                0, 5.5,
                pdf._safe(f"Scene {s['id']}{shot['id']}   ·   "
                          f"{(shot.get('type') or '').upper()}   ·   {dur:.1f}s"),
                new_x="LMARGIN", new_y="NEXT",
            )

            pdf.set_x(col_x)
            pdf.set_font(pdf._body, "I", 8)
            pdf.set_text_color(*MUTE)
            preset = "ltx23-a2v" if shot.get("type") == "lipsync" else "wan22-i2v"
            pdf.cell(
                0, 4,
                pdf._safe(f"{shot_start:.1f}s - {shot_end:.1f}s   ·   "
                          f"seed {shot.get('seed', 0)}   ·   preset: {preset}"),
                new_x="LMARGIN", new_y="NEXT",
            )
            pdf.set_text_color(*INK)

            refs = shot.get("element_refs") or []
            ref_names = []
            for ref in refs:
                rid = ref["element_id"] if isinstance(ref, dict) else ref
                ref_names.append((elements.get(rid, {}).get("name") or rid) if rid in elements else str(rid))
            pdf.set_x(col_x)
            pdf.set_font(pdf._body, "B", 8.5)
            pdf.cell(
                0, 4.2,
                pdf._safe(f"Refs: {', '.join(ref_names) if ref_names else '(none)'}"),
                new_x="LMARGIN", new_y="NEXT",
            )

            pdf.set_x(col_x)
            pdf.set_font(pdf._body, size=8.5)
            prompt = shot.get("prompt") or ""
            if len(prompt) > 450:
                prompt = prompt[:447] + "..."
            pdf.multi_cell(col_w, 3.8, pdf._safe(prompt),
                           new_x="LMARGIN", new_y="NEXT")

            motion = shot.get("motion_prompt") or ""
            if motion:
                pdf.set_x(col_x)
                pdf.set_font(pdf._body, "I", 8)
                pdf.set_text_color(*MUTE)
                if len(motion) > 280:
                    motion = motion[:277] + "..."
                pdf.multi_cell(col_w, 3.8, pdf._safe(f"Motion: {motion}"),
                               new_x="LMARGIN", new_y="NEXT")
                pdf.set_text_color(*INK)

            pdf.set_y(max(pdf.get_y(), start_y + 42))
            pdf.hairline()
            pdf.ln(1)


def _render_pipeline(pdf: ProductionPDF):
    pdf.add_page()
    pdf.h1("Technical pipeline")
    pdf.muted(
        "The stack that generates the video. Every step is retryable in place; "
        "storyboard.json is the single source of truth."
    )
    pdf.ln(2)

    pdf.h2("Data flow")
    steps = [
        ("1.  Plan",
         "comfy_mv_plan(audio, lyrics=...)  →  beat-aligned scenes, "
         "storyboard.json written."),
        ("2.  Brief",
         "comfy_mv_set_brief(narrative, mood, visual_style, color_palette, "
         "camera_style, global_style, suggested_elements)  →  elements auto-"
         "created from lyrics + brief."),
        ("3.  Elements",
         "comfy_mv_generate_element(element_id)  →  reference image per "
         "element (z-turbo or qwen-edit). Review, regenerate with better "
         "prompt if needed."),
        ("4.  Shots",
         "comfy_mv_set_shots(scene_id, [{id, type, duration, prompt, "
         "motion_prompt, element_refs, seed}, ...])  →  per-scene shot specs."),
        ("5.  Images",
         "comfy_mv_generate(step=\"images\")  →  start frame per shot. "
         "Ref-aware: 1 ref → qwen-edit, 2 refs → qwen-edit-2ref, 3 refs → "
         "qwen-edit-3ref, zero refs → z-turbo."),
        ("6.  Audio",
         "comfy_mv_generate(step=\"audio\")  →  slice the song per lipsync "
         "shot (ffmpeg). Broll shots skip this step."),
        ("7.  Clips",
         "comfy_mv_generate(step=\"clips\")  →  ltx23-a2v per lipsync shot "
         "(image + audio slice → lip-synced clip), wan22-i2v per broll shot "
         "(image → silent motion clip). Resume-safe."),
        ("8.  Stitch",
         "comfy_mv_stitch()  →  normalize each clip (trim to target duration, "
         "1280x720, 25fps, yuv420p, h264) → concat-copy → overlay original "
         "audio. Freeze-pads last frame if the stitched video is shorter "
         "than the song."),
        ("9.  Doc (optional)",
         "comfy_mv_production_doc()  →  generates this PDF from the current "
         "storyboard state. Safe to run at any stage — sections without "
         "data are skipped."),
    ]
    for name, body in steps:
        pdf.set_font(pdf._body, "B", 10)
        pdf.cell(0, 5.2, pdf._safe(name), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font(pdf._body, size=9)
        pdf.multi_cell(0, 4.6, pdf._safe("     " + body),
                       new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

    pdf.h2("Presets")
    preset_table = [
        ("z-turbo", "Z-Image Turbo — txt2img, fast. Element refs from scratch + shots without refs."),
        ("qwen-edit", "Qwen-Image-Edit 2509 — single-ref image composition."),
        ("qwen-edit-2ref", "Qwen-Image-Edit 2509 — two-ref composition (character + location, etc.)."),
        ("qwen-edit-3ref", "Qwen-Image-Edit 2509 — three-ref composition (character + location + prop)."),
        ("multi-angles", "Qwen-Edit + angle LoRA — 8 camera angles from one portrait (character sheets)."),
        ("ltx23-a2v", "LTX 2.3 audio-to-video — encodes audio into latent space; lip sync + beat-synced motion."),
        ("ltx23-i2v", "LTX 2.3 image-to-video — higher-quality i2v with dual-pass upscale."),
        ("wan22-i2v", "Wan 2.2 14B img-to-video — fast silent motion from a start frame."),
    ]
    for name, desc in preset_table:
        pdf.set_font(pdf._mono, "B", 9.5)
        pdf.cell(34, 5.2, pdf._safe(name))
        pdf.set_font(pdf._body, size=9)
        pdf.multi_cell(0, 5.2, pdf._safe(desc), new_x="LMARGIN", new_y="NEXT")


def _render_notes(pdf: ProductionPDF):
    pdf.add_page()
    pdf.h1("Production notes & lessons")
    pdf.muted(
        "General principles that apply to any MV project built with this "
        "pipeline. Project-specific notes live in the brief's `notes` field."
    )
    pdf.ln(2)
    notes = [
        ("Use shot-level editing for hyperactive songs",
         "1-clip-per-scene feels like a slideshow against frenetic audio. "
         "Attach shots (comfy_mv_set_shots) to split each lyric section into "
         "3-8 rapid cuts. Lipsync shots keep sync because their audio slice "
         "is derived from the scene's window."),
        ("Avoid readable text in ANY prompt",
         "Qwen-Image and Z-Image both mangle text. State the constraint in "
         "`global_style` (\"NEVER include readable text, logos, or lettering\") "
         "and treat labels as abstract silhouette shapes."),
        ("Don't ask for lipsync on empty frames",
         "ltx23-a2v insists on generating a performer to sync against. An "
         "\"empty room\" prompt used as a lipsync shot will hallucinate a "
         "figure. Use type=\"broll\" (wan22-i2v) for pure environmental cuts."),
        ("Paste lyrics for sample-heavy tracks",
         "Whisper can't transcribe recycled samples reliably. "
         "comfy_mv_plan(lyrics=...) skips Whisper and structures scenes "
         "from [Section] markers."),
        ("Element reference identity is the whole game",
         "Every scene image that includes a recurring character, location, "
         "or prop should route through qwen-edit-Nref with that element's "
         "reference image. This is why identity holds across 20+ cuts "
         "instead of producing a new random face each shot."),
    ]
    for title, body in notes:
        pdf.h3(title)
        pdf.body(body)
        pdf.ln(2)


def _render_inventory(pdf: ProductionPDF, project_dir: Path, data: dict, stage: dict):
    pdf.add_page()
    pdf.h1("File inventory")
    pdf.muted(f"All paths relative to {project_dir}.")
    pdf.ln(2)

    pdf.h2("Final deliverables")
    if stage["final_video"]:
        fd = _probe_duration(stage["final_video"])
        fs = _size_mb(stage["final_video"])
        pdf.kv("Final video", f"{stage['final_video'].name}   ({fd:.1f}s, {fs:.1f} MB)" if fd else stage["final_video"].name)
    else:
        pdf.kv("Final video", "(not yet rendered — call comfy_mv_stitch)")
    pdf.kv("Source song", data.get("audio_path") or "(not recorded)")
    pdf.kv("Storyboard", "storyboard.json")
    pdf.ln(2)

    pdf.h2("Generated assets")
    scenes_dir = project_dir / "scenes"
    elements_dir = project_dir / "elements"
    segments_dir = project_dir / "segments"
    clips_dir = project_dir / "clips"

    def _count(p: Path, pattern: str) -> int:
        return len(list(p.glob(pattern))) if p.exists() else 0

    rows = [
        ("Element references", f"elements/<id>/*.png  →  {_count(elements_dir, '*/*.png')} images"),
        ("Shot start frames", f"scenes/scene_XXX_shot_Y.png  →  {_count(scenes_dir, 'scene_*_shot_*.png')} images"),
        ("Audio slices", f"segments/*.wav  →  {_count(segments_dir, '*.wav')} slices"),
        ("Video clips", f"clips/*.mp4  →  {_count(clips_dir, '*.mp4')} clips"),
    ]
    for k, v in rows:
        pdf.kv(k, v)
    pdf.ln(2)

    pdf.h2("Resuming / iterating")
    pdf.body(
        "Any single shot can be regenerated without touching the rest. "
        "Delete the shot's clip file (or image, or audio) and call "
        "comfy_mv_generate(scenes=[X], step=\"clips\") — it'll rebuild just "
        "the missing one. If a shot's prompt needs edits, pass an updated "
        "shot list via comfy_mv_set_shots. After any regen, re-run "
        "comfy_mv_stitch."
    )


# ── Public entry point ──────────────────────────────────────────────────


def build(project_name: str, output_filename: str | None = None,
          projects_dir: Path | None = None) -> Path:
    """Build a production PDF for project_name. Returns the output path."""
    if projects_dir is None:
        from mcp_server.server import PROJECTS_DIR  # local import avoids cycles
        projects_dir = PROJECTS_DIR
    project_dir = Path(projects_dir) / project_name
    sb_path = project_dir / "storyboard.json"
    if not sb_path.exists():
        raise FileNotFoundError(f"No storyboard for project '{project_name}'")

    data = json.loads(sb_path.read_text())
    title = data.get("title") or project_name

    if not output_filename:
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", title)
        output_filename = f"{safe}_Production_Overview.pdf"
    output_path = project_dir / output_filename

    stage = _detect_stage(data, project_dir)
    pdf = ProductionPDF(title=f"{title} — Production Overview")

    _render_cover(pdf, data, project_dir, stage)
    _render_contents(pdf, stage)
    _render_song(pdf, data)
    if stage["has_brief"]:
        _render_brief(pdf, data)
    if stage["has_elements"]:
        _render_elements(pdf, data)
    _render_scenes(pdf, data)
    if stage["has_shots"]:
        _render_shots(pdf, data, project_dir)
    _render_pipeline(pdf)
    if stage["final_video"]:
        _render_notes(pdf)
    _render_inventory(pdf, project_dir, data, stage)

    pdf.output(str(output_path))
    return output_path
