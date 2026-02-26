#!/usr/bin/env python3
"""
check_missing_days.py
---------------------
Detecta los días de un mes/año concreto que no tienen programación
en la tabla {base}_tv_schedule de CrateDB.

El campo `date` tiene formato simple: YYYY-MM-DD

Uso:
    # Resumen del mes: qué días del mes NO tienen registros en la tabla
    python check_missing_days.py <base> <año> <mes>

    # Detalle de un día concreto: muestra los programas de ese día
    python check_missing_days.py <base> <año> <mes> <día>

Ejemplos:
    python check_missing_days.py teleradio_content 1963 4
    python check_missing_days.py teleradio_content 1978 12 15
    python check_missing_days.py qwen3vl 1965 7 3
"""

import argparse
import calendar
import sys
from datetime import date
from dotenv import load_dotenv
import os
import requests
from requests.auth import HTTPBasicAuth

# ─────────────────────────────────────────────────────────────────────────────
load_dotenv()
CRATEDB_URL      = os.getenv("CRATEDB_URL")
CRATEDB_USERNAME = os.getenv("CRATEDB_USERNAME")
CRATEDB_PASSWORD = os.getenv("CRATEDB_PASSWORD")
# ─────────────────────────────────────────────────────────────────────────────

DAY_NAMES_ES       = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
DAY_NAMES_ES_SHORT = ["Lun",   "Mar",    "Mié",       "Jue",    "Vie",     "Sáb",    "Dom"]


def cratedb_query(sql: str, timeout: int = 30) -> dict | None:
    try:
        resp = requests.post(
            CRATEDB_URL,
            json={"stmt": sql},
            auth=HTTPBasicAuth(CRATEDB_USERNAME, CRATEDB_PASSWORD),
            timeout=timeout,
        )
        if resp.status_code not in (200, 201):
            print(f"❌ CrateDB HTTP {resp.status_code}: {resp.text}", file=sys.stderr)
            return None
        return resp.json()
    except Exception as exc:
        print(f"❌ Error de conexión: {exc}", file=sys.stderr)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Modo día: consulta exacta por date = 'YYYY-MM-DD'
# ─────────────────────────────────────────────────────────────────────────────
def run_day(base_table: str, target: date) -> None:
    """Muestra los programas registrados para un día concreto (date = 'YYYY-MM-DD')."""
    table   = f"{base_table}_tv_schedule"
    day_str = target.isoformat()
    dow     = DAY_NAMES_ES[target.weekday()]

    print(f"\n{'═'*62}")
    print(f"  Tabla : {table}")
    print(f"  Día   : {day_str}  ({dow})")
    print(f"{'═'*62}\n")

    sql = f"""
        SELECT id, date, title, channel, time, day_of_week, content_description
        FROM "{table}"
        WHERE date = '{day_str}'
        ORDER BY time, channel, title
        LIMIT 500
    """
    res = cratedb_query(sql)
    if res is None:
        print(f"❌ No se pudo consultar la tabla '{table}'.")
        sys.exit(1)

    rows = res.get("rows", [])

    if not rows:
        print(f"📭 No hay programación registrada para el {day_str} ({dow}).")
        return

    print(f"📺 Programas encontrados: {len(rows)}\n")
    print(f"  {'HORA':<8}  {'CANAL':<22}  {'TÍTULO'}")
    print(f"  {'─'*8}  {'─'*22}  {'─'*35}")
    for row in rows:
        _id, _date, title, channel, time_val, row_dow, desc = row
        hora   = (time_val or "").strip() or "--:--"
        canal  = (channel  or "").strip() or "(sin canal)"
        titulo = (title    or "").strip() or "(sin título)"
        print(f"  {hora:<8}  {canal:<22}  {titulo}")
        if desc and desc.strip():
            desc_clean = desc.strip().replace("\n", " ")
            if len(desc_clean) > 80:
                desc_clean = desc_clean[:77] + "…"
            print(f"  {'':8}  {'':22}  → {desc_clean}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Modo mes: obtiene todos los dates del mes y detecta días que faltan
# ─────────────────────────────────────────────────────────────────────────────
def run_month(base_table: str, year: int, month: int) -> None:
    """Muestra qué días del mes no tienen ningún registro en la tabla."""
    table      = f"{base_table}_tv_schedule"
    month_name = f"{year}-{month:02d}"
    prefix     = f"{year}-{month:02d}-"
    _, last_day_num = calendar.monthrange(year, month)

    print(f"\n{'═'*52}")
    print(f"  Tabla  : {table}")
    print(f"  Período: {month_name}  ({last_day_num} días en total)")
    print(f"{'═'*52}\n")

    # Obtener los valores distintos de date que existen en el mes
    sql = f"""
        SELECT DISTINCT date
        FROM "{table}"
        WHERE date LIKE '{prefix}%'
        ORDER BY date
    """
    res = cratedb_query(sql)
    if res is None:
        print(f"❌ No se pudo consultar la tabla '{table}'.")
        sys.exit(1)

    # Set de fechas presentes en la BD para este mes
    present_dates: set[date] = set()
    for row in res["rows"]:
        raw = row[0]
        if not raw:
            continue
        try:
            present_dates.add(date.fromisoformat(str(raw).strip()))
        except ValueError:
            pass  # ignorar valores con formato inesperado

    # Calcular días del mes con y sin programación
    all_days = [date(year, month, d) for d in range(1, last_day_num + 1)]
    missing  = [d for d in all_days if d not in present_dates]

    print(f"  Días con programación : {len(present_dates):>3}")
    print(f"  Días SIN programación : {len(missing):>3}")
    print(f"{'═'*52}\n")

    if not missing:
        print("✅ Todos los días del mes tienen programación registrada.")
        return

    print(f"📅 Días sin programación en {month_name}:\n")
    for d in missing:
        dow_short = DAY_NAMES_ES_SHORT[d.weekday()]
        print(f"   {d.isoformat()}  ({dow_short})")

    # Vista de calendario
    print(f"\n📆 Calendario {month_name}  (✗ = sin programación, · = con programación)\n")
    print(f"   Lun   Mar   Mié   Jue   Vie   Sáb   Dom")
    missing_set   = set(missing)
    first_weekday = date(year, month, 1).weekday()  # 0 = Lun
    cells         = ["    "] * (first_weekday)
    for d in all_days:
        mark = " ✗ " if d in missing_set else " · "
        cells.append(f"{d.day:2d}{mark}")
    for week_start in range(0, len(cells), 7):
        print("   " + " ".join(f"{c:4s}" for c in cells[week_start:week_start + 7]))
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Entry-point
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Comprueba programación en {base}_tv_schedule por mes o día.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("base", help="Nombre base de las tablas (p.e. teleradio_content)")
    parser.add_argument("año",  type=int, help="Año  (p.e. 1963)")
    parser.add_argument("mes",  type=int, help="Mes 1-12  (p.e. 4)")
    parser.add_argument(
        "día",
        nargs="?",
        type=int,
        default=None,
        metavar="DÍA",
        help="(Opcional) Día del mes. Si se indica, muestra la programación de ese día exacto.",
    )
    args = parser.parse_args()

    if not 1 <= args.mes <= 12:
        print("❌ El mes debe estar entre 1 y 12.", file=sys.stderr)
        sys.exit(1)

    missing_vars = [v for v in ("CRATEDB_URL", "CRATEDB_USERNAME") if not os.getenv(v)]
    if missing_vars:
        print(f"❌ Variables de entorno faltantes: {', '.join(missing_vars)}", file=sys.stderr)
        sys.exit(1)

    if args.día is not None:
        _, last_day = calendar.monthrange(args.año, args.mes)
        if not 1 <= args.día <= last_day:
            print(f"❌ El día debe estar entre 1 y {last_day} para {args.año}-{args.mes:02d}.",
                  file=sys.stderr)
            sys.exit(1)
        run_day(args.base, date(args.año, args.mes, args.día))
    else:
        run_month(args.base, args.año, args.mes)


if __name__ == "__main__":
    main()
