from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

WIDTH = 1600
HEIGHT = 1000
PADDING = 42
CARD_RADIUS = 30

BACKGROUND_TOP = "#050B16"
SURFACE = (9, 24, 40, 220)
SURFACE_STRONG = (12, 30, 52, 236)
OUTLINE = (255, 255, 255, 44)
TEXT_PRIMARY = "#F8FBFF"
TEXT_SECONDARY = "#D6E6F7"
TEXT_MUTED = "#93ABC5"
ACCENT_BLUE = "#57B8FF"
ACCENT_CYAN = "#3EE6D4"
ACCENT_GREEN = "#75E39A"
ACCENT_GOLD = "#F6C453"
ACCENT_RED = "#FF7A8A"
ACCENT_PURPLE = "#9C88FF"
CHART_GRID = (255, 255, 255, 22)
PANEL_TITLES = {
    "overview": "Asosiy Dashboard",
    "traffic": "Trafik Dashboard",
    "movies": "Top Kinolar",
    "requests": "So'rovlar Holati",
}

def _discover_fonts() -> tuple[list[Path], list[Path]]:
    """OS ga qarab font yo'llarini aniqlash."""
    import platform

    system = platform.system()
    local_fonts = Path(__file__).resolve().parent / "fonts"

    regular: list[Path] = []
    bold: list[Path] = []

    # Loyiha ichidagi fontlar (eng ishonchli)
    if local_fonts.is_dir():
        regular.extend(sorted(local_fonts.glob("*Regular*.ttf")))
        bold.extend(sorted(local_fonts.glob("*Bold*.ttf")))

    if system == "Windows":
        win = Path("C:/Windows/Fonts")
        regular += [win / "segoeui.ttf", win / "arial.ttf"]
        bold += [win / "seguisb.ttf", win / "segoeuib.ttf", win / "arialbd.ttf"]
    elif system == "Darwin":
        mac = Path("/System/Library/Fonts")
        regular += [mac / "Helvetica.ttc"]
        bold += [mac / "Helvetica-Bold.ttf"]
    else:  # Linux
        regular += [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
            Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
        ]
        bold += [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
            Path("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
        ]

    return regular, bold


FONT_REGULAR, FONT_BOLD = _discover_fonts()
SPARKLINE_BARS = "▁▂▃▄▅▆▇█"


def _font(
    size: int, *, bold: bool = False
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = FONT_BOLD if bold else FONT_REGULAR
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _create_background() -> Image.Image:
    image = Image.new("RGBA", (WIDTH, HEIGHT), BACKGROUND_TOP)
    draw = ImageDraw.Draw(image)

    for y in range(HEIGHT):
        progress = y / max(1, HEIGHT - 1)
        r = int(5 + (19 - 5) * progress)
        g = int(11 + (31 - 11) * progress)
        b = int(22 + (56 - 22) * progress)
        draw.line((0, y, WIDTH, y), fill=(r, g, b))

    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    glow = ImageDraw.Draw(overlay)
    glow.ellipse((-200, -120, 680, 560), fill=(87, 184, 255, 54))
    glow.ellipse((WIDTH - 620, -160, WIDTH + 100, 420), fill=(62, 230, 212, 42))
    glow.ellipse(
        (WIDTH - 520, HEIGHT - 360, WIDTH + 80, HEIGHT + 140), fill=(156, 136, 255, 24)
    )
    overlay = overlay.filter(ImageFilter.GaussianBlur(58))
    image.alpha_composite(overlay)

    grid = ImageDraw.Draw(image)
    for x in range(0, WIDTH, 80):
        grid.line((x, 0, x, HEIGHT), fill=(255, 255, 255, 4))
    for y in range(0, HEIGHT, 80):
        grid.line((0, y, WIDTH, y), fill=(255, 255, 255, 4))

    return image


def _panel(
    image: Image.Image,
    box: tuple[int, int, int, int],
    *,
    fill: tuple[int, int, int, int] = SURFACE,
    outline: tuple[int, int, int, int] = OUTLINE,
) -> None:
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    x1, y1, x2, y2 = box
    shadow_draw.rounded_rectangle(
        (x1 + 8, y1 + 14, x2 + 8, y2 + 14),
        radius=CARD_RADIUS,
        fill=(0, 0, 0, 96),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(22))
    image.alpha_composite(shadow)

    panel_image = Image.new("RGBA", image.size, (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel_image)
    panel_draw.rounded_rectangle(
        box, radius=CARD_RADIUS, fill=fill, outline=outline, width=2
    )
    image.alpha_composite(panel_image)


def _text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    *,
    font: ImageFont.ImageFont,
    fill: str | tuple[int, int, int] = TEXT_PRIMARY,
    anchor: str | None = None,
) -> None:
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)


def _fit_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    *,
    font: ImageFont.ImageFont,
) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text

    shortened = text
    while shortened and draw.textlength(f"{shortened}...", font=font) > max_width:
        shortened = shortened[:-1]
    return f"{shortened}..." if shortened else "..."


def _metric_card(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    *,
    label: str,
    value: str,
    accent: str,
    subtitle: str | None = None,
) -> None:
    _panel(image, box, fill=SURFACE_STRONG)
    x1, y1, x2, y2 = box
    draw.rounded_rectangle((x1 + 24, y1 + 24, x1 + 38, y1 + 78), radius=7, fill=accent)
    _text(
        draw, (x1 + 60, y1 + 22), label, font=_font(25, bold=True), fill=TEXT_SECONDARY
    )
    _text(draw, (x1 + 60, y1 + 64), value, font=_font(44, bold=True), fill=TEXT_PRIMARY)
    if subtitle:
        _text(draw, (x1 + 60, y2 - 40), subtitle, font=_font(21), fill=TEXT_MUTED)


def _sparkline(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    values: list[int],
    *,
    line_color: str,
    fill_color: tuple[int, int, int, int],
) -> None:
    x1, y1, x2, y2 = box
    width = x2 - x1
    height = y2 - y1
    if not values:
        draw.line((x1, y2, x2, y2), fill=CHART_GRID, width=2)
        return

    max_value = max(values)
    if max_value <= 0:
        for index in range(4):
            y = y1 + (height * index) / 3
            draw.line((x1, y, x2, y), fill=CHART_GRID, width=1)
        draw.line((x1, y2 - 1, x2, y2 - 1), fill=line_color, width=3)
        return

    for index in range(4):
        y = y1 + (height * index) / 3
        draw.line((x1, y, x2, y), fill=CHART_GRID, width=1)

    points: list[tuple[float, float]] = []
    count = max(1, len(values) - 1)
    for index, value in enumerate(values):
        px = x1 + (width * index) / count
        py = y2 - (value / max_value) * height
        points.append((px, py))

    polygon = [(x1, y2), *points, (x2, y2)]
    draw.polygon(polygon, fill=fill_color)
    draw.line(points, fill=line_color, width=5, joint="curve")
    for point in points:
        draw.ellipse(
            (point[0] - 4, point[1] - 4, point[0] + 4, point[1] + 4), fill=line_color
        )


def _section_title(
    draw: ImageDraw.ImageDraw, x: int, y: int, title: str, subtitle: str | None = None
) -> None:
    _text(draw, (x, y), title, font=_font(32, bold=True))
    if subtitle:
        _text(draw, (x, y + 40), subtitle, font=_font(20), fill=TEXT_MUTED)


def _status_badge(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    *,
    label: str,
    value: int,
    accent: str,
) -> None:
    _panel(image, box, fill=(255, 255, 255, 18), outline=(255, 255, 255, 28))
    x1, y1, x2, y2 = box
    draw.rounded_rectangle((x1 + 20, y1 + 18, x1 + 34, y1 + 48), radius=6, fill=accent)
    _text(
        draw, (x1 + 52, y1 + 16), label, font=_font(22, bold=True), fill=TEXT_SECONDARY
    )
    _text(
        draw,
        (x1 + 52, y1 + 54),
        str(value),
        font=_font(38, bold=True),
        fill=TEXT_PRIMARY,
    )


def _bar_row(
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    width: int,
    label: str,
    value: int,
    max_value: int,
    color: str,
) -> None:
    label_font = _font(22, bold=True)
    value_font = _font(20)
    bar_x = x + 260
    bar_y = y + 10
    bar_width = width - 360
    draw.text((x, y), label, font=label_font, fill=TEXT_PRIMARY)
    draw.rounded_rectangle(
        (bar_x, bar_y, bar_x + bar_width, bar_y + 18),
        radius=9,
        fill=(255, 255, 255, 18),
    )

    fill_width = 0
    if max_value > 0 and value > 0:
        fill_width = max(16, int(bar_width * value / max_value))
    if fill_width:
        draw.rounded_rectangle(
            (bar_x, bar_y, bar_x + fill_width, bar_y + 18), radius=9, fill=color
        )

    value_text = str(value)
    value_x = x + width - int(draw.textlength(value_text, font=value_font))
    draw.text((value_x, y - 2), value_text, font=value_font, fill=TEXT_SECONDARY)


def _donut(
    draw: ImageDraw.ImageDraw,
    *,
    center: tuple[int, int],
    radius: int,
    width: int,
    segments: list[tuple[str, int, str]],
) -> None:
    total = sum(value for _, value, _ in segments)
    bbox = (
        center[0] - radius,
        center[1] - radius,
        center[0] + radius,
        center[1] + radius,
    )
    if total <= 0:
        draw.arc(bbox, start=0, end=359, fill=(255, 255, 255, 40), width=width)
        return

    start = -90
    for _, value, color in segments:
        if value <= 0:
            continue
        end = start + (360 * value / total)
        draw.arc(bbox, start=start, end=end, fill=color, width=width)
        start = end

    total_text = str(total)
    _text(
        draw,
        (center[0], center[1] - 10),
        total_text,
        font=_font(38, bold=True),
        fill=TEXT_PRIMARY,
        anchor="mm",
    )
    _text(
        draw,
        (center[0], center[1] + 28),
        "jami",
        font=_font(18),
        fill=TEXT_MUTED,
        anchor="mm",
    )


def _draw_header(draw: ImageDraw.ImageDraw, panel: str) -> None:
    title = PANEL_TITLES.get(panel, "Dashboard")
    updated = datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC")
    _text(
        draw,
        (PADDING, 34),
        "PrimeCinema Admin",
        font=_font(22, bold=True),
        fill=TEXT_SECONDARY,
    )
    _text(draw, (PADDING, 70), title, font=_font(58, bold=True), fill=TEXT_PRIMARY)
    _text(
        draw,
        (PADDING, 134),
        "Premium canal analytics va hisobotlar",
        font=_font(24),
        fill=TEXT_MUTED,
    )
    draw.rounded_rectangle(
        (WIDTH - 214, 38, WIDTH - 42, 82),
        radius=18,
        fill=(117, 227, 154, 28),
        outline=(117, 227, 154, 85),
        width=2,
    )
    _text(
        draw,
        (WIDTH - 104, 48),
        "LIVE",
        font=_font(18, bold=True),
        fill=ACCENT_GREEN,
        anchor="ma",
    )
    _text(
        draw,
        (WIDTH - PADDING, 102),
        updated,
        font=_font(20),
        fill=TEXT_SECONDARY,
        anchor="ra",
    )
    _text(
        draw,
        (WIDTH - 104, 76),
        "PREMIUM",
        font=_font(16, bold=True),
        fill=ACCENT_BLUE,
        anchor="ma",
    )


def _draw_overview(
    image: Image.Image, draw: ImageDraw.ImageDraw, payload: dict
) -> None:
    summary = payload["summary"]
    trends = payload["trends"]
    recent_users = payload["recent_users"]
    top = 170
    gap = 22
    card_width = (WIDTH - (PADDING * 2) - (gap * 3)) // 4
    card_height = 128

    cards = [
        (
            "Faol obunachilar",
            str(summary["total_users"]),
            ACCENT_BLUE,
            "bloklamaganlar",
        ),
        (
            "Jami kirganlar",
            str(summary["all_time_users"]),
            ACCENT_CYAN,
            "bot ochilgandan beri",
        ),
        (
            "Bugun kirganlar",
            str(summary["entered_today"]),
            ACCENT_GREEN,
            "bloklaganlar ham kiradi",
        ),
        (
            "Bugun yangi",
            str(summary["new_subscribers_today"]),
            ACCENT_BLUE,
            "birinchi marta bugun",
        ),
        (
            "Bloklaganlar",
            str(summary["blocked_users"]),
            ACCENT_RED,
            "hozir blokda",
        ),
        (
            "Kinolar",
            str(summary["total_movies"]),
            ACCENT_GOLD,
            f"Sevimlilar: {summary['total_favorites']}",
        ),
        (
            "Ochiq navbat",
            str(summary["pending_requests"]),
            ACCENT_RED,
            "pending + accepted",
        ),
        (
            "Bajarilgan",
            str(summary["completed_requests"]),
            ACCENT_GREEN,
            "yakunlangan so'rov",
        ),
        (
            "Ko'rishlar",
            str(summary["total_views"]),
            ACCENT_PURPLE,
            f"So'rovlar: {summary['total_requests']}",
        ),
    ]

    for index, (label, value, accent, subtitle) in enumerate(cards):
        row = index // 4
        col = index % 4
        x1 = PADDING + col * (card_width + gap)
        y1 = top + row * (card_height + gap)
        _metric_card(
            image,
            draw,
            (x1, y1, x1 + card_width, y1 + card_height),
            label=label,
            value=value,
            accent=accent,
            subtitle=subtitle,
        )

    trends_box = (PADDING, 468, 1040, HEIGHT - PADDING)
    recent_box = (1062, 468, WIDTH - PADDING, HEIGHT - PADDING)
    _panel(image, trends_box)
    _panel(image, recent_box)

    _section_title(
        draw,
        trends_box[0] + 28,
        trends_box[1] + 24,
        "7 kunlik dinamika",
        "Sparkline ko'rinishidagi trend",
    )
    series = [
        ("So'rovlar", trends["requests"], ACCENT_GOLD, (246, 196, 83, 45)),
        ("Ko'rishlar", trends["movie_views"], ACCENT_CYAN, (62, 230, 212, 45)),
        ("Yangi users", trends["new_users"], ACCENT_BLUE, (87, 184, 255, 45)),
    ]
    for index, (label, values, color, fill) in enumerate(series):
        y = trends_box[1] + 96 + index * 126
        _text(
            draw,
            (trends_box[0] + 28, y),
            label,
            font=_font(24, bold=True),
            fill=TEXT_PRIMARY,
        )
        _text(
            draw,
            (trends_box[2] - 28, y),
            " ".join(str(value) for value in values),
            font=_font(18),
            fill=TEXT_MUTED,
            anchor="ra",
        )
        _sparkline(
            draw,
            (trends_box[0] + 28, y + 36, trends_box[2] - 28, y + 106),
            values,
            line_color=color,
            fill_color=fill,
        )
        _text(
            draw,
            (trends_box[0] + 28, y + 110),
            " ".join(day[8:] for day in trends["labels"]),
            font=_font(18),
            fill=TEXT_MUTED,
        )

    _section_title(
        draw,
        recent_box[0] + 28,
        recent_box[1] + 24,
        "So'nggi faollar",
        "Eng so'nggi ko'ringan foydalanuvchilar",
    )
    base_y = recent_box[1] + 100
    for index, (user_id, username, full_name, last_seen) in enumerate(
        recent_users[:6], start=1
    ):
        row_y = base_y + (index - 1) * 68
        label = f"@{username}" if username else full_name
        label = _fit_line(
            draw, label, recent_box[2] - recent_box[0] - 180, font=_font(24, bold=True)
        )
        _text(
            draw,
            (recent_box[0] + 28, row_y),
            f"{index}. {label}",
            font=_font(24, bold=True),
        )
        _text(
            draw,
            (recent_box[0] + 28, row_y + 30),
            str(user_id),
            font=_font(18),
            fill=TEXT_MUTED,
        )
        _text(
            draw,
            (recent_box[2] - 28, row_y + 12),
            last_seen[11:16] if len(last_seen) >= 16 else last_seen,
            font=_font(20),
            fill=ACCENT_CYAN,
            anchor="ra",
        )


def _draw_traffic(image: Image.Image, draw: ImageDraw.ImageDraw, payload: dict) -> None:
    summary = payload["summary"]
    trends = payload["trends"]

    left = (PADDING, 182, 1060, HEIGHT - PADDING)
    right = (1082, 182, WIDTH - PADDING, HEIGHT - PADDING)
    _panel(image, left)
    _panel(image, right)

    _section_title(
        draw,
        left[0] + 28,
        left[1] + 24,
        "Kunlik oqim",
        "So'rovlar, ko'rishlar va yangi users",
    )
    charts = [
        ("So'rovlar", trends["requests"], ACCENT_GOLD, (246, 196, 83, 50)),
        ("Ko'rishlar", trends["movie_views"], ACCENT_CYAN, (62, 230, 212, 50)),
        ("Yangi users", trends["new_users"], ACCENT_BLUE, (87, 184, 255, 50)),
    ]
    for index, (title, values, color, fill) in enumerate(charts):
        y = left[1] + 92 + index * 190
        _text(draw, (left[0] + 28, y), title, font=_font(26, bold=True))
        _text(
            draw,
            (left[2] - 28, y),
            f"Jami: {sum(values)}",
            font=_font(20),
            fill=TEXT_SECONDARY,
            anchor="ra",
        )
        _sparkline(
            draw,
            (left[0] + 28, y + 40, left[2] - 28, y + 150),
            values,
            line_color=color,
            fill_color=fill,
        )
        for offset, label in enumerate(trends["labels"]):
            x = (
                left[0]
                + 28
                + (left[2] - left[0] - 56) * offset / max(1, len(trends["labels"]) - 1)
            )
            _text(
                draw,
                (int(x), y + 156),
                label[8:],
                font=_font(18),
                fill=TEXT_MUTED,
                anchor="ma",
            )

    _section_title(
        draw, right[0] + 28, right[1] + 24, "Traffic snapshot", "Qisqa ko'rsatkichlar"
    )
    small_cards = [
        ("24h faol", str(summary["active_today"]), ACCENT_GREEN),
        ("7 kun faol", str(summary["active_week"]), ACCENT_BLUE),
        ("Bugun kirgan", str(summary["entered_today"]), ACCENT_CYAN),
        ("Bloklagan", str(summary["blocked_users"]), ACCENT_GOLD),
    ]
    for index, (label, value, accent) in enumerate(small_cards):
        row = index // 2
        col = index % 2
        x1 = right[0] + 28 + col * 190
        y1 = right[1] + 86 + row * 150
        _metric_card(
            image,
            draw,
            (x1, y1, x1 + 170, y1 + 122),
            label=label,
            value=value,
            accent=accent,
        )

    _section_title(draw, right[0] + 28, right[1] + 424, "Spark meter")
    meter_labels = [
        ("Requests", trends["requests"], ACCENT_GOLD),
        ("Views", trends["movie_views"], ACCENT_CYAN),
        ("Users", trends["new_users"], ACCENT_BLUE),
    ]
    for index, (label, values, color) in enumerate(meter_labels):
        y = right[1] + 476 + index * 86
        spark = "".join(
            (
                SPARKLINE_BARS[
                    min(
                        len(SPARKLINE_BARS) - 1,
                        round((value / max(values)) * (len(SPARKLINE_BARS) - 1)),
                    )
                ]
                if max(values) > 0
                else "·"
            )
            for value in values
        )
        _text(draw, (right[0] + 28, y), label, font=_font(22, bold=True))
        _text(draw, (right[0] + 160, y), spark, font=_font(32), fill=color)
        _text(
            draw,
            (right[2] - 28, y + 4),
            str(sum(values)),
            font=_font(22),
            fill=TEXT_SECONDARY,
            anchor="ra",
        )


def _draw_movies(image: Image.Image, draw: ImageDraw.ImageDraw, payload: dict) -> None:
    summary = payload["summary"]
    trends = payload["trends"]
    top_movies = payload["top_movies"]

    header_cards = [
        ("Kinolar", str(summary["total_movies"]), ACCENT_GOLD),
        ("Jami views", str(summary["total_views"]), ACCENT_CYAN),
        ("Sevimlilar", str(summary["total_favorites"]), ACCENT_GREEN),
    ]
    for index, (label, value, accent) in enumerate(header_cards):
        x1 = PADDING + index * 360
        _metric_card(
            image,
            draw,
            (x1, 182, x1 + 330, 310),
            label=label,
            value=value,
            accent=accent,
        )

    ranking_box = (PADDING, 340, 1060, HEIGHT - PADDING)
    side_box = (1082, 182, WIDTH - PADDING, HEIGHT - PADDING)
    _panel(image, ranking_box)
    _panel(image, side_box)

    _section_title(
        draw,
        ranking_box[0] + 28,
        ranking_box[1] + 24,
        "Top ko'rilgan kinolar",
        "Ko'rishlar bo'yicha reyting",
    )
    if top_movies:
        max_views = max(views for _, _, views, _ in top_movies)
        for index, (code, title, views, unique_views) in enumerate(top_movies, start=1):
            y = ranking_box[1] + 96 + (index - 1) * 92
            label = _fit_line(
                draw,
                f"{index}. {title} ({code}) • {unique_views} user",
                470,
                font=_font(24, bold=True),
            )
            _bar_row(
                draw,
                x=ranking_box[0] + 28,
                y=y,
                width=ranking_box[2] - ranking_box[0] - 56,
                label=label,
                value=views,
                max_value=max_views,
                color=ACCENT_CYAN,
            )
    else:
        _text(
            draw,
            (ranking_box[0] + 28, ranking_box[1] + 108),
            "Hali ko'rish statistikasi yig'ilmagan",
            font=_font(26, bold=True),
            fill=TEXT_SECONDARY,
        )

    _section_title(
        draw,
        side_box[0] + 28,
        side_box[1] + 24,
        "Kontent pulse",
        "Oxirgi 7 kunlik signal",
    )
    _sparkline(
        draw,
        (side_box[0] + 28, side_box[1] + 86, side_box[2] - 28, side_box[1] + 210),
        trends["movie_views"],
        line_color=ACCENT_CYAN,
        fill_color=(62, 230, 212, 50),
    )
    _text(
        draw,
        (side_box[0] + 28, side_box[1] + 222),
        " ".join(day[8:] for day in trends["labels"]),
        font=_font(18),
        fill=TEXT_MUTED,
    )

    _section_title(draw, side_box[0] + 28, side_box[1] + 300, "Takliflar")
    tips = [
        "• Eng ko'p ko'rilgan kodlarni pinned xabar qiling",
        "• Kam ko'rilgan kinolarga promo tugma qo'shing",
        "• Top 10 auto-post kanal formatini yoqing",
        "• Admin uchun 'bugun trend' xabarini rejalang",
    ]
    for index, tip in enumerate(tips):
        _text(
            draw,
            (side_box[0] + 28, side_box[1] + 354 + index * 42),
            tip,
            font=_font(20),
            fill=TEXT_SECONDARY,
        )


def _draw_requests(
    image: Image.Image, draw: ImageDraw.ImageDraw, payload: dict
) -> None:
    summary = payload["summary"]
    trends = payload["trends"]
    request_counts = payload["request_counts"]

    donut_box = (PADDING, 182, 760, HEIGHT - PADDING)
    trend_box = (782, 182, WIDTH - PADDING, 560)
    notes_box = (782, 582, WIDTH - PADDING, HEIGHT - PADDING)
    _panel(image, donut_box)
    _panel(image, trend_box)
    _panel(image, notes_box)

    _section_title(
        draw,
        donut_box[0] + 28,
        donut_box[1] + 24,
        "Status Overview",
        "So'rovlar holatini aniq ko'rsatish",
    )
    badge_gap = 16
    badge_width = (donut_box[2] - donut_box[0] - 56 - badge_gap) // 2
    badge_height = 108
    badges = [
        ("Yangi", request_counts.get("pending", 0), ACCENT_GOLD),
        ("Jarayonda", request_counts.get("accepted", 0), ACCENT_BLUE),
        ("Bajarildi", request_counts.get("completed", 0), ACCENT_GREEN),
        ("Rad etildi", request_counts.get("rejected", 0), ACCENT_RED),
    ]
    for index, (label, value, color) in enumerate(badges):
        row = index // 2
        col = index % 2
        x1 = donut_box[0] + 28 + col * (badge_width + badge_gap)
        y1 = donut_box[1] + 88 + row * (badge_height + badge_gap)
        _status_badge(
            image,
            draw,
            (x1, y1, x1 + badge_width, y1 + badge_height),
            label=label,
            value=value,
            accent=color,
        )

    max_count = max(request_counts.values()) if request_counts else 0
    status_rows = [
        ("Yangi navbat", request_counts.get("pending", 0), ACCENT_GOLD),
        ("Jarayonda", request_counts.get("accepted", 0), ACCENT_BLUE),
        ("Bajarilgan", request_counts.get("completed", 0), ACCENT_GREEN),
        ("Rad etilgan", request_counts.get("rejected", 0), ACCENT_RED),
    ]
    base_y = donut_box[1] + 346
    for index, (label, value, color) in enumerate(status_rows):
        _bar_row(
            draw,
            x=donut_box[0] + 28,
            y=base_y + index * 74,
            width=donut_box[2] - donut_box[0] - 56,
            label=label,
            value=value,
            max_value=max_count,
            color=color,
        )

    _text(
        draw,
        (donut_box[0] + 28, donut_box[3] - 72),
        f"Jami so'rovlar: {summary['total_requests']}   •   Ochiq navbat: {summary['pending_requests']}",
        font=_font(22),
        fill=TEXT_SECONDARY,
    )

    _section_title(
        draw,
        trend_box[0] + 28,
        trend_box[1] + 24,
        "7 kunlik request flow",
        "Kunlar bo'yicha kirish dinamikasi",
    )
    _sparkline(
        draw,
        (trend_box[0] + 28, trend_box[1] + 104, trend_box[2] - 28, trend_box[1] + 248),
        trends["requests"],
        line_color=ACCENT_GOLD,
        fill_color=(246, 196, 83, 50),
    )
    _text(
        draw,
        (trend_box[0] + 28, trend_box[1] + 276),
        " ".join(day[8:] for day in trends["labels"]),
        font=_font(20),
        fill=TEXT_MUTED,
    )
    _text(
        draw,
        (trend_box[0] + 28, trend_box[1] + 322),
        f"Oxirgi 7 kun jami: {sum(trends['requests'])}",
        font=_font(24, bold=True),
        fill=TEXT_PRIMARY,
    )
    _text(
        draw,
        (trend_box[0] + 28, trend_box[1] + 362),
        f"O'rtacha kunlik yuklama: {round(sum(trends['requests']) / max(1, len(trends['requests'])), 1)}",
        font=_font(21),
        fill=TEXT_SECONDARY,
    )

    _section_title(
        draw,
        notes_box[0] + 28,
        notes_box[1] + 24,
        "Action Center",
        "Status bo'limi uchun amaliy tavsiyalar",
    )
    notes = [
        f"• Ochiq navbatni {summary['pending_requests']} tadan yuqori chiqarmang",
        f"• Bajarilgan so'rovlar ulushi: {summary['completed_requests']}",
        f"• Rad etilganlar soni: {summary['rejected_requests']}",
        "• 24 soatdan oshgan accepted requestlar uchun alohida filter qo'shing",
        "• Eng ko'p takrorlangan so'rov nomlaridan hot-list tuzing",
    ]
    for index, note in enumerate(notes):
        _text(
            draw,
            (notes_box[0] + 28, notes_box[1] + 96 + index * 46),
            note,
            font=_font(23),
            fill=TEXT_SECONDARY,
        )


def render_dashboard(panel: str, payload: dict) -> bytes:
    image = _create_background()
    draw = ImageDraw.Draw(image)
    _draw_header(draw, panel)

    if panel == "traffic":
        _draw_traffic(image, draw, payload)
    elif panel == "movies":
        _draw_movies(image, draw, payload)
    elif panel == "requests":
        _draw_requests(image, draw, payload)
    else:
        _draw_overview(image, draw, payload)

    output = BytesIO()
    image.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()
