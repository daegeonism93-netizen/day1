"""
제주도 DEM(AWS Terrain Tiles, Terrarium 형식)을 받아 웹(three.js)용 데이터 파일로 변환합니다.
실행: blender -b --python export_dem_js.py
결과: ../jeju-dem.js  (window.JEJU_DEM = { w, h, pixelM, offset, data: base64(Uint16 LE) })
고도(m) = R*256 + G + B/256 - 32768
"""
import base64, math, os, struct, tempfile, urllib.request
import bpy
from array import array

LON_MIN, LON_MAX, LAT_MIN, LAT_MAX = 126.12, 127.00, 33.15, 33.60
ZOOM, STEP, OFFSET = 10, 2, 100

n = 2 ** ZOOM
def px(lon, lat):
    lr = math.radians(lat)
    return ((lon + 180) / 360 * n * 256,
            (1 - math.log(math.tan(lr) + 1 / math.cos(lr)) / math.pi) / 2 * n * 256)

x0, y0 = px(LON_MIN, LAT_MAX)
x1, y1 = px(LON_MAX, LAT_MIN)
tx0, ty0, tx1, ty1 = int(x0 // 256), int(y0 // 256), int(x1 // 256), int(y1 // 256)
W, H = (tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256
mosaic = [[0.0] * W for _ in range(H)]

cache = os.path.join(tempfile.gettempdir(), "jeju_dem_tiles")
os.makedirs(cache, exist_ok=True)
for ty in range(ty0, ty1 + 1):
    for tx in range(tx0, tx1 + 1):
        path = os.path.join(cache, f"{ZOOM}_{tx}_{ty}.png")
        if not os.path.exists(path):
            url = f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{ZOOM}/{tx}/{ty}.png"
            req = urllib.request.Request(url, headers={"User-Agent": "jeju-dem-export"})
            with urllib.request.urlopen(req, timeout=30) as r, open(path, "wb") as f:
                f.write(r.read())
        img = bpy.data.images.load(path, check_existing=False)
        img.colorspace_settings.name = "Non-Color"
        buf = array("f", [0.0]) * (256 * 256 * img.channels)
        img.pixels.foreach_get(buf)
        ch, ox, oy = img.channels, (tx - tx0) * 256, (ty - ty0) * 256
        for rb in range(256):
            row, base = oy + (255 - rb), rb * 256 * ch
            for c in range(256):
                i = base + c * ch
                mosaic[row][ox + c] = (round(buf[i] * 255) * 256 + round(buf[i + 1] * 255)
                                       + round(buf[i + 2] * 255) / 256 - 32768)
        bpy.data.images.remove(img)

cx0, cy0, cx1, cy1 = int(x0 - tx0 * 256), int(y0 - ty0 * 256), int(x1 - tx0 * 256), int(y1 - ty0 * 256)
hts = [[mosaic[r][c] for c in range(cx0, cx1, STEP)] for r in range(cy0, cy1, STEP)]

# 3x3 평균으로 부드럽게 (작은 섬이 가시처럼 솟는 것 방지)
rows, cols = len(hts), len(hts[0])
out = [r[:] for r in hts]
for r in range(1, rows - 1):
    for c in range(1, cols - 1):
        out[r][c] = (hts[r-1][c-1] + hts[r-1][c] + hts[r-1][c+1] + hts[r][c-1] + 2 * hts[r][c]
                     + hts[r][c+1] + hts[r+1][c-1] + hts[r+1][c] + hts[r+1][c+1]) / 10
hts = out

pixel_m = 156543.03392 * math.cos(math.radians((LAT_MIN + LAT_MAX) / 2)) / n * STEP
vals = [max(0, min(65535, round(v + OFFSET))) for row in hts for v in row]
b64 = base64.b64encode(struct.pack("<%dH" % len(vals), *vals)).decode()

dst = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "jeju-dem.js")
with open(dst, "w", encoding="utf-8") as f:
    f.write("// 제주도 DEM (AWS Terrain Tiles / Terrarium, zoom 10) - blender/export_dem_js.py 로 생성\n")
    f.write(f'window.JEJU_DEM = {{ w: {cols}, h: {rows}, pixelM: {pixel_m:.2f}, offset: {OFFSET}, data: "{b64}" }};\n')
print("EXPORT", dst, cols, rows, round(pixel_m, 1), "max", max(vals) - OFFSET, "bytes", os.path.getsize(dst))
