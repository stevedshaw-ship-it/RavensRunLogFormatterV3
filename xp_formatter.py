#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XPression As Run Log Formatter
A standalone replacement for the Ross Video XPression As Run Log Formatter.

Features
--------
* Parse one or many .arlog files from MULTIPLE source folders
* Decode .thumb thumbnail files (Windows BMP) found across all source folders
* Filter by date / time range
* Group by Scene Name / Item Name / Content / Content Items
* Sort by First Time On Air / Scene Name / Item Name / Content
* Partition output by None / Game State / Time Ranges
* Concealing Layers rules (hide concealed layers while a concealing layer is live)
* Live preview, CSV export and PDF export (Ravens purple / gold styling)
* Save / Load report settings (JSON)

Dependencies: reportlab, Pillow  (tkinter ships with Python)
Author: Baltimore Ravens Broadcast Engineering
Version: 1.0
"""

import os
import re
import csv
import json
import struct
import traceback
from io import BytesIO
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
APP_NAME = "Ravens As Run Log Formatter"
APP_VERSION = "4.2"
FOOTER_TEXT = f"{APP_NAME} - version {APP_VERSION}"

# Baltimore Ravens brand colours
PRIMARY_PURPLE = "#24135F"
SECONDARY_GOLD = "#9A7611"
WHITE = "#FFFFFF"
BLACK = "#000000"

# .arlog column order (after the leading log-timestamp prefix is stripped)
COLUMNS = [
    "ONAIRTIME", "OFFAIRTIME", "ITEMID", "ITEMNAME", "FBID",
    "LAYER", "SCENENAME", "SCENEUID", "THUMBHASH", "CONTENT",
]

# Matches the leading "DD-MM-YY HH:MM:SS.mmm: " log timestamp prefix
_LOG_PREFIX_RE = re.compile(r"^\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3}:\s")

# Date formats used inside the data columns (ONAIRTIME / OFFAIRTIME)
_DATE_FORMATS = (
    "%d-%m-%Y %H:%M:%S.%f",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%y %H:%M:%S.%f",
    "%d-%m-%y %H:%M:%S",
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
class PlayRecord:
    """A single on-air / off-air play event parsed from an .arlog line."""

    __slots__ = (
        "on_air", "off_air", "item_id", "item_name", "fbid",
        "layer", "scene_name", "scene_uid", "thumb_hash", "content",
        "source_file", "line_no",
    )

    def __init__(self, on_air, off_air, item_id, item_name, fbid, layer,
                 scene_name, scene_uid, thumb_hash, content,
                 source_file="", line_no=0):
        self.on_air = on_air            # datetime or None
        self.off_air = off_air          # datetime or None
        self.item_id = item_id
        self.item_name = item_name
        self.fbid = fbid
        self.layer = layer              # int or None
        self.scene_name = scene_name
        self.scene_uid = scene_uid
        self.thumb_hash = thumb_hash
        self.content = content
        self.source_file = source_file
        self.line_no = line_no

    @property
    def duration(self):
        """Duration of this play as a timedelta (0 if unknown/negative)."""
        if self.on_air and self.off_air:
            d = self.off_air - self.on_air
            if d.total_seconds() >= 0:
                return d
        return timedelta(0)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def _parse_date(value):
    """Parse a date string from a data column; return datetime or None."""
    value = (value or "").strip()
    if not value:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _parse_int(value):
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None


# Field name -> PlayRecord attribute. Header names are matched case-insensitively
# with non-alphanumeric characters stripped, so "ON AIR TIME", "OnAirTime" and
# "ONAIRTIME" all map to the same field.
_FIELD_ALIASES = {
    "onairtime": "on_air",
    "offairtime": "off_air",
    "itemid": "item_id",
    "itemname": "item_name",
    "fbid": "fbid",
    "layer": "layer",
    "scenename": "scene_name",
    "sceneuid": "scene_uid",
    "thumbhash": "thumb_hash",
    "content": "content",
}


def _normalize_col(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").strip().lower())


def _parse_header(body):
    """Parse an '@COL,COL,...' header line into a list of PlayRecord attribute
    names (or None for unknown columns), preserving positional order. Returns
    None if the line does not look like a column header."""
    body = body.lstrip("@").strip()
    # Header columns are comma-separated; data rows are tab-separated.
    names = [c for c in body.split(",")]
    if len(names) < 2:
        return None
    attrs = [_FIELD_ALIASES.get(_normalize_col(n)) for n in names]
    # Require at least the two timestamps to consider this a valid header.
    if "on_air" not in attrs or "off_air" not in attrs:
        return None
    return attrs


# Default column layout (used when a file has no recognizable @ header).
_DEFAULT_LAYOUT = [_FIELD_ALIASES[_normalize_col(c)] for c in COLUMNS]


def parse_arlog_file(path, warnings=None):
    """
    Parse a single .arlog file.
    Returns a list of PlayRecord. Malformed lines are skipped (with a warning).

    Each file may declare its own column order via an '@COL,COL,...' header
    line (Tessera builds differ). We honour that header and map columns by
    name, falling back to the default layout if no header is present.
    """
    if warnings is None:
        warnings = []
    records = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as exc:
        warnings.append(f"Could not read {os.path.basename(path)}: {exc}")
        return records

    layout = list(_DEFAULT_LAYOUT)   # active column -> attribute mapping
    # Index of the last column that should absorb any extra trailing tabs
    # (normally CONTENT). Computed per layout.
    def _content_idx(lay):
        for i in range(len(lay) - 1, -1, -1):
            if lay[i] == "content":
                return i
        return len(lay) - 1

    content_idx = _content_idx(layout)

    for idx, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n").rstrip("\r")
        if not line.strip():
            continue
        # Skip "*** ... started" banner lines
        if line.startswith("***"):
            continue
        # Column header line: update the active layout.
        if line.lstrip().startswith("@"):
            parsed = _parse_header(line.lstrip())
            if parsed:
                layout = parsed
                content_idx = _content_idx(layout)
            continue

        # Strip the leading log-timestamp prefix "DD-MM-YY HH:MM:SS.mmm: ".
        # Every real record line carries this prefix; a line without it is a
        # continuation of the previous record's CONTENT (content fields may
        # contain embedded line breaks). Append it so nothing is lost.
        m = _LOG_PREFIX_RE.match(line)
        if not m:
            if records:
                records[-1].content = (records[-1].content + "\n" + line).strip()
            else:
                warnings.append(f"{os.path.basename(path)} line {idx}: "
                                f"orphan line with no record, skipped")
            continue
        body = line[m.end():]

        parts = body.split("\t")
        if len(parts) < 2:
            warnings.append(f"{os.path.basename(path)} line {idx}: too few columns "
                            f"({len(parts)}), skipped")
            continue

        # If there are extra tabs (tabs embedded in CONTENT), fold the tail
        # into the content column so trailing columns stay aligned.
        if len(parts) > len(layout) and content_idx is not None:
            # Everything from the content column onward becomes a single field.
            parts = parts[:content_idx] + ["\t".join(parts[content_idx:])]
        # Pad short rows.
        while len(parts) < len(layout):
            parts.append("")

        # Map columns by attribute name per the active layout.
        fields = {}
        for i, attr in enumerate(layout):
            if attr and i < len(parts):
                fields[attr] = parts[i]

        rec = PlayRecord(
            on_air=_parse_date(fields.get("on_air", "")),
            off_air=_parse_date(fields.get("off_air", "")),
            item_id=(fields.get("item_id", "") or "").strip(),
            item_name=(fields.get("item_name", "") or "").strip(),
            fbid=(fields.get("fbid", "") or "").strip(),
            layer=_parse_int(fields.get("layer", "")),
            scene_name=(fields.get("scene_name", "") or "").strip(),
            scene_uid=(fields.get("scene_uid", "") or "").strip(),
            thumb_hash=(fields.get("thumb_hash", "") or "").strip(),
            content=(fields.get("content", "") or "").strip(),
            source_file=path,
            line_no=idx,
        )
        if rec.on_air is None:
            warnings.append(f"{os.path.basename(path)} line {idx}: "
                            f"unparseable on-air time, skipped")
            continue
        records.append(rec)
    return records


def find_arlog_files(folders):
    """Return a sorted list of every .arlog file found in the given folders."""
    found = []
    for folder in folders:
        if not folder or not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            if name.lower().endswith(".arlog"):
                found.append(os.path.join(folder, name))
    return sorted(set(found))


def parse_all_folders(folders, warnings=None):
    """Parse every .arlog file across all folders; return combined records."""
    if warnings is None:
        warnings = []
    all_records = []
    files = find_arlog_files(folders)
    for path in files:
        all_records.extend(parse_arlog_file(path, warnings))
    return all_records, files


# ---------------------------------------------------------------------------
# Thumbnail decoding
# ---------------------------------------------------------------------------
def _decode_raw_rgba(data):
    """
    Fallback decoder for raw RGBA pixel data. Tries common widths and returns
    a PIL Image, or None if nothing sensible can be produced.
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    pixel_count = len(data) // 4
    if pixel_count == 0:
        return None
    for width in (320, 256, 192, 160, 128, 96, 64, 48, 32):
        if pixel_count % width == 0:
            height = pixel_count // width
            if 1 <= height <= 4096:
                try:
                    return Image.frombytes(
                        "RGBA", (width, height), data[:width * height * 4]
                    )
                except Exception:
                    continue
    return None


_THUMB_INDEX_CACHE = {}


def _thumb_index(folders):
    """Map lowercase '<hash>.thumb' -> full path, searching every listed
    folder AND all of its subfolders. Built once per folder set."""
    key = tuple(sorted(f for f in folders if f))
    if key in _THUMB_INDEX_CACHE:
        return _THUMB_INDEX_CACHE[key]
    index = {}
    for folder in key:
        if not os.path.isdir(folder):
            continue
        for root, _dirs, files in os.walk(folder):
            for name in files:
                if name.lower().endswith(".thumb"):
                    index.setdefault(name.lower(), os.path.join(root, name))
    _THUMB_INDEX_CACHE[key] = index
    return index


def clear_thumb_cache():
    _THUMB_INDEX_CACHE.clear()


def load_group_thumbnail(group, folders):
    """Try every thumbnail hash used by the group's plays (most recent play
    first) and return the first image that can be found and decoded."""
    recs = sorted(group.records, key=lambda r: r.on_air or datetime.min,
                  reverse=True)
    seen = set()
    for r in recs:
        h = (r.thumb_hash or "").strip()
        if h and h not in seen:
            seen.add(h)
            img = load_thumbnail(h, folders)
            if img is not None:
                return img
    return None


THUMB_ROW_W, THUMB_ROW_H = 160, 30      # inline thumbnail in preview rows
THUMB_PANEL_W, THUMB_PANEL_H = 320, 320  # selected-item thumbnail panel


def fit_image(pil_img, max_w, max_h):
    """Scale a PIL image to fit within max_w x max_h, keeping aspect ratio
    (may upscale small images). Returns a new image or None."""
    if pil_img is None:
        return None
    w, h = pil_img.size
    if w <= 0 or h <= 0:
        return None
    scale = min(max_w / w, max_h / h)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    from PIL import Image
    return pil_img.resize((nw, nh), Image.LANCZOS)


def make_photo(pil_img, max_w, max_h):
    """Return a Tk PhotoImage of pil_img fitted to the box, or None."""
    img = fit_image(pil_img, max_w, max_h)
    if img is None:
        return None
    try:
        from PIL import ImageTk
        return ImageTk.PhotoImage(img)
    except Exception:
        traceback.print_exc()
        return None


def load_thumbnail(thumb_hash, folders):
    """
    Locate <thumb_hash>.thumb across folders and return a PIL Image (RGB),
    or None. The .thumb files are Windows BMP files, so PIL opens them
    directly; a raw-RGBA fallback is attempted if that fails.
    """
    if not thumb_hash:
        return None
    try:
        from PIL import Image
    except ImportError:
        return None

    path = _thumb_index(folders).get(thumb_hash.lower() + ".thumb")
    if not path:
        return None

    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None

    # Standard BMP / image container first
    try:
        img = Image.open(BytesIO(data))
        img.load()
        return img.convert("RGB")
    except Exception:
        pass

    # Fallback: raw RGBA pixels
    img = _decode_raw_rgba(data)
    if img is not None:
        return img.convert("RGB")
    return None



# ---------------------------------------------------------------------------
# Processing: filter / conceal / partition / group / sort / aggregate
# ---------------------------------------------------------------------------
def split_content_items(content):
    """Split a CONTENT field into individual items (pipe separated)."""
    if not content:
        return []
    return [c.strip() for c in content.split("|") if c.strip()]


def filter_by_range(records, start_dt=None, end_dt=None):
    """Keep records whose on-air time falls within [start_dt, end_dt]."""
    out = []
    for r in records:
        if r.on_air is None:
            continue
        if start_dt and r.on_air < start_dt:
            continue
        if end_dt and r.on_air > end_dt:
            continue
        out.append(r)
    return out


def apply_concealing(records, rules):
    """
    Apply concealing-layer rules.

    Each rule is a dict: {name, concealing, concealed, hide}.
    When hide is True, any record whose FBID matches `concealed` is removed if a
    record whose FBID matches `concealing` was live (overlapping in time) at the
    same moment.
    """
    active = [r for r in rules if r.get("hide")]
    if not active:
        return records

    # Pre-index concealing plays by FBID for quick overlap checks
    def overlaps(a, b):
        a_off = a.off_air or a.on_air
        b_off = b.off_air or b.on_air
        if a.on_air is None or b.on_air is None:
            return False
        return a.on_air <= b_off and b.on_air <= a_off

    kept = []
    for rec in records:
        hidden = False
        for rule in active:
            concealed = (rule.get("concealed") or "").strip()
            concealing = (rule.get("concealing") or "").strip()
            if not concealed or not concealing:
                continue
            if rec.fbid != concealed:
                continue
            # Is there a concealing play overlapping this record?
            for other in records:
                if other is rec:
                    continue
                if other.fbid == concealing and overlaps(rec, other):
                    hidden = True
                    break
            if hidden:
                break
        if not hidden:
            kept.append(rec)
    return kept


def _parse_time_only(value):
    """Parse an HH:MM:SS (or HH:MM) time-of-day string -> time or None."""
    value = (value or "").strip()
    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    return None


def partition_records(records, mode, time_ranges=None, default_state="Other"):
    """
    Partition records into named buckets.

    mode: "none" | "game_state" | "time_ranges"
    Returns an ordered list of (partition_name, [records]).
    """
    if mode == "game_state":
        buckets = {}
        for r in records:
            key = f"Layer {r.layer}" if r.layer is not None else "Layer (unknown)"
            buckets.setdefault(key, []).append(r)
        # Sort by layer number where possible
        def layer_key(name):
            m = re.search(r"-?\d+", name)
            return int(m.group()) if m else 10 ** 9
        return [(k, buckets[k]) for k in sorted(buckets, key=layer_key)]

    if mode == "time_ranges" and time_ranges:
        parsed = []
        for tr in time_ranges:
            start = _parse_time_only(tr.get("start"))
            end = _parse_time_only(tr.get("end"))
            parsed.append((tr.get("name", "Range"), start, end))
        buckets = {name: [] for name, _, _ in parsed}
        other = []
        for r in records:
            if r.on_air is None:
                other.append(r)
                continue
            t = r.on_air.time()
            placed = False
            for name, start, end in parsed:
                if start is None or end is None:
                    continue
                if start <= end:
                    inside = start <= t <= end
                else:  # window wraps past midnight
                    inside = t >= start or t <= end
                if inside:
                    buckets[name].append(r)
                    placed = True
                    break
            if not placed:
                other.append(r)
        result = [(name, buckets[name]) for name, _, _ in parsed]
        if other:
            result.append((default_state or "Other", other))
        return result

    # mode == "none"
    return [("", records)]


def _group_key(record, group_by):
    if group_by == "scene_name":
        return record.scene_name or "(no scene)"
    if group_by == "item_name":
        return record.item_name or "(no name)"
    if group_by == "content":
        return record.content or "(no content)"
    # content_items handled separately (one record -> many keys)
    return record.item_name or "(no name)"


class ReportGroup:
    """An aggregated group of plays shown as one row/block in the report.

    `key` is how records were bucketed (scene name, item name, content, ...).
    The displayed columns are always taken from the records themselves:
    `name` is the item name and `scene_name` the scene name, so they match the
    original report regardless of which field grouping was based on. When the
    display name should instead be the bucketing key itself (Content / Content
    Items grouping), `name_from_key` is set.
    """

    def __init__(self, key, name_from_key=False):
        self.key = key
        self.name = key if name_from_key else ""
        self._name_from_key = name_from_key
        self.scene_name = ""
        self.records = []
        self.thumb_hash = ""
        self.content = ""

    def add(self, record):
        self.records.append(record)
        if not self._name_from_key and not self.name and record.item_name:
            self.name = record.item_name
        if not self.scene_name and record.scene_name:
            self.scene_name = record.scene_name
        if not self.thumb_hash and record.thumb_hash:
            self.thumb_hash = record.thumb_hash
        if not self.content and record.content:
            self.content = record.content

    @property
    def first_played(self):
        times = [r.on_air for r in self.records if r.on_air]
        return min(times) if times else None

    @property
    def last_played(self):
        times = [r.on_air for r in self.records if r.on_air]
        return max(times) if times else None

    @property
    def total_time(self):
        total = timedelta(0)
        for r in self.records:
            total += r.duration
        return total

    @property
    def play_count(self):
        return len(self.records)


def group_records(records, group_by):
    """Group records into ReportGroup objects keyed per the group_by mode."""
    groups = {}
    if group_by == "content_items":
        for r in records:
            items = split_content_items(r.content) or ["(no content)"]
            for item in items:
                g = groups.get(item)
                if g is None:
                    # Content Items grouping: the bucket key *is* the item shown.
                    g = ReportGroup(item, name_from_key=True)
                    groups[item] = g
                g.add(r)
    else:
        for r in records:
            key = _group_key(r, group_by)
            g = groups.get(key)
            if g is None:
                g = ReportGroup(key)
                groups[key] = g
            g.add(r)
    # Fallback: if no item name was found for a group, show the bucket key.
    result = list(groups.values())
    for g in result:
        if not g.name:
            g.name = g.key
    return result


def sort_groups(groups, sort_by):
    """Sort ReportGroup list in place-like fashion; return a new sorted list."""
    if sort_by == "first_time_on_air":
        return sorted(groups, key=lambda g: (g.first_played or datetime.max))
    if sort_by == "scene_name":
        return sorted(groups, key=lambda g: (g.scene_name or "").lower())
    if sort_by == "item_name":
        return sorted(groups, key=lambda g: (g.name or "").lower())
    if sort_by == "content":
        return sorted(groups, key=lambda g: (g.content or "").lower())
    return groups


def build_report(records, settings):
    """
    Full processing pipeline.
    Returns an ordered list of (partition_name, [ReportGroup]).
    """
    recs = filter_by_range(
        records, settings.get("start_dt"), settings.get("end_dt"))
    recs = apply_concealing(recs, settings.get("concealing_rules", []))

    partitions = partition_records(
        recs,
        settings.get("partition_mode", "none"),
        settings.get("time_ranges", []),
        settings.get("default_state", "Other"),
    )

    content_filter = (settings.get("content_filter") or "").strip().lower()
    group_by = settings.get("group_by", "scene_name")
    sort_by = settings.get("sort_by", "first_time_on_air")

    result = []
    for pname, precs in partitions:
        # The "Content Item" filter narrows the underlying play records
        # BEFORE grouping (same as the original tool). Filtering assembled
        # groups would drop whole scene/item groups whose first record did
        # not match, and misattribute the surviving row's scene/content/
        # thumbnail -- the cause of the mismatched 849-count row.
        if content_filter:
            precs = [r for r in precs
                     if content_filter in (r.content or "").lower()
                     or content_filter in (r.item_name or "").lower()]
        groups = group_records(precs, group_by)
        groups = sort_groups(groups, sort_by)
        result.append((pname, groups))
    return result



# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def fmt_datetime(dt):
    """Format a datetime like the original report: M/D/YYYY h:mm:ss AM/PM."""
    if dt is None:
        return ""
    # %-d / %-I are not portable (Windows); strip leading zeros manually.
    s = dt.strftime("%m/%d/%Y %I:%M:%S %p")
    month, rest = s.split("/", 1)
    day, rest2 = rest.split("/", 1)
    date_part = f"{int(month)}/{int(day)}/{rest2.split(' ')[0]}"
    time_part = rest2.split(" ", 1)[1]
    # strip leading zero on hour
    hh, mm_ss_ap = time_part.split(":", 1)
    time_part = f"{int(hh)}:{mm_ss_ap}"
    return f"{date_part} {time_part}"


def fmt_date(dt):
    if dt is None:
        return ""
    return f"{dt.month}/{dt.day}/{dt.year}"


def fmt_duration(td):
    """Format a timedelta as HH:MM:SS (hours may exceed 24)."""
    if td is None:
        return "00:00:00"
    total = int(td.total_seconds())
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def count_items(report):
    return sum(len(groups) for _, groups in report)


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------
def export_csv(report, settings, out_path):
    """Write the processed report to a CSV file."""
    with open(out_path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow([settings.get("title", APP_NAME)])
        writer.writerow(["Client", settings.get("client", "")])
        writer.writerow(["Generated", fmt_date(datetime.now())])
        rng = ""
        if settings.get("start_dt") or settings.get("end_dt"):
            rng = f"{fmt_date(settings.get('start_dt'))} to {fmt_date(settings.get('end_dt'))}"
        writer.writerow(["Range", rng])
        writer.writerow(["Note", settings.get("note", "")])
        writer.writerow(["Item Count", count_items(report)])
        writer.writerow([])

        # Content is the first (leftmost) column; the Partition/Item column
        # has been removed entirely.
        writer.writerow([
            "Content", "Name", "Scene Name", "First Played", "Last Played",
            "Total Time", "Play Count",
        ])
        for pname, groups in report:
            for g in groups:
                writer.writerow([
                    g.content,
                    g.name, g.scene_name,
                    fmt_datetime(g.first_played),
                    fmt_datetime(g.last_played),
                    fmt_duration(g.total_time),
                    g.play_count,
                ])
        writer.writerow([])


# ---------------------------------------------------------------------------
# PDF export (reportlab)
# ---------------------------------------------------------------------------
def export_pdf(report, settings, out_path, folders, logo_path=None):
    """Render the processed report to a PDF using reportlab."""
    clear_thumb_cache()

    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image as RLImage,
    )
    from reportlab.lib.enums import TA_LEFT

    purple = colors.HexColor(PRIMARY_PURPLE)
    gold = colors.HexColor(SECONDARY_GOLD)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "XPTitle", parent=styles["Title"], textColor=purple,
        fontSize=20, alignment=TA_LEFT, spaceAfter=4)
    hdr_label = ParagraphStyle(
        "HdrLabel", parent=styles["Normal"], fontSize=10, alignment=2,
        fontName="Helvetica-Bold")
    hdr_value = ParagraphStyle(
        "HdrValue", parent=styles["Normal"], fontSize=10)
    cell = ParagraphStyle("Cell", parent=styles["Normal"], fontSize=8,
                          leading=10)
    cell_hdr = ParagraphStyle("CellHdr", parent=styles["Normal"], fontSize=9,
                              leading=11, textColor=colors.white,
                              fontName="Helvetica-Bold", alignment=1)
    content_label = ParagraphStyle(
        "ContentLabel", parent=styles["Normal"], fontSize=9,
        fontName="Helvetica-Bold")
    content_value = ParagraphStyle(
        "ContentValue", parent=styles["Normal"], fontSize=8, leading=10)

    def on_page(canvas, doc):
        canvas.saveState()
        w, h = letter
        canvas.setStrokeColor(gold)
        canvas.setLineWidth(1)
        canvas.line(0.6 * inch, 0.6 * inch, w - 0.6 * inch, 0.6 * inch)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawString(0.6 * inch, 0.45 * inch, FOOTER_TEXT)
        canvas.drawRightString(w - 0.6 * inch, 0.45 * inch,
                               f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        out_path, pagesize=letter,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        topMargin=0.6 * inch, bottomMargin=0.8 * inch,
        title=settings.get("title", APP_NAME))

    story = []
    story.append(Paragraph(settings.get("title", "Ravens As Run Report"),
                           title_style))
    # Gold rule under title
    rule = Table([[""]], colWidths=[doc.width])
    rule.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 2, gold),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(rule)
    story.append(Spacer(1, 8))

    # Header block (labels/values) with optional logo on the right
    rng = ""
    if settings.get("start_dt") or settings.get("end_dt"):
        rng = f"{fmt_date(settings.get('start_dt'))} to {fmt_date(settings.get('end_dt'))}"
    info_rows = [
        ("Client:", settings.get("client", "")),
        ("Generated:", fmt_date(datetime.now())),
        ("Range:", rng),
        ("Note:", settings.get("note", "")),
        ("Item Count:", str(count_items(report))),
    ]
    info_tbl = Table(
        [[Paragraph(l, hdr_label), Paragraph(v, hdr_value)] for l, v in info_rows],
        colWidths=[1.1 * inch, doc.width - 3.6 * inch])
    info_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))

    logo_flowable = ""
    if logo_path and os.path.isfile(logo_path):
        try:
            logo_flowable = RLImage(logo_path, width=2.2 * inch, height=0.9 * inch,
                                    kind="proportional")
        except Exception:
            logo_flowable = ""
    top = Table([[info_tbl, logo_flowable]],
                colWidths=[doc.width - 2.4 * inch, 2.4 * inch])
    top.setStyle(TableStyle([
        ("VALIGN", (0, 0), (0, 0), "TOP"),
        ("VALIGN", (1, 0), (1, 0), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    story.append(top)
    story.append(Spacer(1, 14))

    # 7-column item table spanning the full page width. Content is the first
    # (leftmost) column (wider); the Partition/Item column has been removed.
    # Thumbnails are banner images (e.g. 320x14) and are rendered as a
    # separate full-width Image flowable BELOW each item block.
    _weights = [2.6, 1.3, 1.2, 1.15, 1.15, 0.85, 0.75]  # Content first
    _wsum = sum(_weights)
    col_widths = [doc.width * w / _wsum for w in _weights]

    for pname, groups in report:
        if pname:
            story.append(Spacer(1, 4))
            story.append(Paragraph(
                f"<b>{pname}</b>",
                ParagraphStyle("Part", parent=styles["Normal"],
                               fontSize=12, textColor=purple)))
            story.append(Spacer(1, 4))

        for g in groups:
            # Build the banner thumbnail (full page width, max 0.5 inch tall),
            # inserted as its own flowable after the item table below.
            banner_flowable = None
            img = load_group_thumbnail(g, folders)
            if img is not None:
                try:
                    bio = BytesIO()
                    img.save(bio, format="PNG")
                    bio.seek(0)
                    iw, ih = img.size
                    max_w = doc.width
                    max_h = 0.5 * inch
                    ratio = min(max_w / iw, max_h / ih)
                    banner_flowable = RLImage(
                        bio, width=iw * ratio, height=ih * ratio)
                    banner_flowable.hAlign = "LEFT"
                except Exception:
                    banner_flowable = None

            content_txt = g.content or ""
            header = [Paragraph(h, cell_hdr) for h in
                      ["Content", "Name", "Scene Name", "First Played",
                       "Last Played", "Total Time", "Play Count"]]
            data_row = [
                Paragraph(content_txt, cell),
                Paragraph(g.name or "", cell),
                Paragraph(g.scene_name or "", cell),
                Paragraph(fmt_datetime(g.first_played), cell),
                Paragraph(fmt_datetime(g.last_played), cell),
                Paragraph(fmt_duration(g.total_time), cell),
                Paragraph(str(g.play_count), cell),
            ]
            block = Table([header, data_row], colWidths=col_widths)
            block.setStyle(TableStyle([
                # header row purple
                ("BACKGROUND", (0, 0), (-1, 0), purple),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LINEBELOW", (0, 1), (-1, 1), 0.75, gold),
                ("LINEABOVE", (0, 0), (-1, 0), 0.5, colors.lightgrey),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.lightgrey),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(block)

            # Full-width thumbnail banner below the item block
            if banner_flowable is not None:
                story.append(Spacer(1, 3))
                story.append(banner_flowable)
                story.append(Spacer(1, 3))

            # Gold separator between items
            sep = Table([[""]], colWidths=[doc.width])
            sep.setStyle(TableStyle([
                ("LINEBELOW", (0, 0), (-1, -1), 1, gold),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            story.append(sep)

    if not report or count_items(report) == 0:
        story.append(Paragraph("No items matched the selected criteria.",
                               styles["Normal"]))

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)



# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------
def config_path():
    """Location of the config file next to the executable / script."""
    base = os.path.dirname(os.path.abspath(
        __import__("sys").executable
        if getattr(__import__("sys"), "frozen", False) else __file__))
    return os.path.join(base, "xp_formatter_config.json")


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
def run_gui():
    import sys
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    class EditDialog(tk.Toplevel):
        """Generic modal dialog returning a dict of field values."""

        def __init__(self, master, title, fields, initial=None, checks=None):
            super().__init__(master)
            self.title(title)
            self.resizable(False, False)
            self.transient(master)
            self.grab_set()
            self.result = None
            self._vars = {}
            initial = initial or {}
            checks = checks or []
            row = 0
            for key, label in fields:
                ttk.Label(self, text=label).grid(
                    row=row, column=0, sticky="w", padx=8, pady=4)
                var = tk.StringVar(value=str(initial.get(key, "")))
                ttk.Entry(self, textvariable=var, width=30).grid(
                    row=row, column=1, padx=8, pady=4)
                self._vars[key] = var
                row += 1
            for key, label in checks:
                var = tk.BooleanVar(value=bool(initial.get(key, False)))
                ttk.Checkbutton(self, text=label, variable=var).grid(
                    row=row, column=0, columnspan=2, sticky="w", padx=8, pady=4)
                self._vars[key] = var
                row += 1
            btns = ttk.Frame(self)
            btns.grid(row=row, column=0, columnspan=2, pady=8)
            ttk.Button(btns, text="OK", command=self._ok).pack(
                side="left", padx=6)
            ttk.Button(btns, text="Cancel", command=self.destroy).pack(
                side="left", padx=6)
            self.bind("<Return>", lambda e: self._ok())
            self.wait_window()

        def _ok(self):
            self.result = {k: v.get() for k, v in self._vars.items()}
            self.destroy()

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title(f"{APP_NAME} - v{APP_VERSION}")
            self.geometry("1280x800")
            self.minsize(1100, 700)

            self.folders = []
            self.concealing_rules = []
            self.time_ranges = []
            self.records = []
            self.report = []

            self._build_menu()
            self._build_controls()
            # Build the status bar and the preview AFTER the controls. The
            # status bar and the Export button bar are packed to the BOTTOM
            # before the (expanding) results area so they can never be pushed
            # off-screen when the window is not maximised.
            self._build_statusbar()
            self._build_preview()
            self._load_config()

        # ---------------- Menu ----------------
        def _build_menu(self):
            menubar = tk.Menu(self)
            filemenu = tk.Menu(menubar, tearoff=0)
            filemenu.add_command(label="New", command=self.new_report)
            filemenu.add_command(label="Save Report Settings...",
                                 command=self.save_settings)
            filemenu.add_command(label="Load Report Settings...",
                                 command=self.load_settings)
            filemenu.add_separator()
            filemenu.add_command(label="Exit", command=self.destroy)
            menubar.add_cascade(label="File", menu=filemenu)

            reportmenu = tk.Menu(menubar, tearoff=0)
            reportmenu.add_command(label="Refresh Preview",
                                   command=self.refresh_preview)
            reportmenu.add_command(label="Export to CSV...",
                                   command=self.export_csv)
            reportmenu.add_command(label="Export to PDF...",
                                   command=self.export_pdf)
            menubar.add_cascade(label="Report", menu=reportmenu)
            self.config(menu=menubar)

        # ---------------- Controls ----------------
        def _build_controls(self):
            top = ttk.Frame(self, padding=6)
            top.pack(side="top", fill="x")

            # --- Report Header ---
            hdr = ttk.LabelFrame(top, text="Report Header", padding=6)
            hdr.grid(row=0, column=0, sticky="nsew", padx=4, pady=2)
            ttk.Label(hdr, text="Title:").grid(row=0, column=0, sticky="w")
            self.var_title = tk.StringVar(value="Ravens As Run Report")
            ttk.Entry(hdr, textvariable=self.var_title, width=26).grid(
                row=0, column=1, pady=2)
            ttk.Label(hdr, text="Client:").grid(row=1, column=0, sticky="w")
            self.var_client = tk.StringVar()
            ttk.Entry(hdr, textvariable=self.var_client, width=26).grid(
                row=1, column=1, pady=2)
            ttk.Label(hdr, text="Note:").grid(row=2, column=0, sticky="nw")
            self.txt_note = tk.Text(hdr, width=26, height=4)
            self.txt_note.grid(row=2, column=1, pady=2)

            # --- Date / Time Range ---
            dt = ttk.LabelFrame(top, text="Date / Time Range", padding=6)
            dt.grid(row=0, column=1, sticky="nsew", padx=4, pady=2)
            self.var_daterange = tk.StringVar(value="range")
            ttk.Radiobutton(dt, text="Today", value="today",
                            variable=self.var_daterange).grid(
                row=0, column=0, columnspan=2, sticky="w")
            ttk.Radiobutton(dt, text="Range", value="range",
                            variable=self.var_daterange).grid(
                row=1, column=0, sticky="w")
            ttk.Label(dt, text="Start:").grid(row=2, column=0, sticky="e")
            self.var_start = tk.StringVar()
            ttk.Entry(dt, textvariable=self.var_start, width=14).grid(
                row=2, column=1, sticky="w")
            self.var_useend = tk.BooleanVar(value=True)
            ttk.Checkbutton(dt, text="End:", variable=self.var_useend).grid(
                row=3, column=0, sticky="e")
            self.var_end = tk.StringVar()
            ttk.Entry(dt, textvariable=self.var_end, width=14).grid(
                row=3, column=1, sticky="w")
            self.var_usetime = tk.BooleanVar(value=False)
            ttk.Checkbutton(dt, text="Time Start:",
                            variable=self.var_usetime).grid(
                row=4, column=0, sticky="e")
            self.var_tstart = tk.StringVar(value="00:00:00")
            ttk.Entry(dt, textvariable=self.var_tstart, width=14).grid(
                row=4, column=1, sticky="w")
            ttk.Label(dt, text="Time End:").grid(row=5, column=0, sticky="e")
            self.var_tend = tk.StringVar(value="23:59:59")
            ttk.Entry(dt, textvariable=self.var_tend, width=14).grid(
                row=5, column=1, sticky="w")
            ttk.Label(dt, text="(MM/DD/YYYY)", foreground="grey").grid(
                row=6, column=0, columnspan=2, sticky="w")

            # --- Group Based On ---
            grp = ttk.LabelFrame(top, text="Group Based On", padding=6)
            grp.grid(row=0, column=2, sticky="nsew", padx=4, pady=2)
            self.var_group = tk.StringVar(value="scene_name")
            for i, (val, lbl) in enumerate([
                    ("scene_name", "Scene Name"), ("item_name", "Item Name"),
                    ("content", "Content"), ("content_items", "Content Items")]):
                ttk.Radiobutton(grp, text=lbl, value=val,
                                variable=self.var_group).grid(
                    row=i, column=0, sticky="w")

            # --- Sort Based On ---
            srt = ttk.LabelFrame(top, text="Sort Based On", padding=6)
            srt.grid(row=0, column=3, sticky="nsew", padx=4, pady=2)
            self.var_sort = tk.StringVar(value="first_time_on_air")
            for i, (val, lbl) in enumerate([
                    ("first_time_on_air", "First Time On Air"),
                    ("scene_name", "Scene Name"),
                    ("item_name", "Item Name"), ("content", "Content")]):
                ttk.Radiobutton(srt, text=lbl, value=val,
                                variable=self.var_sort).grid(
                    row=i, column=0, sticky="w")

            # --- Partitioning ---
            part = ttk.LabelFrame(top, text="Partitioning", padding=6)
            part.grid(row=0, column=4, sticky="nsew", padx=4, pady=2)
            self.var_part = tk.StringVar(value="none")
            ttk.Radiobutton(part, text="None", value="none",
                            variable=self.var_part).grid(
                row=0, column=0, sticky="w")
            ttk.Radiobutton(part, text="Game State", value="game_state",
                            variable=self.var_part).grid(
                row=1, column=0, sticky="w")
            ttk.Radiobutton(part, text="Time Ranges", value="time_ranges",
                            variable=self.var_part).grid(
                row=2, column=0, sticky="w")
            ttk.Label(part, text="Default State:").grid(
                row=3, column=0, sticky="w")
            self.var_default_state = tk.StringVar(value="Other")
            ttk.Entry(part, textvariable=self.var_default_state,
                      width=14).grid(row=4, column=0, sticky="w")
            ttk.Label(part, text="Time Ranges:").grid(row=5, column=0, sticky="w")
            self.lst_timeranges = tk.Listbox(part, height=3, width=20)
            self.lst_timeranges.grid(row=6, column=0, sticky="w")
            trbtns = ttk.Frame(part)
            trbtns.grid(row=7, column=0, sticky="w", pady=2)
            ttk.Button(trbtns, text="Add", width=6,
                       command=self.add_timerange).pack(side="left")
            ttk.Button(trbtns, text="Edit", width=6,
                       command=self.edit_timerange).pack(side="left")
            ttk.Button(trbtns, text="Del", width=6,
                       command=self.del_timerange).pack(side="left")

            # --- Concealing Layers ---
            con = ttk.LabelFrame(top, text="Concealing Layers", padding=6)
            con.grid(row=0, column=5, sticky="nsew", padx=4, pady=2)
            cols = ("name", "concealing", "concealed", "hide")
            self.tree_conceal = ttk.Treeview(
                con, columns=cols, show="headings", height=4)
            for c, w in zip(cols, (90, 90, 90, 40)):
                self.tree_conceal.heading(c, text=c.capitalize())
                self.tree_conceal.column(c, width=w, anchor="w")
            self.tree_conceal.grid(row=0, column=0, columnspan=3)
            cbtns = ttk.Frame(con)
            cbtns.grid(row=1, column=0, columnspan=3, sticky="w", pady=2)
            ttk.Button(cbtns, text="Add", width=6,
                       command=self.add_conceal).pack(side="left")
            ttk.Button(cbtns, text="Edit", width=6,
                       command=self.edit_conceal).pack(side="left")
            ttk.Button(cbtns, text="Delete", width=7,
                       command=self.del_conceal).pack(side="left")
            ttk.Label(con, text="Filter - Content Item:").grid(
                row=2, column=0, sticky="w", pady=(6, 0))
            self.var_filter = tk.StringVar()
            # NOTE: no trace/key binding -> the content-item filter does NOT
            # apply live while typing. It is only applied when the Search
            # button is clicked (or Enter is pressed in this field).
            ent_filter = ttk.Entry(con, textvariable=self.var_filter, width=24)
            ent_filter.grid(row=3, column=0, columnspan=2, sticky="w")
            ent_filter.bind("<Return>", lambda _e: self.refresh_preview())
            ttk.Button(con, text="Search", width=6,
                       command=self.refresh_preview).grid(
                row=3, column=2, sticky="w")

            # --- As Run Log File Source (multi-folder) ---
            src = ttk.LabelFrame(self, text="As Run Log File Source (Folders)",
                                 padding=6)
            src.pack(side="top", fill="x", padx=10, pady=4)
            self.lst_folders = tk.Listbox(src, height=3)
            self.lst_folders.pack(side="left", fill="x", expand=True, padx=4)
            sbtns = ttk.Frame(src)
            sbtns.pack(side="left", padx=4)
            ttk.Button(sbtns, text="Add Folder...",
                       command=self.add_folder).pack(fill="x", pady=1)
            ttk.Button(sbtns, text="Remove",
                       command=self.remove_folder).pack(fill="x", pady=1)
            ttk.Button(sbtns, text="Load / Refresh",
                       command=self.load_logs).pack(fill="x", pady=1)

            for i in range(6):
                top.columnconfigure(i, weight=1)

        # ---------------- Preview ----------------
        def _build_preview(self):
            # IMPORTANT: pack the Export button bar to the BOTTOM of the window
            # BEFORE the (expanding) results frame. pack() gives space to the
            # widgets requested earliest, so anchoring the button bar first
            # guarantees the Export to CSV / Export To PDF buttons stay visible
            # even when the window is small / not maximised.
            btnbar = ttk.Frame(self, padding=4)
            btnbar.pack(side="bottom", fill="x")
            ttk.Button(btnbar, text="Export To PDF...",
                       command=self.export_pdf).pack(side="right", padx=4)
            ttk.Button(btnbar, text="Export to CSV...",
                       command=self.export_csv).pack(side="right", padx=4)
            ttk.Button(btnbar, text="Refresh Preview",
                       command=self.refresh_preview).pack(side="right", padx=4)

            frame = ttk.LabelFrame(self, text="Report Preview", padding=4)
            frame.pack(side="top", fill="both", expand=True, padx=10, pady=4)
            cols = ("content", "name", "scene", "first", "last", "total", "count")
            heads = ("Content", "Name", "Scene Name", "First Played",
                     "Last Played", "Total Time", "Play Count")
            widths = (340, 160, 140, 120, 120, 80, 60)

            style = ttk.Style(self)
            style.configure("Preview.Treeview", rowheight=THUMB_ROW_H + 6)
            # Keep selected rows visible on top of the alternating shading
            style.map("Preview.Treeview",
                      background=[("selected", "#24135F")],
                      foreground=[("selected", "white")])
            self.tree = ttk.Treeview(frame, columns=cols, show="tree headings",
                                     style="Preview.Treeview")
            self.tree.heading("#0", text="Thumbnail")
            self.tree.column("#0", width=THUMB_ROW_W + 30, stretch=False)
            # Content stretches to take spare space; the short columns stay
            # compact. Numeric/time columns are centred.
            stretch = {"content": True, "name": True, "scene": True}
            anchors = {"total": "center", "count": "center",
                       "first": "center", "last": "center"}
            for c, h, w in zip(cols, heads, widths):
                self.tree.heading(c, text=h, anchor=anchors.get(c, "w"))
                self.tree.column(c, width=w, minwidth=40,
                                 anchor=anchors.get(c, "w"),
                                 stretch=stretch.get(c, False))
            vsb = ttk.Scrollbar(frame, orient="vertical",
                                command=self.tree.yview)
            self.tree.configure(yscrollcommand=vsb.set)
            vsb.pack(side="right", fill="y")
            self.tree.pack(side="left", fill="both", expand=True)
            # Alternating row colours
            self.tree.tag_configure("oddrow",  background="#FFFFFF")
            self.tree.tag_configure("evenrow", background="#EFEFEF")
            # Keep PhotoImage references alive (Tk drops unreferenced images)
            self._row_thumbs = {}
            self._row_groups = {}
            self._row_counter = 0

            # Hover thumbnail popup: a larger preview of the inline thumbnail
            # that follows the row the mouse is over.
            self._hover_popup = None        # the Toplevel, or None
            self._hover_photo = None         # keep PhotoImage reference alive
            self._hover_row = None           # iid currently shown in popup
            self.tree.bind("<Motion>", self._on_tree_motion)
            self.tree.bind("<Leave>", self._on_tree_leave)

        def _build_statusbar(self):
            self.var_status = tk.StringVar(value="Ready.")
            bar = ttk.Frame(self, relief="sunken")
            bar.pack(side="bottom", fill="x")
            ttk.Label(bar, textvariable=self.var_status, anchor="w").pack(
                side="left", fill="x", padx=6)

        def set_status(self, msg):
            self.var_status.set(msg)
            self.update_idletasks()

        # ---------------- Folder management ----------------
        def add_folder(self):
            d = filedialog.askdirectory(title="Select As Run Log folder")
            if d and d not in self.folders:
                self.folders.append(d)
                self.lst_folders.insert("end", d)

        def remove_folder(self):
            sel = list(self.lst_folders.curselection())
            for idx in reversed(sel):
                self.lst_folders.delete(idx)
                del self.folders[idx]

        def load_logs(self):
            if not self.folders:
                messagebox.showwarning(APP_NAME, "Add at least one source folder.")
                return
            self.set_status("Parsing logs...")
            warnings = []
            self.records, files = parse_all_folders(self.folders, warnings)
            msg = (f"Loaded {len(self.records)} records from {len(files)} "
                   f".arlog file(s) across {len(self.folders)} folder(s).")
            if warnings:
                msg += f"  ({len(warnings)} warning(s))"
            self.set_status(msg)
            self.refresh_preview()

        # ---------------- Concealing rules ----------------
        def _refresh_conceal_tree(self):
            self.tree_conceal.delete(*self.tree_conceal.get_children())
            for r in self.concealing_rules:
                self.tree_conceal.insert(
                    "", "end",
                    values=(r.get("name", ""), r.get("concealing", ""),
                            r.get("concealed", ""),
                            "Yes" if r.get("hide") else "No"))

        def add_conceal(self):
            dlg = EditDialog(
                self, "Add Concealing Layer",
                [("name", "Name:"), ("concealing", "Concealing (FBID):"),
                 ("concealed", "Concealed (FBID):")],
                checks=[("hide", "Hide concealed item")])
            if dlg.result:
                self.concealing_rules.append(dlg.result)
                self._refresh_conceal_tree()

        def edit_conceal(self):
            sel = self.tree_conceal.selection()
            if not sel:
                return
            idx = self.tree_conceal.index(sel[0])
            dlg = EditDialog(
                self, "Edit Concealing Layer",
                [("name", "Name:"), ("concealing", "Concealing (FBID):"),
                 ("concealed", "Concealed (FBID):")],
                initial=self.concealing_rules[idx],
                checks=[("hide", "Hide concealed item")])
            if dlg.result:
                self.concealing_rules[idx] = dlg.result
                self._refresh_conceal_tree()

        def del_conceal(self):
            sel = self.tree_conceal.selection()
            if not sel:
                return
            idx = self.tree_conceal.index(sel[0])
            del self.concealing_rules[idx]
            self._refresh_conceal_tree()

        # ---------------- Time ranges ----------------
        def _refresh_tr_list(self):
            self.lst_timeranges.delete(0, "end")
            for tr in self.time_ranges:
                self.lst_timeranges.insert(
                    "end", f"{tr.get('name','')} "
                           f"({tr.get('start','')}-{tr.get('end','')})")

        def add_timerange(self):
            dlg = EditDialog(self, "Add Time Range",
                             [("name", "Name:"), ("start", "Start (HH:MM:SS):"),
                              ("end", "End (HH:MM:SS):")])
            if dlg.result:
                self.time_ranges.append(dlg.result)
                self._refresh_tr_list()

        def edit_timerange(self):
            sel = self.lst_timeranges.curselection()
            if not sel:
                return
            idx = sel[0]
            dlg = EditDialog(self, "Edit Time Range",
                             [("name", "Name:"), ("start", "Start (HH:MM:SS):"),
                              ("end", "End (HH:MM:SS):")],
                             initial=self.time_ranges[idx])
            if dlg.result:
                self.time_ranges[idx] = dlg.result
                self._refresh_tr_list()

        def del_timerange(self):
            sel = self.lst_timeranges.curselection()
            if not sel:
                return
            del self.time_ranges[sel[0]]
            self._refresh_tr_list()

        # ---------------- Settings collection ----------------
        def _parse_date_field(self, value, end=False):
            value = (value or "").strip()
            if not value:
                return None
            # Accept both 4-digit (MM/DD/YYYY) and 2-digit (MM/DD/YY) years.
            # The date-range Entry fields display 2-digit years (e.g.
            # "10/05/26"); if we only tried %Y formats the parse would fail
            # silently, leaving start_dt/end_dt None so the Start filter was
            # never applied.
            for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%d/%m/%Y",
                        "%m/%d/%y", "%m-%d-%y", "%d/%m/%y"):
                try:
                    d = datetime.strptime(value, fmt)
                    return d
                except ValueError:
                    continue
            return None

        def collect_settings(self):
            start_dt = end_dt = None
            if self.var_daterange.get() == "today":
                today = datetime.now().replace(
                    hour=0, minute=0, second=0, microsecond=0)
                start_dt = today
                end_dt = today.replace(hour=23, minute=59, second=59)
            else:
                start_dt = self._parse_date_field(self.var_start.get())
                if self.var_useend.get():
                    end_dt = self._parse_date_field(self.var_end.get())
                    if end_dt:
                        end_dt = end_dt.replace(hour=23, minute=59, second=59)
                # time-of-day override
                if self.var_usetime.get() and start_dt:
                    t = _parse_time_only(self.var_tstart.get())
                    if t:
                        start_dt = start_dt.replace(
                            hour=t.hour, minute=t.minute, second=t.second)
                    te = _parse_time_only(self.var_tend.get())
                    if te and end_dt:
                        end_dt = end_dt.replace(
                            hour=te.hour, minute=te.minute, second=te.second)
            return {
                "title": self.var_title.get(),
                "client": self.var_client.get(),
                "note": self.txt_note.get("1.0", "end").strip(),
                "start_dt": start_dt,
                "end_dt": end_dt,
                "group_by": self.var_group.get(),
                "sort_by": self.var_sort.get(),
                "partition_mode": self.var_part.get(),
                "default_state": self.var_default_state.get(),
                "time_ranges": self.time_ranges,
                "concealing_rules": self.concealing_rules,
                "content_filter": self.var_filter.get(),
            }

        # ---------------- Hover thumbnail popup ----------------
        # Box the enlarged hover image is fitted into. 400x100 keeps the wide
        # banner thumbnails readable while staying larger than the inline size.
        HOVER_W, HOVER_H = 400, 100

        def _on_tree_motion(self, event):
            """Show an enlarged thumbnail popup for the row under the cursor."""
            iid = self.tree.identify_row(event.y)
            if not iid or iid not in self._row_groups:
                self._hide_hover_popup()
                return
            # Only (re)build the popup when the hovered row changes.
            if iid == self._hover_row and self._hover_popup is not None:
                self._move_hover_popup(event)
                return
            g, pil = self._row_groups.get(iid, (None, None))
            if pil is None:
                # No thumbnail for this row -> show nothing.
                self._hide_hover_popup()
                return
            photo = make_photo(pil, self.HOVER_W, self.HOVER_H)
            if photo is None:
                self._hide_hover_popup()
                return
            self._hide_hover_popup()
            self._hover_row = iid
            self._hover_photo = photo
            popup = tk.Toplevel(self)
            popup.overrideredirect(True)        # no title bar / decorations
            popup.attributes("-topmost", True)
            lbl = tk.Label(popup, image=photo, bd=1, relief="solid",
                           bg="white")
            lbl.pack()
            self._hover_popup = popup
            self._move_hover_popup(event)

        def _move_hover_popup(self, event):
            if self._hover_popup is None:
                return
            # Position a little below-right of the cursor.
            x = self.tree.winfo_pointerx() + 16
            y = self.tree.winfo_pointery() + 16
            self._hover_popup.geometry(f"+{x}+{y}")

        def _on_tree_leave(self, _event=None):
            self._hide_hover_popup()

        def _hide_hover_popup(self):
            if getattr(self, "_hover_popup", None) is not None:
                try:
                    self._hover_popup.destroy()
                except Exception:
                    pass
            self._hover_popup = None
            self._hover_photo = None
            self._hover_row = None

        # ---------------- Preview / export ----------------
        def refresh_preview(self):
            if not self.records:
                self.load_logs_if_possible()
            settings = self.collect_settings()
            try:
                self.report = build_report(self.records, settings)
            except Exception as exc:
                messagebox.showerror(APP_NAME, f"Processing error:\n{exc}")
                traceback.print_exc()
                return
            self._hide_hover_popup()
            self.tree.delete(*self.tree.get_children())
            self._row_thumbs.clear()
            self._row_groups.clear()
            clear_thumb_cache()
            n_thumbs = 0
            self._row_counter = 0
            for pname, groups in self.report:
                parent = ""
                if pname:
                    parent = self.tree.insert("", "end", text=pname, open=True)
                for g in groups:
                    pil = load_group_thumbnail(g, self.folders)
                    photo = make_photo(pil, THUMB_ROW_W, THUMB_ROW_H)
                    kw = {"image": photo} if photo else {}
                    iid = self.tree.insert(
                        parent, "end", text="" if photo else "(no thumbnail)",
                        values=(g.content, g.name, g.scene_name,
                                fmt_datetime(g.first_played),
                                fmt_datetime(g.last_played),
                                fmt_duration(g.total_time),
                                g.play_count),
                        tags=("evenrow" if self._row_counter % 2 else "oddrow",),
                        **kw)
                    self._row_counter += 1
                    self._row_groups[iid] = (g, pil)
                    if photo:
                        self._row_thumbs[iid] = photo
                        n_thumbs += 1
            self.set_status(f"Preview updated - {count_items(self.report)} "
                            f"item(s), {n_thumbs} with thumbnails.")

        def load_logs_if_possible(self):
            if self.folders and not self.records:
                self.records, _ = parse_all_folders(self.folders, [])

        def export_csv(self):
            self.refresh_preview()
            if not self.report:
                messagebox.showwarning(APP_NAME, "Nothing to export.")
                return
            path = filedialog.asksaveasfilename(
                title="Export to CSV", defaultextension=".csv",
                filetypes=[("CSV files", "*.csv")])
            if not path:
                return
            try:
                export_csv(self.report, self.collect_settings(), path)
                self.set_status(f"CSV exported: {path}")
                messagebox.showinfo(APP_NAME, f"CSV exported:\n{path}")
            except Exception as exc:
                messagebox.showerror(APP_NAME, f"CSV export failed:\n{exc}")
                traceback.print_exc()

        def export_pdf(self):
            self.refresh_preview()
            if not self.report:
                messagebox.showwarning(APP_NAME, "Nothing to export.")
                return
            path = filedialog.asksaveasfilename(
                title="Export to PDF", defaultextension=".pdf",
                filetypes=[("PDF files", "*.pdf")])
            if not path:
                return
            logo = getattr(self, "_logo_path", None)
            try:
                export_pdf(self.report, self.collect_settings(), path,
                           self.folders, logo)
                self.set_status(f"PDF exported: {path}")
                messagebox.showinfo(APP_NAME, f"PDF exported:\n{path}")
            except Exception as exc:
                messagebox.showerror(APP_NAME, f"PDF export failed:\n{exc}")
                traceback.print_exc()

        # ---------------- New / Save / Load ----------------
        def new_report(self):
            self.var_title.set("Ravens As Run Report")
            self.var_client.set("")
            self.txt_note.delete("1.0", "end")
            self.folders.clear()
            self.lst_folders.delete(0, "end")
            self.concealing_rules.clear()
            self._refresh_conceal_tree()
            self.time_ranges.clear()
            self._refresh_tr_list()
            self.records = []
            self.report = []
            self.tree.delete(*self.tree.get_children())
            self.set_status("New report.")

        def _serialize(self):
            return {
                "title": self.var_title.get(),
                "client": self.var_client.get(),
                "note": self.txt_note.get("1.0", "end").strip(),
                "daterange": self.var_daterange.get(),
                "start": self.var_start.get(),
                "end": self.var_end.get(),
                "useend": self.var_useend.get(),
                "usetime": self.var_usetime.get(),
                "tstart": self.var_tstart.get(),
                "tend": self.var_tend.get(),
                "group_by": self.var_group.get(),
                "sort_by": self.var_sort.get(),
                "partition_mode": self.var_part.get(),
                "default_state": self.var_default_state.get(),
                "content_filter": self.var_filter.get(),
                "folders": self.folders,
                "concealing_rules": self.concealing_rules,
                "time_ranges": self.time_ranges,
                "logo_path": getattr(self, "_logo_path", None),
            }

        def _apply(self, data):
            self.var_title.set(data.get("title", "Ravens As Run Report"))
            self.var_client.set(data.get("client", ""))
            self.txt_note.delete("1.0", "end")
            self.txt_note.insert("1.0", data.get("note", ""))
            self.var_daterange.set(data.get("daterange", "range"))
            self.var_start.set(data.get("start", ""))
            self.var_end.set(data.get("end", ""))
            self.var_useend.set(data.get("useend", True))
            self.var_usetime.set(data.get("usetime", False))
            self.var_tstart.set(data.get("tstart", "00:00:00"))
            self.var_tend.set(data.get("tend", "23:59:59"))
            self.var_group.set(data.get("group_by", "scene_name"))
            self.var_sort.set(data.get("sort_by", "first_time_on_air"))
            self.var_part.set(data.get("partition_mode", "none"))
            self.var_default_state.set(data.get("default_state", "Other"))
            self.var_filter.set(data.get("content_filter", ""))
            self.folders = list(data.get("folders", []))
            self.lst_folders.delete(0, "end")
            for f in self.folders:
                self.lst_folders.insert("end", f)
            self.concealing_rules = list(data.get("concealing_rules", []))
            self._refresh_conceal_tree()
            self.time_ranges = list(data.get("time_ranges", []))
            self._refresh_tr_list()
            self._logo_path = data.get("logo_path")

        def save_settings(self):
            path = filedialog.asksaveasfilename(
                title="Save Report Settings", defaultextension=".json",
                filetypes=[("JSON files", "*.json")])
            if not path:
                return
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(self._serialize(), fh, indent=2)
                self.set_status(f"Settings saved: {path}")
            except Exception as exc:
                messagebox.showerror(APP_NAME, f"Save failed:\n{exc}")

        def load_settings(self):
            path = filedialog.askopenfilename(
                title="Load Report Settings",
                filetypes=[("JSON files", "*.json")])
            if not path:
                return
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    self._apply(json.load(fh))
                self.set_status(f"Settings loaded: {path}")
            except Exception as exc:
                messagebox.showerror(APP_NAME, f"Load failed:\n{exc}")

        # ---------------- Config autosave ----------------
        def _load_config(self):
            try:
                if os.path.isfile(config_path()):
                    with open(config_path(), "r", encoding="utf-8") as fh:
                        self._apply(json.load(fh))
            except Exception:
                pass
            self.protocol("WM_DELETE_WINDOW", self._on_close)

        def _on_close(self):
            try:
                with open(config_path(), "w", encoding="utf-8") as fh:
                    json.dump(self._serialize(), fh, indent=2)
            except Exception:
                pass
            self.destroy()

    app = App()
    app.mainloop()


if __name__ == "__main__":
    run_gui()
