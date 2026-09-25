"""Alles wat het dashboard van een CDN laadt heeft een Subresource Integrity-hash: een gecompromitteerde CDN kan
dan geen andere code of stijl in de pagina's (met toegang tot camera, lampen en instellingen) brengen."""
import re
from pathlib import Path

DASH = Path(__file__).resolve().parents[1] / "Dashboard"
CDN_HOSTS = ("cdnjs.cloudflare.com", "cdn.jsdelivr.net", "unpkg.com", "ajax.googleapis.com")


def test_every_external_link_or_script_tag_has_an_integrity_hash():
    checked = 0
    for page in sorted(DASH.glob("*.html")):
        html = page.read_text(encoding="utf-8")
        for tag in re.findall(r"<(?:link|script)\b[^>]*>", html, flags=re.S):
            if any(host in tag for host in CDN_HOSTS):
                checked += 1
                assert re.search(r'integrity="sha(256|384|512)-[A-Za-z0-9+/=]+"', tag), f"{page.name}: SRI ontbreekt in {tag[:90]}"
                assert 'crossorigin="anonymous"' in tag, f"{page.name}: crossorigin ontbreekt"
    assert checked >= 10                                     # Font Awesome staat op elke pagina


def test_camera_scripts_from_the_cdn_are_loaded_with_integrity():
    js = (DASH / "static" / "scripts" / "camera.js").read_text(encoding="utf-8")
    calls = re.findall(r"loadScript\(`\$\{CDN\}[^`]+`(?:,\s*\n?\s*\"(sha256-[A-Za-z0-9+/=]+)\")?\)", js)
    assert len(calls) == 2 and all(calls), "elke CDN-script in camera.js heeft een sha256-hash nodig"
    assert "s.integrity = integrity" in js and 's.crossOrigin = "anonymous"' in js
    # versies zijn gepind (een vlottende versie zou de hash ongeldig maken)
    assert re.search(r"tfjs@\d+\.\d+\.\d+/", js) and re.search(r"coco-ssd@\d+\.\d+\.\d+/", js)
