"""
Generate dark-styled mockup images from a clear SourceCraft screenshot.
Creates images that match the website's dark purple/indigo aesthetic, 
but WITHOUT the Mac title bar. Includes drop shadow and bottom reflection.
"""
import sys
import shutil
from PIL import Image, ImageDraw, ImageFilter, ImageEnhance

def generate_styled_image_no_macbar(input_path, output_path):
    print(f"Processing {input_path} -> {output_path} ...")
    img = Image.open(input_path).convert("RGBA")
    img_w, img_h = img.size

    # --- Settings ---
    pad_x, pad_top, pad_bot = 80, 80, 100
    corner_r = 8 # Windows 11 style slight rounded corners

    canvas_w = img_w + pad_x * 2
    canvas_h = img_h + pad_top + pad_bot

    # 1. Dark gradient background matching website (#0a0e1a -> #141233 -> #0f172a)
    bg = Image.new("RGBA", (canvas_w, canvas_h), (10, 14, 26, 255))
    draw = ImageDraw.Draw(bg)
    for y in range(canvas_h):
        t = y / canvas_h
        r = int(10 + 10 * t * (1 - t) * 4)
        g = int(14 + 4 * t * (1 - t) * 4)
        b = int(26 + 25 * t * (1 - t) * 4)
        draw.line([(0, y), (canvas_w, y)], fill=(r, g, b, 255))

    # 2. Add purple/indigo glow blobs (like the website bg-glow)
    for cx, cy, color, size in [
        (canvas_w * 0.3, canvas_h * 0.3, (99, 102, 241), 350),   # indigo
        (canvas_w * 0.7, canvas_h * 0.7, (139, 92, 246), 300),   # purple
        (canvas_w * 0.5, canvas_h * 0.1, (79, 70, 229), 250),    # blue
    ]:
        glow = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.ellipse(
            [int(cx - size), int(cy - size), int(cx + size), int(cy + size)],
            fill=(*color, 35)
        )
        glow = glow.filter(ImageFilter.GaussianBlur(120))
        bg = Image.alpha_composite(bg, glow)

    # 3. Build the Window (just the image with rounded corners and border)
    win_w, win_h = img_w, img_h
    window = Image.new("RGBA", (win_w, win_h), (0, 0, 0, 0))
    wd = ImageDraw.Draw(window)

    # 3.1 Draw rounded mask with 225 opacity (~88%) for Glassmorphism effect
    mask = Image.new("L", (win_w, win_h), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([0, 0, win_w - 1, win_h - 1], radius=corner_r, fill=225)
    
    # 3.2 Reduce brightness of the bright white screenshot so it doesn't pop as much
    img = img.convert("RGBA")
    enhancer = ImageEnhance.Brightness(img)
    img = enhancer.enhance(0.9)  # 10% darker
    
    # 3.3 Apply the translucent mask to the image 
    # (Because the image is now slightly transparent, the website background 
    # and purple/blue glows behind it will naturally bleed through, creating a true glass fusion)
    img.putalpha(mask)
    
    window.paste(img, (0, 0))
    
    # Draw subtle 1px border
    wd.rounded_rectangle([0, 0, win_w - 1, win_h - 1], radius=corner_r, outline=(60, 60, 75, 180), width=1)

    # 4. Drop shadow
    shadow_layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow_layer)
    sx, sy = pad_x + 8, pad_top + 12
    sd.rounded_rectangle(
        [sx, sy, sx + win_w, sy + win_h],
        radius=corner_r, fill=(0, 0, 0, 140)
    )
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(40))
    bg = Image.alpha_composite(bg, shadow_layer)

    # 5. Paste window onto background
    bg.paste(window, (pad_x, pad_top), window)

    # 6. Add subtle bottom reflection / glass glow under window
    ref_glow = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ref_glow)
    ref_y = pad_top + win_h + 10
    rd.ellipse(
        [pad_x + win_w // 4, ref_y, pad_x + win_w * 3 // 4, ref_y + 80],
        fill=(99, 102, 241, 25)
    )
    ref_glow = ref_glow.filter(ImageFilter.GaussianBlur(35))
    bg = Image.alpha_composite(bg, ref_glow)

    # Save
    bg.convert("RGB").save(output_path, "PNG", quality=95)
    print(f"  Saved: {output_path}")

if __name__ == "__main__":
    import os

    # Using the exact image uploaded earlier (clear windows screenshot)
    input_img = r"E:\trae\1\website\屏幕截图 2026-03-27 145059_画质修复.png"

    if not os.path.exists(input_img):
        print(f"ERROR: Input not found: {input_img}")
        sys.exit(1)

    hero_out = r"e:\trae\1\website\hero_screenshot.png"
    showcase_out = r"e:\trae\1\website\showcase_screenshot.png"

    generate_styled_image_no_macbar(input_img, hero_out)
    shutil.copy2(hero_out, showcase_out)

    print("\nAll done! Images ready for upload.")
