#!/usr/bin/env python3
"""Evalúa uno o varios ``cruces.jsonl`` del modo de análisis contra las etiquetas del conjunto B.

Solo stdlib; corre en el ThinkPad. Entradas sin video ni imágenes (Q3): los ``cruces.jsonl``
traídos de la Pi y ``b7_1_verdad_B_etiquetas.csv``.

Por cada configuración (``modelo@cfg``) y cada grupo ``n`` del CSV:

1. Junta los clips del grupo (``nNN_…``) ordenados por inicio. El tramo de cada clip es
   ``[inicio, inicio + dur_video_s]`` (sin ``dur_video_s``: ``[inicio, fin del nombre]``); el
   nombre trunca al segundo y el video real dura más. En el tramo en que dos clips seguidos se
   solapan (2-4 s de pregrabación), toma los cruces de UN solo clip, salidas y entradas por
   igual: el que tenga más cruces en ese tramo, contando los dos sentidos (empate: el primero).
   Fuera del solape se suman.
2. **Ventana** (``--ventana``): ``clips`` (por defecto) cuenta los cruces de todo el tramo de
   los clips del grupo (unión de sus tramos) e ignora ``desde``/``hasta``; ``planilla`` cuenta
   solo los de ``[desde, hasta]``, para comparar. El modo queda en el encabezado del informe.
3. **Unidad:** ``det = max(0, salidas - entradas)`` por grupo; quien sale, vuelve y sale otra
   vez es una persona. ``evaluacion.csv`` trae también ``salidas`` y ``entradas``.
4. **Personas medibles:** columna opcional ``no_medibles`` del CSV de etiquetas (entero; vacía o
   ausente = 0), para quien cruza en el borde del clip y no se puede medir.
   ``real_medible = max(0, personas_reales - no_medibles)`` es la verdad de los niveles 1 y 2,
   para el detector y para la Dahua. El total de no medibles se informa siempre.
5. **Nivel 1** (sin marcaciones): ``det`` contra ``real_medible``: ``TP = min(det, real)``,
   ``FN = real - TP``, ``FP = max(0, det - real)``. Sensibilidad = ΣTP / Σreal; cruces
   falsos = ΣFP. La Dahua (``cruces_dahua``, tal cual) se evalúa igual sobre los mismos grupos,
   como referencia.
6. **Nivel 2** (por grupo de ``checkOut``): ¿habría alertado (``det`` > ``marcaron_checkout``)
   contra ¿debía alertar (``real_medible`` > ``marcaron_checkout``)?

Uso:
    python3 tools/evaluar.py --etiquetas b7_1_verdad_B_etiquetas.csv --salida eval/ \
        [--ventana clips|planilla] run1/cruces.jsonl run2/cruces.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

CASOS_DIFICILES = (5, 18, 21, 22, 25, 27, 28, 29)
VENTANAS = ("clips", "planilla")


@dataclass
class ClipInfo:
    nombre: str
    grupo: int | None
    inicio: datetime
    fin: datetime | None  # hora del nombre, truncada al segundo
    dur_video_s: float | None = None

    @property
    def fin_efectivo(self) -> datetime | None:
        """Final real del video: ``inicio + dur_video_s``; sin duración, el del nombre."""
        if self.dur_video_s is not None:
            return self.inicio + timedelta(seconds=self.dur_video_s)
        return self.fin


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
    no_medibles: int = 0


@dataclass
class ResultadoGrupo:
    n: int
    real: int
    dahua: int
    det: int
    marcaron: int
    salidas: int = 0
    entradas: int = 0
    no_medibles: int = 0

    @property
    def real_medible(self) -> int:
        return max(0, self.real - self.no_medibles)

    @property
    def tp(self) -> int:
        return min(self.det, self.real_medible)

    @property
    def fn(self) -> int:
        return self.real_medible - self.tp

    @property
    def fp(self) -> int:
        return max(0, self.det - self.real_medible)


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
                    dur = r.get("dur_video_s")
                    d.clips[etiqueta][r["clip"]] = ClipInfo(
                        r["clip"],
                        r.get("grupo"),
                        datetime.fromisoformat(r["inicio"]),
                        fin,
                        float(dur) if dur is not None else None,
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
                    no_medibles=int((row.get("no_medibles") or "").strip() or 0),
                )
            )
    return out


def cruces_del_grupo(clips: list[ClipInfo], cruces: list[dict]) -> list[dict]:
    """Cruces (salidas y entradas) de los clips de un grupo, resolviendo el solape entre clips seguidos.

    El solape va de ``b.inicio`` al final efectivo de ``a`` (``inicio + dur_video_s``); ahí se
    quedan los cruces del clip con más cruces en ese tramo, contando los dos sentidos.
    """
    clips = sorted(clips, key=lambda c: c.inicio)
    por_clip = {c.nombre: [r for r in cruces if r["clip"] == c.nombre] for c in clips}
    for a, b in zip(clips, clips[1:], strict=False):
        fin_a = a.fin_efectivo
        if fin_a is None or fin_a <= b.inicio:
            continue
        ini, fin = b.inicio, fin_a
        en_a = [r for r in por_clip[a.nombre] if ini <= r["_t"] <= fin]
        en_b = [r for r in por_clip[b.nombre] if ini <= r["_t"] <= fin]
        if len(en_b) > len(en_a):
            por_clip[a.nombre] = [r for r in por_clip[a.nombre] if r not in en_a]
        else:
            por_clip[b.nombre] = [r for r in por_clip[b.nombre] if r not in en_b]
    return [r for c in clips for r in por_clip[c.nombre]]


def salidas_del_grupo(clips: list[ClipInfo], cruces: list[dict]) -> list[dict]:
    """Solo las salidas de :func:`cruces_del_grupo`."""
    return [r for r in cruces_del_grupo(clips, cruces) if r["sentido"] == "salida"]


def _en_tramo_de_clips(t: datetime, clips: list[ClipInfo]) -> bool:
    """¿``t`` cae en la unión de los tramos ``[inicio, fin efectivo]`` de los clips?"""
    return any(c.inicio <= t and (c.fin_efectivo is None or t <= c.fin_efectivo) for c in clips)


def evaluar(datos: Datos, grupos: list[Grupo], ventana: str = "clips") -> dict[str, list[ResultadoGrupo]]:
    if ventana not in VENTANAS:
        raise ValueError(f"ventana debe ser una de {VENTANAS}: {ventana!r}")
    res: dict[str, list[ResultadoGrupo]] = {}
    for etiqueta in sorted(datos.clips):
        clips = datos.clips[etiqueta]
        filas = []
        for g in grupos:
            del_grupo = [c for c in clips.values() if c.grupo == g.n]
            if not del_grupo:
                continue  # grupo no analizado con esta configuración
            cr = cruces_del_grupo(del_grupo, datos.cruces.get(etiqueta, []))
            if ventana == "planilla":
                cr = [r for r in cr if g.desde <= r["_t"] <= g.hasta]
            else:
                cr = [r for r in cr if _en_tramo_de_clips(r["_t"], del_grupo)]
            sal = sum(1 for r in cr if r["sentido"] == "salida")
            ent = sum(1 for r in cr if r["sentido"] == "entrada")
            filas.append(ResultadoGrupo(g.n, g.real, g.dahua, max(0, sal - ent), g.marcaron, sal, ent, g.no_medibles))
        res[etiqueta] = filas
    return res


def _metricas(filas: list[ResultadoGrupo], usar_dahua: bool = False) -> dict[str, float | int]:
    tp = fn = fp = 0
    vp = fpa = fna = vn = 0
    for f in filas:
        det = f.dahua if usar_dahua else f.det
        r = ResultadoGrupo(f.n, f.real, f.dahua, det, f.marcaron, no_medibles=f.no_medibles)
        tp, fn, fp = tp + r.tp, fn + r.fn, fp + r.fp
        alerta, debia = det > f.marcaron, f.real_medible > f.marcaron
        vp += alerta and debia
        fpa += alerta and not debia
        fna += (not alerta) and debia
        vn += (not alerta) and not debia
    real = sum(f.real_medible for f in filas)
    return {
        "grupos": len(filas),
        "real": real,
        "real_total": sum(f.real for f in filas),
        "no_medibles": sum(f.real - f.real_medible for f in filas),
        "tp": tp,
        "fn": fn,
        "fp": fp,
        "sensibilidad": round(tp / real, 3) if real else 0.0,
        "alerta_vp": vp,
        "alerta_fp": fpa,
        "alerta_fn": fna,
        "alerta_vn": vn,
    }


def informe(
    res: dict[str, list[ResultadoGrupo]], grupos: list[Grupo], ventana: str = "clips"
) -> tuple[str, list[dict]]:
    por_n = {g.n: g for g in grupos}
    desc = {
        "clips": "tramo completo de los clips de cada grupo (`desde`/`hasta` ignorados)",
        "planilla": "`[desde, hasta]` de la planilla",
    }[ventana]
    lineas = ["# Evaluación sobre el conjunto B", ""]
    lineas += [
        f"Ventana: `{ventana}` — {desc}. Detector: `det = max(0, salidas − entradas)` por grupo.",
        "Reales = personas medibles (`personas_reales − no_medibles`).",
        "",
    ]
    lineas += [
        "## Nivel 1 y 2 por configuración",
        "",
        "| Config | Grupos | Reales | TP | FN | FP (cruces falsos) | Sensibilidad | Dahua (mismos grupos) "
        "| Alerta VP/FP/FN/VN |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    csv_filas: list[dict] = []
    no_medibles: list[str] = []
    for etiqueta, filas in res.items():
        m, md = _metricas(filas), _metricas(filas, usar_dahua=True)
        lineas.append(
            f"| {etiqueta} | {m['grupos']} | {m['real']} | {m['tp']} | {m['fn']} | {m['fp']} "
            f"| {m['sensibilidad']:.1%} | "
            f"{md['tp']}/{md['real']} = {md['sensibilidad']:.1%}, FP {md['fp']} | "
            f"{m['alerta_vp']}/{m['alerta_fp']}/{m['alerta_fn']}/{m['alerta_vn']} |"
        )
        no_medibles.append(
            f"- No medibles `{etiqueta}`: {m['no_medibles']} de {m['real_total']} personas reales "
            "(fuera de TP/FN/FP, del detector y de la Dahua)."
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
                    "salidas": f.salidas,
                    "entradas": f.entradas,
                    "no_medibles": f.no_medibles,
                    "real_medible": f.real_medible,
                    "marcaron": f.marcaron,
                    "alerta": int(f.det > f.marcaron),
                    "debia_alertar": int(f.real_medible > f.marcaron),
                    "juntas_separadas": g.juntas,
                    "luz": g.luz,
                }
            )
    lineas += ["", *no_medibles]
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
        "| n | Reales | No medibles | Marcaron | Dahua | " + " | ".join(res) + " |",
        "|---|---|---|---|---|" + "---|" * len(res),
    ]
    for n in CASOS_DIFICILES:
        if n not in por_n:
            continue
        g = por_n[n]
        dets = []
        for filas in res.values():
            f = next((f for f in filas if f.n == n), None)
            dets.append(str(f.det) if f else "—")
        lineas.append(f"| {n} | {g.real} | {g.no_medibles} | {g.marcaron} | {g.dahua} | " + " | ".join(dets) + " |")
    return "\n".join(lineas) + "\n", csv_filas


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cruces", nargs="+", type=Path, help="uno o varios cruces.jsonl")
    ap.add_argument("--etiquetas", required=True, type=Path)
    ap.add_argument("--salida", type=Path, default=None, help="carpeta para evaluacion.md y evaluacion.csv")
    ap.add_argument("--tz", default="America/Bogota")
    ap.add_argument(
        "--ventana",
        choices=VENTANAS,
        default="clips",
        help="clips (por defecto): tramo completo de los clips de cada grupo, ignora desde/hasta; "
        "planilla: solo los cruces dentro de [desde, hasta], para comparar",
    )
    a = ap.parse_args(argv)
    grupos = leer_etiquetas(a.etiquetas, ZoneInfo(a.tz))
    datos = leer_cruces(a.cruces)
    if not datos.clips:
        print("Ningún registro kind=clip en los cruces.jsonl: nada que evaluar.", file=sys.stderr)
        return 1
    md, filas = informe(evaluar(datos, grupos, a.ventana), grupos, a.ventana)
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
