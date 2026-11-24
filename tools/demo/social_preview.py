import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

poster, out = sys.argv[1], sys.argv[2]
W, H = 1280, 640
img = Image.new("RGB", (W, H), "#f4f5f7")
# soft openswx-red glow, top left
glow = Image.new("L", (W, H), 0)
ImageDraw.Draw(glow).ellipse((-300, -350, 700, 450), fill=60)
glow = glow.filter(ImageFilter.GaussianBlur(120))
img = Image.composite(Image.new("RGB", (W, H), "#f6c9d0"), img, glow)
d = ImageDraw.Draw(img)


def font(size, bold=False):
    for name in (("segoeuib.ttf" if bold else "segoeui.ttf"), "arialbd.ttf" if bold else "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


# logo: the red hexagon cube + "open" / "swx"
cx, cy, r = 92, 150, 40
hexagon = [(cx, cy - r), (cx + 0.866 * r, cy - r / 2), (cx + 0.866 * r, cy + r / 2), (cx, cy + r), (cx - 0.866 * r, cy + r / 2), (cx - 0.866 * r, cy - r / 2)]
d.polygon(hexagon, fill="#c8102e")
d.polygon([hexagon[0], hexagon[1], (cx, cy), hexagon[5]], fill="#ff6b80")
d.polygon([hexagon[1], hexagon[2], hexagon[3], (cx, cy)], fill="#8d0b1f")
f_logo = font(78, bold=True)
d.text((150, 102), "open", font=f_logo, fill="#14171c")
w_open = d.textlength("open", font=f_logo)
d.text((150 + w_open, 102), "swx", font=f_logo, fill="#d61f3c")
f1, f2 = font(34, bold=True), font(24)
lines = ["Open-source parametric CAD", "with a built-in MCP server"]
for i, t in enumerate(lines):
    d.text((60, 250 + 46 * i), t, font=f1, fill="#14171c")
for i, t in enumerate(["AI agents design parts, assemblies", "and drawings, live in the open app.", "",
                       "Windows · macOS · Linux · Apache-2.0"]):
    d.text((60, 370 + 34 * i), t, font=f2, fill="#4a5060")

# a frame of the demo, as a floating window on the right
shot = Image.open(poster).convert("RGB")
sw, sh = shot.size
crop = shot.crop((int(sw * 0.33), int(sh * 0.08), int(sw * 0.98), int(sh * 0.875)))
tw = 620
th = int(crop.height * tw / crop.width)
crop = crop.resize((tw, th), Image.LANCZOS)
x0, y0 = W - tw - 40, (H - th) // 2
shadow = Image.new("L", (W, H), 0)
ImageDraw.Draw(shadow).rounded_rectangle((x0 + 6, y0 + 14, x0 + tw + 6, y0 + th + 14), 18, fill=90)
shadow = shadow.filter(ImageFilter.GaussianBlur(18))
img = Image.composite(Image.new("RGB", (W, H), "#3a2a30"), img, shadow)
mask = Image.new("L", (tw, th), 0)
ImageDraw.Draw(mask).rounded_rectangle((0, 0, tw, th), 14, fill=255)
img.paste(crop, (x0, y0), mask)
img.save(out, optimize=True)
print(out, img.size)
