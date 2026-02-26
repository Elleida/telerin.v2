#!/usr/bin/env python3
"""
fix_tv_schedule_dates.py
------------------------
Corrige el campo `date` de la tabla {base}_tv_schedule en CrateDB.

Patrón de error : el campo date contiene '2023-XX-XX/2023-XX-XX' (año erróneo).
Corrección      : extraer la fecha real de magazine_id (formato 19XX-XX-XX) y
                  reconstruir el campo como '{fecha_magazine}/{fecha_magazine + 7 días}'.

Uso:
    python fix_tv_schedule_dates.py <base>           # dry-run: muestra cambios sin tocar BD
    python fix_tv_schedule_dates.py <base> --apply   # aplica los cambios en CrateDB

Ejemplos:
    python fix_tv_schedule_dates.py teleradio_content
    python fix_tv_schedule_dates.py qwen3vl --apply
    python fix_tv_schedule_dates.py teleradio_content --apply --batch-size 200
"""

import argparse
import re
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os
import requests
from requests.auth import HTTPBasicAuth

# ─────────────────────────────────────────────────────────────────────────────
# Configuración – reutiliza el .env del proyecto
# ─────────────────────────────────────────────────────────────────────────────
load_dotenv()

CRATEDB_URL      = os.getenv("CRATEDB_URL")
CRATEDB_USERNAME = os.getenv("CRATEDB_USERNAME")
CRATEDB_PASSWORD = os.getenv("CRATEDB_PASSWORD")

# Patrón de fecha errónea en el campo date: empieza por 2023-
ERROR_DATE_RE = re.compile(r"^2023-")

# Patrón para extraer una fecha válida (19XX-XX-XX) del campo magazine_id
MAGAZINE_DATE_RE = re.compile(r"(1[0-9]\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01]))")


# ─────────────────────────────────────────────────────────────────────────────
# CrateDB helper
# ─────────────────────────────────────────────────────────────────────────────
def cratedb_query(sql: str, args: list | None = None, timeout: int = 60) -> dict | None:
    """Ejecuta una sentencia SQL contra CrateDB mediante la HTTP API."""
    payload: dict = {"stmt": sql}
    if args is not None:
        payload["args"] = args
    try:
        resp = requests.post(
            CRATEDB_URL,
            json=payload,
            auth=HTTPBasicAuth(CRATEDB_USERNAME, CRATEDB_PASSWORD),
            timeout=timeout,
        )
        if resp.status_code not in (200, 201):
            print(f"❌ CrateDB HTTP {resp.status_code}: {resp.text}", file=sys.stderr)
            return None
        return resp.json()
    except Exception as exc:
        print(f"❌ Error de conexión a CrateDB: {exc}", file=sys.stderr)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Utilidades de fecha
# ─────────────────────────────────────────────────────────────────────────────
def extract_magazine_date(magazine_id: object) -> datetime | None:
    """
    Extrae la primera fecha con formato 19XX-MM-DD que aparezca en magazine_id.
    Devuelve None si no se encuentra ninguna.
    """
    if magazine_id is None:
        return None
    m = MAGAZINE_DATE_RE.search(str(magazine_id))
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d")
    except ValueError:
        return None


def build_corrected_date(start: datetime) -> str:
    """
    Construye el valor corregido del campo date como rango de 7 días:
    'YYYY-MM-DD/YYYY-MM-DD'  (inicio = fecha de magazine_id, fin = inicio + 7 días)
    """
    end = start + timedelta(days=7)
    return f"{start.strftime('%Y-%m-%d')}/{end.strftime('%Y-%m-%d')}"


# ─────────────────────────────────────────────────────────────────────────────
# Lógica principal
# ─────────────────────────────────────────────────────────────────────────────
def run(base_table: str, apply: bool, batch_size: int) -> None:
    table = f"{base_table}_tv_schedule"

    print(f"\n{'═'*62}")
    print(f"  Tabla objetivo : {table}")
    print(f"  Modo           : {'⚠️  APLICAR CAMBIOS' if apply else '🔒 DRY-RUN (sin modificar BD)'}")
    print(f"{'═'*62}\n")

    # ── 1. Contar filas afectadas ─────────────────────────────────────────────
    count_res = cratedb_query(
        f'SELECT COUNT(*) FROM "{table}" WHERE date LIKE \'2023-%\''
    )
    if count_res is None:
        print("❌ No se pudo conectar a CrateDB o la tabla no existe.")
        sys.exit(1)

    total: int = count_res["rows"][0][0]
    print(f"🔍 Filas con fecha errónea (2023-…): {total:,}")

    if total == 0:
        print("✅ No hay filas que corregir. Todo está bien.")
        return

    # ── 2. Descargar filas afectadas ──────────────────────────────────────────
    print(f"📥 Descargando datos de las {total:,} filas afectadas…")
    fetch_res = cratedb_query(
        f'SELECT id, magazine_id, date FROM "{table}" WHERE date LIKE \'2023-%\' LIMIT 500000'
    )
    if fetch_res is None:
        print("❌ Error al recuperar los datos.")
        sys.exit(1)

    rows: list[list] = fetch_res["rows"]
    print(f"   → {len(rows):,} filas descargadas.\n")

    # ── 3. Calcular correcciones ──────────────────────────────────────────────
    corrections: list[tuple] = []   # (id, old_date, new_date)
    skipped:     list[tuple] = []   # (id, magazine_id, motivo)
    already_ok:  int = 0

    for row_id, magazine_id, old_date in rows:
        mag_date = extract_magazine_date(magazine_id)
        if mag_date is None:
            skipped.append((row_id, magazine_id,
                            "No se encontró fecha 19XX-XX-XX en magazine_id"))
            continue
        new_date = build_corrected_date(mag_date)
        if new_date == old_date:
            already_ok += 1
            continue
        corrections.append((row_id, old_date, new_date))

    print(f"📊 Resumen del análisis:")
    print(f"   ✅ Correcciones a aplicar         : {len(corrections):>6,}")
    print(f"   ✓  Ya tenían el valor correcto     : {already_ok:>6,}")
    print(f"   ⚠️  Sin fecha válida en magazine_id : {len(skipped):>6,}")

    # Muestra de correcciones
    preview_n = min(10, len(corrections))
    if preview_n:
        print(f"\n📋 Primeras {preview_n} correcciones:")
        hdr_id = "ID / magazine_id"
        print(f"   {'FECHA ERRÓNEA':<25}  →  {'FECHA CORRECTA':<25}  ({hdr_id})")
        print(f"   {'─'*25}     {'─'*25}  {'─'*36}")
        for row_id, old_d, new_d in corrections[:preview_n]:
            print(f"   {old_d:<25}  →  {new_d:<25}  ({row_id})")

    # Muestra de omisiones
    if skipped:
        skip_n = min(5, len(skipped))
        print(f"\n⚠️  Primeras {skip_n} filas omitidas (sin fecha inferible):")
        for row_id, mid, reason in skipped[:skip_n]:
            print(f"   id={row_id}  magazine_id={mid!r}")
            print(f"      → {reason}")

    # ── 4. Dry-run: salir sin modificar ────────────────────────────────────────
    if not apply:
        print(f"\n🔒 DRY-RUN completado — base de datos NO modificada.")
        print(f"   Ejecuta con --apply para aplicar {len(corrections):,} cambios.")
        return

    # ── 5. Aplicar en lotes ────────────────────────────────────────────────────
    print(f"\n🔄 Aplicando {len(corrections):,} correcciones en lotes de {batch_size}…")

    updated_ok = 0
    update_errors = 0
    total_batches = (len(corrections) + batch_size - 1) // batch_size

    for batch_idx in range(total_batches):
        batch = corrections[batch_idx * batch_size : (batch_idx + 1) * batch_size]
        batch_ok = 0

        for row_id, _old, new_date in batch:
            res = cratedb_query(
                f'UPDATE "{table}" SET date = ? WHERE id = ?',
                args=[new_date, row_id],
            )
            if res is not None:
                batch_ok += 1
            else:
                update_errors += 1

        updated_ok += batch_ok
        done = batch_idx * batch_size + len(batch)
        pct = 100.0 * done / len(corrections)
        print(f"   [{pct:5.1f}%] Lote {batch_idx + 1}/{total_batches}: "
              f"{batch_ok}/{len(batch)} OK  (acum. {updated_ok:,})")

    # ── 6. Refresh y verificación ─────────────────────────────────────────────
    print(f"\n🔄 Refrescando tabla…")
    cratedb_query(f'REFRESH TABLE "{table}"')

    verify = cratedb_query(
        f'SELECT COUNT(*) FROM "{table}" WHERE date LIKE \'2023-%\''
    )
    remaining = verify["rows"][0][0] if verify else "?"

    print(f"\n{'═'*62}")
    print(f"  ✅ Filas actualizadas  : {updated_ok:,}")
    print(f"  ❌ Errores de UPDATE   : {update_errors:,}")
    print(f"  ⚠️  Filas omitidas      : {len(skipped):,}")
    print(f"  🔍 Aún con 2023-…      : {remaining}")
    print(f"{'═'*62}\n")

    if remaining == 0:
        print("🎉 Corrección completada con éxito. No quedan fechas erróneas.")
    elif remaining:
        print(f"⚠️  Quedan {remaining} filas con fecha 2023-… (probablemente las omitidas).")


# ─────────────────────────────────────────────────────────────────────────────
# Entry-point
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Corrige fechas erróneas (2023-XX-XX/2023-XX-XX) en {base}_tv_schedule "
            "usando la fecha real extraída de magazine_id (formato 19XX-XX-XX)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "base",
        help="Nombre base de las tablas (p.e. teleradio_content, qwen3vl)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplicar los cambios en CrateDB (por defecto: dry-run, solo lectura)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        metavar="N",
        help="Número máximo de UPDATEs por lote (default: 500)",
    )
    args = parser.parse_args()

    # Validar credenciales
    missing_vars = [v for v in ("CRATEDB_URL", "CRATEDB_USERNAME")
                    if not os.getenv(v)]
    if missing_vars:
        print(f"❌ Variables de entorno faltantes: {', '.join(missing_vars)}", file=sys.stderr)
        print("   Asegúrate de tener un fichero .env con esas variables.", file=sys.stderr)
        sys.exit(1)

    run(args.base, args.apply, args.batch_size)


if __name__ == "__main__":
    main()
