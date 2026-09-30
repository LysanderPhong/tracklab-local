"""Read only the public files used by the local workbench."""
import mimetypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PUBLIC_FILES = {
    'index.html', 'connect.html', 'app.js', 'connection-state.js',
    'geometry.js', 'style.css', 'vendor/leaflet.js', 'vendor/leaflet.css',
}


def asset(path):
    if path in ('/', '/fixed'):
        name = 'index.html'
    elif path == '/connect':
        name = 'connect.html'
    elif path.startswith('/portal/'):
        name = path[len('/portal/'):]
    else:
        return None
    if name not in PUBLIC_FILES:
        return None
    file = ROOT / 'portal' / name
    if not file.is_file():
        return None
    return file.read_bytes(), mimetypes.guess_type(file)[0] or 'application/octet-stream'
