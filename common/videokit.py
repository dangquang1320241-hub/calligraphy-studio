#!/usr/bin/env python3
"""书法视频模板共用工具：图像预处理（只裁切+全局调色）、缓动、ffprobe 校验、联系表。
红线：只做裁切和逐像素全局色调映射（白平衡、对比），不做任何局部修补/生成，笔画像素关系不变。
"""
import json, subprocess, hashlib, math
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont

COMMON = Path(__file__).resolve().parent
DEFAULT_FONT = str(COMMON / 'fonts' / 'LXGWWenKai-Regular.ttf')
FALLBACK_FONT = '/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc'


def font(path=None, size=48):
    for p in [path, DEFAULT_FONT, FALLBACK_FONT]:
        if p and Path(p).exists():
            return ImageFont.truetype(p, size)
    raise FileNotFoundError('no CJK font')


def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def grade(img, crop=None, deyellow=0.35, contrast=1.10, brightness=1.0, paper_pct=70):
    """img: PIL RGB。crop=[x0,y0,x1,y1]（原图像素）。返回 float32 RGB 0..1。
    deyellow: 0=保留原纸色，1=纸色完全拉到中性；contrast: 以纸色为白点的线性对比；全局逐像素映射。"""
    if crop:
        img = img.crop(tuple(int(v) for v in crop))
    a = np.asarray(img.convert('RGB')).astype(np.float32) / 255.0
    lum = a.mean(2)
    paper = lum > np.percentile(lum, 40)
    pc = np.array([np.percentile(a[..., c][paper], paper_pct) for c in range(3)])
    neutral = pc.mean()
    target = pc * (1 - deyellow) + neutral * deyellow
    gain = target / pc
    a = a * gain
    white = (target.mean())
    # 对比：以纸色为参考点向下拉伸（墨更黑、纸不过曝）
    a = white - (white - a) * contrast
    a = a * brightness
    return np.clip(a, 0, 1)


def paper_mask(img, crop=None, feather=1.5):
    """纸张抠底遮罩（只决定纸外墙面用背景色替代，纸内像素原样保留）：
    纸=偏暖且较亮的最大连通区域，闭运算+填洞，保证纸内墨迹全部在遮罩内。返回 float32 0..1。"""
    import cv2
    if crop:
        img = img.crop(tuple(int(v) for v in crop))
    a = np.asarray(img.convert('RGB')).astype(np.int16)
    warm = ((a[..., 0] - a[..., 2]) > 22) & (a.mean(2) > 110)
    P = 40  # 先四周补零，避免贴边区域在形态学/轮廓填充时被误吞
    m = np.pad(warm.astype(np.uint8) * 255, P)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k, iterations=2)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
    idx = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    m = (lab == idx).astype(np.uint8) * 255
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    full = np.zeros_like(m)
    cv2.drawContours(full, cnts, -1, 255, thickness=-1)  # 填洞：纸内一切（含墨）保留
    f = full[P:-P, P:-P].astype(np.float32) / 255
    if feather > 0:
        f = cv2.GaussianBlur(f, (0, 0), feather)
    return f


def ease(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def ease_io_cubic(x):
    x = min(max(x, 0.0), 1.0)
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def ffprobe_report(mp4):
    def run(args):
        return subprocess.run(args, capture_output=True, text=True).stdout.strip()
    v = run(['ffprobe', '-v', 'error', '-count_frames', '-select_streams', 'v:0', '-show_entries',
             'stream=codec_name,width,height,r_frame_rate,nb_read_frames,pix_fmt', '-of', 'json', mp4])
    a = run(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries',
             'stream=codec_name,sample_rate,channels,duration', '-of', 'json', mp4])
    d = run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration,size,bit_rate', '-of', 'json', mp4])
    dec = subprocess.run(['ffmpeg', '-v', 'error', '-i', mp4, '-f', 'null', '-'], capture_output=True, text=True)
    lufs = subprocess.run(['ffmpeg', '-hide_banner', '-i', mp4, '-af', 'ebur128=peak=true', '-f', 'null', '-'],
                          capture_output=True, text=True).stderr
    summ = {}
    tail = lufs[lufs.rfind('Summary:'):]
    for line in tail.splitlines():
        line = line.strip()
        if line.startswith('I:'):
            summ['integrated_lufs'] = float(line.split()[1])
        if line.startswith('Peak:'):
            summ['true_peak_dbfs'] = float(line.split()[1])
    rep = {'video': json.loads(v).get('streams', [{}])[0], 'audio': json.loads(a).get('streams', [{}])[0],
           'format': json.loads(d).get('format', {}), 'decode_ok': dec.returncode == 0 and not dec.stderr.strip(),
           'loudness': summ, 'sha256': sha256(mp4)}
    return rep


def grab_frames(mp4, times, outdir):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, t in enumerate(times):
        p = outdir / f'kf_{i:02d}_{t:06.2f}.png'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{t:.3f}', '-i', mp4, '-frames:v', '1', str(p)], check=True)
        paths.append(p)
    return paths


def contact_sheet(frame_paths, labels, out, cols=4, thumb_w=360, safe=(0.80, 0.85)):
    """联系表：每张缩略图叠加平台 UI 安全线（红：底部 y=80%，右侧 x=85%）。"""
    ims = [Image.open(p).convert('RGB') for p in frame_paths]
    W, H = ims[0].size
    th = int(thumb_w * H / W)
    rows = math.ceil(len(ims) / cols)
    lab_h = 44
    sheet = Image.new('RGB', (cols * (thumb_w + 16) + 16, rows * (th + lab_h + 16) + 16), (40, 40, 40))
    f = font(None, 26)
    d = ImageDraw.Draw(sheet)
    for i, (im, lab) in enumerate(zip(ims, labels)):
        r, c = divmod(i, cols)
        x = 16 + c * (thumb_w + 16)
        y = 16 + r * (th + lab_h + 16)
        t = im.resize((thumb_w, th), Image.LANCZOS)
        td = ImageDraw.Draw(t)
        yy = int(th * safe[0]); xx = int(thumb_w * safe[1])
        for k in range(0, thumb_w, 12):
            td.line([(k, yy), (k + 6, yy)], fill=(255, 60, 60), width=2)
        for k in range(0, th, 12):
            td.line([(xx, k), (xx, k + 6)], fill=(255, 60, 60), width=2)
        sheet.paste(t, (x, y))
        d.text((x, y + th + 6), lab, fill=(235, 235, 235), font=f)
    sheet.save(out, quality=90)
    return out
