# -*- coding: utf-8 -*-
"""
图片生成核心逻辑 - 完整复刻 index.html 的全部逻辑
"""
import os
import re
import io
import base64
import zipfile
import time
import shutil
import tempfile
import requests
from PIL import Image, ImageDraw, ImageFont
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import (
    API_BASE, API_KEYS, LLM_MODEL, IMG_MODEL,
    MAX_CONCURRENT_IMAGES, REQUEST_TIMEOUT, TEMP_DIR
)
from feishu_client import get_feishu_client

# 禁用代理（当前环境代理无法访问外部API，直连正常）
NO_PROXY = {"http": None, "https": None}

# ==================== 分辨率映射（与 index.html 完全一致） ====================
VIP_PIXEL_MAP = {
    "1:1":  {"1K": "1024x1024", "2K": "2048x2048", "4K": "2880x2880"},
    "16:9": {"1K": "1280x720",  "2K": "2048x1152", "4K": "3840x2160"},
    "9:16": {"1K": "720x1280",  "2K": "1152x2048", "4K": "2160x3840"},
    "4:3":  {"1K": "1152x864",  "2K": "2304x1728", "4K": "3264x2448"},
    "3:4":  {"1K": "864x1152",  "2K": "1728x2304", "4K": "2448x3264"},
    "3:2":  {"1K": "1536x1024", "2K": "2048x1360", "4K": "3504x2336"},
    "2:3":  {"1K": "1024x1536", "2K": "1360x2048", "4K": "2336x3504"},
    "5:4":  {"1K": "1120x896",  "2K": "2240x1792", "4K": "3200x2560"},
    "4:5":  {"1K": "896x1120",  "2K": "1792x2240", "4K": "2560x3200"},
    "21:9": {"1K": "1456x624",  "2K": "2912x1248", "4K": "3840x1648"},
    "9:21": {"1K": "624x1456",  "2K": "1248x2912", "4K": "1648x3840"},
    "1:2":  {"1K": "768x1536",  "2K": "1536x3072", "4K": "1920x3840"},
    "2:1":  {"1K": "1536x768",  "2K": "3072x1536", "4K": "3840x1920"},
    "1:3":  {"1K": "688x2048",  "2K": "1280x3840"},
    "3:1":  {"1K": "2048x688",  "2K": "3840x1280"},
}


# ==================== 工具函数 ====================
def log(msg, level="info"):
    """打印日志"""
    colors = {"info": "\033[36m", "success": "\033[32m", "error": "\033[31m", "warn": "\033[33m"}
    reset = "\033[0m"
    now = time.strftime("%H:%M:%S")
    print(f"{colors.get(level, '')}[{now}] {msg}{reset}", flush=True)


def sanitize_filename(name):
    """清理文件名"""
    return re.sub(r'[\\/:*?"<>|]', '_', str(name or 'image')).replace(' ', '_')[:80]


def get_file_ext_from_url(url):
    """从URL获取文件扩展名"""
    if not url:
        return 'jpg'
    m = re.search(r'\.([a-zA-Z0-9]{2,5})(?:\?|#|$)', url)
    if m:
        return m.group(1).lower()
    if url.startswith('data:image/png'):
        return 'png'
    if url.startswith('data:image/webp'):
        return 'webp'
    return 'jpg'


def get_field_value(fields, field_name, default=None):
    """安全获取字段值（处理单选字段返回列表的情况）"""
    val = fields.get(field_name, default)
    if val is None:
        return default
    if isinstance(val, list) and len(val) > 0:
        return val[0].get("text", val[0]) if isinstance(val[0], dict) else val[0]
    return val


# ==================== 图片处理 ====================
def compress_image(img_path, max_dim=1024, quality=82):
    """压缩图片到指定尺寸，返回 base64"""
    img = Image.open(img_path)
    w, h = img.size
    if w > max_dim or h > max_dim:
        if w > h:
            h = int(h * max_dim / w)
            w = max_dim
        else:
            w = int(w * max_dim / h)
            h = max_dim
        img = img.resize((w, h), Image.LANCZOS)
    if img.mode != 'RGB':
        img = img.convert('RGB')
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=quality)
    return base64.b64encode(buf.getvalue()).decode('utf-8'), img


def wrap_text(draw, text, font, max_width):
    """文本换行"""
    lines = []
    for para in text.split('\n'):
        if para == '':
            lines.append('')
            continue
        line = ''
        for ch in para:
            test = line + ch
            if draw.textlength(test, font=font) > max_width and line:
                lines.append(line)
                line = ch
            else:
                line = test
        if line:
            lines.append(line)
    return lines


def generate_table_image(product_name, country, selling_points, ref_images, work_dir):
    """生成参考图表格（与 index.html 的 generateTableImage 一致）"""
    canvas_w = 560
    title_h = 50
    header_h = 44
    gap = 10
    ref_img_w = 200
    ref_img_h = 200
    sp_row_h = 22
    sp_item_pad_h = 16

    # 加载字体
    font_paths = [
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    font_path = None
    for fp in font_paths:
        if os.path.exists(fp):
            font_path = fp
            break

    if font_path:
        font_title = ImageFont.truetype(font_path, 16)
        font_header = ImageFont.truetype(font_path, 15)
        font_body = ImageFont.truetype(font_path, 14)
        font_label = ImageFont.truetype(font_path, 13)
    else:
        font_title = ImageFont.load_default()
        font_header = ImageFont.load_default()
        font_body = ImageFont.load_default()
        font_label = ImageFont.load_default()

    # 测量卖点高度
    tmp_img = Image.new('RGB', (canvas_w, 100))
    tmp_draw = ImageDraw.Draw(tmp_img)
    sp_max_w = canvas_w - 24 - 30

    sp_items = []
    total_sp_h = 0
    for si, text in enumerate(selling_points):
        text = (text or '').strip()
        if text:
            prefix = f"{si+1}. "
            lines = wrap_text(tmp_draw, text, font_body, sp_max_w)
            content_h = max(len(lines) * sp_row_h, 22)
            sp_items.append({"num": si+1, "text": text, "prefix": prefix, "lines": lines, "content_h": content_h})
            total_sp_h += content_h + sp_item_pad_h

    num_ref = len(ref_images)
    title_section_h = title_h
    sp_section_h = total_sp_h > 0 and (header_h + total_sp_h) or 0
    ref_section_h = num_ref > 0 and (header_h + ref_img_h) or 0
    total_h = title_section_h + sp_section_h + (ref_section_h > 0 and (gap + ref_section_h) or 0) + 12

    canvas = Image.new('RGB', (canvas_w, total_h), '#FFFFFF')
    draw = ImageDraw.Draw(canvas)
    border_color = '#333333'

    # 标题区
    draw.rectangle([0, 0, canvas_w, title_h], fill='#E8EAF6')
    draw.rectangle([0, 0, canvas_w-1, title_h-1], outline=border_color, width=2)
    draw.text((14, title_h//2), f'品名: {product_name}    |    销售国家: {country}', fill='#1A1A2E', font=font_title, anchor='lm')

    y_offset = title_section_h
    # 卖点区
    if total_sp_h > 0:
        draw.rectangle([0, y_offset, canvas_w, y_offset+header_h], fill='#C8E6C9')
        draw.rectangle([0, y_offset, canvas_w-1, y_offset+header_h-1], outline=border_color, width=2)
        draw.text((12, y_offset+header_h//2), '产品卖点（每点对应一张图）', fill='#000000', font=font_header, anchor='lm')
        y_offset += header_h

        draw.rectangle([0, y_offset, canvas_w, y_offset+total_sp_h+8], fill='#FFFFFF')
        draw.rectangle([0, y_offset, canvas_w-1, y_offset+total_sp_h+7], outline=border_color, width=1)

        item_text_y = y_offset + 8
        for item in sp_items:
            draw.text((12, item_text_y), item['prefix'], fill='#4A90D9', font=font_label)
            prefix_w = draw.textlength(item['prefix'], font=font_label)
            for li, line in enumerate(item['lines']):
                if li == 0:
                    draw.text((12+prefix_w, item_text_y), line, fill='#333333', font=font_body)
                else:
                    draw.text((12+prefix_w, item_text_y + li*sp_row_h), line, fill='#333333', font=font_body)
            item_text_y += item['content_h'] + sp_item_pad_h
        y_offset += total_sp_h + 8

    # 参考图区
    if num_ref > 0:
        y_offset += gap
        draw.rectangle([0, y_offset, canvas_w, y_offset+header_h], fill='#FFCDD2')
        draw.rectangle([0, y_offset, canvas_w-1, y_offset+header_h-1], outline=border_color, width=2)
        draw.text((12, y_offset+header_h//2), f'参考图 ({num_ref} 张)', fill='#000000', font=font_header, anchor='lm')
        y_offset += header_h

        cols = max(1, canvas_w // ref_img_w)
        for i, ref_path in enumerate(ref_images[:10]):
            col = i % cols
            row = i // cols
            cell_x = col * ref_img_w
            cell_y = y_offset + row * ref_img_h
            draw.rectangle([cell_x, cell_y, cell_x+ref_img_w, cell_y+ref_img_h], fill='#FFFFFF')
            draw.rectangle([cell_x, cell_y, cell_x+ref_img_w-1, cell_y+ref_img_h-1], outline=border_color, width=1)
            try:
                ref_img = Image.open(ref_path)
                target_w = ref_img_w - 16
                target_h = ref_img_h - 16
                scale = min(target_w/ref_img.width, target_h/ref_img.height)
                nw = int(ref_img.width * scale)
                nh = int(ref_img.height * scale)
                ix = cell_x + (ref_img_w - nw) // 2
                iy = cell_y + (ref_img_h - nh) // 2
                canvas.paste(ref_img, (ix, iy))
            except Exception as e:
                log(f"参考图{i+1}加载失败: {e}", "warn")
            draw.rectangle([cell_x, cell_y+ref_img_h-20, cell_x+ref_img_w, cell_y+ref_img_h], fill=(0,0,0,150))
            draw.text((cell_x+ref_img_w//2, cell_y+ref_img_h-10), f'参考图{i+1}', fill='#FFFFFF', font=font_body, anchor='mm')

    table_path = os.path.join(work_dir, 'table.jpg')
    canvas.save(table_path, 'JPEG', quality=85)
    return table_path


# ==================== API 调用 ====================
def call_llm(table_base64, prompt_text, extra_images_base64):
    """调用 LLM API"""
    url = f"{API_BASE}/chat/completions"
    content = [{"type": "text", "text": prompt_text}]
    if table_base64:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{table_base64}"}})
    for img_b64 in extra_images_base64:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}})

    body = {"model": LLM_MODEL, "stream": False, "messages": [{"role": "user", "content": content}]}

    last_error = None
    for ki, key in enumerate(API_KEYS):
        try:
            log(f"LLM 请求 (Key {ki+1}/{len(API_KEYS)})")
            resp = requests.post(url, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                                  json=body, timeout=REQUEST_TIMEOUT, proxies=NO_PROXY)
            if resp.ok:
                result = resp.json()
                text = (result.get("choices", [{}])[0].get("message", {}).get("content", "")) or ""
                if text:
                    log(f"LLM 请求成功（使用 Key {ki+1}）", "success")
                    return text
                raise Exception("API 返回内容为空")
            last_error = f"HTTP {resp.status}"
            log(f"LLM Key {ki+1} 失败 (HTTP {resp.status})", "warn")
        except Exception as e:
            last_error = str(e)
            log(f"LLM Key {ki+1} 出错: {e}", "warn")
    raise Exception(f"LLM 调用失败（已尝试 {len(API_KEYS)} 个 Key）: {last_error}")


def call_image_gen(prompt, product_base64s, aspect_ratio, image_size):
    """调用生图 API"""
    size_px = ""
    if aspect_ratio and aspect_ratio != "auto（自动）":
        ratio = aspect_ratio.split('（')[0] if '（' in aspect_ratio else aspect_ratio
        m = VIP_PIXEL_MAP.get(ratio)
        if m:
            tier = image_size.split('（')[0] if '（' in image_size else image_size
            size_px = m.get(tier) or m.get("2K") or m.get("1K")
    if not size_px:
        sq = VIP_PIXEL_MAP["1:1"]
        tier = image_size.split('（')[0] if '（' in image_size else image_size
        size_px = sq.get(tier) or sq["2K"]

    has_images = len(product_base64s) > 0
    endpoint = has_images and "/images/edits" or "/images/generations"
    url = f"{API_BASE}{endpoint}"

    last_error = None
    for ki, key in enumerate(API_KEYS):
        try:
            log(f"生图请求 (Key {ki+1}/{len(API_KEYS)}) size={size_px} {'有参考图' if has_images else '纯文生图'}")
            if has_images:
                form_data = {"model": IMG_MODEL, "prompt": prompt, "quality": "high"}
                if size_px:
                    form_data["size"] = size_px
                files = []
                for ii, b64 in enumerate(product_base64s):
                    img_data = base64.b64decode(b64)
                    files.append(("image", (f"product_{ii+1}.png", io.BytesIO(img_data), "image/png")))
                resp = requests.post(url, headers={"Authorization": f"Bearer {key}"},
                                      data=form_data, files=files, timeout=REQUEST_TIMEOUT, proxies=NO_PROXY)
            else:
                body = {"model": IMG_MODEL, "prompt": prompt, "quality": "high"}
                if size_px:
                    body["size"] = size_px
                resp = requests.post(url, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                                      json=body, timeout=REQUEST_TIMEOUT, proxies=NO_PROXY)

            if resp.ok:
                result = resp.json()
                if result.get("data") and result["data"][0]:
                    item = result["data"][0]
                    if item.get("b64_json"):
                        b64 = item["b64_json"]
                        if b64.startswith("data:"):
                            return b64
                        return f"data:image/png;base64,{b64}"
                    if item.get("url"):
                        return item["url"]
                raise Exception("API 返回结果中无图片数据")
            last_error = f"HTTP {resp.status}"
            log(f"生图 Key {ki+1} 失败 (HTTP {resp.status}): {resp.text[:200]}", "warn")
        except Exception as e:
            last_error = str(e)
            log(f"生图 Key {ki+1} 出错: {e}", "warn")
    raise Exception(f"生图调用失败（已尝试 {len(API_KEYS)} 个 Key）: {last_error}")


# ==================== LLM 输出解析 ====================
def parse_llm_output(text):
    """解析 LLM 输出"""
    appearance = ""
    style = ""
    images = []

    sections = []
    regex = r'【([^】]+)】\s*([\s\S]*?)(?=【[^】]+】|$)'
    for match in re.finditer(regex, text):
        label = match.group(1).strip()
        content = match.group(2).strip()
        if label and content:
            sections.append({"label": label, "content": content})

    for s in sections:
        if any(kw in s["label"] for kw in ["外观", "产品分析", "Appearance"]):
            appearance = s["content"]
        elif any(kw in s["label"] for kw in ["风格", "统一", "规范", "Style", "style"]):
            style = s["content"]
        else:
            images.append({"label": s["label"], "desc": s["content"]})

    if not sections and text.strip():
        lines = text.strip().split('\n')
        style_lines = []
        image_lines = []
        found_numbered = False
        current_label = ""
        current_plan = ""
        for line in lines:
            num_match = re.match(r'^\s*(\d+)[\.、\)]\s*(.+)', line)
            if num_match:
                found_numbered = True
                if current_label:
                    image_lines.append({"label": current_label, "desc": current_plan.strip()})
                current_label = num_match.group(2).strip()
                current_plan = ""
            elif found_numbered:
                current_plan += line + "\n"
            else:
                style_lines.append(line)
        if current_label:
            image_lines.append({"label": current_label, "desc": current_plan.strip()})
        style = "\n".join(style_lines).strip()
        images = image_lines

    images = images[:10]
    return {"appearance": appearance, "style": style, "images": images}


# ==================== Prompt 构建 ====================
def build_image_prompt(product_name, index, plan, product_base64s, unified_style, ref_weight, layout_main_color, layout_aux_color, is_pure_scene):
    """构建单张图片的生图 prompt"""
    img_system_prompt = (
        "You are a professional e-commerce product photography engine. Your SOLE PURPOSE is to place an EXISTING product into a new scene while keeping the product 100% IDENTICAL to the reference images.\n\n"
        "PRODUCT FIDELITY — THIS IS YOUR ABSOLUTE HIGHEST LAW:\n"
        "The product in the output MUST be a pixel-perfect visual copy of the product in the input images. Treat the product as SACRED and UNCHANGEABLE:\n"
        "- Shape / silhouette / contour / form → EXACT MATCH, ZERO DEVIATION\n"
        "- Every color, shade, gradient, color-block boundary → EXACT MATCH\n"
        "- Surface material: gloss level, matte areas, texture pattern, reflectivity → EXACT MATCH\n"
        "- Proportions, size ratios between parts → EXACT MATCH\n"
        "- ALL small details: edges, corners, curves, seams, ports, buttons, logos, labels, indentations, ridges → EXACT MATCH\n"
        "- Camera angle, perspective, foreshortening → MATCH the reference product views\n"
        "You MUST NOT redesign, reinterpret, stylize, simplify, or \"improve\" the product. Copy it EXACTLY.\n\n"
        "WHAT YOU CAN CHANGE (and ONLY these):\n"
        "- The environment/scene/background surrounding the product\n"
        "- Lighting mood and direction (but product surface must still look authentic)\n"
        "- Text overlays, UI elements, decorative graphics\n"
        "- Camera framing / composition of the overall shot\n\n"
        "BEFORE GENERATING: Mentally list every visible feature of the product in the input images. Then ensure EVERY feature appears identically in the output. If you cannot guarantee an exact match for any feature, do NOT generate — this is a failure condition."
    )

    style_directive = ""
    if unified_style:
        style_directive = (
            "\n\n=== 套装统一风格规范（10张图必须严格遵守以下风格，确保看起来是一套图）===\n"
            + unified_style +
            "\n=== 统一风格规范结束 ===\n"
        )

    ref_weight_directive = ""
    if ref_weight:
        rw = int(ref_weight.split('%')[0])
        if rw == 100:
            ref_weight_directive = '\n【参考权重指令 — 100%：严格全盘参考】排版颜色、字体、版式形状图案全部严格遵循上方统一风格规范中的设定，不要自行更改。'
        elif rw == 60:
            ref_weight_directive = '\n【参考权重指令 — 60%：字体+版式参考，主色参考，辅助色自主】字体风格和版式形状图案可以遵循统一风格规范中的设定。配色方面：只参考参考图的主色调，辅助色由你根据产品自行重新搭配，不受参考图辅助色的约束。'
        else:
            ref_weight_directive = '\n【参考权重指令 — 20%：仅版式形状参考，完全自主配色】排版布局和图形形状图案可以少量参考风格规范，但不要参考参考图的字体风格。配色方案必须完全由你根据产品自行选择最合适的配色，不受任何参考图颜色的约束。整体排版要与参考图有明显区别。'
        if layout_main_color or layout_aux_color:
            ref_weight_directive += '\n【排版配色优先】用户已填写排版配色，颜色以用户填写的排版配色为准。'

    if is_pure_scene:
        scene_instructions = (
            '\n>>> PURE SCENE MODE — NO TEXT, NO LAYOUT, NO GRAPHICS <<<\n'
            'THIS IMAGE MUST BE A PURE PHOTOREALISTIC SCENE ONLY. ABSOLUTELY NO TEXT, NO TYPOGRAPHY, NO INFO CARDS, NO DECORATIVE GRAPHICS, NO UI ELEMENTS, NO LAYOUT OVERLAYS.\n'
            '- The output should look like a professional product photograph taken in a real environment\n'
            '- ONLY the product and its natural surroundings — nothing else\n'
            '- Any text/layout instructions in the plan below MUST BE IGNORED for this image\n'
            '- Focus entirely on: realistic scene composition, natural lighting, environmental storytelling, product placement in context\n'
        )
    else:
        scene_instructions = (
            '\n>>> SCENE & LAYOUT INSTRUCTIONS (secondary — never override product accuracy) <<<\n'
            '- Scene/background must be photorealistic, with natural lighting, depth of field, environmental detail\n'
            '- Product and scene are NOT affected by text/decoration colors\n'
            '- Layout colors only apply to text, decorative graphics, info cards — never to the product or scene\n'
            '- FONT SIZE & WORD COUNT CONSTRAINT (CRITICAL — OVERSIZE OR FAIL) — TWO RULES THAT MUST BE FOLLOWED TOGETHER:\n'
            '  RULE 1 — WORD LIMIT: Each image must contain 13 TO 30 WORDS of text total. Do NOT go below 13 or above 30. Every image must have enough text to convey key info while staying concise. Keep the most essential selling point keywords, product name, and critical info. Delete ALL unnecessary descriptive text, long sentences, and paragraphs. Replace long phrases with punchy short keywords. Example: instead of "Our premium stainless steel water bottle keeps your drinks cold for 24 hours", just write "24H COLD".\n'
            '  RULE 2 — OVERSIZE FONTS: Because text is concise (13-30 words), EVERY word MUST be EXTREMELY LARGE, BOLD, and DOMINANT. Minimum 52px equivalent on a phone screen — this is the floor. Headlines: 64px+, extra bold, fill the frame. Body/feature text: 52px+, bold. No text smaller than 48px under any circumstance. No thin/light/regular fonts — bold or nothing. With concise text, each word can and should be massive. Oversized text that fills the frame is the goal, not the exception. If text looks "balanced" or "proportionate," it is TOO SMALL — make it bigger.\n'
        )

    final_prompt = (
        img_system_prompt + "\n\n"
        "═══════════════════════════════════════\n"
        f"PRODUCT TO PHOTOGRAPH: {product_name}\n"
        f"Image {index+1} of 5 in a unified e-commerce photo set.\n"
        "═══════════════════════════════════════\n\n"
        ">>> PRODUCT PRESERVATION (READ FIRST — THIS OVERRIDES EVERYTHING ELSE) <<<\n"
        f"The {product_name} in the input images is your ONLY reference for what the product looks like.\n"
        "Copy it EXACTLY — shape, colors, materials, details, proportions, angle.\n"
        "If the scene or text instructions below conflict with product accuracy, IGNORE the scene/text — product accuracy ALWAYS wins.\n"
        + scene_instructions
        + ("" if is_pure_scene else style_directive + ref_weight_directive)
        + "\nSpecific design plan for this image:\n" + plan + "\n\n"
        ">>> FINAL REMINDER <<<\n"
        "Before you generate, verify: does the product in your output look IDENTICAL to the product in the input images?\n"
        "Shape? Colors? Materials? Proportions? Details? ALL must match EXACTLY.\n"
        + ("This is a PURE SCENE image — no text, no layout, no graphics. Only product + environment." if is_pure_scene else "Only the scene, text overlays, and composition may differ.")
    )
    return final_prompt


def build_llm_prompt(product_name, sales_country, selling_points, filled_spots, prod_count, ref_count,
                      layout_main_color, layout_aux_color, ref_weight):
    """构建 LLM prompt"""
    prod_img_start = 2
    prod_img_end = 1 + prod_count
    ref_img_start = 2 + prod_count
    ref_img_end = 1 + prod_count + ref_count
    total_images = 1 + prod_count + ref_count

    if prod_count == 1:
        prod_desc = f"第{prod_img_start}张 = 1张独立的产品原图（高清，是你要分析产品外观的唯一依据）"
    else:
        prod_desc = f"第{prod_img_start}-{prod_img_end}张 = {prod_count}张独立的产品原图（高清，是你要分析产品外观的唯一依据）"

    ref_desc = ""
    if ref_count > 0:
        if ref_count == 1:
            ref_desc = f"第{ref_img_start}张 = 1张独立的参考图原图（高清，是你要分析排版布局和形状风格的依据）"
        else:
            ref_desc = f"第{ref_img_start}-{ref_img_end}张 = {ref_count}张独立的参考图原图（高清，是你要分析排版布局和形状风格的依据）"

    has_colors = bool(layout_main_color or layout_aux_color)
    has_refs = ref_count > 0
    color_directive = ""
    ref_directive = ""

    if has_colors:
        color_directive = "【强制排版配色约束】用户已指定排版配色，你必须在统一风格规范中严格使用以下颜色作为排版主色和辅助色。这些颜色仅约束排版、文字、装饰元素、信息卡片的颜色，不约束场景、背景和产品本身的颜色：\n"
        if layout_main_color:
            color_directive += f"- 排版主色：{layout_main_color}（用于大标题、关键信息、重点装饰、按钮风格元素）\n"
        if layout_aux_color:
            color_directive += f"- 排版辅助色：{layout_aux_color}（用于副标题、次要文字、背景色块、分隔线、标签等）\n"
        color_directive += "请在统一风格规范中直接使用这些色值，不要自行替换或修改。\n\n"
        if has_refs:
            ref_directive = f"【参考图使用规则】除了总表，你还收到了 {ref_count} 张参考图的高清原图（第{ref_img_start}{'-'+str(ref_img_end) if ref_count>1 else '张'}）。你必须仔细查看这些独立传来的参考图原图（不要依赖总表中的缩略图），严格参考它们的排版布局和图形形状风格来设计10张图的排版，但排版配色使用上面用户指定的颜色，不要使用参考图本身的配色。\n"
            ref_directive += "重点分析并运用参考图中以下形状元素：\n"
            ref_directive += "- 几何色块形状：波浪形色块、圆形/椭圆形装饰、方形/矩形卡片、三角形/多边形装饰、不规则有机形状色块、斜切/倾斜色块\n"
            ref_directive += "- 色块之间的关系：叠加层次、透明度和混合方式、色块大小比例和位置分布\n"
            ref_directive += "- 线条与分隔：曲线分隔线、直线分隔、点线虚线装饰、渐隐线条\n"
            ref_directive += "- 文字区域形状：文字底框的形状（圆角矩形/胶囊形/下划线色块）、标题与正文的位置关系\n"
            ref_directive += "- 整体排版骨架：产品在画面中的位置和占比、文字和装饰元素的分布密度、留白区域的大小和位置\n"
            ref_directive += "在统一风格规范中明确列出你从参考图中提取到的具体形状类型和排版结构，不要笼统描述。\n\n"
    elif has_refs:
        rw = int(ref_weight.split('%')[0])
        if rw == 100:
            color_directive = "【参考图配色约束 — 100%权重】排版配色栏未填写但上传了参考图（100%参考权重），你必须完整提取参考图中使用的所有颜色及其配色比例，全盘应用到10张图的排版设计中。具体要求：\n"
            color_directive += "- 提取参考图中的所有颜色（主色、辅助色、点缀色、背景色、文字色等），不要遗漏任何一种\n"
            color_directive += "- 保持参考图中各颜色的占比比例关系（如主色占60%、辅助色占30%、点缀色占10%）\n"
            color_directive += "- 保持颜色之间的过渡与渐变方式\n"
            color_directive += "- 这些颜色仅约束排版、文字、装饰元素、信息卡片的颜色，不约束场景、背景和产品本身的颜色\n\n"
        elif rw == 60:
            color_directive = f"【主色参考+辅助色自主 — 60%权重】参考权重60%，配色栏未填写。配色策略：从参考图中提取主色调作为排版主色参考，但辅助色和点缀色由你根据产品信息（品牌调性、目标市场={sales_country}、产品特点）自行重新搭配，不受参考图辅助色的约束。\n"
            color_directive += "请在统一风格规范中给出：参考图的主色调（含色值）+ 你自主设计的辅助色方案（含色值），并说明为什么这套辅助色更适合该产品。\n\n"
        else:
            color_directive = f"【自主配色设计 — 20%权重】参考权重20%，配色栏未填写。你必须根据产品信息（品牌调性、目标市场={sales_country}、产品特点）自行设计一套适合该产品的排版配色方案。\n"
            color_directive += "不要照抄参考图的配色。你设计的配色要与参考图有明显区别，更适合当前产品。\n"
            color_directive += "请在统一风格规范中给出你设计的具体配色方案（含色值或色名），并说明为什么这套配色更适合该产品。\n\n"

        ref_shape_directive = ""
        if rw == 100:
            ref_shape_directive = "2. 配色方案：必须完整提取参考图的配色（所有颜色及比例），应用到10张图的排版中\n"
        elif rw == 60:
            ref_shape_directive = "2. 配色方案：只参考参考图的主色调，辅助色必须根据产品自行重新搭配设计\n"
        else:
            ref_shape_directive = "2. 配色方案：不要照抄参考图配色，必须根据产品自行设计配色方案\n"

        ref_directive = f"【参考图使用规则】除了总表，你还收到了 {ref_count} 张参考图的高清原图（第{ref_img_start}{'-'+str(ref_img_end) if ref_count>1 else '张'}）。你必须仔细查看这些独立传来的参考图原图（不要依赖总表中的缩略图），{'严格以参考图为准' if rw==100 else '适度参考' if rw==60 else '仅参考版式形状'}，将参考图的以下元素应用到10张图的设计中：\n"
        ref_directive += f"1. 排版布局与图形形状{'（必须精确还原，这是最重要的参考维度）' if rw==100 else '（可参考运用，字体风格也可参考）' if rw==60 else '（可少量参考运用，但不要参考参考图的字体风格）'}：\n"
        ref_directive += "   - 几何色块形状类型：仔细观察参考图中使用了哪些形状——波浪形色块、圆形/椭圆形、方形/矩形卡片、三角形/多边形、斜切色块、不规则有机形状——逐一识别并列出\n"
        ref_directive += "   - 色块的层次关系：叠加顺序、透明度、大小比例、画面中的位置分布\n"
        ref_directive += "   - 线条与分隔元素：曲线分隔、直线分隔、点线虚线装饰、渐隐线条的具体形态\n"
        ref_directive += "   - 文字区域形状：文字底框形状（圆角矩形/胶囊形/下划线色块等）、标题与正文的层级排版\n"
        ref_directive += "   - 整体排版骨架：产品在画面中的位置和占比、文字装饰的分布密度、留白区域布局\n"
        ref_directive += ref_shape_directive
        ref_directive += "在统一风格规范中，必须逐项列出你从参考图中提取到的具体形状和排版骨架，不要用\"现代简约风格\"这类笼统描述替代具体的形状分析。\n"
        ref_directive += "注意：排版布局和配色仅约束排版层面的设计，场景内容和产品外观不受其影响。\n\n"
    else:
        color_directive = f"【自主配色设计】排版配色栏未填写且无参考图，请根据产品信息（品牌调性、目标市场={sales_country}、产品特点）自行设计一套适合的排版配色方案。\n"
        color_directive += "请直接在统一风格规范中给出你设计的具体配色方案（含色值或色名）。\n\n"

    sp_desc_parts = []
    for spot in filled_spots:
        sp_desc_parts.append(f"第{spot['display_number']}张图 → {spot['text']}")
    sp_mapping_desc = "\n".join(sp_desc_parts)

    llm_output_format_parts = []
    for spot in filled_spots:
        llm_output_format_parts.append(
            f"【图片{spot['display_number']}标题】\n产品特征锚点（全部列出）：...\n产品外观摘要：...\n本图设计方案：...\n文字内容（每张图13-30个单词/词，列出本图中将出现的所有文字，并标注总词数）：...\n"
        )
    llm_output_format = "\n".join(llm_output_format_parts)

    active_count = len(filled_spots)

    llm_prompt = (
        "【收到的图片说明】\n"
        f"你一共收到 {total_images} 张图片：\n"
        "第1张 = 信息总表（包含产品名称、销售国家、卖点、参考图缩略图、产品图缩略图——仅供参考信息，不要用缩略图分析视觉细节）\n"
        f"{prod_desc}\n"
        f"{ref_desc}\n\n"
        "你是一位专业的电商产品图片设计专家。请根据以上信息，设计一套10张风格统一的电商产品图片。\n\n"
        "核心要求：这10张图片必须是一套风格统一的图集，它们在排版风格、颜色搭配、图案形状风格、字体颜色格式风格上必须高度一致，让人一眼看出是同一套图。\n\n"
        + color_directive + ref_directive
        + (f"【参考权重 {ref_weight.split('%')[0]}%】\n" if has_refs else "")
        + ("请按以下两大部分输出：\n\n"
           "第一部分：【统一风格规范】—— 设计一套统一的视觉风格规范，必须包含以下内容：\n"
           "- 排版主色调与辅助色（具体到色值或色名，如：主色#2C3E50深蓝灰，辅助色#ECF0F1浅灰白，点缀色#E74C3C朱红）——仅用于排版元素\n"
           "- 场景风格与背景（描述每张图的真实拍摄场景，如：北欧风厨房自然光环境/户外阳光草坪/影棚柔光背景 等，必须是真实摄影级别的场景，不受排版配色影响）\n"
           "- 排版布局规范（如：产品居中偏左，文字信息右侧排列；或产品占画面60%上方，卖点文字下方横排）\n"
           "- 字体风格（如：无衬线粗体白色大标题+细体灰色副标题；或衬线体优雅排版）——字体必须超大加粗，配合13-30词的文字限制，让每个词都足够醒目\n"
           "- 装饰元素与图形风格（如：圆角矩形信息卡片、细线条分隔、圆点标注；或极简无装饰）——仅用于排版层面\n"
           "- 整体调性（如：高端简约/活泼明快/专业科技/温馨居家，一句话定调）\n"
           "- 光影风格（如：柔和自然光/影棚均匀布光/戏剧性侧光）——用于场景，追求真实摄影品质\n\n"
           "【重要：卖点与图片的1:1映射关系】\n"
           f"用户已将每个卖点/内容指定给了对应的图片编号，以下是映射表（{active_count}张图，只生成这些编号）：\n"
           + sp_mapping_desc + "\n\n"
           f"第二部分：在统一风格规范之下，为这 {active_count} 张图片分别设计方案。每张图片的方案必须遵循上面映射表中该编号对应的卖点/内容，直接使用用户指定的卖点和表达方向。\n\n"
           "要求：\n"
           f"1. 严格按照上面的1:1映射表，只生成上述 {active_count} 张图，每张图聚焦该编号对应的卖点/内容，不要生成未列出的编号\n"
           "2. 每张图片的方案直接描述本张图的场景和内容\n"
           "3. 方案包括：本张图场景（必须是真实拍摄级别的场景描述，如具体环境、光线条件、氛围，不要用排版配色来渲染场景）、构图位置、具体展示的卖点、文字内容（每张图文字总量13-30个单词，不要低于13个也不要超过30个，只保留最核心的卖点关键词和必要信息）、与统一风格的呼应\n"
           f"4. 销售国家为{sales_country}，设计内容及语言必须遵循销售国家本土的习惯和特征\n"
           "5. 不需要白底主图和尺寸规格图，聚焦卖点展示、使用场景、功能展示、细节特写等\n"
           "6. 场景内容和产品外观绝对不受排版配色和参考图配色/布局的影响，场景必须是真实摄影级别的画面\n"
           "7. 每张图的标题请用\"第N张：[卖点关键词]\"格式，如\"第1张：北欧简约客厅场景\"\n"
           "8. 【文字数量限制 — 极其重要】每张图片中出现的文字总量必须控制在13-30个单词（中文13-30个词/字组）之间。不要低于13个，也不要超过30个。只保留最核心的卖点关键词、产品名称和必要信息，删除一切多余的描述性文字、长句、段落。用最精简的短词组替代长句子。例如：不要写\"Our premium stainless steel water bottle keeps your drinks cold for 24 hours\"，只写\"24H Cold\"。在\"文字内容\"字段中必须逐条列出本图将出现的所有文字，并标注总词数。\n\n"
           "请严格按照以下格式输出，只输出这 " + str(active_count) + " 张图的方案：\n\n"
           "【统一风格规范】\n排版主色调：...\n排版辅助色：...\n参考图形状提取（如有参考图）：...\n场景风格与背景：...\n排版布局：...\n字体风格：...\n装饰元素：...\n整体调性：...\n光影风格：...\n\n"
           + llm_output_format)
    )
    return llm_prompt


# ==================== 主流程 ====================
def process_record(record_id):
    """处理单条记录（完整跑图流程）"""
    feishu = get_feishu_client()

    # 获取记录详情
    record = feishu.get_record(record_id)
    fields = record.get("fields", {})

    # 读取产品信息
    product_name = get_field_value(fields, "品名", "")
    sales_country = get_field_value(fields, "销售国家", "")
    image_size = get_field_value(fields, "生图分辨率", "2K（高清）")
    aspect_ratio = get_field_value(fields, "生图宽高比", "1:1")
    layout_main_color = get_field_value(fields, "排版主色", "")
    layout_aux_color = get_field_value(fields, "排版辅助色", "")
    ref_weight = get_field_value(fields, "参考权重", "100%（严格参考）")

    if not product_name:
        raise Exception("缺少品名")
    if not sales_country:
        raise Exception("缺少销售国家")

    # 读取卖点和纯场景
    selling_points = []
    filled_spots = []
    for i in range(1, 11):
        sp = get_field_value(fields, f"卖点{i}", "")
        pure = get_field_value(fields, f"纯场景{i}", "否")
        selling_points.append(sp)
        if sp and str(sp).strip():
            filled_spots.append({
                "original_index": i - 1,
                "display_number": i,
                "text": str(sp).strip(),
                "pure_scene": str(pure) == "是"
            })

    if not filled_spots:
        raise Exception("未填写任何卖点")

    # 创建工作目录
    work_dir = tempfile.mkdtemp(prefix=f"ecom_gen_{record_id}_", dir=TEMP_DIR)
    log(f"工作目录: {work_dir}")

    try:
        # 下载产品图
        prod_images_data = fields.get("产品图", [])
        if not prod_images_data:
            raise Exception("未上传产品图")

        prod_images = []
        prod_base64s = []
        for idx, att in enumerate(prod_images_data[:4]):
            file_token = att.get("file_token") if isinstance(att, dict) else att
            tmp_url = att.get("tmp_url") if isinstance(att, dict) else None
            save_path = os.path.join(work_dir, f"prod_{idx+1}.jpg")
            try:
                feishu.download_attachment(file_token, save_path, record_id=record_id, tmp_url=tmp_url, field_id="fld0qtJ498")
                b64, img = compress_image(save_path, 1024, 82)
                prod_base64s.append(b64)
                prod_images.append(save_path)
                log(f"产品图{idx+1}下载并压缩完成")
            except Exception as e:
                log(f"产品图{idx+1}下载失败: {e}", "warn")

        if not prod_images:
            raise Exception("产品图下载失败")

        # 下载参考图
        ref_images_data = fields.get("参考图", [])
        ref_images = []
        ref_base64s = []
        for idx, att in enumerate(ref_images_data[:10]):
            file_token = att.get("file_token") if isinstance(att, dict) else att
            tmp_url = att.get("tmp_url") if isinstance(att, dict) else None
            save_path = os.path.join(work_dir, f"ref_{idx+1}.jpg")
            try:
                feishu.download_attachment(file_token, save_path, record_id=record_id, tmp_url=tmp_url, field_id="fld16BFz3m")
                b64, img = compress_image(save_path, 1024, 82)
                ref_base64s.append(b64)
                ref_images.append(save_path)
                log(f"参考图{idx+1}下载并压缩完成")
            except Exception as e:
                log(f"参考图{idx+1}下载失败: {e}", "warn")

        # ===== Step 1: 生成参考图表格 =====
        log("Step 1: 生成参考图表格...")
        table_path = generate_table_image(product_name, sales_country, selling_points, ref_images, work_dir)
        with open(table_path, "rb") as f:
            table_base64 = base64.b64encode(f.read()).decode('utf-8')
        log("Step 1: 表格生成完成", "success")

        # ===== Step 2: LLM 分析 =====
        log("Step 2: 调用 LLM 分析产品外观，生成设计方案...")
        llm_prompt = build_llm_prompt(
            product_name, sales_country, selling_points, filled_spots,
            len(prod_images), len(ref_images),
            layout_main_color, layout_aux_color, ref_weight
        )
        all_extra_images = prod_base64s + ref_base64s
        llm_text = call_llm(table_base64, llm_prompt, all_extra_images)

        parsed = parse_llm_output(llm_text)
        unified_style = parsed["style"]
        raw_images = parsed["images"][:len(filled_spots)]

        image_types = [None] * 10
        for mi, img in enumerate(raw_images):
            spot = filled_spots[mi]
            img["original_index"] = spot["original_index"]
            img["pure_scene"] = spot["pure_scene"]
            image_types[spot["original_index"]] = img

        log(f"Step 2: LLM 分析完成，解析出 {len(raw_images)} 个设计方案", "success")

        if len(raw_images) == 0:
            raise Exception("LLM 未返回有效的设计方案")

        # ===== Step 3: 批量生成图片 =====
        log(f"Step 3: 并行生成 {len(raw_images)} 张产品图...")
        results = [None] * 10

        def gen_single(idx):
            gtype = image_types[idx]
            if not gtype:
                return idx, None, None
            prompt = build_image_prompt(
                product_name, idx, gtype["desc"], prod_base64s,
                unified_style, ref_weight, layout_main_color, layout_aux_color,
                gtype.get("pure_scene", False)
            )
            try:
                url = call_image_gen(prompt, prod_base64s, aspect_ratio, image_size)
                log(f"[{gtype['label']}] 生成完成", "success")
                return idx, url, None
            except Exception as e:
                log(f"[{gtype['label']}] 生成失败: {e}", "error")
                return idx, None, str(e)

        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_IMAGES) as executor:
            futures = []
            for gi in range(10):
                if image_types[gi]:
                    futures.append(executor.submit(gen_single, gi))
            for future in as_completed(futures):
                idx, url, error = future.result()
                results[idx] = {"url": url, "error": error}

        success_count = sum(1 for r in results if r and r.get("url"))
        fail_count = sum(1 for r in results if r and r.get("error"))
        log(f"Step 3: 全部生成完成，成功 {success_count} 张，失败 {fail_count} 张", "success")

        if success_count == 0:
            raise Exception("所有图片生成失败")

        # ===== 逐张下载并上传图片到飞书表格 =====
        log("正在逐张上传图片到飞书表格...")
        safe_name = sanitize_filename(product_name)
        upload_success_count = 0
        upload_fail_count = 0

        for idx in range(10):
            r = results[idx]
            if not r or not r.get("url"):
                continue
            url = r["url"]
            label = sanitize_filename(image_types[idx]["label"] if image_types[idx] else f"image_{idx+1}")
            ext = get_file_ext_from_url(url)
            img_filename = f"{safe_name}_{str(idx+1).zfill(2)}_{label}.{ext}"
            img_path = os.path.join(work_dir, img_filename)

            # 下载图片
            try:
                if url.startswith("data:"):
                    parts = url.split(',', 1)
                    img_data = base64.b64decode(parts[1])
                    with open(img_path, "wb") as f:
                        f.write(img_data)
                else:
                    resp = requests.get(url, timeout=60, proxies=NO_PROXY)
                    if not resp.ok:
                        log(f"图片 {idx+1} 下载失败: HTTP {resp.status_code}", "warn")
                        upload_fail_count += 1
                        continue
                    with open(img_path, "wb") as f:
                        f.write(resp.content)
                log(f"图片 {idx+1} 下载完成: {os.path.getsize(img_path)/1024:.0f}KB")
            except Exception as e:
                log(f"图片 {idx+1} 下载失败: {e}", "warn")
                upload_fail_count += 1
                continue

            # 上传到飞书表格（追加到结果ZIP字段）
            try:
                feishu.append_attachment(record_id, "结果ZIP", img_path)
                upload_success_count += 1
                log(f"图片 {idx+1} 上传成功 ({upload_success_count}/{success_count})", "success")
            except Exception as e:
                log(f"图片 {idx+1} 上传失败: {e}", "warn")
                upload_fail_count += 1

        log(f"图片上传完成：成功 {upload_success_count} 张，失败 {upload_fail_count} 张", "success")

        if upload_success_count == 0:
            raise Exception("所有图片上传失败")

        # 更新状态为已完成
        if upload_fail_count > 0:
            feishu.update_record_status(record_id, "部分成功", f"成功{upload_success_count}张，失败{upload_fail_count}张")
            log(f"记录 {record_id} 部分成功：成功 {upload_success_count} 张，失败 {upload_fail_count} 张", "warn")
        else:
            feishu.update_record_status(record_id, "已完成")
            log(f"记录 {record_id} 处理完成！成功 {success_count} 张", "success")

        return True

    except Exception as e:
        log(f"处理记录 {record_id} 时出错: {e}", "error")
        import traceback
        traceback.print_exc()
        try:
            feishu.update_record_status(record_id, "失败", str(e)[:500])
        except:
            pass
        raise
    finally:
        try:
            shutil.rmtree(work_dir)
        except:
            pass
