#!/usr/bin/env python3
"""
Convertit chaque couche de chaque fichier KML d'un dossier en un GeoJSON
distinct (EPSG:4326), enregistré dans le même dossier que les KML.
Les styles du KML (couleur, épaisseur, remplissage) sont conservés comme
propriétés de chaque entité (convention « simplestyle ») :
    stroke, stroke-opacity, stroke-width, fill, fill-opacity

Sortie : <nom_du_kml>__<nom_de_la_couche>.geojson

Usage :
    python kml_vers_geojson.py
    python kml_vers_geojson.py "C:\\autre\\dossier"
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

DOSSIER_PAR_DEFAUT = r"C:\Users\quart\OneDrive\Documents\Google Earth\KML"
CONSERVER_STYLES = True

TAGS_GEOMETRIE = {"Point", "LineString", "LinearRing", "Polygon",
                  "MultiGeometry", "Track", "MultiTrack", "Model"}


# --------------------------------------------------------------------------
# Localisation de GDAL (ogr2ogr / ogrinfo) fourni par QGIS / OSGeo4W
# --------------------------------------------------------------------------
def trouver_dossier_bin_gdal():
    """Retourne le dossier contenant ogr2ogr.exe, ou None."""
    exe = shutil.which("ogr2ogr")
    if exe:
        return Path(exe).parent
    motifs = [
        r"C:\OSGeo4W*\bin\ogr2ogr.exe",
        r"C:\Program Files\QGIS*\bin\ogr2ogr.exe",
        r"C:\Program Files (x86)\QGIS*\bin\ogr2ogr.exe",
    ]
    for motif in motifs:
        trouves = sorted(glob.glob(motif), reverse=True)
        if trouves:
            return Path(trouves[0]).parent
    return None


def preparer_environnement(bin_dir):
    """Prépare PATH / GDAL_DATA / PROJ_LIB pour que ogr2ogr fonctionne seul."""
    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    racine = bin_dir.parent
    for c in (racine / "apps" / "gdal" / "share" / "gdal", racine / "share" / "gdal"):
        if c.is_dir() and "GDAL_DATA" not in env:
            env["GDAL_DATA"] = str(c)
            break
    for c in (racine / "share" / "proj", racine / "apps" / "proj" / "share" / "proj"):
        if c.is_dir() and "PROJ_LIB" not in env:
            env["PROJ_LIB"] = str(c)
            break
    return env


# --------------------------------------------------------------------------
# Lecture des styles KML
# --------------------------------------------------------------------------
def _nom(tag):
    return tag.rsplit("}", 1)[-1]


def _couleur_kml(texte):
    """KML 'aabbggrr' -> ('#rrggbb', opacité 0-1)."""
    s = (texte or "").strip().lstrip("#")
    if len(s) != 8:
        return None, None
    try:
        a, b, g, r = s[0:2], s[2:4], s[4:6], s[6:8]
        return f"#{r}{g}{b}", round(int(a, 16) / 255, 3)
    except ValueError:
        return None, None


def _style_depuis_element(el):
    """Convertit un élément <Style> en propriétés simplestyle."""
    props = {}
    for enfant in el:
        n = _nom(enfant.tag)
        if n == "LineStyle":
            for c in enfant:
                cn = _nom(c.tag)
                if cn == "color":
                    col, op = _couleur_kml(c.text)
                    if col:
                        props["stroke"], props["stroke-opacity"] = col, op
                elif cn == "width":
                    try:
                        props["stroke-width"] = float(c.text)
                    except (TypeError, ValueError):
                        pass
        elif n == "PolyStyle":
            remplir, contour = True, True
            for c in enfant:
                cn = _nom(c.tag)
                if cn == "color":
                    col, op = _couleur_kml(c.text)
                    if col:
                        props["fill"], props["fill-opacity"] = col, op
                elif cn == "fill":
                    remplir = (c.text or "1").strip() != "0"
                elif cn == "outline":
                    contour = (c.text or "1").strip() != "0"
            if not remplir:
                props["fill-opacity"] = 0
            if not contour:
                props["stroke-opacity"] = 0
    return props


def lire_styles_par_couche(kml):
    """
    Parcourt le KML et retourne {nom_de_couche: [liste par occurrence de
    dossier : [propriétés de style de chaque Placemark, dans l'ordre]]}.
    """
    styles, stylemaps, dossiers = {}, {}, []
    pile, pm = [], None

    for evenement, el in ET.iterparse(str(kml), events=("start", "end")):
        tag = _nom(el.tag)

        if evenement == "start":
            entree = {"tag": tag}
            if tag in ("Document", "Folder"):
                dossiers.append({"name": None, "pms": []})
                entree["f"] = len(dossiers) - 1
            elif tag == "Placemark":
                courant = next((e["f"] for e in reversed(pile) if "f" in e), None)
                pm = {"geom": False, "url": None, "inline": None, "folder": courant}
            elif pm is not None and tag in TAGS_GEOMETRIE:
                pm["geom"] = True
            pile.append(entree)
            continue

        # --- evenement == "end" ---
        entree = pile.pop()
        parent = pile[-1] if pile else {}
        texte = (el.text or "").strip()

        if tag == "name" and parent.get("tag") in ("Document", "Folder"):
            if dossiers[parent["f"]]["name"] is None:
                dossiers[parent["f"]]["name"] = texte
        elif tag == "styleUrl":
            if parent.get("tag") == "Placemark" and pm is not None:
                pm["url"] = texte
            elif parent.get("tag") == "Pair":
                parent["url"] = texte
        elif tag == "key" and parent.get("tag") == "Pair":
            parent["key"] = texte
        elif tag == "Pair":
            if entree.get("key") == "normal" and entree.get("url"):
                parent["normal"] = entree["url"]
        elif tag == "StyleMap":
            sid = el.get("id")
            if sid and entree.get("normal"):
                stylemaps[sid] = entree["normal"]
        elif tag == "Style":
            if parent.get("tag") == "Placemark" and pm is not None:
                pm["inline"] = _style_depuis_element(el)
            elif parent.get("tag") != "Pair" and el.get("id"):
                styles[el.get("id")] = _style_depuis_element(el)
        elif tag == "Placemark":
            if pm is not None and pm["geom"] and pm["folder"] is not None:
                dossiers[pm["folder"]]["pms"].append(pm)
            pm = None
            el.clear()

    def resoudre(p):
        props = {}
        url = p["url"]
        if url and url.startswith("#"):
            sid = url[1:]
            if sid in stylemaps:
                sid = stylemaps[sid].lstrip("#")
            props.update(styles.get(sid, {}))
        if p["inline"]:
            props.update(p["inline"])
        return props

    par_couche = {}
    for d in dossiers:
        if d["name"] and d["pms"]:
            par_couche.setdefault(d["name"], []).append([resoudre(p) for p in d["pms"]])
    return par_couche


# --------------------------------------------------------------------------
# Outils GeoJSON / fichiers
# --------------------------------------------------------------------------
def lister_couches(ogrinfo, env, kml):
    """Liste les noms de couches d'un KML via ogrinfo."""
    r = subprocess.run(
        [str(ogrinfo), "-so", "-q", str(kml)],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=env,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "ogrinfo a échoué")
    couches = []
    for ligne in r.stdout.splitlines():
        m = re.match(r"^\s*\d+:\s+(.*?)(?:\s+\([^()]*\))?\s*$", ligne)
        if m and m.group(1):
            couches.append(m.group(1))
    return couches


def nom_fichier_sur(texte):
    texte = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", texte).strip(" .")
    return texte or "couche"


def est_vide(chemin):
    try:
        if chemin.stat().st_size > 2000:
            return False
        with open(chemin, encoding="utf-8") as f:
            return len(json.load(f).get("features", [])) == 0
    except Exception:
        return False


def appliquer_styles(chemin, styles_placemarks):
    """
    Ajoute les propriétés de style aux entités du GeoJSON.
    Retourne (ok, message). Le fichier n'est modifié que si le nombre
    d'entités correspond exactement au nombre de Placemarks du KML.
    """
    with open(chemin, encoding="utf-8") as f:
        data = json.load(f)
    entites = data.get("features", [])
    if len(entites) != len(styles_placemarks):
        return False, (f"styles non appliqués : {len(entites)} entités "
                       f"vs {len(styles_placemarks)} Placemarks dans le KML")
    for ft, st in zip(entites, styles_placemarks):
        if st:
            if ft.get("properties") is None:
                ft["properties"] = {}
            ft["properties"].update(st)

    extras = {k: v for k, v in data.items() if k not in ("type", "features")}
    with open(chemin, "w", encoding="utf-8") as f:
        f.write('{"type": "FeatureCollection", ')
        for k, v in extras.items():
            f.write(f'{json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}, ')
        f.write('"features": [\n')
        f.write(",\n".join(json.dumps(ft, ensure_ascii=False) for ft in entites))
        f.write("\n]}\n")
    return True, "styles ajoutés"


# --------------------------------------------------------------------------
# Programme principal
# --------------------------------------------------------------------------
def main():
    dossier = Path(sys.argv[1] if len(sys.argv) > 1 else DOSSIER_PAR_DEFAUT)
    if not dossier.is_dir():
        sys.exit(f"Dossier introuvable : {dossier}")

    bin_dir = trouver_dossier_bin_gdal()
    if bin_dir is None:
        sys.exit(
            "ogr2ogr est introuvable. Lance le script depuis le « OSGeo4W Shell » "
            "ou ajoute le dossier bin de QGIS/OSGeo4W à ton PATH."
        )
    env = preparer_environnement(bin_dir)
    ogr2ogr = bin_dir / "ogr2ogr.exe"
    ogrinfo = bin_dir / "ogrinfo.exe"
    print(f"GDAL : {bin_dir}\n")

    kmls = sorted(p for p in dossier.iterdir() if p.suffix.lower() == ".kml")
    if not kmls:
        sys.exit(f"Aucun fichier .kml dans {dossier}")

    total_ok, total_vides, total_erreurs = 0, 0, 0

    for kml in kmls:
        print(f"=== {kml.name}")
        try:
            couches = lister_couches(ogrinfo, env, kml)
        except Exception as e:
            print(f"  ERREUR (lecture des couches) : {e}\n")
            total_erreurs += 1
            continue
        if not couches:
            print("  Aucune couche trouvée.\n")
            continue

        styles_kml = {}
        if CONSERVER_STYLES:
            try:
                styles_kml = lire_styles_par_couche(kml)
            except Exception as e:
                print(f"  (!) Styles illisibles, conversion sans styles : {e}")

        noms_fichiers, occurrences = {}, {}
        for couche in couches:
            base = f"{kml.stem}__{nom_fichier_sur(couche)}"
            n = noms_fichiers.get(base, 0) + 1
            noms_fichiers[base] = n
            sortie = dossier / (f"{base}.geojson" if n == 1 else f"{base}_{n}.geojson")

            occ = occurrences.get(couche, 0)
            occurrences[couche] = occ + 1

            if sortie.exists():
                sortie.unlink()

            cmd = [
                str(ogr2ogr), "-f", "GeoJSON",
                "-t_srs", "EPSG:4326",
                "-lco", "RFC7946=YES",
                "-lco", "COORDINATE_PRECISION=8",
                str(sortie), str(kml), couche,
            ]
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", env=env)

            if r.returncode != 0 or not sortie.exists():
                print(f"  ✗ {couche} : {r.stderr.strip() or 'échec'}")
                total_erreurs += 1
                continue
            if est_vide(sortie):
                sortie.unlink()
                print(f"  - {couche} : couche vide, ignorée")
                total_vides += 1
                continue

            note = ""
            if CONSERVER_STYLES:
                liste = styles_kml.get(couche, [])
                if occ < len(liste):
                    try:
                        _, msg = appliquer_styles(sortie, liste[occ])
                        note = f" — {msg}"
                    except Exception as e:
                        note = f" — styles non appliqués ({e})"
                else:
                    note = " — styles introuvables pour cette couche"

            ko = sortie.stat().st_size / 1024
            print(f"  ✓ {couche} -> {sortie.name} ({ko:,.0f} Ko){note}")
            total_ok += 1
        print()

    print(f"Terminé : {total_ok} créé(s), {total_vides} vide(s), {total_erreurs} erreur(s).")


if __name__ == "__main__":
    main()
