import sys
import struct
import numpy as np
from pathlib import Path

def parse_binary_stl(path):
    data = Path(path).read_bytes()
    num_triangles = struct.unpack_from("<I", data, 80)[0]
    vertices = []
    offset = 84
    for _ in range(num_triangles):
        offset += 12
        for _ in range(3):
            x, y, z = struct.unpack_from("<fff", data, offset)
            vertices.extend([x, y, z])
            offset += 12
        offset += 2
    return vertices

def save_binary_stl(path, vertices):
    num_triangles = len(vertices) // 9
    header = b"Auto-oriented by Antigravity AI" + b"\x00" * 49  # 80 bytes
    
    with open(path, "wb") as f:
        f.write(header)
        f.write(struct.pack("<I", num_triangles))
        
        offset = 0
        for _ in range(num_triangles):
            f.write(struct.pack("<fff", 0.0, 0.0, 0.0))
            v1 = vertices[offset : offset + 3]
            v2 = vertices[offset + 3 : offset + 6]
            v3 = vertices[offset + 6 : offset + 9]
            f.write(struct.pack("<fff", *v1))
            f.write(struct.pack("<fff", *v2))
            f.write(struct.pack("<fff", *v3))
            f.write(struct.pack("<H", 0))
            offset += 9
    print(f"Guardado STL binario: {path} ({num_triangles} triángulos).")

def detect_upper_jaw(vertices_0, vertices_1):
    coords_0 = np.array(vertices_0, dtype=np.float32).reshape(-1, 3)
    coords_1 = np.array(vertices_1, dtype=np.float32).reshape(-1, 3)
    
    # Centrar y proyectar en espacio PCA para medir densidad en el centro
    center_0 = coords_0.mean(axis=0)
    centered_0 = coords_0 - center_0
    cov = np.cov(centered_0, rowvar=False)
    _, eigenvectors = np.linalg.eigh(cov)
    # R alineado: columnas 2 (max var, X), 1 (mid var, Y), 0 (min var, Z)
    R = np.column_stack((eigenvectors[:, 2], eigenvectors[:, 1], eigenvectors[:, 0]))
    
    rot_0 = centered_0 @ R
    rot_1 = (coords_1 - coords_1.mean(axis=0)) @ R
    
    # Contar puntos en el cilindro central (radio < 12mm en el plano XY)
    dist_0 = np.sqrt(rot_0[:, 0]**2 + rot_0[:, 1]**2)
    dist_1 = np.sqrt(rot_1[:, 0]**2 + rot_1[:, 1]**2)
    
    count_0 = (dist_0 < 12.0).sum()
    count_1 = (dist_1 < 12.0).sum()
    
    print(f"[Detección] Puntos en centro: Modelo 0 = {count_0}, Modelo 1 = {count_1}")
    if count_0 > count_1:
        return 0, 1 # idx_0 es upper, idx_1 es lower
    else:
        return 1, 0 # idx_1 es upper, idx_0 es lower

def align_dual_jaws(upper_verts, lower_verts):
    coords_up = np.array(upper_verts, dtype=np.float32).reshape(-1, 3)
    coords_lo = np.array(lower_verts, dtype=np.float32).reshape(-1, 3)
    
    # 1. Centro compartido (punto medio de ambos centros)
    center_up = coords_up.mean(axis=0)
    center_lo = coords_lo.mean(axis=0)
    shared_center = (center_up + center_lo) / 2.0
    
    centered_up = coords_up - shared_center
    centered_lo = coords_lo - shared_center
    
    # 2. PCA sobre el superior para encontrar orientación inicial R
    cov = np.cov(centered_up, rowvar=False)
    eigenvalues, eigenvectors = np.linalg.eigh(cov)
    
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
    
    # Vista individual del superior e inferior mantienen la misma orientación anatómica que en oclusión
    individual_up = final_rotated_up
    individual_lo = final_rotated_lo
    
    return (
        final_rotated_up.flatten().tolist(),
        final_rotated_lo.flatten().tolist(),
        individual_up.flatten().tolist(),
        individual_lo.flatten().tolist()
    )

if __name__ == "__main__":
    if len(sys.argv) < 3:
        folder = Path(r"d:\Proyectos\Aprobaciones\Escaneos\Escaneo A-20260408T203127Z-3-001\Escaneo A - rotado")
        stl_path_a = folder / "upper rotated.stl"
        stl_path_b = folder / "lower rotated.stl"
    else:
        stl_path_a = Path(sys.argv[1])
        stl_path_b = Path(sys.argv[2])

    if not stl_path_a.exists() or not stl_path_b.exists():
        print(f"Error: No se encuentran los archivos:\n  - {stl_path_a}\n  - {stl_path_b}")
        sys.exit(1)

    print(f"Leyendo archivo A: {stl_path_a.name}")
    verts_a = parse_binary_stl(stl_path_a)
    print(f"Leyendo archivo B: {stl_path_b.name}")
    verts_b = parse_binary_stl(stl_path_b)
    
    # Detectar cuál es físicamente el superior
    up_idx, lo_idx = detect_upper_jaw(verts_a, verts_b)
    
    if up_idx == 0:
        print("-> Identificado: Archivo A es SUPERIOR, Archivo B es INFERIOR.")
        verts_up = verts_a
        verts_lo = verts_b
        out_name_up = "upper_aligned"
        out_name_lo = "lower_aligned"
    else:
        print("-> Identificado: Archivo A es INFERIOR, Archivo B es SUPERIOR (intercambiando...).")
        verts_up = verts_b
        verts_lo = verts_a
        out_name_up = "lower_aligned" # espera, si el archivo A es inferior, entonces su salida es lower_aligned
        # en realidad queremos guardar las salidas correspondientes a los nombres correctos:
        # si A es inferior (lo_idx == 0) y B es superior (up_idx == 1):
        # A_out -> lower_aligned
        # B_out -> upper_aligned
        
    print("Alineando modelos en oclusión...")
    aligned_up, aligned_lo, indiv_up, indiv_lo = align_dual_jaws(verts_up, verts_lo)
    
    out_dir = stl_path_a.parent
    if up_idx == 0:
        save_binary_stl(out_dir / "upper_aligned_occl.stl", aligned_up)
        save_binary_stl(out_dir / "lower_aligned_occl.stl", aligned_lo)
        save_binary_stl(out_dir / "upper_aligned_indiv.stl", indiv_up)
        save_binary_stl(out_dir / "lower_aligned_indiv.stl", indiv_lo)
    else:
        # Si venían invertidos, los guardamos alineados cruzando las variables correspondientes
        save_binary_stl(out_dir / "upper_aligned_occl.stl", aligned_up)
        save_binary_stl(out_dir / "lower_aligned_occl.stl", aligned_lo)
        save_binary_stl(out_dir / "upper_aligned_indiv.stl", indiv_up)
        save_binary_stl(out_dir / "lower_aligned_indiv.stl", indiv_lo)
    
    print("\n¡Listo! Archivos generados correctamente.")
