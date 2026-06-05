#!/usr/bin/env python3
"""
playlist_to_stepmania.py
------------------------
Extrae las canciones de una playlist de YouTube y genera un informe (HTML + CSV)
con enlaces de busqueda directos a webs de simfiles de StepMania para localizar
y descargar los stepcharts de cada cancion.

Webs de busqueda usadas:
  - Zenius-I-vanisher (ZIv)  -> via Google "site:" (su buscador interno es POST,
                                no enlazable; el site-search de Google es fiable)
  - StepManiaOnline (SMO)    -> https://search.stepmaniaonline.net/?q=...
  - Google generico          -> "<artista> <titulo> stepmania simfile"

Uso:
  python playlist_to_stepmania.py "URL_DE_LA_PLAYLIST"
  python playlist_to_stepmania.py "URL" --out informe          (prefijo de salida)
  python playlist_to_stepmania.py "URL" --download-audio       (baja tambien MP3s)
  python playlist_to_stepmania.py "URL" --no-open              (no abrir el HTML)

Requisitos: yt-dlp  (pip install yt-dlp)
            Para --download-audio tambien hace falta ffmpeg en el PATH.
"""

from __future__ import annotations

import argparse
import csv
import html
import re
import sys
import urllib.parse
import webbrowser
from pathlib import Path

try:
    from yt_dlp import YoutubeDL
except ImportError:
    sys.exit("Falta yt-dlp. Instalalo con:  pip install yt-dlp")


# --- Limpieza de titulos -----------------------------------------------------

# Etiquetas de ruido que se eliminan del titulo (sin distinguir mayusculas).
_NOISE_PATTERNS = [
    r"\(.*?official.*?\)",
    r"\[.*?official.*?\]",
    r"\(.*?music\s*video.*?\)",
    r"\[.*?music\s*video.*?\]",
    r"\(.*?lyric.*?\)",
    r"\[.*?lyric.*?\]",
    r"\(.*?audio.*?\)",
    r"\[.*?audio.*?\]",
    r"\(.*?visualizer.*?\)",
    r"\[.*?(mv|m/v|pv)\]",
    r"（[^）]*?official[^）]*?）",   # parentesis japoneses con 'official'
    r"（[^）]*?video[^）]*?）",
    r"\b(official\s*(music\s*)?video|official\s*audio|lyric\s*video|music\s*video)\b",
    r"\bM/?V\b",
    r"【[^】]*】",          # corchetes japoneses (vocaloid, etc.)
    r"\bHD\b",
    r"\b4K\b",
]

# "feat. X", "ft. X", "featuring X" -> se guarda aparte como colaboradores.
_FEAT_RE = re.compile(r"\s*[\(\[]?\s*(?:feat\.?|ft\.?|featuring)\s+([^\)\]\-|]+)[\)\]]?",
                      re.IGNORECASE)

# Sufijos tipicos de nombres de canal de YouTube que no forman parte del artista.
_UPLOADER_NOISE_RE = re.compile(
    r"\s*(?:-\s*Topic|VEVO|Official|Officiel|Records?|Music|Channel|TV|Entertainment)\s*$",
    re.IGNORECASE,
)


def _clean_uploader(name: str) -> str:
    """Limpia el nombre del canal para usarlo como artista (quita '- Topic', etc.)."""
    name = (name or "").strip()
    prev = None
    while name and name != prev:
        prev = name
        name = _UPLOADER_NOISE_RE.sub("", name).strip(" -–—·|").strip()
    return name


def _strip_feat(text: str) -> tuple[str, str]:
    """Quita 'feat./ft.' de 'text' y devuelve (texto_limpio, colaboradores)."""
    feat = ""
    m = _FEAT_RE.search(text)
    if m:
        feat = m.group(1).strip(" -　")
        text = _FEAT_RE.sub("", text)
    return text.strip(), feat


def clean_title(raw: str, uploader: str = "") -> dict:
    """Devuelve {artist, song, feat, query} a partir del titulo de YouTube.

    Si el titulo no trae el patron 'Artista - Titulo', se usa el nombre del
    canal (``uploader``) como artista de respaldo.
    """
    title = raw.strip()

    # 1) Quitar etiquetas de ruido (official video, [MV], etc.).
    for pat in _NOISE_PATTERNS:
        title = re.sub(pat, "", title, flags=re.IGNORECASE)
    # Parentesis/comillas vacios que pudieran quedar tras la limpieza.
    title = re.sub(r"[\(\[（【「]\s*[\)\]）】」]", "", title)
    title = re.sub(r"\s{2,}", " ", title).strip(" -|–—~・　").strip()

    # 2) Separar "Artista - Titulo" si procede.
    artist, song = "", title
    for sep in (" - ", " – ", " — ", " ‐ "):
        if sep in title:
            left, right = title.split(sep, 1)
            left, right = left.strip(), right.strip()
            if left and right:
                artist, song = left, right
                break

    # 3) Quitar 'feat.' de cada parte (puede estar en el artista o en el titulo).
    artist, feat_a = _strip_feat(artist)
    song, feat_s = _strip_feat(song)
    feat = feat_s or feat_a

    song = song.strip(" -|–—~・　\"'“”「」").strip()
    artist = artist.strip(" -|–—~・　\"'“”").strip()

    # 4) Si no hay artista en el titulo, usar el canal como respaldo.
    if not artist:
        artist = _clean_uploader(uploader)

    query = song if song else title
    return {
        "artist": artist,
        "song": song or title,
        "feat": feat,
        "query": query.strip(),
    }


# --- Generacion de enlaces ---------------------------------------------------

def build_links(info: dict) -> dict:
    song = info["song"].strip()
    artist = info["artist"].strip()

    # StepManiaOnline busca por path: /search/title/<termino>. Su buscador trata
    # la "/" como separador de ruta, asi que se elimina del termino de busqueda.
    smo_term = re.sub(r"\s*/\s*", " ", song).strip()
    smo_term = re.sub(r"\s{2,}", " ", smo_term)
    smo = "https://stepmaniaonline.net/search/title/" + urllib.parse.quote(smo_term, safe="")

    # ZIv: busqueda en Google con site:, incluyendo el artista si existe.
    ziv_terms = f'"{song}"'
    if artist:
        ziv_terms += f" {artist}"
    ziv = ("https://www.google.com/search?q="
           + urllib.parse.quote_plus(f"site:zenius-i-vanisher.com {ziv_terms}"))

    google = ("https://www.google.com/search?q="
              + urllib.parse.quote_plus(f"{artist} {song} stepmania simfile sm download".strip()))
    return {"ziv": ziv, "smo": smo, "google": google}


# --- Extraccion de la playlist ----------------------------------------------

def extract_playlist(url: str) -> list[dict]:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,      # solo metadatos, no descarga
        "skip_download": True,
    }
    with YoutubeDL(opts) as ydl:
        data = ydl.extract_info(url, download=False)

    entries = data.get("entries") or []
    songs = []
    for i, e in enumerate(entries, 1):
        if not e:
            continue
        raw_title = e.get("title") or "(sin titulo)"
        vid = e.get("id") or ""
        watch = e.get("url") or (f"https://www.youtube.com/watch?v={vid}" if vid else "")
        if watch and not watch.startswith("http"):
            watch = f"https://www.youtube.com/watch?v={watch}"
        uploader = e.get("uploader") or e.get("channel") or ""
        parsed = clean_title(raw_title, uploader)
        songs.append({
            "index": i,
            "raw_title": raw_title,
            "uploader": uploader,
            "video_id": vid,
            "youtube_url": watch,
            **parsed,
            **build_links(parsed),
        })
    return songs


# --- Salidas -----------------------------------------------------------------

def write_csv(songs: list[dict], path: Path) -> None:
    cols = ["index", "raw_title", "artist", "song", "feat", "uploader",
            "youtube_url", "ziv", "smo", "google"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for s in songs:
            w.writerow(s)


def render_rows(songs: list[dict], status_map: dict | None = None) -> str:
    """Genera el HTML de las filas <tr> de la tabla de canciones.

    Si se pasa ``status_map`` (dict video_id -> estado), se anade una columna
    interactiva con botones para marcar cada cancion como descargada / no
    encontrada / pendiente. El informe estatico no lo usa.
    """
    def esc(x: str) -> str:
        return html.escape(x or "")

    interactive = status_map is not None
    rows = []
    for s in songs:
        artist = esc(s["artist"])
        title = esc(s["song"])
        artist_disp = (f'<span class="artist copyable" data-copy="{artist}" '
                       f'title="Clic para copiar el artista">{artist}</span>') if s["artist"] else ""
        title_disp = (f'<span class="song copyable" data-copy="{title}" '
                      f'title="Clic para copiar el título">{title}</span>')
        feat_disp = f'<span class="feat">feat. {esc(s["feat"])}</span>' if s["feat"] else ""

        status_cell = ""
        row_attrs = ""
        if interactive:
            vid = esc(s.get("video_id", ""))
            state = (status_map or {}).get(s.get("video_id", ""), "pending")
            row_attrs = f' data-vid="{vid}" data-status="{esc(state)}"'
            status_cell = f"""
          <td class="status">
            <button class="sbtn ok"      data-set="downloaded" title="Descargada">✔</button>
            <button class="sbtn missing" data-set="notfound"   title="No encontrada">✖</button>
            <button class="sbtn clear"   data-set="pending"    title="Pendiente">○</button>
          </td>"""

        rows.append(f"""
        <tr{row_attrs}>
          <td class="num">{s['index']}</td>
          <td class="title">{artist_disp}{title_disp}{feat_disp}
              <div class="raw">{esc(s['raw_title'])}</div></td>
          <td class="links">
            <a class="btn yt"  href="{esc(s['youtube_url'])}" target="_blank">YouTube</a>
            <a class="btn ziv" href="{esc(s['ziv'])}" target="_blank">Zenius-I-vanisher</a>
            <a class="btn smo" href="{esc(s['smo'])}" target="_blank">StepManiaOnline</a>
            <a class="btn gg"  href="{esc(s['google'])}" target="_blank">Google</a>
          </td>{status_cell}
        </tr>""")
    return "".join(rows)


# Estilos compartidos entre el informe estatico y la web (http.server).
REPORT_CSS = """
  :root { color-scheme: dark; }
  body { font-family: system-ui, Segoe UI, sans-serif; margin: 0; background:#121316; color:#e7e7ea; }
  header { padding: 20px 24px; background:#1b1d22; border-bottom:1px solid #2a2d34; }
  header h1 { margin:0 0 6px; font-size:20px; }
  header p { margin:0; color:#9aa0ad; font-size:13px; }
  header a { color:#7db4ff; }
  table { width:100%; border-collapse:collapse; }
  td { padding:10px 14px; border-bottom:1px solid #23262c; vertical-align:top; }
  .num { color:#6a6f7a; width:42px; text-align:right; font-variant-numeric:tabular-nums; }
  .title { font-size:15px; }
  .artist { display:inline-block; font-size:11px; text-transform:uppercase; letter-spacing:.04em;
            color:#cfa6ff; background:#241c38; border:1px solid #3a2e5c; border-radius:5px;
            padding:1px 7px; margin-bottom:4px; }
  .song { display:block; font-size:15px; font-weight:600; color:#f2f2f5; }
  .feat { display:block; color:#8a8f99; font-size:12px; margin-top:2px; }
  .raw { color:#6a6f7a; font-size:11px; margin-top:3px; }
  .copyable { cursor:pointer; position:relative; }
  .copyable:hover { filter:brightness(1.2); text-decoration:underline dotted; }
  .copyable.copied::after {
      content:"Copiado ✓"; position:absolute; left:0; top:-22px; white-space:nowrap;
      background:#1f9d57; color:#fff; font-size:11px; text-transform:none; letter-spacing:0;
      padding:2px 7px; border-radius:5px; pointer-events:none; z-index:5; }
  .links { white-space:nowrap; }
  .btn { display:inline-block; margin:2px 4px 2px 0; padding:5px 10px; border-radius:6px;
          font-size:12px; text-decoration:none; color:#fff; }
  .yt  { background:#c4302b; }
  .ziv { background:#2f6fed; }
  .smo { background:#1f9d57; }
  .gg  { background:#444b57; }
  .btn:hover { filter:brightness(1.15); }
  tbody tr:hover { background:#171a1f; }
"""


# JS para copiar al portapapeles al hacer clic en artista/titulo (.copyable).
# Incluye fallback con execCommand para contextos file:// donde la Clipboard
# API puede no estar disponible.
COPY_JS = """
<script>
(function () {
  function flash(el) {
    el.classList.add('copied');
    setTimeout(function () { el.classList.remove('copied'); }, 900);
  }
  function copyText(text, el) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { flash(el); })
        .catch(function () { fallback(text, el); });
    } else {
      fallback(text, el);
    }
  }
  function fallback(text, el) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); flash(el); } catch (e) {}
    document.body.removeChild(ta);
  }
  document.addEventListener('click', function (e) {
    var el = e.target.closest('.copyable');
    if (!el) return;
    copyText(el.getAttribute('data-copy') || el.textContent, el);
  });
})();
</script>
"""


def write_html(songs: list[dict], path: Path, playlist_url: str) -> None:
    esc = lambda x: html.escape(x or "")
    doc = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Playlist -> StepMania ({len(songs)} canciones)</title>
<style>{REPORT_CSS}</style></head>
<body>
<header>
  <h1>Playlist &rarr; StepMania &middot; {len(songs)} canciones</h1>
  <p>Origen: <a href="{esc(playlist_url)}" target="_blank">{esc(playlist_url)}</a><br>
     Pulsa los botones para buscar el stepchart de cada cancion. Descarga el .zip,
     extraelo en <code>StepMania/Songs/</code> y a jugar.</p>
</header>
<table><tbody>
{render_rows(songs)}
</tbody></table>
{COPY_JS}
</body></html>"""
    path.write_text(doc, encoding="utf-8")


# --- Descarga opcional de audio ---------------------------------------------

def download_audio(url: str, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(out_dir / "%(playlist_index)s - %(title)s.%(ext)s"),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "0",
        }],
        "ignoreerrors": True,
        "quiet": False,
    }
    with YoutubeDL(opts) as ydl:
        ydl.download([url])


# --- Main --------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Playlist de YouTube -> informe de busqueda de simfiles de StepMania.")
    ap.add_argument("url", help="URL de la playlist de YouTube")
    ap.add_argument("--out", default="playlist_stepmania", help="prefijo de los archivos de salida")
    ap.add_argument("--download-audio", action="store_true", help="descargar tambien los MP3 (requiere ffmpeg)")
    ap.add_argument("--no-open", action="store_true", help="no abrir el informe HTML al terminar")
    args = ap.parse_args()

    print("Extrayendo canciones de la playlist...")
    songs = extract_playlist(args.url)
    if not songs:
        sys.exit("No se encontraron canciones. Revisa que la URL sea una playlist publica.")
    print(f"  -> {len(songs)} canciones encontradas.")

    csv_path = Path(f"{args.out}.csv")
    html_path = Path(f"{args.out}.html")
    write_csv(songs, csv_path)
    write_html(songs, html_path, args.url)
    print(f"CSV : {csv_path.resolve()}")
    print(f"HTML: {html_path.resolve()}")

    if args.download_audio:
        audio_dir = Path(f"{args.out}_audio")
        print(f"Descargando audio en {audio_dir.resolve()} ...")
        download_audio(args.url, audio_dir)

    if not args.no_open:
        webbrowser.open(html_path.resolve().as_uri())


if __name__ == "__main__":
    main()
