"""
제주도 DEM + 물결치는 바다 + 하늘을 이동하는 태양  (Blender 5.x 용)

실행 방법
  1) Blender 실행 → 상단 [Scripting] 탭 → [Open] 으로 이 파일 열기 → [Run Script] (Alt+P)
  2) 3D 뷰포트에서 카메라 시점(숫자패드 0)으로 보고, Z 키 → [Rendered] 로 미리보기
  3) 애니메이션 렌더링: [Render] → [Render Animation] (Ctrl+F12) → //render/ 폴더에 PNG 저장

DEM(고도 데이터)
  "ONLINE"     : AWS 공개 지형 타일(Terrarium)에서 제주도 영역을 자동으로 내려받아
                 이 파일 옆 dem_tiles 폴더에 저장합니다. (처음 한 번만 인터넷 필요, 약 0.6MB)
  "FILE"       : 직접 받은 DEM 높이맵(16비트 PNG/TIFF)을 사용합니다.
  "PROCEDURAL" : 인터넷/파일 없이 제주도 모양을 수식으로 만듭니다.
  ONLINE 이나 FILE 이 실패하면 자동으로 PROCEDURAL 로 대신 만듭니다.
"""

import bpy
import math
import os
import random
import tempfile
import urllib.request
from array import array
from mathutils import Vector

# ============================================================
# 설정 (여기만 바꿔도 됩니다)
# ============================================================
DEM_SOURCE = "ONLINE"            # "ONLINE" | "FILE" | "PROCEDURAL"
DEM_FILE = r""                   # FILE 일 때 높이맵 경로
DEM_FILE_MAX_HEIGHT_M = 1950.0   # 높이맵의 흰색(최댓값)이 몇 m 인지
DEM_FILE_PIXEL_M = 100.0         # 높이맵 한 픽셀이 몇 m 인지

UNIT_M = 10.0          # 블렌더 1칸 = 10 m  (섬이 73 km 라서 축소)
VERT_EXAG = 1.3        # 높이 과장 배율 (1.0 = 실제 비율)

FPS = 24
FRAMES = 240           # 240 프레임 = 10초

# 태양 경로: (프레임, 방위각°, 고도°)  방위각: 0=북, 90=동, 180=남
SUN_PATH = [
    (1,   72.0,  0.8),   # 수평선 위로 막 떠오르는 해
    (120, 79.0,  7.0),
    (240, 86.0, 14.0),   # 점점 높이 떠오르며 오른쪽으로 이동
]

# 카메라: 서귀포 앞바다 위에서 동쪽(해 뜨는 쪽)을 바라봄. 왼쪽에 한라산이 보임
CAM_POS_M = (-12000.0, -23000.0, 12.0)   # 섬 중심 기준 (동쪽 m, 북쪽 m, 해발 m)
CAM_AZIMUTH = 58.0     # 카메라가 바라보는 방위각°
CAM_PITCH = 3.0        # 위/아래 기울기° (+면 하늘 쪽)
CAM_LENS_MM = 20.0

RENDER_ENGINE = "CYCLES"   # "CYCLES" (사실적) | "BLENDER_EEVEE" (빠름)
SAMPLES = 64
RES_X, RES_Y, RES_PERCENT = 1920, 1080, 50
OUTPUT_PATH = "//render/jeju_"

# 제주도 영역 (경도/위도)
LON_MIN, LON_MAX = 126.12, 127.00
LAT_MIN, LAT_MAX = 33.15, 33.60
LAT_C = (LAT_MIN + LAT_MAX) / 2


# ============================================================
# 1. 장면 초기화
# ============================================================
def clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.lights, bpy.data.cameras, bpy.data.worlds):
        for block in list(coll):
            if block.users == 0:
                coll.remove(block)


# ============================================================
# 2. DEM 불러오기 → 높이 격자(m) 만들기
#    결과: (heights[row][col], 픽셀 간격 m)   row 0 = 북쪽
# ============================================================
def read_image_pixels(img):
    buf = array("f", [0.0]) * (img.size[0] * img.size[1] * img.channels)
    img.pixels.foreach_get(buf)
    return buf


def dem_online(zoom=10, step=1):
    """AWS Terrarium 타일: 고도 = R*256 + G + B/256 - 32768 (m)"""
    n = 2 ** zoom

    def lonlat_to_px(lon, lat):
        x = (lon + 180.0) / 360.0 * n * 256
        lr = math.radians(lat)
        y = (1 - math.log(math.tan(lr) + 1 / math.cos(lr)) / math.pi) / 2 * n * 256
        return x, y

    x0, y0 = lonlat_to_px(LON_MIN, LAT_MAX)   # 북서
    x1, y1 = lonlat_to_px(LON_MAX, LAT_MIN)   # 남동
    tx0, ty0, tx1, ty1 = int(x0 // 256), int(y0 // 256), int(x1 // 256), int(y1 // 256)
    W, H = (tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256
    mosaic = [[0.0] * W for _ in range(H)]

    # 스크립트 옆 dem_tiles 폴더에 저장 (한 번 받으면 다음부터는 인터넷 없이 사용)
    here = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else ""
    if not here and bpy.data.texts and bpy.data.filepath:
        here = os.path.dirname(bpy.data.filepath)
    cache = os.path.join(here, "dem_tiles") if here else os.path.join(tempfile.gettempdir(), "jeju_dem_tiles")
    try:
        os.makedirs(cache, exist_ok=True)
    except OSError:
        cache = os.path.join(tempfile.gettempdir(), "jeju_dem_tiles")
        os.makedirs(cache, exist_ok=True)

    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            path = os.path.join(cache, f"{zoom}_{tx}_{ty}.png")
            if not os.path.exists(path):
                url = f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{zoom}/{tx}/{ty}.png"
                print("DEM 타일 다운로드:", url)
                req = urllib.request.Request(url, headers={"User-Agent": "blender-jeju-sea"})
                with urllib.request.urlopen(req, timeout=30) as r, open(path, "wb") as f:
                    f.write(r.read())
            img = bpy.data.images.load(path, check_existing=False)
            img.colorspace_settings.name = "Non-Color"
            px = read_image_pixels(img)
            ch = img.channels
            ox, oy = (tx - tx0) * 256, (ty - ty0) * 256
            for rb in range(256):                 # 블렌더 픽셀은 아래쪽 행부터
                row = oy + (255 - rb)
                base = rb * 256 * ch
                for c in range(256):
                    i = base + c * ch
                    r, g, b = round(px[i] * 255), round(px[i + 1] * 255), round(px[i + 2] * 255)
                    mosaic[row][ox + c] = r * 256 + g + b / 256 - 32768
            bpy.data.images.remove(img)

    # 제주도 영역만 잘라내고 step 간격으로 줄이기
    cx0, cy0 = int(x0 - tx0 * 256), int(y0 - ty0 * 256)
    cx1, cy1 = int(x1 - tx0 * 256), int(y1 - ty0 * 256)
    heights = [[mosaic[r][c] for c in range(cx0, cx1, step)] for r in range(cy0, cy1, step)]
    pixel_m = 156543.03392 * math.cos(math.radians(LAT_C)) / n * step
    return heights, pixel_m


def dem_file(path, max_px=600):
    img = bpy.data.images.load(path, check_existing=False)
    img.colorspace_settings.name = "Non-Color"
    w, h, ch = img.size[0], img.size[1], img.channels
    px = read_image_pixels(img)
    step = max(1, math.ceil(max(w, h) / max_px))
    scale = 1.0 if img.is_float and max(px[0::ch]) > 2.0 else DEM_FILE_MAX_HEIGHT_M   # float=m 단위, 정수=0~1
    heights = [[px[((h - 1 - r) * w + c) * ch] * scale for c in range(0, w, step)] for r in range(0, h, step)]
    bpy.data.images.remove(img)
    return heights, DEM_FILE_PIXEL_M * step


def dem_procedural(pixel_m=200.0):
    """제주도 모양 근사: 동서로 긴 타원형 순상화산 + 한라산 + 오름 + 성산일출봉"""
    rnd = random.Random(7)
    a, b, tilt = 36500.0, 15500.0, math.radians(8)
    peak = (-2000.0, -1500.0)          # 한라산은 중심에서 약간 남서쪽
    oreums = []
    for _ in range(160):               # 기생화산(오름)
        ang, rr = rnd.uniform(0, 2 * math.pi), rnd.uniform(0.15, 0.9) ** 0.8
        oreums.append((math.cos(ang) * a * rr, math.sin(ang) * b * rr, rnd.uniform(30, 140), rnd.uniform(300, 700)))
    oreums.append((33500.0, 4500.0, 180.0, 700.0))   # 성산일출봉

    W_m, H_m = 86000.0, 50000.0
    cols, rows = int(W_m / pixel_m), int(H_m / pixel_m)
    heights = []
    for r in range(rows):
        y = H_m / 2 - r * pixel_m
        row = []
        for c in range(cols):
            x = c * pixel_m - W_m / 2
            xr = x * math.cos(tilt) + y * math.sin(tilt)
            yr = -x * math.sin(tilt) + y * math.cos(tilt)
            d = math.hypot(xr / a, yr / b)
            if d >= 1.0:
                row.append(-40.0 * min(1.0, (d - 1.0) * 6))
                continue
            hgt = 900.0 * (1 - d) ** 1.25
            pd = math.hypot(x - peak[0], y - peak[1])
            hgt += 1050.0 * math.exp(-(pd / 6000.0) ** 2)                 # 한라산
            hgt -= 60.0 * math.exp(-(pd / 350.0) ** 2)                   # 백록담 분화구
            for ox, oy, oh, orad in oreums:
                dd = (x - ox) ** 2 + (y - oy) ** 2
                if dd < (orad * 3) ** 2:
                    hgt += oh * math.exp(-dd / orad ** 2) * min(1.0, (1 - d) * 8)
            row.append(hgt)
        heights.append(row)
    return heights, pixel_m


def smooth(heights, passes=1):
    """3x3 평균으로 DEM 을 부드럽게 (작은 섬이 가시처럼 솟는 것 방지)"""
    rows, cols = len(heights), len(heights[0])
    for _ in range(passes):
        out = [row[:] for row in heights]
        for r in range(1, rows - 1):
            for c in range(1, cols - 1):
                out[r][c] = (heights[r - 1][c - 1] + heights[r - 1][c] + heights[r - 1][c + 1] +
                             heights[r][c - 1] + 2 * heights[r][c] + heights[r][c + 1] +
                             heights[r + 1][c - 1] + heights[r + 1][c] + heights[r + 1][c + 1]) / 10
        heights = out
    return heights


def load_dem():
    try:
        if DEM_SOURCE == "ONLINE":
            h, px = dem_online()
            return (smooth(h), px), "ONLINE (AWS Terrarium)"
        if DEM_SOURCE == "FILE" and DEM_FILE:
            h, px = dem_file(bpy.path.abspath(DEM_FILE))
            return (smooth(h), px), "FILE"
    except Exception as e:
        print("DEM 불러오기 실패 → 수식으로 만든 제주도를 사용합니다:", e)
    return dem_procedural(), "PROCEDURAL"


# ============================================================
# 3. 섬 메쉬 만들기
# ============================================================
def build_island(heights, pixel_m):
    rows, cols = len(heights), len(heights[0])
    sx = pixel_m / UNIT_M
    verts = []
    for r in range(rows):
        for c in range(cols):
            hm = max(heights[r][c], -60.0)
            verts.append(((c - cols / 2) * sx, (rows / 2 - r) * sx, hm * VERT_EXAG / UNIT_M))
    faces = []
    for r in range(rows - 1):
        for c in range(cols - 1):
            i = r * cols + c
            faces.append((i, i + cols, i + cols + 1, i + 1))
    mesh = bpy.data.meshes.new("Jeju_DEM")
    mesh.from_pydata(verts, [], faces)
    mesh.shade_smooth()
    obj = bpy.data.objects.new("Jeju_Island", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(island_material())
    return obj


def island_material():
    mat = bpy.data.materials.new("Jeju_Land")
    nt = mat.node_tree
    nodes, links = nt.nodes, nt.links
    bsdf = nodes.get("Principled BSDF")
    geo = nodes.new("ShaderNodeNewGeometry")
    sep = nodes.new("ShaderNodeSeparateXYZ")
    mr = nodes.new("ShaderNodeMapRange")
    mr.inputs["From Min"].default_value = 0.0
    mr.inputs["From Max"].default_value = 1950.0 * VERT_EXAG / UNIT_M
    ramp = nodes.new("ShaderNodeValToRGB")
    el = ramp.color_ramp.elements
    el[0].position, el[0].color = 0.0, (0.55, 0.50, 0.38, 1)      # 해안 모래/현무암 해변
    el[1].position, el[1].color = 0.03, (0.16, 0.24, 0.08, 1)     # 들판·귤밭
    for pos, col in [(0.35, (0.06, 0.13, 0.05, 1)),               # 숲
                     (0.75, (0.20, 0.19, 0.12, 1)),               # 관목·고산
                     (1.0, (0.45, 0.42, 0.38, 1))]:               # 정상 바위
        e = el.new(pos)
        e.color = col
    links.new(geo.outputs["Position"], sep.inputs[0])
    links.new(sep.outputs["Z"], mr.inputs["Value"])
    links.new(mr.outputs["Result"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.85
    return mat


# ============================================================
# 4. 바다 (가까운 곳: Ocean 모디파이어 / 먼 곳: 큰 평면)
# ============================================================
def water_material(name, far=False):
    mat = bpy.data.materials.new(name)
    nt = mat.node_tree
    nodes, links = nt.nodes, nt.links
    bsdf = nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (0.004, 0.035, 0.055, 1)   # 깊은 남색 바다
    bsdf.inputs["Roughness"].default_value = 0.04 if not far else 0.08
    bsdf.inputs["IOR"].default_value = 1.333

    if not far:
        # Ocean 모디파이어가 만든 거품(foam)을 흰색으로 섞기
        attr = nodes.new("ShaderNodeAttribute")
        attr.attribute_name = "foam"
        ramp = nodes.new("ShaderNodeValToRGB")
        ramp.color_ramp.elements[0].position = 0.45
        ramp.color_ramp.elements[1].position = 0.9
        mix = nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        mix.inputs["A"].default_value = (0.004, 0.035, 0.055, 1)
        mix.inputs["B"].default_value = (0.85, 0.9, 0.92, 1)
        links.new(attr.outputs["Fac"], ramp.inputs["Fac"])
        links.new(ramp.outputs["Color"], mix.inputs["Factor"])
        links.new(mix.outputs["Result"], bsdf.inputs["Base Color"])
    else:
        # 먼 바다: 잔물결을 노멀(Bump)로 표현해서 햇빛이 반짝이게
        tc = nodes.new("ShaderNodeTexCoord")
        mapping = nodes.new("ShaderNodeMapping")
        mapping.inputs["Scale"].default_value = (1.0, 2.5, 1.0)       # 바람 방향으로 늘어진 물결
        noise = nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = 0.25
        noise.inputs["Detail"].default_value = 8.0
        bump = nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = 0.35
        bump.inputs["Distance"].default_value = 0.05
        links.new(tc.outputs["Object"], mapping.inputs["Vector"])
        links.new(mapping.outputs["Vector"], noise.inputs["Vector"])
        links.new(noise.outputs["Fac"], bump.inputs["Height"])
        links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
        # 물결이 시간에 따라 흐르도록 W(4D) 값 애니메이션
        noise.noise_dimensions = "4D"
        fc = noise.inputs["W"].driver_add("default_value")
        fc.driver.expression = f"frame / {FPS} * 0.15"
    return mat


def build_ocean(center_xy):
    # 가까운 바다: 물리 기반 파도 (단위: m → 오브젝트를 1/UNIT_M 로 축소)
    bpy.ops.mesh.primitive_plane_add(size=2)
    ocean = bpy.context.object
    ocean.name = "Ocean_Near"
    ocean.scale = (1 / UNIT_M,) * 3
    mod = ocean.modifiers.new("Ocean", "OCEAN")
    mod.geometry_mode = "GENERATE"
    mod.spatial_size = 100          # 파도 타일 100 m
    mod.repeat_x = mod.repeat_y = 14  # 1.4 km × 1.4 km
    mod.resolution = 12
    mod.viewport_resolution = 6
    mod.wind_velocity = 9.0
    mod.wave_scale = 1.2
    mod.choppiness = 1.1
    mod.wave_alignment = 0.35
    mod.wave_direction = math.radians(-60)
    mod.depth = 200
    mod.use_normals = True
    mod.use_foam = True
    mod.foam_layer_name = "foam"
    mod.foam_coverage = 0.15
    # 시간이 흐르면 파도가 움직이도록: time = 프레임 / FPS (초)
    fc = mod.driver_add("time")
    fc.driver.expression = f"frame / {FPS}"
    ocean.data.materials.append(water_material("Sea_Near"))

    # Ocean 모디파이어가 만든 바다의 중심을 카메라 앞에 맞추기
    bpy.context.view_layer.update()
    ev = ocean.evaluated_get(bpy.context.evaluated_depsgraph_get())
    corners = [ev.matrix_world @ Vector(c) for c in ev.bound_box]
    mid = sum(corners, Vector()) / 8
    ocean.location.x += center_xy[0] - mid.x
    ocean.location.y += center_xy[1] - mid.y
    ocean.data.shade_smooth()

    # 먼 바다: 수평선까지 이어지는 큰 평면 (파도 골보다 살짝 아래)
    bpy.ops.mesh.primitive_plane_add(size=60000, location=(0, 0, -0.2))
    far = bpy.context.object
    far.name = "Ocean_Far"
    far.data.materials.append(water_material("Sea_Far", far=True))
    return ocean, far


# ============================================================
# 5. 하늘 + 태양 (이동 애니메이션)
# ============================================================
def sun_vector(az_deg, el_deg):
    """방위각(0=북=+Y, 90=동=+X), 고도 → 태양 쪽 방향 벡터"""
    a, e = math.radians(az_deg), math.radians(el_deg)
    return Vector((math.sin(a) * math.cos(e), math.cos(a) * math.cos(e), math.sin(e)))


def sun_at(frame):
    """SUN_PATH 사이를 부드럽게(코사인) 보간"""
    pts = SUN_PATH
    if frame <= pts[0][0]:
        return pts[0][1:]
    for (f0, a0, e0), (f1, a1, e1) in zip(pts, pts[1:]):
        if frame <= f1:
            t = (frame - f0) / (f1 - f0)
            t = (1 - math.cos(t * math.pi)) / 2
            return a0 + (a1 - a0) * t, e0 + (e1 - e0) * t
    return pts[-1][1:]


def build_sky_and_sun():
    scn = bpy.context.scene
    world = bpy.data.worlds.new("Jeju_Sky")
    scn.world = world
    nt = world.node_tree
    nt.nodes.clear()
    sky = nt.nodes.new("ShaderNodeTexSky")
    types = [e.identifier for e in sky.bl_rna.properties["sky_type"].enum_items]
    sky.sky_type = next(t for t in ("MULTIPLE_SCATTERING", "NISHITA", "SINGLE_SCATTERING") if t in types)
    sky.sun_disc = True
    sky.sun_size = math.radians(0.8)
    sky.altitude = 10.0
    sky.air_density = 1.0
    sky.aerosol_density = 1.6       # 바닷가의 살짝 뿌연 공기
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Strength"].default_value = 0.35
    out = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])

    # 그림자를 만들어 줄 태양 조명 (하늘의 태양과 같은 방향)
    sun_data = bpy.data.lights.new("Sun", "SUN")
    sun_data.angle = math.radians(0.6)
    sun = bpy.data.objects.new("Sun", sun_data)
    scn.collection.objects.link(sun)

    # 10 프레임마다 키프레임: 하늘의 태양 + 조명을 함께 이동
    for f in list(range(1, FRAMES + 1, 10)) + [FRAMES]:
        az, el = sun_at(f)
        sky.sun_rotation = math.radians(az)
        sky.sun_elevation = math.radians(el)
        sky.keyframe_insert("sun_rotation", frame=f)
        sky.keyframe_insert("sun_elevation", frame=f)

        sun.rotation_euler = sun_vector(az, el).to_track_quat("Z", "Y").to_euler()
        sun.keyframe_insert("rotation_euler", frame=f)
        warm = min(1.0, el / 15.0)            # 낮을수록 붉은 햇빛
        sun_data.color = (1.0, 0.55 + 0.4 * warm, 0.3 + 0.6 * warm)
        sun_data.energy = 0.6 + 2.4 * math.sin(math.radians(max(el, 0.5))) ** 0.5
        sun_data.keyframe_insert("color", frame=f)
        sun_data.keyframe_insert("energy", frame=f)

        # 해가 높아질수록 하늘이 매우 밝아지므로 카메라 노출을 낮춤 (눈이 적응하듯이)
        scn.view_settings.exposure = -1.8 * min(1.0, el / 14.0) ** 0.7
        scn.view_settings.keyframe_insert("exposure", frame=f)
    return sky, sun


# ============================================================
# 6. 카메라 + 렌더 설정
# ============================================================
def build_camera():
    scn = bpy.context.scene
    cam_data = bpy.data.cameras.new("Camera")
    cam_data.lens = CAM_LENS_MM
    cam_data.clip_start = 0.05
    cam_data.clip_end = 80000
    cam = bpy.data.objects.new("Camera", cam_data)
    scn.collection.objects.link(cam)
    cam.location = (CAM_POS_M[0] / UNIT_M, CAM_POS_M[1] / UNIT_M, CAM_POS_M[2] / UNIT_M)
    cam.rotation_euler = (math.radians(90 + CAM_PITCH), 0, math.radians(-CAM_AZIMUTH))
    scn.camera = cam
    return cam


def setup_render():
    scn = bpy.context.scene
    scn.render.fps = FPS
    scn.frame_start, scn.frame_end = 1, FRAMES
    scn.render.resolution_x, scn.render.resolution_y = RES_X, RES_Y
    scn.render.resolution_percentage = RES_PERCENT
    scn.render.filepath = OUTPUT_PATH
    scn.render.image_settings.file_format = "PNG"
    try:
        scn.render.engine = RENDER_ENGINE
    except TypeError:
        scn.render.engine = "BLENDER_EEVEE"
    if scn.render.engine == "CYCLES":
        scn.cycles.samples = SAMPLES
        scn.cycles.use_denoising = True
        # GPU 가 있으면 사용
        try:
            prefs = bpy.context.preferences.addons["cycles"].preferences
            for dev_type in ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL"):
                try:
                    prefs.compute_device_type = dev_type
                    prefs.get_devices()
                    if any(d.type == dev_type for d in prefs.devices):
                        for d in prefs.devices:
                            d.use = True
                        scn.cycles.device = "GPU"
                        break
                except TypeError:
                    continue
        except Exception:
            pass
    scn.view_settings.view_transform = "AgX"


# ============================================================
# 실행
# ============================================================
def main():
    clear_scene()
    (heights, pixel_m), source = load_dem()
    top = max(max(r) for r in heights)
    print(f"DEM: {source}, 격자 {len(heights[0])} x {len(heights)}, 간격 {pixel_m:.0f} m, 최고 {top:.0f} m")
    build_island(heights, pixel_m)

    cam = build_camera()
    fwd = Vector((math.sin(math.radians(CAM_AZIMUTH)), math.cos(math.radians(CAM_AZIMUTH)), 0))
    center = cam.location.xy + fwd.xy * 55      # 카메라 앞 550 m 를 바다 중심으로
    build_ocean(center)
    build_sky_and_sun()
    setup_render()
    bpy.context.scene.frame_set(1)
    print("완료! 카메라 시점(숫자패드 0)으로 보고, Ctrl+F12 로 애니메이션을 렌더링하세요.")


if __name__ == "__main__":
    main()
