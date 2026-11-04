"""플랫 컬러 일러스트(쿠로이트)를 SVG 벡터로 변환한다.
순서: 4배 확대 → 색 양자화(K-means, Lab) → 라벨 맵 정리(작은 조각 제거·다수결) → 라벨별 윤곽 추출
      → 윤곽 스무딩 → 3차 베지어 곡선 SVG
사용: python3 -I vectorize.py in.png out.svg [K] [SCALE]
"""
import sys

import cv2
import numpy as np
from scipy import ndimage as ndi

src, dst = sys.argv[1], sys.argv[2]
K = int(sys.argv[3]) if len(sys.argv) > 3 else 16
SIG = float(sys.argv[6]) if len(sys.argv) > 6 else 7.0
TRANS_T = float(sys.argv[5]) if len(sys.argv) > 5 else 9.0
S = int(sys.argv[4]) if len(sys.argv) > 4 else 4

im = cv2.imread(src, cv2.IMREAD_UNCHANGED)
h0, w0 = im.shape[:2]
rgb0, a0 = im[..., :3], im[..., 3]

# 1) 확대 (색은 알파 가중으로 번짐 방지)
a_f = a0.astype(np.float32) / 255
rgb_pm = (rgb0.astype(np.float32) * a_f[..., None])
rgb_up = cv2.resize(rgb_pm, None, fx=S, fy=S, interpolation=cv2.INTER_CUBIC)
a_up = cv2.resize(a_f, None, fx=S, fy=S, interpolation=cv2.INTER_CUBIC)
rgb_up = rgb_up / np.maximum(a_up[..., None], 1e-3)
rgb_up = np.clip(rgb_up, 0, 255).astype(np.uint8)
H, W = rgb_up.shape[:2]

# 2) 실루엣: 알파를 부드럽게 한 뒤 0.5 임계값
a_s = cv2.GaussianBlur(a_up, (0, 0), S * 0.9)
sil = a_s > 0.5
# 작은 구멍(눈동자 하이라이트 등 알파 잡음)만 메우고, 큰 구멍(팔과 갈기 사이 빈 공간)은 투명으로 유지
filled = ndi.binary_fill_holes(sil)
holes = filled & ~sil
hn, hcc, hst, _ = cv2.connectedComponentsWithStats(holes.astype(np.uint8), connectivity=4)
small_h = np.zeros(hn, bool)
for i in range(1, hn):
    small_h[i] = hst[i, cv2.CC_STAT_AREA] < 120 * S * S
sil = sil | small_h[hcc]
# 작은 부유물 제거
n, cc, st, _ = cv2.connectedComponentsWithStats(sil.astype(np.uint8), connectivity=8)
keep = np.zeros(n, bool)
for i in range(1, n):
    if st[i, cv2.CC_STAT_AREA] > 200 * S * S:
        keep[i] = True
sil = keep[cc]

# 3) 색 양자화 (원본 해상도의 불투명 픽셀로 학습 → 확대본에 적용)
lab0 = cv2.cvtColor(rgb0, cv2.COLOR_BGR2LAB).astype(np.float32)
solid = a0 > 250
samples = lab0[solid]
if len(samples) > 120000:
    samples = samples[np.random.RandomState(0).choice(len(samples), 120000, replace=False)]
crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 60, 0.2)
_, _, centers = cv2.kmeans(samples, K, None, crit, 4, cv2.KMEANS_PP_CENTERS)

# 연회색(펜촉 등) 색 하나를 추가: K-means가 소수 색을 놓치는 경우 보정
gray_lab = cv2.cvtColor(np.uint8([[[206, 204, 204]]]), cv2.COLOR_BGR2LAB).astype(np.float32)[0]
centers = np.vstack([centers, gray_lab])
K = len(centers)

lab_up = cv2.cvtColor(rgb_up, cv2.COLOR_BGR2LAB).astype(np.float32)
# 가장자리 번짐을 줄이기 위해 양방향 필터로 살짝 정리
lab_up = cv2.bilateralFilter(lab_up, 0, 18, S * 1.5)
d = ((lab_up[:, :, None, :] - centers[None, None, :, :]) ** 2).sum(-1)
lab_map = d.argmin(-1).astype(np.int32)
# 거의 같은 색 라벨 병합 (예: 윤곽선 갈색 2종)
parent = list(range(K))
def find(x):
    while parent[x] != x:
        x = parent[x]
    return x
for i in range(K):
    for j in range(i + 1, K):
        if np.linalg.norm(centers[i] - centers[j]) < (16 if max(centers[i][0], centers[j][0]) < 30 else 9):
            parent[find(j)] = find(i)
remap = np.array([find(k) for k in range(K)])
lab_map = np.where(lab_map >= 0, remap[np.clip(lab_map, 0, K - 1)], -1)
lab_map[~sil] = -1

# 4) 라벨 맵 정리: 작은 조각은 주변 라벨로 흡수
MIN_AREA = int(10 * S * S)  # 원본 기준 약 10px² 미만 제거
for _ in range(3):
    bad = np.zeros((H, W), bool)
    for k in range(K):
        m = (lab_map == k).astype(np.uint8)
        if not m.any():
            continue
        n, cc, st, _ = cv2.connectedComponentsWithStats(m, connectivity=4)
        small = np.where(st[1:, cv2.CC_STAT_AREA] < MIN_AREA)[0] + 1
        if len(small):
            bad |= np.isin(cc, small)
    if not bad.any():
        break
    tmp = lab_map.copy()
    tmp[bad] = -2
    valid = tmp >= 0
    idx = ndi.distance_transform_edt(~valid, return_distances=False, return_indices=True)
    filled = tmp[idx[0], idx[1]]
    lab_map = np.where(bad, filled, lab_map)
    lab_map[~sil] = -1

# 4-a) 두 색 사이에 낀 '중간색' 라벨(경계 번짐)을 찾아 주변으로 흡수
def seg_dist(c, a, b):
    ab = b - a
    t = np.clip(np.dot(c - a, ab) / (np.dot(ab, ab) + 1e-9), 0.05, 0.95)
    return np.linalg.norm(c - (a + t * ab)), t

areas = np.array([(lab_map == k).sum() for k in range(K)])
# 빨강(갈기)과 어두운 윤곽선 사이 경계 번짐 색(어두운 빨강 띠)만 골라 흡수
red = int(np.argmax(centers[:, 1] * (areas > 200000)))      # a*가 가장 큰 큰 면적 라벨
darks = [k for k in range(K) if centers[k][0] < 30 and areas[k] > 150000]
trans = []
for k in range(K):
    if k == red or k in darks:
        continue
    if centers[k][1] < 15:      # 붉은 기가 없으면 건드리지 않음 (피부·털색 보존)
        continue
    dd = min(seg_dist(centers[k], centers[red], centers[d])[0] for d in darks)
    if dd < TRANS_T:
        trans.append(k)
print("dissolve:", [("#%02x%02x%02x" % tuple(cv2.cvtColor(np.uint8([[centers[k]]]), cv2.COLOR_LAB2RGB)[0, 0])) for k in trans])
if trans:
    bad = np.isin(lab_map, trans)
    tmp = lab_map.copy()
    tmp[bad] = -2
    idx = ndi.distance_transform_edt(~(tmp >= 0), return_distances=False, return_indices=True)
    lab_map = np.where(bad, tmp[idx[0], idx[1]], lab_map)
    lab_map[~sil] = -1

# 4-b) 안티앨리어싱 때문에 경계에 생긴 얇은 중간색 띠 제거 (두께 ≈1.5 원본px 미만)
THIN = 0.8 * S
bad = np.zeros((H, W), bool)
for k in range(K):
    m = (lab_map == k)
    if not m.any():
        continue
    dt = cv2.distanceTransform(m.astype(np.uint8), cv2.DIST_L2, 5)
    bad |= m & (dt < THIN)
tmp = lab_map.copy()
tmp[bad] = -2
valid = tmp >= 0
idx = ndi.distance_transform_edt(~valid, return_distances=False, return_indices=True)
lab_map = np.where(bad, tmp[idx[0], idx[1]], lab_map)
lab_map[~sil] = -1

# 라벨 경계 다듬기: 라벨별 마스크를 가우시안 → 최대값 선택 (모서리 계단 제거)
sm = np.full((K, H, W), -1.0, np.float32)
for k in range(K):
    m = (lab_map == k).astype(np.float32)
    if m.any():
        sm[k] = cv2.GaussianBlur(m, (0, 0), S * 0.9)
lab_map2 = sm.argmax(0).astype(np.int32)
lab_map2[~sil] = -1
lab_map = lab_map2

# 4-c) 셔츠 깃(넥타이 양옆) 좌우 대칭 보정: 왼쪽을 좌우반전해 오른쪽에 덮어쓴다 (4배 확대 좌표 기준)
if S == 4:
    AX, MX0, MY0, MY1 = 1070, 890, 1650, 1980
    left = lab_map[MY0:MY1, MX0:AX].copy()
    lab_map[MY0:MY1, AX + 1:AX + 1 + (AX - MX0)] = left[:, ::-1]
    sl = sil[MY0:MY1, MX0:AX].copy()  # 투명도(실루엣)도 같이 대칭
    sil[MY0:MY1, AX + 1:AX + 1 + (AX - MX0)] = sl[:, ::-1]

# 5) 대표 색: 라벨 영역의 중앙값 (원본 BGR)
def center_color(k):
    m = lab_map == k
    if not m.any():
        return None
    px = rgb_up[m]
    c = np.median(px, axis=0).astype(int)
    return "#%02x%02x%02x" % (c[2], c[1], c[0])


# 6) 윤곽 → 베지어
def smooth_closed(pts, sigma):
    pts = pts.astype(np.float64)
    if len(pts) < 8:
        return pts
    # 호 길이 기준 균등 리샘플
    seg = np.r_[0, np.cumsum(np.hypot(*np.diff(np.vstack([pts, pts[:1]]), axis=0).T))]
    total = seg[-1]
    n = max(int(total / 1.0), 8)
    t = np.linspace(0, total, n, endpoint=False)
    pp = np.vstack([pts, pts[:1]])
    x = np.interp(t, seg, pp[:, 0])
    y = np.interp(t, seg, pp[:, 1])
    out = []
    for arr in (x, y):
        out.append(ndi.gaussian_filter1d(arr, sigma, mode="wrap"))
    return np.stack(out, 1)


def simplify(pts, eps):
    c = pts.astype(np.float32).reshape(-1, 1, 2)
    a = cv2.approxPolyDP(c, eps, True).reshape(-1, 2)
    return a


def to_bezier(P, tension=0.5):
    n = len(P)
    if n < 3:
        return ""
    segs = [f"M{P[0][0]:.2f},{P[0][1]:.2f}"]
    for i in range(n):
        p0, p1, p2, p3 = P[(i - 1) % n], P[i], P[(i + 1) % n], P[(i + 2) % n]
        # 급한 모서리는 곡선화하지 않고 직선으로 유지 (뾰족한 털 끝 보존)
        def ang(a, b, c):
            v1, v2 = a - b, c - b
            cs = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-9)
            return cs
        c1 = p1 + (p2 - p0) * tension / 3 * (0.0 if ang(p0, p1, p2) > 0.5 else 1.0)
        c2 = p2 - (p3 - p1) * tension / 3 * (0.0 if ang(p1, p2, p3) > 0.5 else 1.0)
        segs.append(f"C{c1[0]:.2f},{c1[1]:.2f} {c2[0]:.2f},{c2[1]:.2f} {p2[0]:.2f},{p2[1]:.2f}")
    return "".join(segs) + "Z"


layers = []
for k in range(K):
    col = center_color(k)
    if col is None:
        continue
    m = (lab_map == k)
    area = int(m.sum())
    if area < MIN_AREA:
        continue
    # 아래 레이어와 틈이 생기지 않도록 아주 살짝(≈0.5원본px) 팽창
    mm = cv2.dilate(m.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=max(1, S // 4))
    mm &= sil.astype(np.uint8)
    cnts, hier = cv2.findContours(mm, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    d_parts = []
    for c in cnts:
        c = c.reshape(-1, 2)
        if len(c) < 12:
            continue
        sp = smooth_closed(c, sigma=SIG)
        sp = simplify(sp, 1.4)
        if len(sp) < 3:
            continue
        sp = sp / S  # 원본 좌표계로
        d_parts.append(to_bezier(sp))
    if d_parts:
        layers.append((area, col, "".join(d_parts)))

# 바닥층: 실루엣 전체를 윤곽선 색으로 깔아 층 사이 미세한 틈이 비치지 않게 함
dark_cols = [(a, c) for a, c, _ in layers if c in [center_color(k) for k in range(K) if centers[k][0] < 30]]
base_col = max(dark_cols)[1] if dark_cols else "#3a1006"
cn, _ = cv2.findContours(sil.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
base_d = "".join(to_bezier(simplify(smooth_closed(c.reshape(-1, 2), SIG), 1.4) / S) for c in cn if len(c) > 30)

# 큰 면적부터 아래에 깔고 작은 것을 위에 (배경색 큰 면 → 디테일)
layers.sort(key=lambda t: -t[0])
svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w0} {h0}" width="{w0}" height="{h0}">']
# 실루엣 전체를 가장 큰 색으로 깔아 가장자리 틈을 방지
svg.append('<g fill-rule="evenodd" stroke="none">')
svg.append(f'<path fill="{base_col}" d="{base_d}"/>')
for area, col, d in layers:
    svg.append(f'<path fill="{col}" d="{d}"/>')
svg.append("</g></svg>")
open(dst, "w").write("\n".join(svg))
print("layers", len(layers), "colors", [l[1] for l in layers])
