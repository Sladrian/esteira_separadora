"""Processa somente o recorte; não grava imagens nem mantém histórico de quadros."""
import cv2
import numpy as np
from core import SorterFault


class Vision:
    def __init__(self, cfg):
        self.cfg = cfg
        self.kernel = np.ones((3, 3), np.uint8)

    def inspect(self, frame):
        objects, hsv, occupied, labels = self._components(frame)
        if len(objects) > 1:
            raise SorterFault("Mais de um objeto na faixa; alimente uma tampinha por vez")
        if not objects:
            return None, {}, hsv, np.zeros_like(occupied)
        component = np.uint8(labels == objects[0]) * 255
        label, fractions = self._classify(hsv, component)
        return label, fractions, hsv, component

    def _components(self, frame):
        x, y, w, h = self.cfg["roi"]
        roi = frame[y:y+h, x:x+w]
        if roi.shape[:2] != (h, w):
            raise SorterFault("Dimensões do quadro incompatíveis com a ROI")
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        lo, hi = self.cfg["occupancy_hsv"]
        occupied = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
        occupied = cv2.morphologyEx(occupied, cv2.MORPH_OPEN, self.kernel)
        occupied = cv2.morphologyEx(occupied, cv2.MORPH_CLOSE, self.kernel)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(occupied, connectivity=8)
        objects = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= self.cfg["min_object_pixels"]]
        return objects, hsv, occupied, labels

    def _classify(self, hsv, component):
        area = cv2.countNonZero(component)
        fractions = {}
        for color, entry in self.cfg["colors"].items():
            mask = np.zeros_like(component)
            for lower, upper in entry["ranges"]:
                mask |= cv2.inRange(hsv, np.array(lower, np.uint8), np.array(upper, np.uint8))
            fractions[color] = cv2.countNonZero(cv2.bitwise_and(mask, component)) / area
        ranked = sorted(fractions, key=fractions.get, reverse=True)
        first, second = ranked[:2]
        good = (fractions[first] >= self.cfg["min_color_fraction"] and
                fractions[first]-fractions[second] >= self.cfg["winner_margin"])
        return first if good else "desconhecida", fractions

    def detect(self, frame):
        """Uma classificação por componente; a calibração inspect continua exigindo um só."""
        objects, hsv, _, labels = self._components(frame)
        x, y, _, _ = self.cfg["roi"]
        detections = []
        for index in objects:
            component = np.uint8(labels == index) * 255
            label, fractions = self._classify(hsv, component)
            points = cv2.findNonZero(component)
            bx, by, bw, bh = cv2.boundingRect(points)
            moments = cv2.moments(component, binaryImage=True)
            detections.append({"label": label, "fractions": fractions,
                               "center": (x+moments["m10"]/moments["m00"], y+moments["m01"]/moments["m00"]),
                               "box": (x+bx, y+by, bw, bh)})
        return detections


def annotate_objects(frame, roi, detections):
    result = annotate(frame, roi, f"{len(detections)} objeto(s)", {})
    for item in detections:
        x, y, w, h = item["box"]
        cv2.rectangle(result, (x, y), (x+w-1, y+h-1), (80, 220, 220), 1)
        cv2.putText(result, item["label"], (x, max(30, y-3)), cv2.FONT_HERSHEY_SIMPLEX,
                    .35, (80, 220, 220), 1)
    return result


def learn_ranges(hsv, mask, min_pixels):
    """Matiz circular; tolera até 30% de tons dispersos se houver um grupo dominante."""
    pixels = hsv[mask > 0]
    if len(pixels) < min_pixels:
        raise ValueError("Poucos pixels coloridos; coloque uma tampinha no centro da faixa")
    def bounds(sample):
        angles = sample[:, 0].astype(float) * (2*np.pi/180)
        center = np.arctan2(np.sin(angles).mean(), np.cos(angles).mean()) * 180/(2*np.pi)
        hue = (sample[:, 0].astype(float)-center+90) % 180 - 90
        low, high = np.percentile(hue, [3, 97])
        return center, low, high
    center, low, high = bounds(pixels)
    if high-low > 40:
        # Busca um intervalo compacto na roda de cores. Uma mistura equilibrada
        # continua sendo rejeitada; uma minoria de reflexos não amplia toda a faixa.
        histogram = np.bincount(pixels[:, 0], minlength=180)
        dominant = None
        # Prefere a menor faixa que explica a maioria: evita incluir o fundo
        # só porque ainda caberia no limite máximo de largura.
        for span in range(41):
            wrapped = np.r_[histogram, histogram[:span]] if span else histogram
            windows = np.convolve(wrapped, np.ones(span+1, dtype=int), mode="valid")
            start = int(np.argmax(windows))
            if windows[start] >= max(min_pixels, .70*len(pixels)):
                dominant = (pixels[:, 0].astype(int)-start) % 180 <= span
                break
        if dominant is None:
            raise ValueError("Amostra mistura cores/reflexos demais; use somente uma tampinha e luz uniforme")
        pixels = pixels[dominant]
        center, low, high = bounds(pixels)
        if high-low > 40:
            raise ValueError("Amostra sem uma faixa de cor distinta; confira iluminação e fundo")
    low, high = int(np.floor(center+low-5)), int(np.ceil(center+high+5))
    s, v = np.maximum([25, 25], np.percentile(pixels[:, 1:], 5, axis=0)-20).astype(int)
    if low//180 != high//180:
        hs = [(0, high % 180), (low % 180, 179)]
    else:
        hs = [(low % 180, high % 180)]
    return [[[int(a), int(s), int(v)], [int(b), 255, 255]] for a, b in hs]


def annotate(frame, roi, label, fractions):
    result = frame.copy()
    x, y, w, h = roi
    cv2.rectangle(result, (x, y), (x+w-1, y+h-1), (255, 255, 255), 1)
    text = (label or "vazio") + " " + " ".join(f"{k}:{v:.0%}" for k, v in fractions.items())
    cv2.putText(result, text, (4, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1)
    return result
