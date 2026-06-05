#!/usr/bin/env python3
"""
server.py
---------
Web local minima (sin dependencias extra, usa http.server de la libreria
estandar) para usar playlist_to_stepmania desde el navegador.

Abre un formulario donde pegas la URL de una playlist de YouTube y te muestra
la tabla con los enlaces de busqueda de simfiles de StepMania.

Uso:
  python server.py                 # arranca en http://localhost:8000
  python server.py --port 9000     # otro puerto
  python server.py --no-open       # no abrir el navegador

Requisitos: yt-dlp (lo usa playlist_to_stepmania).
"""

from __future__ import annotations

import argparse
import html
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from playlist_to_stepmania import REPORT_CSS, extract_playlist, render_rows


# --- Plantillas HTML ---------------------------------------------------------

PAGE_CSS = REPORT_CSS + """
  .wrap { max-width: 1100px; margin: 0 auto; }
  form.search { display:flex; gap:10px; margin-top:14px; }
  form.search input[type=text] {
      flex:1; padding:10px 12px; border-radius:8px; border:1px solid #2a2d34;
      background:#0e0f12; color:#e7e7ea; font-size:14px; }
  form.search button {
      padding:10px 18px; border:0; border-radius:8px; background:#2f6fed;
      color:#fff; font-size:14px; cursor:pointer; }
  form.search button:hover { filter:brightness(1.1); }
  .msg { margin-top:12px; padding:10px 14px; border-radius:8px; font-size:13px; }
  .msg.error { background:#3a1d1f; color:#ffb3b3; border:1px solid #5a2a2d; }
  .msg.info  { background:#16202b; color:#9ec9ff; border:1px solid #244258; }
  .spinner { display:none; margin-left:8px; }
  form.search.loading .spinner { display:inline; }
"""

FORM_HTML = """
<header>
  <div class="wrap">
    <h1>Playlist &rarr; StepMania</h1>
    <p>Pega la URL de una playlist (o un video) de YouTube y genera los enlaces
       de busqueda de stepcharts para cada cancion.</p>
    <form class="search" method="get" action="/"
          onsubmit="this.classList.add('loading');">
      <input type="text" name="url" placeholder="https://www.youtube.com/playlist?list=..."
             value="{url_value}" autofocus required>
      <button type="submit">Buscar</button>
      <span class="spinner">Procesando, esto puede tardar unos segundos...</span>
    </form>
    {message}
  </div>
</header>
"""


def page(body: str, title: str = "Playlist -> StepMania") -> bytes:
    doc = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{PAGE_CSS}</style></head>
<body>
{body}
</body></html>"""
    return doc.encode("utf-8")


def render_index(url_value: str = "", message: str = "") -> bytes:
    form = FORM_HTML.format(url_value=html.escape(url_value), message=message)
    return page(form)


def render_results(url: str) -> bytes:
    songs = extract_playlist(url)
    if not songs:
        msg = ('<div class="msg error">No se encontraron canciones. '
               'Revisa que la URL sea una playlist o video publico.</div>')
        return render_index(url, msg)

    info = (f'<div class="msg info">{len(songs)} canciones encontradas. '
            'Pulsa los botones para buscar el stepchart de cada una.</div>')
    form = FORM_HTML.format(url_value=html.escape(url), message=info)
    body = f"""{form}
<table><tbody>
{render_rows(songs)}
</tbody></table>"""
    return page(body, f"Playlist -> StepMania ({len(songs)} canciones)")


# --- Servidor ----------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in ("/", "/index.html"):
            self._send(page('<div class="wrap"><p>No encontrado.</p></div>'), 404)
            return

        params = parse_qs(parsed.query)
        url = (params.get("url", [""])[0] or "").strip()
        if not url:
            self._send(render_index())
            return

        try:
            self._send(render_results(url))
        except Exception:
            traceback.print_exc()
            err = ('<div class="msg error">Error procesando la playlist. '
                   'Comprueba la URL e intentalo de nuevo.</div>')
            self._send(render_index(url, err))

    def log_message(self, fmt: str, *args) -> None:  # menos ruido en consola
        return


def main() -> None:
    ap = argparse.ArgumentParser(description="Web local para playlist_to_stepmania (http.server).")
    ap.add_argument("--port", type=int, default=8000, help="puerto (por defecto 8000)")
    ap.add_argument("--host", default="127.0.0.1", help="host (por defecto 127.0.0.1)")
    ap.add_argument("--no-open", action="store_true", help="no abrir el navegador al arrancar")
    args = ap.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Servidor en {url}  (Ctrl+C para parar)")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nParando...")
        server.shutdown()


if __name__ == "__main__":
    main()
