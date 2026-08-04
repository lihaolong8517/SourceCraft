import sys
import shutil
import random
import os
from PIL import Image, ImageDraw, ImageFilter, ImageEnhance, ImageChops

def add_noise(img, amount=10):
    """Subtle film grain/noise for optical realism."""
    noise_small = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    pixels = noise_small.load()
    for y in range(300):
        for x in range(300):
            r = random.randint(-amount, amount)
            if r > 0:
                pixels[x, y] = (255, 255, 255, r)
            else:
                pixels[x, y] = (0, 0, 0, -r)
    noise = noise_small.resize(img.size, Image.Resampling.NEAREST)
    return Image.alpha_composite(img, noise)

def chromatic_aberration(img, shift=5):
    """Chromatic aberration (RGB separate and offset)"""
    r, g, b, a = img.split()
    r_offset = ImageChops.offset(r, -shift, 0)
    b_offset = ImageChops.offset(b, shift, 0)
    return Image.merge("RGBA", (r_offset, g, b_offset, a))

def create_dribbble_glassmorphism(input_path, output_path):
    print(f"Creating Natural Glass Showcase: {input_path} -> {output_path}")
    try:
        source_img = Image.open(input_path).convert("RGBA")
    except Exception as e:
        print(f"Error loading image: {e}")
        return
        
    # --- PROPER CROP: Remove invisible OS shadows first! ---
    bbox = source_img.getbbox()
    if bbox:
        source_img = source_img.crop(bbox)
        
    # --- CROP TOP OS BAR ---
    crop_title = 46 
    img_w, original_h = source_img.size
    if original_h > crop_title + 100:
        source_img = source_img.crop((0, crop_title, img_w, original_h))
        img_h = original_h - crop_title
    else:
        img_h = original_h
        
    # Layout Config: The screenshot itself IS the glass plate
    glass_w = img_w
    glass_h = img_h
    glass_r = 16 # Window border radius
    
    cv_pad_x = 120 
    cv_pad_y = 120
    canvas_w = glass_w + cv_pad_x * 2
    canvas_h = glass_h + cv_pad_y * 2
    
    glass_x = cv_pad_x
    glass_y = cv_pad_y
    
    # 1. Fully Transparent Background for Native PNG
    bg = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    
    # 2. Add abstract floating glowing orbs (Light sources)
    glow_layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_layer)
    
    # Top Left Purple Glow
    gd.ellipse((glass_x - 100, glass_y - 100, glass_x + 500, glass_y + 500), fill=(124, 58, 237, 240))
    # Bottom Right Cyan Glow
    gd.ellipse((glass_x+glass_w-500, glass_y+glass_h-500, glass_x+glass_w+100, glass_y+glass_h+100), fill=(6, 182, 212, 220))
    
    # Blur the glows heavily
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(140))
    bg = Image.alpha_composite(bg, glow_layer)
    
    # === SHADOWS FOR THE APP WINDOW ===
    shadow = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(shadow)
    
    # Deep ambient shadow
    s_draw.rounded_rectangle([glass_x + 20, glass_y + 30, glass_x + glass_w - 20, glass_y + glass_h + 30],
                              radius=glass_r, fill=(0, 0, 0, 170))
    shadow = shadow.filter(ImageFilter.GaussianBlur(40))
    
    # Sharp close shadow
    shadow_close = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    sc_draw = ImageDraw.Draw(shadow_close)
    sc_draw.rounded_rectangle([glass_x, glass_y + 10, glass_x + glass_w, glass_y + glass_h + 10],
                              radius=glass_r, fill=(0, 0, 0, 100))
    shadow_close = shadow_close.filter(ImageFilter.GaussianBlur(15))
    
    bg = Image.alpha_composite(bg, shadow)
    bg = Image.alpha_composite(bg, shadow_close)
    
    # === PREPARE SCREENSHOT TINT AND REFRACTION ===
    # We create a true refracted/frosted background behind the UI for the material feel
    refract_zm = 0.82 # Ultra strong magnification for thick glass feel
    cw, ch = int(img_w * refract_zm), int(img_h * refract_zm)
    
    # Displace the refraction center slightly to simulate angle of incidence bending 
    cx, cy = glass_x + img_w//2 - 60, glass_y + img_h//2 + 60
    refract_box = (cx - cw//2, cy - ch//2, cx + cw//2, cy + ch//2)
    
    frosted_bg = bg.crop(refract_box).resize((img_w, img_h), Image.Resampling.LANCZOS)
    frosted_bg = frosted_bg.filter(ImageFilter.GaussianBlur(25)) # Heavy frosted blur
    frosted_bg = chromatic_aberration(frosted_bg, shift=10) # Strong chromatic glass edges
    
    # Boost colors and contrast to compensate for blur, making refraction pop
    frosted_bg = ImageEnhance.Color(frosted_bg).enhance(1.6)
    frosted_bg = ImageEnhance.Contrast(frosted_bg).enhance(1.3)
    
    # Base glass tint to avoid black artifacts when masking
    glass_pane = Image.new("RGBA", (img_w, img_h), (255, 255, 255, 8))
    frosted_bg = Image.alpha_composite(glass_pane, frosted_bg)
    frosted_bg = add_noise(frosted_bg, amount=12) # Subtle glass grain
    
    # Create the rounded mask for the composition
    ui_mask = Image.new("L", (img_w, img_h), 0)
    uid = ImageDraw.Draw(ui_mask)
    uid.rounded_rectangle([0, 0, img_w, img_h], radius=glass_r, fill=255)
    
    # Layer screenshot on top of frosted_bg BEFORE pasting onto the main canvas
    # Reduce opacity significantly for extremely translucent glass feel
    source_alpha = source_img.split()[3]
    source_alpha = source_alpha.point(lambda p: int(p * 0.55)) 
    source_img.putalpha(source_alpha)
    frosted_composite = Image.alpha_composite(frosted_bg, source_img)
    
    # Apply mask correctly preserving native transparency
    frosted_alpha = ImageChops.multiply(frosted_composite.split()[3], ui_mask)
    frosted_composite.putalpha(frosted_alpha)
    
    # Paste composite
    bg.paste(frosted_composite, (glass_x, glass_y), frosted_composite)
    
    # === DIAGONAL GLASS SHEEN/REFLECTION ===
    sheen_layer = Image.new("RGBA", (canvas_w, canvas_h), (0,0,0,0))
    
    # Create the diagonal gloss shape
    sheen = Image.new("RGBA", (img_w, img_h), (0,0,0,0))
    sheen_draw = ImageDraw.Draw(sheen)
    
    # A massive diagonal polygon spanning from top-left to middle-right
    pts = [(0, 0), (int(img_w * 0.85), 0), (0, int(img_h * 0.70))]
    sheen_draw.polygon(pts, fill=(255, 255, 255, 255))
    
    # Apply a vertical gradient mask to the sheen so it naturally fades out
    sheen_gradient = Image.new("L", (img_w, img_h), 0)
    for y in range(img_h):
        # Extremely soft and elegant curve for the sheen falloff, but brighter at top
        norm_y = y / (img_h * 0.75)
        if norm_y > 1.0:
            alpha_val = 0
        else:
            alpha_val = int(180 * (1.0 - norm_y)**2.5) # Brighter reflection
        for x in range(img_w):
            sheen_gradient.putpixel((x, y), alpha_val)
            
    # Combine polygon with gradient
    sheen.putalpha(ImageChops.multiply(sheen.split()[3], sheen_gradient))
    
    # Add a secondary thinner distinct reflection line
    sheen_line = Image.new("RGBA", (img_w, img_h), (0,0,0,0))
    sl_draw = ImageDraw.Draw(sheen_line)
    pts_line = [(0, 15), (int(img_w * 0.85)+15, 0), (int(img_w * 0.85)+18, 0), (0, 18)]
    sl_draw.polygon(pts_line, fill=(255, 255, 255, 160))
    
    # Add bottom-right counter-reflection
    pts_bottom = [(img_w, img_h), (int(img_w * 0.4), img_h), (img_w, int(img_h * 0.4))]
    sl_draw.polygon(pts_bottom, fill=(255, 255, 255, 30))
    
    sheen = Image.alpha_composite(sheen, sheen_line)
    
    # Mask sheen with UI rounded corners
    sheen.putalpha(ImageChops.multiply(sheen.split()[3], ui_mask))
    
    # Add directional glossy border (Top/Left bright, Bottom/Right dark)
    border_layer = Image.new("RGBA", (img_w, img_h), (0,0,0,0))
    b_draw = ImageDraw.Draw(border_layer)
    b_draw.rounded_rectangle([0, 0, img_w-1, img_h-1], radius=glass_r, outline=(255, 255, 255, 20), width=1) # base weak
    b_draw.rounded_rectangle([1, 1, img_w-2, img_h-2], radius=glass_r-1, outline=(255, 255, 255, 140), width=1) # top/left simulated
    
    # Overwrite bottom and right with dark transparency for real depth
    b_draw.line([(0, img_h-1), (img_w, img_h-1)], fill=(0, 0, 0, 80), width=2)
    b_draw.line([(img_w-1, 0), (img_w-1, img_h)], fill=(0, 0, 0, 80), width=2)
    
    # Compose sheen and border
    sheen = Image.alpha_composite(sheen, border_layer)
    sheen_layer.paste(sheen, (glass_x, glass_y), sheen)
    
    # Paste sheen on top of everything
    bg = Image.alpha_composite(bg, sheen_layer)

    # Save as native transparent RGBA!
    bg.save(output_path, "PNG", quality=95)
    print(f"Saved: {output_path}")

if __name__ == "__main__":
    input_img = r"E:\trae\1\website\屏幕截图 2026-03-27 145059_画质修复.png"
    hero_out = r"E:\trae\1\website\hero_screenshot.png"
    showcase_out = r"E:\trae\1\website\showcase_screenshot.png"
    
    create_dribbble_glassmorphism(input_img, hero_out)
    shutil.copy2(hero_out, showcase_out)
    print("Done!")
