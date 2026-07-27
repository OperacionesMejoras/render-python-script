"""
dental_scan_reporter.py
-----------------------
Dependencias:
    pip install requests

Uso rápido:
    from dental_scan_reporter import DentalScanReporter

    reporter = DentalScanReporter()

    result = reporter.process(
        stl_paths=["upper.stl", "lower.stl"],
        nombre="Paciente Juan Pérez",        # opcional
        referencia="CASO-001",               # opcional — ID de paciente o caso
        external_id="bot-scan-20260408",
    )

    # Guardar log en cualquier momento, con la ruta que quieras:
    reporter.save_log("logs/reporte_20260612.json")

La función process() consulta automáticamente GET /function en Xano
para decidir qué camino tomar:
  "report"    → Railway render → POST /report/create
  "full_rail" → POST /models/full_rail con la geometría STL en bruto

Lógica de vistas por cantidad de STLs:
  2 STLs (upper + lower) → vistas de oclusión (ambos juntos) +
                            6 vistas cardinales de cada maxilar aislado
  1 STL  (upper o lower) → solo 6 vistas cardinales de ese maxilar
"""

import json
import struct
import datetime
import requests
import numpy as np
from pathlib import Path
from typing import Union, Optional


class DentalScanReporter:
    """
    Pipeline: STL → Railway render server → Xano API.

    El enrutamiento lo controla Xano vía GET /function, que devuelve
    "report" o "full_rail". Esto permite cambiar el comportamiento
    desde Xano sin tocar código en el bot.

    Parámetros
    ----------
    railway_url   : URL del servidor Railway. Fallback automático a IP interna.
    xano_base_url : Base URL de la API de Xano. Normalmente no hace falta cambiarla.
    """

    RAILWAY_DEFAULT  = "https://aprobador.keepsmiling.click"
    RAILWAY_FALLBACK = "http://192.168.2.132:3000"
    XANO_BASE        = "https://xoas-qetn-dh88.n7c.xano.io/api:xiRf2PrY"

    # Vistas de oclusión (ambos maxilares juntos).
    OCCLUSION_VIEWS = ["left", "front", "right", "back"]

    # Vistas cardinales de un maxilar aislado (upper o lower por separado).
    ARCH_CARDINAL_VIEWS = ["top", "bottom", "left", "front", "right", "back"]

    def __init__(
        self,
        railway_url: str = None,
        xano_base_url: str = None,
    ):
        self.railway_base  = (railway_url or self.RAILWAY_DEFAULT).rstrip("/")
        self.railway_url   = self.railway_base + "/render"
        self.xano_base     = (xano_base_url or self.XANO_BASE).rstrip("/")
        self.xano_function = self.xano_base + "/function"
        self.xano_report   = self.xano_base + "/report/create"
        self.xano_fullrail = self.xano_base + "/models/full_rail"
        self._last_log     = None  # se actualiza después de cada process()

    # ──────────────────────────────────────────────
    # Logging
    # ──────────────────────────────────────────────

    def save_log(self, path: str) -> Path:
        """
        Guarda el log del último process() en el archivo indicado.
        Llamar después de process().

        Ejemplo:
            result = reporter.process(...)
            reporter.save_log("logs/reporte_paciente_123.json")
        """
        if self._last_log is None:
            raise RuntimeError("No hay log disponible. Llamá a process() antes de save_log().")

        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(self._last_log, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        print(f"[DentalScanReporter] Log guardado: {out}")
        return out

    # ──────────────────────────────────────────────
    # STL parsing
    # ──────────────────────────────────────────────

    @staticmethod
    def parse_stl(path: Union[str, Path]) -> dict:
        """
        Lee un archivo STL (binario o ASCII) y retorna el dict de modelo
        que espera el servidor Railway: {"vertices": [x, y, z, ...]}.
        """
        data = Path(path).read_bytes()

        try:
            head     = data[:256].decode("ascii", errors="ignore")
            is_ascii = head.strip().lower().startswith("solid") and "facet" in head
        except Exception:
            is_ascii = False

        if is_ascii:
            try:
                return DentalScanReporter._parse_ascii_stl(
                    data.decode("ascii", errors="ignore")
                )
            except Exception:
                pass

        return DentalScanReporter._parse_binary_stl(data)

    @staticmethod
    def _parse_binary_stl(data: bytes) -> dict:
        num_triangles = struct.unpack_from("<I", data, 80)[0]
        vertices = []
        offset   = 84
        for _ in range(num_triangles):
            offset += 12
            for _ in range(3):
                x, y, z = struct.unpack_from("<fff", data, offset)
                vertices.extend([x, y, z])
                offset += 12
            offset += 2
        return {"vertices": vertices}

    @staticmethod
    def _parse_ascii_stl(text: str) -> dict:
        vertices = []
        for line in text.splitlines():
            s = line.strip()
            if s.lower().startswith("vertex"):
                parts = s.split()
                vertices.extend([float(parts[1]), float(parts[2]), float(parts[3])])
        if not vertices:
            raise ValueError("No se encontraron vértices en el STL ASCII.")
        return {"vertices": vertices}

    # ──────────────────────────────────────────────
    # Routing: consultar /function
    # ──────────────────────────────────────────────

    def get_function(self, timeout: int = 10) -> str:
        """
        Consulta GET /function en Xano para obtener el modo de operación.
        Retorna "report" o "full_rail".
        """
        response = requests.get(self.xano_function, timeout=timeout)
        response.raise_for_status()
        data = response.json()

        if isinstance(data, str):
            return data.strip().lower()
        if isinstance(data, dict):
            for value in data.values():
                if isinstance(value, str) and value.strip().lower() in ("report", "full_rail"):
                    return value.strip().lower()

        raise ValueError(f"Respuesta inesperada de /function: {data!r}")

    # ──────────────────────────────────────────────
    # Camino A: Railway render → /report/create
    # ──────────────────────────────────────────────

    def render(
        self,
        stl_paths: list,
        views: list = None,
        width: int = 800,
        height: int = 800,
        analysis_mode: str = "limits",
        bfs_layers: int = 5,
        radio_threshold: float = 1.0,
        include_metrics: bool = True,
        cam_options: dict = None,
        timeout: int = 180,
    ) -> dict:
        """
        Envía los modelos STL al servidor Railway y retorna la respuesta.
        Si el servidor principal falla, reintenta con el fallback.

        Retorna dict con:
            captures : list[{view, image}]   — imágenes PNG en base64
            metrics  : list[{modelIndex, estado, descripcion, ...}]
        """
        models = [
            self.parse_stl(p) if isinstance(p, (str, Path)) else p
            for p in stl_paths
        ]

        payload = {
            "models":         models,
            "width":          width,
            "height":         height,
            "analysisMode":   analysis_mode,
            "bfsLayers":      bfs_layers,
            "radioThreshold": radio_threshold,
            "includeMetrics": include_metrics,
        }
        if views:
            payload["views"] = views
        if cam_options:
            payload["camOptions"] = cam_options

        urls_to_try = [self.railway_url]
        fallback    = self.RAILWAY_FALLBACK.rstrip("/") + "/render"
        if fallback != self.railway_url:
            urls_to_try.append(fallback)

        last_error = None
        for url in urls_to_try:
            try:
                response = requests.post(url, json=payload, timeout=timeout)
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                last_error = exc
                print(f"[DentalScanReporter] Railway {url} falló: {exc}. Intentando siguiente...")

        raise last_error

    @staticmethod
    def _auto_orient_single(vertices: list, is_upper: bool = False) -> list:
        coords = np.array(vertices, dtype=np.float32).reshape(-1, 3)
        if len(coords) < 3:
            return vertices
        
        # 1. Centro compartido
        center = coords.mean(axis=0)
        centered = coords - center
        
        # 2. PCA
        cov = np.cov(centered, rowvar=False)
        _, eigenvectors = np.linalg.eigh(cov)
        z_axis = eigenvectors[:, 0]
        y_axis = eigenvectors[:, 1]
        x_axis = eigenvectors[:, 2]
        
        R = np.column_stack((x_axis, y_axis, z_axis))
        if np.linalg.det(R) < 0:
            y_axis = -y_axis
            R = np.column_stack((x_axis, y_axis, z_axis))
            
        # 3. Orientar Y (U-shape anterior)
        rotated_check = centered @ R
        y_vals = rotated_check[:, 1]
        half_pos = rotated_check[y_vals > 0]
        half_neg = rotated_check[y_vals < 0]
        if len(half_pos) > 0 and len(half_neg) > 0:
            if half_pos[:, 0].std() > half_neg[:, 0].std():
                R_flip_y = np.array([[-1, 0, 0], [0, -1, 0], [0, 0, 1]], dtype=np.float32)
                R = R @ R_flip_y
                
        # 4. Refinar molar horizontal
        temp = centered @ R
        left_mask = temp[:, 0] < -5
        right_mask = temp[:, 0] > 5
        if left_mask.sum() > 100 and right_mask.sum() > 100:
            left_pts = temp[left_mask]
            right_pts = temp[right_mask]
            left_molar = left_pts[np.argsort(left_pts[:, 1])][:200].mean(axis=0)
            right_molar = right_pts[np.argsort(right_pts[:, 1])][:200].mean(axis=0)
            dy = right_molar[1] - left_molar[1]
            dx = right_molar[0] - left_molar[0]
            angle = np.arctan2(dy, dx)
            cos_a = np.cos(-angle)
            sin_a = np.sin(-angle)
            R_molar = np.array([[cos_a, -sin_a, 0], [sin_a, cos_a, 0], [0, 0, 1]], dtype=np.float32)
            R = R @ R_molar

        # 5. Z-flip dependiente de si es maxilar superior o inferior
        rotated = centered @ R
        z_vals = rotated[:, 2]
        hist, bin_edges = np.histogram(z_vals, bins=50)
        peak_z = (bin_edges[np.argmax(hist)] + bin_edges[np.argmax(hist)+1]) / 2.0
        z_mid = (z_vals.min() + z_vals.max()) / 2.0
        
        # Para superior, queremos los dientes apuntando a Z- (base plana peak_z en la mitad superior Z > z_mid)
        # Para inferior, queremos los dientes apuntando a Z+ (base plana peak_z en la mitad inferior Z < z_mid)
        if is_upper:
            if peak_z < z_mid:
                R_flip = np.array([[1, 0, 0], [0, -1, 0], [0, 0, -1]], dtype=np.float32)
                rotated = rotated @ R_flip
        else:
            if peak_z > z_mid:
                R_flip = np.array([[1, 0, 0], [0, -1, 0], [0, 0, -1]], dtype=np.float32)
                rotated = rotated @ R_flip
            
        return rotated.flatten().tolist()

    @staticmethod
    def _align_dual_jaws(upper_verts: list, lower_verts: list) -> tuple:
        coords_up = np.array(upper_verts, dtype=np.float32).reshape(-1, 3)
        coords_lo = np.array(lower_verts, dtype=np.float32).reshape(-1, 3)
        
        # 1. Centro compartido
        center_up = coords_up.mean(axis=0)
        center_lo = coords_lo.mean(axis=0)
        shared_center = (center_up + center_lo) / 2.0
        
        centered_up = coords_up - shared_center
        centered_lo = coords_lo - shared_center
        
        # 2. PCA sobre el superior
        cov = np.cov(centered_up, rowvar=False)
        _, eigenvectors = np.linalg.eigh(cov)
        z_axis = eigenvectors[:, 0]
        y_axis = eigenvectors[:, 1]
        x_axis = eigenvectors[:, 2]
        
        R = np.column_stack((x_axis, y_axis, z_axis))
        if np.linalg.det(R) < 0:
            y_axis = -y_axis
            R = np.column_stack((x_axis, y_axis, z_axis))
            
        # 3. Orientar eje Z usando vector V_up (superior debe quedar arriba de inferior)
        V_up = center_up - center_lo
        V_up_rot = V_up @ R
        if V_up_rot[2] < 0:
            R_flip = np.array([
                [1,  0,  0],
                [0, -1,  0],
                [0,  0, -1]
            ])
            R = R @ R_flip
            
        # 4. Orientar eje Y usando simetría del arco (anterior vs posterior)
        rotated_up_check = centered_up @ R
        y_vals = rotated_up_check[:, 1]
        half_positive = rotated_up_check[y_vals > 0]
        half_negative = rotated_up_check[y_vals < 0]
        
        if len(half_positive) > 0 and len(half_negative) > 0:
            std_x_pos = half_positive[:, 0].std()
            std_x_neg = half_negative[:, 0].std()
            if std_x_pos > std_x_neg:
                R_flip_y = np.array([
                    [-1,  0,  0],
                    [ 0, -1,  0],
                    [ 0,  0,  1]
                ])
                R = R @ R_flip_y

        # 5. Centrar/horizontalizar usando extremos de los molares
        temp_up = centered_up @ R
        left_mask = temp_up[:, 0] < -5
        right_mask = temp_up[:, 0] > 5
        
        if left_mask.sum() > 100 and right_mask.sum() > 100:
            left_pts = temp_up[left_mask]
            right_pts = temp_up[right_mask]
            
            left_sorted = left_pts[np.argsort(left_pts[:, 1])]
            right_sorted = right_pts[np.argsort(right_pts[:, 1])]
            
            left_molar = left_sorted[:200].mean(axis=0)
            right_molar = right_sorted[:200].mean(axis=0)
            
            dy = right_molar[1] - left_molar[1]
            dx = right_molar[0] - left_molar[0]
            angle = np.arctan2(dy, dx)
            
            cos_a = np.cos(-angle)
            sin_a = np.sin(-angle)
            R_molar = np.array([
                [cos_a, -sin_a, 0],
                [sin_a,  cos_a, 0],
                [    0,      0, 1]
            ])
            R = R @ R_molar

        final_rotated_up = centered_up @ R
        final_rotated_lo = centered_lo @ R
        
        # Mantenemos la orientación anatómica correcta (superior con dientes hacia abajo en Z-)
        individual_up = final_rotated_up
        individual_lo = final_rotated_lo
        
        return (
            final_rotated_up.flatten().tolist(),
            final_rotated_lo.flatten().tolist(),
            individual_up.flatten().tolist(),
            individual_lo.flatten().tolist()
        )

    def render_full(
        self,
        stl_upper: str = None,
        stl_lower: str = None,
        width: int = 800,
        height: int = 800,
        analysis_mode: str = "limits",
        bfs_layers: int = 5,
        radio_threshold: float = 1.0,
        include_metrics: bool = True,
        cam_options: dict = None,
        timeout: int = 180,
        auto_orient: bool = True,
    ) -> dict:
        """
        Renderiza todas las vistas necesarias según los maxilares provistos:

        stl_upper + stl_lower → vistas de oclusión (ambos modelos juntos) +
                                6 vistas cardinales de cada maxilar por separado.
        stl_upper solo        → 6 vistas cardinales del maxilar superior únicamente.
        stl_lower solo        → 6 vistas cardinales del maxilar inferior únicamente.

        Retorna el mismo formato que render():
            {"captures": [...], "metrics": [...]}
        """
        if not stl_upper and not stl_lower:
            raise ValueError("Debés proveer al menos stl_upper o stl_lower.")

        shared_kwargs = dict(
            width=width,
            height=height,
            analysis_mode=analysis_mode,
            bfs_layers=bfs_layers,
            radio_threshold=radio_threshold,
            include_metrics=include_metrics,
            cam_options=cam_options,
            timeout=timeout,
        )

        # ── Un solo maxilar ───────────────────────────────────────────────
        if not (stl_upper and stl_lower):
            path     = stl_upper or stl_lower
            label    = "upper" if stl_upper else "lower"
            is_upper = stl_upper is not None

            model = self.parse_stl(path)
            if auto_orient:
                model["vertices"] = self._auto_orient_single(model["vertices"], is_upper=is_upper)

            result   = self.render([model], views=self.ARCH_CARDINAL_VIEWS, **shared_kwargs)
            captures = [
                {"view": f"{label}_{cap['view']}", "image": cap["image"]}
                for cap in result.get("captures", [])
            ]
            print(f"[DentalScanReporter] render_full: {label} → {len(captures)} vistas cardinales")
            return {"captures": captures, "metrics": result.get("metrics", [])}

        # ── Ambos maxilares ───────────────────────────────────────────────
        if auto_orient:
            model_up = self.parse_stl(stl_upper)
            model_lo = self.parse_stl(stl_lower)
            aligned_up, aligned_lo, indiv_up, indiv_lo = self._align_dual_jaws(
                model_up["vertices"], model_lo["vertices"]
            )
            occl_models    = [{"vertices": aligned_up}, {"vertices": aligned_lo}]
            indiv_up_model = {"vertices": indiv_up}
            indiv_lo_model = {"vertices": indiv_lo}
        else:
            occl_models    = [stl_upper, stl_lower]
            indiv_up_model = stl_upper
            indiv_lo_model = stl_lower

        # Llamada 1: vistas de oclusión (ambos modelos juntos).
        occlusion = self.render(occl_models, views=self.OCCLUSION_VIEWS, **shared_kwargs)
        captures  = [
            {"view": f"occlusion_{cap['view']}", "image": cap["image"]}
            for cap in occlusion.get("captures", [])
        ]
        metrics = occlusion.get("metrics", [])
        print(f"[DentalScanReporter] render_full: oclusión → {len(captures)} vistas")

        # Llamada 2: vistas cardinales del maxilar superior.
        result_up = self.render([indiv_up_model], views=self.ARCH_CARDINAL_VIEWS, **shared_kwargs)
        for cap in result_up.get("captures", []):
            captures.append({"view": f"upper_{cap['view']}", "image": cap["image"]})
        print(f"[DentalScanReporter] render_full: upper → {len(result_up.get('captures', []))} vistas cardinales")

        # Llamada 3: vistas cardinales del maxilar inferior.
        result_lo = self.render([indiv_lo_model], views=self.ARCH_CARDINAL_VIEWS, **shared_kwargs)
        for cap in result_lo.get("captures", []):
            captures.append({"view": f"lower_{cap['view']}", "image": cap["image"]})
        print(f"[DentalScanReporter] render_full: lower → {len(result_lo.get('captures', []))} vistas cardinales")

        return {"captures": captures, "metrics": metrics}

    def create_report(
        self,
        render_result: dict,
        nombre: Optional[str] = None,
        referencia: Optional[str] = None,
        descripcion: str = "",
        external_id: str = None,
        timeout: int = 60,
    ) -> dict:
        """
        Crea el informe en Xano POST /report/create.

        nombre     → nombre visible del informe (ej. nombre del paciente, ID de caso)
        referencia → ID del paciente, nombre, o cualquier referencia del sistema.
                     Si es None, usa external_id como fallback.
        """
        captures   = render_result.get("captures", [])
        metrics    = render_result.get("metrics", [])
        image_data = {c["view"]: c["image"] for c in captures}

        _nombre     = nombre     or external_id or "sin-nombre"
        _referencia = referencia or external_id or "sin-referencia"

        payload = {
            "nombre":      _nombre,
            "referencia":  _referencia,
            "descripcion": descripcion,
            "data":        {"metrics": metrics},
            "image_data":  image_data,
        }
        if external_id:
            payload["external_id"] = str(external_id)

        response = requests.post(self.xano_report, json=payload, timeout=timeout)
        response.raise_for_status()
        return response.json()

    # ──────────────────────────────────────────────
    # Camino B: geometría STL directo a /models/full_rail
    # ──────────────────────────────────────────────

    def send_full_rail(
        self,
        stl_upper: str = None,
        stl_lower: str = None,
        nombre: Optional[str] = None,
        referencia: Optional[str] = None,
        external_id: str = None,
        timeout: int = 60,
    ) -> dict:
        """
        Envía la geometría STL directamente a Xano POST /models/full_rail.
        Xano toma el control desde ahí.
        """
        if not stl_upper and not stl_lower:
            raise ValueError("Debés proveer al menos stl_upper o stl_lower.")

        arches = {k: v for k, v in [("upper", stl_upper), ("lower", stl_lower)] if v}
        stl_data = {
            "models": [
                {**self.parse_stl(path), "filename": Path(path).name, "arch": arch}
                for arch, path in arches.items()
            ]
        }
        if nombre:
            stl_data["nombre"] = nombre
        if referencia:
            stl_data["referencia"] = referencia
        if external_id:
            stl_data["external_id"] = str(external_id)

        payload  = {"stl_data": stl_data}
        response = requests.post(self.xano_fullrail, json=payload, timeout=timeout)
        response.raise_for_status()
        return response.json()

    # ──────────────────────────────────────────────
    # Pipeline completo (enrutado por /function)
    # ──────────────────────────────────────────────

    def process(
        self,
        stl_upper: str = None,
        stl_lower: str = None,
        nombre: Optional[str] = None,
        referencia: Optional[str] = None,
        descripcion: str = "",
        external_id: str = None,
        render_options: dict = None,
    ) -> dict:
        """
        Pipeline principal. Consulta /function en Xano para decidir el camino:

        "report"    → render en Railway → POST /report/create en Xano
        "full_rail" → POST /models/full_rail con la geometría STL

        Después de llamar a process(), podés guardar el log con:
            reporter.save_log("ruta/al/archivo.json")

        Parámetros
        ----------
        stl_upper      : ruta al archivo .stl del maxilar superior (opcional)
        stl_lower      : ruta al archivo .stl del maxilar inferior (opcional)
        nombre         : nombre del informe / paciente (opcional)
        referencia     : ID de paciente, nombre, o referencia del sistema (opcional)
        descripcion    : texto libre (solo para camino "report")
        external_id    : ID del bot / sistema externo
        render_options : dict con parámetros extra para render_full()
        """
        if not stl_upper and not stl_lower:
            raise ValueError("Debés proveer al menos stl_upper o stl_lower.")

        timestamp = datetime.datetime.now().isoformat()
        mode      = self.get_function()
        print(f"[DentalScanReporter] Modo activo: {mode!r}")

        stl_info = {
            k: {"path": str(v), "filename": Path(v).name, "size_bytes": Path(v).stat().st_size}
            for k, v in [("upper", stl_upper), ("lower", stl_lower)]
            if v
        }

        # ── Camino A: report ─────────────────────────────
        if mode == "report":
            options       = render_options or {}
            render_result = self.render_full(stl_upper=stl_upper, stl_lower=stl_lower, **options)
            xano_result   = self.create_report(
                render_result=render_result,
                nombre=nombre,
                referencia=referencia,
                descripcion=descripcion,
                external_id=external_id,
            )
            self._last_log = {
                "timestamp":      timestamp,
                "mode":           mode,
                "stl_files":      stl_info,
                "render_options": options,
                "xano_response":  xano_result,
            }
            return xano_result

        # ── Camino B: full_rail ──────────────────────────
        if mode == "full_rail":
            xano_result = self.send_full_rail(
                stl_upper=stl_upper,
                stl_lower=stl_lower,
                nombre=nombre,
                referencia=referencia,
                external_id=external_id,
            )
            self._last_log = {
                "timestamp":     timestamp,
                "mode":          mode,
                "stl_files":     stl_info,
                "xano_response": xano_result,
            }
            return xano_result

        raise ValueError(f"Modo desconocido recibido de /function: {mode!r}")


# ──────────────────────────────────────────────────────────────────────────────
# Prueba local — ejecutar: python dental_scan_reporter.py
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    upper_stl = "Escaneos/Escaneo A-20260408T203127Z-3-001/Escaneo A/upper.stl"
    lower_stl = "Escaneos/Escaneo A-20260408T203127Z-3-001/Escaneo A/lower.stl"

    reporter = DentalScanReporter()

    result = reporter.process(
        stl_upper=upper_stl,
        stl_lower=lower_stl,
        nombre="Escaneo A - 2026-04-08",
        referencia="Escaneo A-20260408T203127Z-3-001",
        descripcion="Escaneo inicial maxilar y mandibular",
        external_id="Escaneo A-20260408T203127Z-3-001",
    )

    reporter.save_log("logs/escaneo_a_20260408.json")

    print(json.dumps(result, indent=2, ensure_ascii=False))
