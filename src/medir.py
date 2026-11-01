"""
Corre el motor sobre las 5 consultas de data/consultas.json y arma la tabla de medición:
¿validó?, tokens (entrada/salida), costo USD y latencia ms, más los promedios y la
comparación con la meta del KPI técnico de la PE1.

Uso:  python -m src.medir
Guarda: resultados/mediciones.json y resultados/mediciones.md
"""
import json
import os
import time
from pathlib import Path
from statistics import mean

from src.motor import Motor, RAIZ

META_KPI_TECNICO = 0.95  # PE1: ≥95% de las altas con datos físicos coherentes
PAUSA_S = float(os.getenv("PAUSA_ENTRE_LLAMADAS_S", 2))  # respeta los límites del nivel gratuito


def main() -> None:
    consultas = json.loads((RAIZ / "data" / "consultas.json").read_text(encoding="utf-8"))
    motor = Motor()
    filas = []
    for i, c in enumerate(consultas, 1):
        if i > 1:
            time.sleep(PAUSA_S)
        r = motor.procesar(c["entrada"])
        m = r.metricas
        estado = r.salida.estado.value if r.ok else r.error.codigo
        filas.append({
            "n": i, "consulta": c["nombre"], "valido": r.ok,
            "estado": estado, "esperado": c["esperado"], "acierto": estado == c["esperado"],
            "input_tokens": m.input_tokens if m else 0, "output_tokens": m.output_tokens if m else 0,
            "costo_usd": m.costo_usd if m else 0.0, "latencia_ms": m.latencia_ms if m else 0,
            "intentos": m.intentos if m else 0,
            "resultado": r.model_dump(mode="json"),
        })

    prom = {k: mean(f[k] for f in filas) for k in ("input_tokens", "output_tokens", "costo_usd", "latencia_ms")}
    tasa_valida = sum(f["valido"] for f in filas) / len(filas)
    tasa_acierto = sum(f["acierto"] for f in filas) / len(filas)

    lineas = [
        f"Modelo: {motor.proveedor.modelo}",
        "",
        "| # | Consulta | ¿Validó? | Estado (esperado) | Tokens (entrada / salida) | Costo USD (tarifa paga) | Latencia ms |",
        "|---|---|---|---|---|---|---|",
    ]
    for f in filas:
        lineas.append(
            f"| {f['n']} | {f['consulta']} | {'Sí' if f['valido'] else 'No'} | {f['estado']} ({f['esperado']}) "
            f"| {f['input_tokens']} / {f['output_tokens']} | {f['costo_usd']:.6f} | {f['latencia_ms']} |")
    lineas.append(
        f"| | **Promedio** | {tasa_valida:.0%} | {tasa_acierto:.0%} acierto | "
        f"{prom['input_tokens']:.0f} / {prom['output_tokens']:.0f} | {prom['costo_usd']:.6f} | {prom['latencia_ms']:.0f} |")
    lineas += [
        "",
        f"KPI técnico PE1 (meta ≥{META_KPI_TECNICO:.0%} de datos coherentes): "
        f"acierto de estado en esta muestra = {tasa_acierto:.0%} → "
        f"{'cumple' if tasa_acierto >= META_KPI_TECNICO else 'no cumple'} (muestra de {len(filas)}, indicativa).",
        "Costo: hipotético con la tarifa paga publicada; en el nivel gratuito el costo real es USD 0.",
    ]
    tabla = "\n".join(lineas)
    print("\n" + tabla)

    salida_dir = RAIZ / "resultados"
    salida_dir.mkdir(exist_ok=True)
    (salida_dir / "mediciones.md").write_text(tabla + "\n", encoding="utf-8")
    (salida_dir / "mediciones.json").write_text(
        json.dumps({"filas": filas, "promedios": prom, "tasa_valida": tasa_valida,
                    "tasa_acierto": tasa_acierto}, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
