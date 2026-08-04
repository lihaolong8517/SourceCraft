import sys
import os
import shutil
import random
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageEnhance

def generate_enhanced_glass_image(input_path, output_path):
    print(f"Processing {input_path} -> {output_path} ...")
    img = Image.open(input_path).convert("RGBA")
    img_w, img_h = img.size

    # --- Settings ---
    pad_x, pad_top, pad_bot = 100, 100, 120
    corner_r = 12 # Slightly more rounded for premium look
    
    canvas_w = img_w + pad_x * 2
    canvas_h = img_h + pad_top + pad_bot

    # 1. Dark elegant background gradient
    bg = Image.new("RGBA", (canvas_w, canvas_h), (8, 10, 20, 255))
    draw = ImageDraw.Draw(bg)
    for y in range(canvas_h):
        t = y / canvas_h
        # Deep blue to very dark indigo
        r = int(10 + 5 * (1-t))
        g = int(12 + 6 * (1-t))
        b = int(28 + 12 * (1-t))
        draw.line([(0, y), (canvas_w, y)], fill=(r, g, b, 255))

    # 2. Dynamic glow blobs for background depth
    for cx, cy, color, size, opacity in [
        (canvas_w * 0.2, canvas_h * 0.2, (79, 70, 229), 450, 45),   # Indigo glow
        (canvas_w * 0.8, canvas_h * 0.4, (139, 92, 246), 400, 40),  # Purple glow
        (canvas_w * 0.4, canvas_h * 0.8, (56, 189, 248), 350, 30),  # Sky blue glow
        (canvas_w * 0.6, canvas_h * 0.1, (168, 85, 247), 300, 25),  # Violet glow
    ]:
        glow = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.ellipse(
            [int(cx - size), int(cy - size), int(cx + size), int(cy + size)],
            fill=(*color, opacity)
        )
        glow = glow.filter(ImageFilter.GaussianBlur(150))
        bg = Image.alpha_composite(bg, glow)

    # 3. BACKDROP BLUR & REFRACTION (Magnification)
    # Extract the area under the window, blur it, and slightly scale it for refractive depth
    backdrop_area = bg.crop((pad_x, pad_top, pad_x + img_w, pad_top + img_h))
    # Subtle zoom to simulate refraction (1.02x)
    zoom_w, zoom_h = int(img_w * 1.02), int(img_h * 1.02)
    backdrop_blur = backdrop_area.resize((zoom_w, zoom_h), Image.LANCZOS)
    backdrop_blur = backdrop_blur.crop(((zoom_w - img_w)//2, (zoom_h - img_h)//2, (zoom_w + img_w)//2, (zoom_h + img_h)//2))
    backdrop_blur = backdrop_blur.filter(ImageFilter.GaussianBlur(60))
    
    # 4. PREPARE GLASS WINDOW
    window = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    wd = ImageDraw.Draw(window)

    # 4.1 Mask for rounded corners
    mask = Image.new("L", (img_w, img_h), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([0, 0, img_w - 1, img_h - 1], radius=corner_r, fill=255)

    # 4.2 Screenshot processing
    enhancer = ImageEnhance.Brightness(img)
    img_dark = enhancer.enhance(0.88)
    img_dark.putalpha(170)
    
    # Composite: Blured Backdrop + Darkened Translucent Screenshot
    glass_base = Image.alpha_composite(backdrop_blur.convert("RGBA"), img_dark)
    window.paste(glass_base, (0, 0), mask)
    
    # 5. GLASS SURFACE REFINEMENTS
    # 5.1 THICKNESS: Inner Bevel (Highlight & Shadow)
    bevel_layer = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bevel_layer)
    # Top-Left Highlight (White)
    bd.rounded_rectangle([2, 2, img_w - 4, img_h - 4], radius=corner_r, outline=(255, 255, 255, 40), width=3)
    # Bottom-Right Shadow (Black)
    shadow_mask = Image.new("L", (img_w, img_h), 0)
    smd = ImageDraw.Draw(shadow_mask)
    smd.chord([img_w//2, img_h//2, img_w*2, img_h*2], 0, 360, fill=255) # Half-screen mask
    
    # Apply inner shadow only to bottom right
    inner_shadow = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    isd = ImageDraw.Draw(inner_shadow)
    isd.rounded_rectangle([2, 2, img_w - 4, img_h - 4], radius=corner_r, outline=(0, 0, 0, 60), width=4)
    # window = Image.alpha_composite(window, bevel_layer) # Apply white bevel
    # window = Image.alpha_composite(window, inner_shadow)

    # 5.2 LIGHT FLOW: Multiple Curved Sheens
    sheen_layer = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    shd = ImageDraw.Draw(sheen_layer)
    
    # Create "flowing" light beams using arcs or wide lines
    for offset, color, alpha, width in [
        (-200, (255, 255, 255), 15, 120), # Main beam
        (100, (200, 220, 255), 10, 80),   # Secondary blueish beam
        (-500, (255, 255, 255), 8, 150),  # Broad soft light
    ]:
        # Draw soft curved beams
        for i in range(10): # Soft edges through stacking
            shd.arc([offset + i, -500, offset + img_w*1.5 + i, img_h + 500], 
                    -45, 135, fill=(*color, alpha//2), width=width-i*5)

    sheen_layer = sheen_layer.filter(ImageFilter.GaussianBlur(50))
    window = Image.alpha_composite(window, sheen_layer)

    # 5.3 Surface Grain
    noise = np.random.randint(0, 40, (img_h, img_w, 4), dtype=np.uint8)
    noise[:, :, 3] = 10
    noise_img = Image.fromarray(noise, mode="RGBA")
    window = Image.alpha_composite(window, noise_img)

    # 5.4 PRISMATIC BORDER (Chromatic Aberration)
    # Red Shift Border
    border_r = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    rd_draw = ImageDraw.Draw(border_r)
    rd_draw.rounded_rectangle([0, 0, img_w - 1, img_h - 1], radius=corner_r, outline=(255, 50, 50, 40), width=1)
    
    # Blue Shift Border
    border_b = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    bl_draw = ImageDraw.Draw(border_b)
    bl_draw.rounded_rectangle([2, 2, img_w + 1, img_h + 1], radius=corner_r, outline=(50, 50, 255, 40), width=1)

    # White sharp edge
    border_w = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    wt_draw = ImageDraw.Draw(border_w)
    wt_draw.rounded_rectangle([1, 1, img_w - 2, img_h - 2], radius=corner_r, outline=(255, 255, 255, 90), width=1)
    
    window = Image.alpha_composite(window, border_r)
    window = Image.alpha_composite(window, border_b)
    window = Image.alpha_composite(window, border_w)

    # 6. SHADOW & DEPTH
    shadow_layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow_layer)
    # Multi-layered shadow for softness
    # Deep shadow
    sd.rounded_rectangle(
        [pad_x + 10, pad_top + 15, pad_x + img_w + 10, pad_top + img_h + 15],
        radius=corner_r, fill=(0, 0, 0, 150)
    )
    # Broad soft shadow
    sd.rounded_rectangle(
        [pad_x - 5, pad_top + 25, pad_x + img_w + 5, pad_top + img_h + 40],
        radius=corner_r, fill=(0, 0, 0, 60)
    )
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(60))
    bg = Image.alpha_composite(bg, shadow_layer)

    # 7. FINAL REFLECTION GLOW (Underneath)
    ref_glow = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ref_glow)
    ref_y = pad_top + img_h + 15
    rd.ellipse(
        [pad_x + img_w // 5, ref_y, pad_x + img_w * 4 // 5, ref_y + 100],
        fill=(99, 102, 241, 30)
    )
    ref_glow = ref_glow.filter(ImageFilter.GaussianBlur(50))
    bg = Image.alpha_composite(bg, ref_glow)

    # Paste window onto background
    bg.paste(window, (pad_x, pad_top), window)

    # Save
    bg.convert("RGB").save(output_path, "PNG", quality=100)
    print(f"  Saved: {output_path}")

def generate_ultra_glossy_image(input_path, output_path):
    print(f"Processing (Ultra-Glossy) {input_path} -> {output_path} ...")
    img = Image.open(input_path).convert("RGBA")
    img_w, img_h = img.size

    # --- Settings ---
    pad_x, pad_top, pad_bot = 120, 120, 140
    corner_r = 14
    
    canvas_w = img_w + pad_x * 2
    canvas_h = img_h + pad_top + pad_bot

    # 1. DEEP High-Contrast Background
    bg = Image.new("RGBA", (canvas_w, canvas_h), (5, 5, 12, 255))
    draw = ImageDraw.Draw(bg)
    for y in range(canvas_h):
        t = y / canvas_h
        r = int(6 + 8 * (1-t)**2)
        g = int(6 + 8 * (1-t)**2)
        b = int(18 + 20 * (1-t)**2)
        draw.line([(0, y), (canvas_w, y)], fill=(r, g, b, 255))

    # 2. Strategic Glows for Luster
    for cx, cy, color, size, opacity in [
        (canvas_w * 0.1, canvas_h * 0.1, (120, 119, 255), 500, 50), # Top-left light source
        (canvas_w * 0.9, canvas_h * 0.8, (168, 85, 247), 450, 45),  # Bot-right depth
        (canvas_w * 0.5, canvas_h * 0.5, (99, 102, 241), 600, 30),  # Central soft fill
    ]:
        glow = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.ellipse([int(cx - size), int(cy - size), int(cx + size), int(cy + size)], fill=(*color, opacity))
        glow = glow.filter(ImageFilter.GaussianBlur(180))
        bg = Image.alpha_composite(bg, glow)

    # 3. BACKDROP BLUR & PRISMATIC REFRACTION
    backdrop_area = bg.crop((pad_x, pad_top, pad_x + img_w, pad_top + img_h))
    # Refractive Zoom (1.03x)
    z_w, z_h = int(img_w * 1.03), int(img_h * 1.03)
    backdrop_blur = backdrop_area.resize((z_w, z_h), Image.LANCZOS)
    backdrop_blur = backdrop_blur.crop(((z_w - img_w)//2, (z_h - img_h)//2, (z_w + img_w)//2, (z_h + img_h)//2))
    backdrop_blur = backdrop_blur.filter(ImageFilter.GaussianBlur(80)) # Smoother blur
    
    # 4. WINDOW BASE
    window = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    wd = ImageDraw.Draw(window)
    mask = Image.new("L", (img_w, img_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img_w - 1, img_h - 1], radius=corner_r, fill=255)

    # Screenshot: High Luster
    enhancer = ImageEnhance.Contrast(img)
    img_contrast = enhancer.enhance(1.1) # Boost contrast for glossy feel
    img_contrast = ImageEnhance.Brightness(img_contrast).enhance(0.9)
    img_contrast.putalpha(160)
    
    glass_base = Image.alpha_composite(backdrop_blur.convert("RGBA"), img_contrast)
    window.paste(glass_base, (0, 0), mask)
    
    # 5. ULTRA-GLOSSY EFFECTS
    # 5.1 Vertical Luster (Top-Down shine)
    luster = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    ld = ImageDraw.Draw(luster)
    for y in range(img_h // 2):
        alpha = int(25 * (1 - y / (img_h // 2))) # Fade out from top
        ld.line([(0, y), (img_w, y)], fill=(255, 255, 255, alpha))
    window = Image.alpha_composite(window, luster)

    # 5.2 Dynamic Flow: Sharp specularity
    sheen = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    shd = ImageDraw.Draw(sheen)
    # sharper, thinner curved arcs
    for offset, alpha, width in [
        (-300, 25, 60), 
        (0, 15, 120),
        (500, 20, 40)
    ]:
        shd.arc([offset, -800, offset + img_w*2, img_h + 300], -30, 150, fill=(255, 255, 255, alpha), width=width)
    sheen = sheen.filter(ImageFilter.GaussianBlur(25))
    window = Image.alpha_composite(window, sheen)

    # 5.3 Corner Lens Flare (Glow source)
    flare = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    fd = ImageDraw.Draw(flare)
    fd.ellipse([-100, -100, 300, 300], fill=(255, 255, 255, 40))
    flare = flare.filter(ImageFilter.GaussianBlur(80))
    window = Image.alpha_composite(window, flare)

    # 5.4 High-End Crystal Border
    # Inner glow highlight bias
    idraw = ImageDraw.Draw(window)
    idraw.rounded_rectangle([1, 1, img_w - 2, img_h - 2], radius=corner_r, outline=(255, 255, 255, 160), width=1) # Sharp white
    # Second outer dark
    idraw.rounded_rectangle([0, 0, img_w - 1, img_h - 1], radius=corner_r, outline=(0, 0, 0, 120), width=2)
    
    # 6. DEPTH SHADOWS
    shadow_layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow_layer)
    # Sharp close shadow
    sd.rounded_rectangle([pad_x + 5, pad_top + 5, pad_x + img_w + 5, pad_top + img_h + 5], radius=corner_r, fill=(0, 0, 0, 180))
    # Broad atmospheric shadow
    sd.rounded_rectangle([pad_x - 10, pad_top + 30, pad_x + img_w + 10, pad_top + img_h + 60], radius=corner_r, fill=(0, 0, 0, 80))
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(50))
    bg = Image.alpha_composite(bg, shadow_layer)

    bg.paste(window, (pad_x, pad_top), window)
    bg.convert("RGB").save(output_path, "PNG", quality=100)
    print(f"  Saved: {output_path}")

if __name__ == "__main__":
    # Target the new image requested by user
    input_img = r"E:\trae\1\website\hero_screenshot - 副本 - 副本.png"
    output_img = r"E:\trae\1\website\hero_screenshot_ultra_glossy.png"
    
    if os.path.exists(input_img):
        generate_ultra_glossy_image(input_img, output_img)
        # Also update the main name for convenience
        shutil.copy2(output_img, r"E:\trae\1\website\hero_screenshot.png")
    else:
        print(f"ERROR: Input not found: {input_img}")
