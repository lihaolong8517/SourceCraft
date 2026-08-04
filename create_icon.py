"""
SourceCraft - 现代化图标生成器
更时尚的渐变效果 + 玻璃拟态风格
"""
from PIL import Image, ImageDraw, ImageFilter
import math
import os

def create_modern_icon():
    """创建更现代化的图标"""
    sizes = [256, 128, 64, 48, 32, 16]
    images = []
    
    for size in sizes:
        # 创建 RGBA 图像
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        scale = size / 256
        margin = int(8 * scale)
        corner_radius = int(56 * scale)  # 更大的圆角
        
        # === 绘制主背景（深色渐变） ===
        for y in range(margin, size - margin):
            for x in range(margin, size - margin):
                # 对角渐变 - 从深紫到青色
                progress = ((x - margin) + (y - margin)) / ((size - 2 * margin) * 2)
                
                # 颜色：深紫 (79, 20, 140) -> 青色 (20, 184, 166)
                r = int(79 + (20 - 79) * progress)
                g = int(20 + (184 - 20) * progress)
                b = int(140 + (166 - 140) * progress)
                
                # 检查圆角
                in_bounds = True
                corners = [
                    (margin + corner_radius, margin + corner_radius, margin, margin),
                    (size - margin - corner_radius, margin + corner_radius, size - margin, margin),
                    (margin + corner_radius, size - margin - corner_radius, margin, size - margin),
                    (size - margin - corner_radius, size - margin - corner_radius, size - margin, size - margin),
                ]
                
                for cx, cy, corner_x, corner_y in corners:
                    if (x < cx and corner_x == margin) or (x > cx and corner_x == size - margin):
                        if (y < cy and corner_y == margin) or (y > cy and corner_y == size - margin):
                            dx = abs(x - cx)
                            dy = abs(y - cy)
                            if dx * dx + dy * dy > corner_radius * corner_radius:
                                in_bounds = False
                                break
                
                if in_bounds:
                    img.putpixel((x, y), (r, g, b, 255))
        
        # === 绘制玻璃高光效果（顶部） ===
        highlight_height = int(80 * scale)
        for y in range(margin, margin + highlight_height):
            alpha = int(60 * (1 - (y - margin) / highlight_height))  # 渐变透明
            for x in range(margin, size - margin):
                # 检查圆角
                in_bounds = True
                if y < margin + corner_radius:
                    if x < margin + corner_radius:
                        dx = abs(x - (margin + corner_radius))
                        dy = abs(y - (margin + corner_radius))
                        if dx * dx + dy * dy > corner_radius * corner_radius:
                            in_bounds = False
                    elif x > size - margin - corner_radius:
                        dx = abs(x - (size - margin - corner_radius))
                        dy = abs(y - (margin + corner_radius))
                        if dx * dx + dy * dy > corner_radius * corner_radius:
                            in_bounds = False
                
                if in_bounds:
                    current = img.getpixel((x, y))
                    if current[3] > 0:
                        new_r = min(255, current[0] + alpha)
                        new_g = min(255, current[1] + alpha)
                        new_b = min(255, current[2] + alpha)
                        img.putpixel((x, y), (new_r, new_g, new_b, 255))
        
        # === 绘制下载箭头（白色，带阴影效果） ===
        center_x = size // 2
        arrow_color = (255, 255, 255, 255)
        shadow_color = (0, 0, 0, 40)
        
        line_width = max(3, int(16 * scale))
        arrow_top = int(65 * scale)
        arrow_bottom = int(155 * scale)
        arrow_head_start = int(120 * scale)
        arrow_head_width = int(35 * scale)
        
        # 绘制箭头主体
        for dx in range(-line_width//2, line_width//2 + 1):
            for y in range(arrow_top, arrow_bottom):
                px = center_x + dx
                if 0 <= px < size and 0 <= y < size:
                    img.putpixel((px, y), arrow_color)
        
        # 绘制箭头头部（V 形）
        for i in range(arrow_head_width):
            y_offset = arrow_head_start + i
            x_left = center_x - i
            x_right = center_x + i
            
            for dx in range(-line_width//2, line_width//2 + 1):
                if 0 <= x_left + dx < size and 0 <= y_offset < size:
                    img.putpixel((x_left + dx, y_offset), arrow_color)
                if 0 <= x_right + dx < size and 0 <= y_offset < size:
                    img.putpixel((x_right + dx, y_offset), arrow_color)
        
        # === 绘制底部托盘（圆角矩形） ===
        tray_y = int(175 * scale)
        tray_height = int(20 * scale)
        tray_left = int(70 * scale)
        tray_right = int(186 * scale)
        tray_corner = int(8 * scale)
        
        for y in range(tray_y, tray_y + tray_height):
            for x in range(tray_left, tray_right):
                in_tray = True
                
                # 检查四个角
                if y < tray_y + tray_corner:
                    if x < tray_left + tray_corner:
                        dx = x - (tray_left + tray_corner)
                        dy = y - (tray_y + tray_corner)
                        if dx * dx + dy * dy > tray_corner * tray_corner:
                            in_tray = False
                    elif x > tray_right - tray_corner:
                        dx = x - (tray_right - tray_corner)
                        dy = y - (tray_y + tray_corner)
                        if dx * dx + dy * dy > tray_corner * tray_corner:
                            in_tray = False
                elif y > tray_y + tray_height - tray_corner:
                    if x < tray_left + tray_corner:
                        dx = x - (tray_left + tray_corner)
                        dy = y - (tray_y + tray_height - tray_corner)
                        if dx * dx + dy * dy > tray_corner * tray_corner:
                            in_tray = False
                    elif x > tray_right - tray_corner:
                        dx = x - (tray_right - tray_corner)
                        dy = y - (tray_y + tray_height - tray_corner)
                        if dx * dx + dy * dy > tray_corner * tray_corner:
                            in_tray = False
                
                if in_tray and 0 <= x < size and 0 <= y < size:
                    img.putpixel((x, y), arrow_color)
        
        images.append(img)
    
    # 保存为 ICO
    ico_path = os.path.join(os.path.dirname(__file__), 'app_icon.ico')
    images[0].save(ico_path, format='ICO', sizes=[(s, s) for s in sizes])
    print(f"✅ 现代化图标已生成: {ico_path}")
    
    # 同时保存 PNG 预览
    png_path = os.path.join(os.path.dirname(__file__), 'app_icon.png')
    images[0].save(png_path, format='PNG')
    print(f"✅ PNG 预览已生成: {png_path}")

if __name__ == "__main__":
    create_modern_icon()
