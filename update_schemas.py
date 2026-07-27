"""
update_schemas.py
-----------------
Actualiza las definiciones de APIs y el tipo TypeScript desde Xano.

Hace dos cosas:
  1. GET /swagger  → descarga el spec OpenAPI completo del grupo de APIs
                     y guarda cada endpoint como archivo JSON separado en apis/
  2. GET /report/schema → descarga el schema de la tabla informes
                          y regenera types/informes.ts

Ejecutar:
    python update_schemas.py
"""

import json
import requests
from pathlib import Path

XANO_BASE = "https://xoas-qetn-dh88.n7c.xano.io/api:xiRf2PrY"
APIS_DIR  = Path(__file__).parent / "apis"
TYPES_DIR = Path(__file__).parent / "types"

# ──────────────────────────────────────────────
# Mapeo de tipos Xano / OpenAPI → TypeScript
# ──────────────────────────────────────────────

XANO_TYPE_MAP = {
    "id":          "number",
    "int":         "number",
    "integer":     "number",
    "int64":       "number",
    "float":       "number",
    "double":      "number",
    "decimal":     "number",
    "timestamp":   "number",
    "timestamptz": "number",
    "text":        "string",
    "string":      "string",
    "bool":        "boolean",
    "boolean":     "boolean",
    "json":        "any",
    "object":      "any",
    "array":       "any[]",
    "null":        "null",
}

XANO_FILE_TYPE = (
    "{ access: string; path: string; name: string; type: string; "
    "size: number; mime: string; meta: { width?: number; height?: number }; url: string | null }"
)


# ──────────────────────────────────────────────
# 1. Fetch y split de APIs
# ──────────────────────────────────────────────

def fetch_openapi_spec(timeout: int = 15) -> dict:
    """Descarga el spec OpenAPI completo del grupo de APIs de Xano."""
    url = XANO_BASE + "/swagger"
    print(f"Fetching spec desde {url} ...")
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def save_apis(spec: dict) -> list:
    """
    Divide el spec por path+método y guarda cada uno como JSON en apis/.
    Nombre de archivo: {tag}-{path-slug}-{method}.json
    """
    APIS_DIR.mkdir(exist_ok=True)

    # Doc base sin paths (info, servers, components)
    base  = {k: v for k, v in spec.items() if k != "paths"}
    saved = []

    for path, methods in spec.get("paths", {}).items():
        for method, operation in methods.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete"):
                continue

            tag       = (operation.get("tags") or ["misc"])[0].lower()
            path_slug = path.strip("/").replace("/", "-")
            filename  = f"{tag}-{path_slug}-{method.lower()}.json"

            doc = {**base, "paths": {path: {method: operation}}}
            out = APIS_DIR / filename
            out.write_text(json.dumps(doc, indent=4, ensure_ascii=False), encoding="utf-8")
            saved.append(filename)
            print(f"  ✓ apis/{filename}")

    return saved


# ──────────────────────────────────────────────
# 2. Schema → TypeScript
# ──────────────────────────────────────────────

def _openapi_prop_to_ts(prop: dict, depth: int = 0) -> str:
    """Convierte una propiedad OpenAPI a su tipo TypeScript (recursivo)."""
    t   = prop.get("type", "any")
    fmt = prop.get("format", "")

    if t in ("integer", "number"):
        return "number"
    if t == "string":
        return "string"
    if t == "boolean":
        return "boolean"
    if t == "array":
        item_type = _openapi_prop_to_ts(prop.get("items", {}), depth)
        return f"{item_type}[]"
    if t == "object":
        inner = prop.get("properties")
        if not inner:
            return "any"
        pad  = "  " * (depth + 1)
        end  = "  " * depth
        rows = [f"{pad}{k}: {_openapi_prop_to_ts(v, depth + 1)};" for k, v in inner.items()]
        return "{\n" + "\n".join(rows) + f"\n{end}}}"
    return "any"


def _xano_col_to_ts(col: dict) -> str:
    """Convierte una columna de schema Xano a tipo TypeScript."""
    t = col.get("type", "").lower()
    if t in XANO_TYPE_MAP:
        return XANO_TYPE_MAP[t]
    if t == "file":
        return XANO_FILE_TYPE
    if t in ("table", "file_list"):
        return f"{XANO_FILE_TYPE}[]"
    return "any"


def generate_ts_from_openapi(data: dict, name: str = "InformesSchema") -> str:
    """Genera interfaz TypeScript desde un objeto OpenAPI con `properties`."""
    props = data.get("properties", data)
    if not isinstance(props, dict) or not props:
        return f"// No se encontraron propiedades en el schema recibido.\ninterface {name} {{}}\n"

    lines = [f"interface {name} {{"]
    for field, prop in props.items():
        nullable = prop.get("nullable", False)
        ts_type  = _openapi_prop_to_ts(prop)
        suffix   = " | null" if nullable else ""
        lines.append(f"    {field}: {ts_type}{suffix};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def generate_ts_from_xano_columns(columns: list, name: str = "InformesSchema") -> str:
    """Genera interfaz TypeScript desde una lista de columnas de Xano."""
    lines = [f"interface {name} {{"]
    for col in columns:
        field    = col.get("name", "unknown")
        ts_type  = _xano_col_to_ts(col)
        nullable = col.get("nullable", False)
        suffix   = " | null" if nullable else ""
        lines.append(f"    {field}: {ts_type}{suffix};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def fetch_and_update_schema(timeout: int = 15):
    """Llama a GET /report/schema y regenera types/informes.ts."""
    url = XANO_BASE + "/report/schema"
    print(f"Fetching schema desde {url} ...")
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    preview = json.dumps(data, ensure_ascii=False)
    print(f"  Schema recibido ({len(preview)} chars): {preview[:300]}{'...' if len(preview) > 300 else ''}")

    if isinstance(data, list):
        ts = generate_ts_from_xano_columns(data)
    elif isinstance(data, dict):
        ts = generate_ts_from_openapi(data)
    else:
        ts = f"// Respuesta inesperada de /report/schema:\n// {data!r}\n"

    TYPES_DIR.mkdir(exist_ok=True)
    out = TYPES_DIR / "informes.ts"
    out.write_text(ts, encoding="utf-8")
    print(f"  ✓ types/informes.ts actualizado")
    return data


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

if __name__ == "__main__":
    print("══════════════════════════════════════")
    print("  Actualizando APIs")
    print("══════════════════════════════════════")
    try:
        spec  = fetch_openapi_spec()
        saved = save_apis(spec)
        print(f"\n  {len(saved)} archivo(s) guardados en apis/")
    except Exception as e:
        print(f"\n  ERROR al obtener spec OpenAPI: {e}")
        print("  Verificá que el endpoint GET /swagger esté habilitado en el grupo de APIs de Xano.")

    print("\n══════════════════════════════════════")
    print("  Actualizando Schema TypeScript")
    print("══════════════════════════════════════")
    try:
        fetch_and_update_schema()
    except Exception as e:
        print(f"\n  ERROR al obtener schema: {e}")
        print("  Verificá que el endpoint GET /report/schema esté activo en Xano.")
