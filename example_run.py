"""
example_run.py
--------------
Petición de ejemplo para DentalScanReporter.
Modificá los parámetros de la sección CONFIG y ejecutá:

    python example_run.py

El log del reporte se guarda automáticamente en la carpeta LOG_DIR
como un JSON con timestamp (sin base64 ni vértices — solo lo relevante).
"""

from dental_scan_reporter import DentalScanReporter

# ═══════════════════════════════════════════════════════════════════
#  CONFIG — modificar según el caso antes de ejecutar
# ═══════════════════════════════════════════════════════════════════

# ── Archivos STL ────────────────────────────────────────────────────
# Rutas relativas al directorio donde se ejecuta el script,
# o rutas absolutas.
STL_UPPER = r"\\server-nuevo\Escaneos Externos\BU148\upper-diagnostic.stl"
STL_LOWER = r"\\server-nuevo\Escaneos Externos\BU148\lower-diagnostic.stl"


# ── Identificación del paciente / caso ──────────────────────────────
# El dev elige qué campo del bot va en cada parámetro.
# Ejemplos:
#   NOMBRE     = paciente.nombre_completo
#   REFERENCIA = paciente.id  o  caso.numero_expediente
#   EXTERNAL_ID = scan.uuid  (ID en el sistema del bot)

NOMBRE      = "Informe de escaneo"           # nombre visible en Xano
REFERENCIA  = "Lugo Gonzalo"  # ID de paciente o caso
EXTERNAL_ID = "BU148"  # ID en el sistema del bot
DESCRIPCION = "Escaneo inicial maxilar y mandibular"

# ── Opciones de render (solo se usan en modo "report") ───────────────
# Dejá en None para usar los defaults del servidor.
RENDER_OPTIONS = None
# Ejemplo con valores personalizados:
# RENDER_OPTIONS = {
#     "views":           ["front", "back", "top", "bottom", "left", "right"],
#     "analysis_mode":   "limits",   # "limits" | "default"
#     "include_metrics": True,
#     "width":           800,
#     "height":          800,
# }

# ── Log ──────────────────────────────────────────────────────────────
# Ruta completa del archivo JSON donde se guarda el log.
# None = no guardar.
LOG_PATH = None
# LOG_PATH = None

# ── Servidor Railway (opcional) ──────────────────────────────────────
# None = usa el dominio por defecto (aprobador.keepsmiling.click)
# RAILWAY_URL = "http://192.168.2.132:3000"
RAILWAY_URL = None

# ═══════════════════════════════════════════════════════════════════
#  EJECUCIÓN — no hace falta modificar nada de acá para abajo
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import json

    if not STL_UPPER and not STL_LOWER:
        print("ERROR: no hay archivos STL configurados.")
        raise SystemExit(1)

    reporter = DentalScanReporter(railway_url=RAILWAY_URL)

    print("═" * 50)
    print(f"  STL upper   : {STL_UPPER or '(no configurado)'}")
    print(f"  STL lower   : {STL_LOWER or '(no configurado)'}")
    print(f"  Nombre      : {NOMBRE or '(no configurado)'}")
    print(f"  Referencia  : {REFERENCIA or '(no configurado)'}")
    print(f"  External ID : {EXTERNAL_ID or '(no configurado)'}")
    print(f"  Log path    : {LOG_PATH or 'deshabilitado'}")
    print("═" * 50)

    print("\nConsultando modo de operación en Xano...")
    mode = reporter.get_function()
    print(f"Modo activo: {mode!r}\n")

    print("Procesando escaneo...")
    result = reporter.process(
        stl_upper=STL_UPPER,
        stl_lower=STL_LOWER,
        nombre=NOMBRE,
        referencia=REFERENCIA,
        descripcion=DESCRIPCION,
        external_id=EXTERNAL_ID,
        render_options=RENDER_OPTIONS,
    )

    if LOG_PATH:
        reporter.save_log(LOG_PATH)

    print("\n── Respuesta de Xano ──────────────────────────")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("──────────────────────────────────────────────")
    print(f"\nListo. Informe ID: {result.get('id', '?')}")
