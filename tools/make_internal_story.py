"""Create a diagnostic Story card locally; no network or account access."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / ".local" / "internal-story.png"
    output.parent.mkdir(exist_ok=True)
    image = Image.new("RGB", (1080, 1920), "#101820")
    draw = ImageDraw.Draw(image)
    fonts = Path("C:/Windows/Fonts")
    def font(size, bold=False):
        return ImageFont.truetype(str(fonts / ("arialbd.ttf" if bold else "arial.ttf")), size)
    for x in range(90, 1080, 90):
        draw.line((x, 0, x, 1920), fill="#172934", width=1)
    for y in range(90, 1920, 90):
        draw.line((0, y, 1080, y), fill="#172934", width=1)
    draw.rounded_rectangle((80, 150, 410, 215), radius=20, fill="#a9f04f")
    draw.text((110, 163), "TESTE INTERNO", font=font(30, True), fill="#101820")
    draw.text((80, 350), "QrStack", font=font(112, True), fill="white")
    draw.text((85, 505), "@testesqrstack", font=font(44), fill="#a9f04f")
    draw.text((85, 700), "Story de imagem", font=font(57, True), fill="white")
    draw.text((85, 780), "com link interativo", font=font(57, True), fill="white")
    draw.text((85, 925), "Validação interna de publicação.", font=font(36), fill="#b9c7cf")
    draw.text((85, 985), "Toque no sticker para testar o destino.", font=font(36), fill="#b9c7cf")
    # Leave the normalized sticker area empty: center (.5, .72), size (.56, .10).
    draw.line((540, 1160, 540, 1240), fill="#a9f04f", width=6)
    draw.polygon([(520, 1220), (560, 1220), (540, 1250)], fill="#a9f04f")
    draw.text((85, 1650), "1080 × 1920  /  04.10.2026", font=font(30), fill="#b9c7cf")
    draw.text((85, 1710), "Arte técnica para uma conta interna.", font=font(30), fill="#b9c7cf")
    image.save(output)
    print(output)


if __name__ == "__main__":
    main()
