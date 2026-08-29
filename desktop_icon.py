"""The panel's mark, drawn at whatever size Windows asks for.

There is one mark and it already exists: the inline SVG favicon in `frontend/index.html`
(a `#0072F5` rounded square with a white tick). The numbers below are that SVG's own, in
its 32-unit viewBox, so the tray icon, the taskbar icon and the browser tab all show the
same thing. Change one and change the other — nothing here can detect the drift.

Two consumers, one geometry: `image()` for pystray, which wants a live PIL image, and
`write_ico()` for PyInstaller, which wants a file on disk. The .ico is generated at build
time by `desktop.spec` rather than committed, so there is no binary in git that can quietly
stop matching the favicon.

Pillow only — no SVG renderer. Parsing the path would buy nothing: it is one rectangle and
one three-point polyline.
"""

from pathlib import Path

# frontend/index.html, verbatim: viewBox 0 0 32 32, rx 8, stroke-width 3.5, M9 16.5 13.5 21 23 11
VIEWBOX = 32
RADIUS = 8
BLUE = (0, 114, 245, 255)
WHITE = (255, 255, 255, 255)
STROKE = 3.5
TICK = ((9, 16.5), (13.5, 21), (23, 11))

# What Windows actually reads: 16 and 32 for the tray and the title bar, 48 and 256 for
# Explorer's larger views. An .ico missing 256 shows a blurry upscale in the file list.
ICO_SIZES = (16, 32, 48, 256)


def image(size: int = 256):
	"""The mark as an RGBA image, `size` px square."""
	from PIL import Image, ImageDraw

	canvas = Image.new('RGBA', (size, size), (0, 0, 0, 0))
	draw = ImageDraw.Draw(canvas)
	k = size / VIEWBOX

	draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=RADIUS * k, fill=BLUE)

	points = [(x * k, y * k) for x, y in TICK]
	width = max(1, round(STROKE * k))
	# `joint='curve'` rounds the corner between the two segments; the SVG's round line *caps*
	# have no equivalent in Pillow, so the ends get a circle of the same diameter.
	draw.line(points, fill=WHITE, width=width, joint='curve')
	for x, y in (points[0], points[-1]):
		r = width / 2
		draw.ellipse((x - r, y - r, x + r, y + r), fill=WHITE)

	return canvas


def write_ico(path: Path) -> Path:
	"""Render the mark to a multi-resolution .ico. Returns the path, for the spec's convenience."""
	path = Path(path)
	path.parent.mkdir(parents=True, exist_ok=True)
	# Pillow downsamples from the one image it is given, so hand it the largest size wanted.
	image(max(ICO_SIZES)).save(path, format='ICO', sizes=[(s, s) for s in ICO_SIZES])
	return path


if __name__ == '__main__':
	print(write_ico(Path(__file__).resolve().parent / 'build' / 'desktop.ico'))
