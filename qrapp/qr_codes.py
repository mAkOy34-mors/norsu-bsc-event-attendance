import base64
import io
import os
import zipfile
from datetime import date

import qrcode
from django.conf import settings
from django.http import HttpResponse
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .student_export import filter_students_queryset


# The scanner only reads the ID and TOKEN lines; every other line is there
# for the human reader. Very long college/program/major entries used to push
# the payload past the QR symbol's maximum capacity (version 40) and crash
# generation, so each informational line is clipped and the whole payload is
# hard-capped — the printed code also stays compact enough to scan reliably.
_INFO_LINE_LIMIT = 60
_MAX_PAYLOAD_BYTES = 600


def _clip(value, limit=_INFO_LINE_LIMIT):
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip()


def build_qr_data(student):
    from .qr_security import make_qr_token

    college = getattr(student, "college_code", None) or (
        student.college.code if getattr(student, "college_id", None) else str(getattr(student, "college", "") or "")
    )
    program = getattr(student, "program_code", None) or (
        student.program.code if getattr(student, "program_id", None) else str(getattr(student, "program", "") or "")
    )
    token = make_qr_token(student.student_id)

    head = f"ID: {student.student_id}\nTOKEN: {token}"
    budget = _MAX_PAYLOAD_BYTES - len(head.encode("utf-8"))

    info_lines = [
        f"Name: {_clip(student.name)}",
        f"Sex: {_clip(student.sex, 10)}",
        f"College: {_clip(college)}",
        f"Program: {_clip(program)}",
        f"Year: {student.year}",
    ]
    major = _clip(student.major_name)
    if major:
        info_lines.append(f"Major: {major}")

    kept = []
    for line in info_lines:
        size = len(line.encode("utf-8")) + 1  # count the joining newline
        if size > budget:
            break
        kept.append(line)
        budget -= size
    return "\n".join([head] + kept)


_FONT_CACHE = {}


def _load_font(size=14):
    """Cached font loader: loading a TTF per student dominated QR bulk time."""
    cached = _FONT_CACHE.get(size)
    if cached is not None:
        return cached
    font_paths = [
        "arial.ttf",
        "Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
        "/System/Library/Fonts/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    font = ImageFont.load_default()
    for font_path in font_paths:
        try:
            font = ImageFont.truetype(font_path, size)
            break
        except OSError:
            continue
    _FONT_CACHE[size] = font
    return font


def build_qr_labeled_image(student):
    # A fixed mask_pattern skips the library's 8-pass best-mask search, the
    # single biggest cost when encoding a whole roster (any mask 0-7 scans).
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
        mask_pattern=0,
    )
    qr.add_data(build_qr_data(student))
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    width, height = qr_img.size
    label_height = 60
    new_img = Image.new("RGB", (width, height + label_height), "white")
    new_img.paste(qr_img, (0, 0))

    draw = ImageDraw.Draw(new_img)
    font = _load_font()

    short_name = student.name.split(",")[0]
    if len(short_name) > 20:
        short_name = short_name[:17] + "..."
    text = f"{student.student_id} - {short_name}"

    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
    except Exception:
        text_w = len(text) * 8

    text_x = (width - text_w) / 2
    draw.text((text_x, height + 10), text, fill="black", font=font)
    return new_img


def build_qr_preview_base64(student):
    """Small in-memory preview (box_size=3): ~4x fewer pixels than box_size=5,
    which was the difference between seconds and minutes on a full roster."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=3,
        border=2,
        mask_pattern=0,
    )
    qr.add_data(build_qr_data(student))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=False)
    return base64.b64encode(buffer.getvalue()).decode()


def _student_lastname(student):
    """Extract last name from the 'Lastname, Firstname' format and sanitize."""
    name = getattr(student, "name", "") or ""
    last = name.split(",")[0].strip()
    return "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in last) or "student"


def qr_image_basename(student):
    """Filename stem for a student's QR image: '{student_id}_{lastname}'."""
    sid = "".join(
        ch if ch.isalnum() or ch in "-_" else "_" for ch in str(student.student_id).strip()
    )
    return f"{sid}_{_student_lastname(student)}"


def generate_qr_with_label(student):
    """Generate QR code + text label and save PNG in media folder."""
    try:
        new_img = build_qr_labeled_image(student)
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
        img_path = os.path.join(settings.MEDIA_ROOT, f"{qr_image_basename(student)}.png")
        new_img.save(img_path)
        return img_path
    except Exception as exc:
        print(f"Error generating QR for {student.student_id}: {exc}")
        return None


def build_qr_filename(params, extension):
    parts = ["qrcodes"]
    for key in ("college", "program", "year", "major"):
        value = (params.get(key) or "").strip()
        if value:
            safe = "".join(ch if ch.isalnum() else "_" for ch in str(value))
            parts.append(safe.lower())
    parts.append(date.today().isoformat())
    return f"{'_'.join(parts)}.{extension}"


def serialize_qr_list(students):
    qr_list = []
    for student in students:
        try:
            qr_list.append(
                {
                    "student_id": student.student_id,
                    "name": student.name,
                    "college": student.college_code,
                    "program": student.program_code,
                    "year": student.year,
                    "major": student.major_name,
                    "sex": student.sex,
                    "qr_img": build_qr_preview_base64(student),
                }
            )
        except Exception as exc:
            print(f"Error building QR preview for {student.student_id}: {exc}")
    return qr_list


def export_qr_pdf(students, filename):
    if not students.exists():
        return None, "No students match the selected filters."

    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    pdf = canvas.Canvas(response, pagesize=A4)
    page_width, page_height = A4

    qr_width = 45 * mm
    qr_height = 45 * mm
    label_height = 15 * mm
    total_height = qr_height + label_height

    # 4 cols x 45mm + 3 gaps x 5mm = 195mm; 7.5mm margins center it on 210mm A4
    margin_x = 7.5 * mm
    margin_y = 15 * mm
    gap_x = 5 * mm
    gap_y = 5 * mm

    cols = 4
    # Fit as many whole rows as the page height allows so nothing is ever
    # drawn off-page (A4 fits 4 rows of 60mm cells + 5mm gaps).
    rows = max(1, int((page_height - 2 * margin_y) // (total_height + gap_y)))
    per_page = cols * rows

    x_positions = [margin_x + (qr_width + gap_x) * c for c in range(cols)]
    # Top-edge Y per row; drawImage anchors at the BOTTOM-left, so each cell
    # is drawn from (y_top - total_height) up to y_top.
    y_tops = [page_height - margin_y - (total_height + gap_y) * r for r in range(rows)]

    added = 0
    for student in students:
        # In-memory: the old flow PNG-encoded + wrote a disk file per student,
        # then reportlab re-decoded it. Feeding the PIL image directly skips
        # all of that (and no media-folder litter).
        try:
            img = build_qr_labeled_image(student)
        except Exception as exc:
            print(f"Error generating QR for {student.student_id}: {exc}")
            # Still count this student for page layout purposes, but don't add image
            added += 1
            continue

        if added > 0 and added % per_page == 0:
            pdf.showPage()

        index_on_page = added % per_page
        row = index_on_page // cols
        col = index_on_page % cols
        pdf.drawImage(
            ImageReader(img),
            x_positions[col],
            y_tops[row] - total_height,
            width=qr_width,
            height=total_height,
            preserveAspectRatio=True,
            anchor="c",
        )
        added += 1

    if added == 0:
        return None, "Could not generate QR codes for the selected students."

    pdf.save()
    return response, None


def export_qr_zip(students, filename):
    if not students.exists():
        return None, "No students match the selected filters."

    buffer = io.BytesIO()
    added = 0
    failed = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for student in students:
            try:
                img = build_qr_labeled_image(student)
                png_buffer = io.BytesIO()
                img.save(png_buffer, format="PNG")
                archive.writestr(f"{qr_image_basename(student)}.png", png_buffer.getvalue())
                added += 1
            except Exception as exc:
                failed += 1
                print(f"Error adding QR to zip for {student.student_id}: {exc}")

    if added == 0:
        return None, "Could not generate QR codes for the selected students."

    buffer.seek(0)
    response = HttpResponse(buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    # Attach failure count for debugging/reporting
    if failed > 0:
        response["X-QR-Failed-Count"] = str(failed)
    return response, None


def get_filtered_students(params):
    return filter_students_queryset(params)


def export_qr_response(params):
    export_format = (params.get("format") or "pdf").lower()
    students = get_filtered_students(params)

    if export_format == "zip":
        filename = build_qr_filename(params, "zip")
        return export_qr_zip(students, filename)

    filename = build_qr_filename(params, "pdf")
    return export_qr_pdf(students, filename)
