#!/usr/bin/env python3
"""Evalúa uno o varios ``cruces.jsonl`` del modo de análisis contra las etiquetas del conjunto B.

Solo stdlib; corre en el ThinkPad. Entradas sin video ni imágenes (Q3): los ``cruces.jsonl``
traídos de la Pi y ``b7_1_verdad_B_etiquetas.csv``.

Por cada configuración (``modelo@cfg``) y cada grupo ``n`` del CSV:

1. Junta los clips del grupo (``nNN_…``) ordenados por inicio. En el tramo en que dos clips
   seguidos se solapan (2-4 s de pregrabación), toma los cruces de UN solo clip: el que tenga
   más cruces en ese tramo (empate: el primero). Fuera del solape se suman.
2. **Nivel 1** (sin marcaciones): salidas detectadas dentro de ``[desde, hasta]`` contra
   ``personas_reales``: ``TP = min(det, real)``, ``FN = real - TP``, ``FP = max(0, det - real)``.
   Sensibilidad = ΣTP / Σreal; cruces falsos = ΣFP. La Dahua (``cruces_dahua``) se evalúa igual
   sobre los mismos grupos, como referencia.
3. **Nivel 2** (por grupo de ``checkOut``): ¿habría alertado (salidas > ``marcaron_checkout``)
   contra ¿debía alertar (``personas_reales`` > ``marcaron_checkout``)?

Uso:
    python3 tools/evaluar.py --etiquetas b7_1_verdad_B_etiquetas.csv --salida eval/ run1/cruces.jsonl run2/cruces.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

CASOS_DIFICILES = (5, 18, 21, 22, 25, 27, 28, 29)


@dataclass
class ClipInfo:
    nombre: str
    grupo: int | None
    inicio: datetime
    fin: datetime | None


@dataclass
class Grupo:
    n: int
    desde: datetime
    hasta: datetime
    marcaron: int
    dahua: int
    real: int
    juntas: str
    luz: str
    nota: str = ""


@dataclass
class ResultadoGrupo:
    n: int
    real: int
    dahua: int
    det: int
    marcaron: int

    @property
    def tp(self) -> int:
        return min(self.det, self.real)

    @property
    def fn(self) -> int:
        return self.real - self.tp

    @property
    def fp(self) -> int:
        return max(0, self.det - self.real)


@dataclass
class Datos:
    clips: dict[str, dict[str, ClipInfo]] = field(default_factory=lambda: defaultdict(dict))  # cfg → clip → info
    cruces: dict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))  # cfg → cruces


def leer_cruces(paths: list[Path]) -> Datos:
    d = Datos()
    vistos: set[tuple] = set()
    for p in paths:
        with p.open(encoding="utf-8") as fh:
            for linea in fh:
                linea = linea.strip()
                if not linea:
                    continue
                r = json.loads(linea)
                etiqueta = f"{r['modelo']}@{r['cfg']}"
                if r.get("kind") == "clip":
                    fin = datetime.fromisoformat(r["fin"]) if r.get("fin") else None
                    d.clips[etiqueta][r["clip"]] = ClipInfo(
                        r["clip"], r.get("grupo"), datetime.fromisoformat(r["inicio"]), fin
                    )
                elif r.get("kind") == "cruce":
                    k = (etiqueta, r["clip"], r["t_clip"], r["track"], r["sentido"])
                    if k in vistos:  # el mismo clip analizado en dos corridas
                        continue
                    vistos.add(k)
                    r["_t"] = datetime.fromisoformat(r["t_abs"])
                    d.cruces[etiqueta].append(r)
    return d


def leer_etiquetas(path: Path, tz: ZoneInfo) -> list[Grupo]:
    out = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            fecha = row["fecha"].strip()
            out.append(
                Grupo(
                    n=int(row["n"]),
                    desde=datetime.fromisoformat(f"{fecha}T{row['desde'].strip()}").replace(tzinfo=tz),
                    hasta=datetime.fromisoformat(f"{fecha}T{row['hasta'].strip()}").replace(tzinfo=tz),
                    marcaron=int(row["marcaron_checkout"]),
                    dahua=int(row["cruces_dahua"]),
                    real=int(row["personas_reales"]),
                    juntas=(row.get("juntas_separadas") or "").strip(),
                    luz=(row.get("luz") or "").strip(),
                    nota=(row.get("nota") or "").strip(),
                )
            )
    return out


def salidas_del_grupo(clips: list[ClipInfo], cruces: list[dict]) -> list[dict]:
    """Salidas de los clips de un grupo, resolviendo el solape entre clips seguidos."""
    clips = sorted(clips, key=lambda c: c.inicio)
    por_clip = {c.nombre: [r for r in cruces if r["clip"] == c.nombre and r["sentido"] == "salida"] for c in clips}
    for a, b in zip(clips, clips[1:], strict=False):
        if a.fin is None or a.fin <= b.inicio:
            continue
        ini, fin = b.inicio, a.fin
        en_a = [r for r in por_clip[a.nombre] if ini <= r["_t"] <= fin]
        en_b = [r for r in por_clip[b.nombre] if ini <= r["_t"] <= fin]
        if len(en_b) > len(en_a):
            por_clip[a.nombre] = [r for r in por_clip[a.nombre] if r not in en_a]
        else:
            por_clip[b.nombre] = [r for r in por_clip[b.nombre] if r not in en_b]
    return [r for c in clips for r in por_clip[c.nombre]]


def evaluar(datos: Datos, grupos: list[Grupo]) -> dict[str, list[ResultadoGrupo]]:
    res: dict[str, list[ResultadoGrupo]] = {}
    for etiqueta in sorted(datos.clips):
        clips = datos.clips[etiqueta]
        filas = []
        for g in grupos:
            del_grupo = [c for c in clips.values() if c.grupo == g.n]
            if not del_grupo:
                continue  # grupo no analizado con esta configuración
            sal = salidas_del_grupo(del_grupo, datos.cruces.get(etiqueta, []))
            det = sum(1 for r in sal if g.desde <= r["_t"] <= g.hasta)
            filas.append(ResultadoGrupo(g.n, g.real, g.dahua, det, g.marcaron))
        res[etiqueta] = filas
    return res


def _metricas(filas: list[ResultadoGrupo], usar_dahua: bool = False) -> dict[str, float | int]:
    tp = fn = fp = 0
    vp = fpa = fna = vn = 0
    for f in filas:
        det = f.dahua if usar_dahua else f.det
        r = ResultadoGrupo(f.n, f.real, f.dahua, det, f.marcaron)
        tp, fn, fp = tp + r.tp, fn + r.fn, fp + r.fp
        alerta, debia = det > f.marcaron, f.real > f.marcaron
        vp += alerta and debia
        fpa += alerta and not debia
        fna += (not alerta) and debia
        vn += (not alerta) and not debia
    real = sum(f.real for f in filas)
    return {
        "grupos": len(filas),
        "real": real,
        "tp": tp,
        "fn": fn,
        "fp": fp,
        "sensibilidad": round(tp / real, 3) if real else 0.0,
        "alerta_vp": vp,
        "alerta_fp": fpa,
        "alerta_fn": fna,
        "alerta_vn": vn,
    }


def informe(res: dict[str, list[ResultadoGrupo]], grupos: list[Grupo]) -> tuple[str, list[dict]]:
    por_n = {g.n: g for g in grupos}
    lineas = ["# Evaluación sobre el conjunto B", ""]
    lineas += [
        "## Nivel 1 y 2 por configuración",
        "",
        "| Config | Grupos | Reales | TP | FN | FP (cruces falsos) | Sensibilidad | Dahua (mismos grupos) "
        "| Alerta VP/FP/FN/VN |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    csv_filas: list[dict] = []
    for etiqueta, filas in res.items():
        m, md = _metricas(filas), _metricas(filas, usar_dahua=True)
        lineas.append(
            f"| {etiqueta} | {m['grupos']} | {m['real']} | {m['tp']} | {m['fn']} | {m['fp']} "
            f"| {m['sensibilidad']:.1%} | "
            f"{md['tp']}/{md['real']} = {md['sensibilidad']:.1%}, FP {md['fp']} | "
            f"{m['alerta_vp']}/{m['alerta_fp']}/{m['alerta_fn']}/{m['alerta_vn']} |"
        )
        for f in filas:
            g = por_n[f.n]
            csv_filas.append(
                {
                    "config": etiqueta,
                    "n": f.n,
                    "real": f.real,
                    "dahua": f.dahua,
                    "det": f.det,
                    "tp": f.tp,
                    "fn": f.fn,
                    "fp": f.fp,
                    "marcaron": f.marcaron,
                    "alerta": int(f.det > f.marcaron),
                    "debia_alertar": int(f.real > f.marcaron),
                    "juntas_separadas": g.juntas,
                    "luz": g.luz,
                }
            )
    for campo, titulo in (("juntas", "juntas_separadas"), ("luz", "luz")):
        lineas += [
            "",
            f"## Desglose por `{titulo}`",
            "",
            "| Config | Valor | Grupos | Reales | TP | FP | Sensibilidad | Dahua |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for etiqueta, filas in res.items():
            valores = sorted({getattr(por_n[f.n], campo) for f in filas})
            for v in valores:
                sub = [f for f in filas if getattr(por_n[f.n], campo) == v]
                m, md = _metricas(sub), _metricas(sub, usar_dahua=True)
                lineas.append(
                    f"| {etiqueta} | {v or '(vacío)'} | {m['grupos']} | {m['real']} | {m['tp']} | {m['fp']} | "
                    f"{m['sensibilidad']:.1%} | {md['sensibilidad']:.1%} |"
                )
    lineas += [
        "",
        "## Casos difíciles",
        "",
        "| n | Reales | Marcaron | Dahua | " + " | ".join(res) + " |",
        "|---|---|---|---|" + "---|" * len(res),
    ]
    for n in CASOS_DIFICILES:
        if n not in por_n:
            continue
        g = por_n[n]
        dets = []
        for filas in res.values():
            f = next((f for f in filas if f.n == n), None)
            dets.append(str(f.det) if f else "—")
        lineas.append(f"| {n} | {g.real} | {g.marcaron} | {g.dahua} | " + " | ".join(dets) + " |")
    return "\n".join(lineas) + "\n", csv_filas


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cruces", nargs="+", type=Path, help="uno o varios cruces.jsonl")
    ap.add_argument("--etiquetas", required=True, type=Path)
    ap.add_argument("--salida", type=Path, default=None, help="carpeta para evaluacion.md y evaluacion.csv")
    ap.add_argument("--tz", default="America/Bogota")
    a = ap.parse_args(argv)
    grupos = leer_etiquetas(a.etiquetas, ZoneInfo(a.tz))
    datos = leer_cruces(a.cruces)
    if not datos.clips:
        print("Ningún registro kind=clip en los cruces.jsonl: nada que evaluar.", file=sys.stderr)
        return 1
    md, filas = informe(evaluar(datos, grupos), grupos)
    print(md)
    if a.salida:
        a.salida.mkdir(parents=True, exist_ok=True)
        (a.salida / "evaluacion.md").write_text(md, encoding="utf-8")
        with (a.salida / "evaluacion.csv").open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(filas[0]) if filas else ["config"])
            w.writeheader()
            w.writerows(filas)
    return 0


if __name__ == "__main__":
    sys.exit(main())
