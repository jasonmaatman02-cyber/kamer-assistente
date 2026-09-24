"""Kleine gedeelde helpers voor de API-blueprints."""
from flask import request


def json_body() -> dict:
    """De JSON-body als dict. Een body die geen JSON-object is (``"tekst"``, ``123``,
    ``[1]``, kapotte JSON) geeft ``{}``: ``request.get_json(silent=True) or {}`` liet
    elke niet-lege niet-dict door en gaf daarna op ``.get()`` een AttributeError ->
    HTML-500 i.p.v. een nette 400 van de route zelf."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}
