"""Weather.

Providers (``config: weather.provider``):

* ``open-meteo`` – free, no API key. Default.
* ``weatherapi`` – needs ``WEATHERAPI_KEY``.

``fetch_weather()`` returns ``{"temp", "wind", "humidity", "condition", "city"}``
regardless of provider, so callers don't care which one is active.
"""
from __future__ import annotations

import time

import requests

import config

_CACHE_TTL = 300  # weer verandert niet in 5 minuten

# WMO weather-code -> Dutch text (Open-Meteo).
WMO_NL = {
    0: "Helder", 1: "Overwegend helder", 2: "Half bewolkt", 3: "Bewolkt",
    45: "Mist", 48: "Aanvriezende mist",
    51: "Lichte motregen", 53: "Motregen", 55: "Dichte motregen",
    56: "Lichte ijzel", 57: "Ijzel",
    61: "Lichte regen", 63: "Regen", 65: "Zware regen",
    66: "Lichte ijsregen", 67: "Ijsregen",
    71: "Lichte sneeuw", 73: "Sneeuw", 75: "Zware sneeuw", 77: "Sneeuwkorrels",
    80: "Lichte buien", 81: "Buien", 82: "Zware buien",
    85: "Lichte sneeuwbuien", 86: "Sneeuwbuien",
    95: "Onweer", 96: "Onweer met lichte hagel", 99: "Onweer met hagel",
}

_WEATHERAPI_NL = {
    "Sunny": "Zonnig", "Clear": "Helder", "Partly cloudy": "Half bewolkt",
    "Cloudy": "Bewolkt", "Overcast": "Zwaarbewolkt", "Mist": "Mistig",
    "Patchy rain possible": "Plaatselijke regen mogelijk", "Light rain": "Lichte regen",
    "Heavy rain": "Zware regen", "Moderate rain": "Matige regen",
    "Snow": "Sneeuw", "Thunderstorm": "Onweer",
}


class WeerAPI:
    def __init__(self):
        self._geo_cache: dict[str, tuple[float, float]] = {}
        self._cache: dict[str, tuple[float, dict]] = {}   # city -> (ts, data)

    # ------------------------------------------------------------------ #
    # provider dispatch
    # ------------------------------------------------------------------ #
    def fetch_weather(self, city: str | None = None):
        city = city or config.get("weather.city", "Arnhem")
        provider = (config.get("weather.provider") or "open-meteo").lower()
        key = f"{provider}:{city}"

        hit = self._cache.get(key)
        if hit and (time.time() - hit[0]) < _CACHE_TTL:
            return hit[1]

        try:
            if provider == "weatherapi" and config.secret("WEATHERAPI_KEY"):
                data = self._fetch_weatherapi(city)
            else:
                data = self._fetch_open_meteo(city)
        except requests.RequestException as exc:
            print(f"[weer] ophalen mislukt: {exc}")
            return hit[1] if hit else None   # val terug op oude data indien beschikbaar

        if data:
            self._cache[key] = (time.time(), data)
        return data

    # ------------------------------------------------------------------ #
    # Open-Meteo
    # ------------------------------------------------------------------ #
    def _geocode(self, city: str) -> tuple[float, float]:
        if city in self._geo_cache:
            return self._geo_cache[city]
        lat = config.get("weather.latitude")
        lon = config.get("weather.longitude")
        if city == config.get("weather.city") and lat is not None and lon is not None:
            self._geo_cache[city] = (lat, lon)
            return lat, lon
        r = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "nl"},
            timeout=5,
        )
        r.raise_for_status()
        results = r.json().get("results") or []
        if not results:
            raise requests.RequestException(f"stad '{city}' niet gevonden")
        loc = results[0]
        self._geo_cache[city] = (loc["latitude"], loc["longitude"])
        return self._geo_cache[city]

    def _fetch_open_meteo(self, city: str):
        lat, lon = self._geocode(city)
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
                "wind_speed_unit": "kmh",
                "timezone": "auto",
            },
            timeout=5,
        )
        r.raise_for_status()
        cur = r.json()["current"]
        return {
            "temp": round(cur["temperature_2m"], 1),
            "wind": round(cur["wind_speed_10m"], 1),
            "humidity": cur["relative_humidity_2m"],
            "condition": WMO_NL.get(cur["weather_code"], "Onbekend weer"),
            "city": city,
        }

    # ------------------------------------------------------------------ #
    # WeatherAPI
    # ------------------------------------------------------------------ #
    def _fetch_weatherapi(self, city: str):
        r = requests.get(
            "http://api.weatherapi.com/v1/current.json",
            params={"key": config.secret("WEATHERAPI_KEY"), "q": city},
            timeout=5,
        )
        r.raise_for_status()
        data = r.json()
        cur = data["current"]
        return {
            "temp": cur["temp_c"],
            "wind": cur["wind_kph"],
            "humidity": cur["humidity"],
            "condition": _WEATHERAPI_NL.get(cur["condition"]["text"], cur["condition"]["text"]),
            "city": data["location"]["name"],
        }

    # ------------------------------------------------------------------ #
    # text helpers (unchanged interface)
    # ------------------------------------------------------------------ #
    def _d(self, city):
        return self.fetch_weather(city)

    def get_temp(self, city="Arnhem"):
        d = self._d(city)
        return self.error_msg() if not d else f"In {d['city']} is het momenteel {d['temp']} graden Celsius."

    def get_wind(self, city="Arnhem"):
        d = self._d(city)
        return self.error_msg() if not d else f"De wind gaat {d['wind']} kilometer per uur in {d['city']}."

    def get_humidity(self, city="Arnhem"):
        d = self._d(city)
        return self.error_msg() if not d else f"De luchtvochtigheid in {d['city']} is nu ongeveer {d['humidity']} procent."

    def get_condition(self, city="Arnhem"):
        d = self._d(city)
        return self.error_msg() if not d else f"In {d['city']} is het weer op dit moment: {d['condition']}."

    def get_all(self, city="Arnhem"):
        d = self._d(city)
        if not d:
            return self.error_msg()
        return (
            f"In {d['city']} is het nu {d['temp']} graden Celsius, "
            f"met een wind van {d['wind']} kilometer per uur. "
            f"De luchtvochtigheid ligt rond de {d['humidity']} procent, "
            f"en de weersomstandigheden zijn: {d['condition']}."
        )

    def get_all_simple(self, city="Arnhem"):
        return self._d(city)

    def error_msg(self):
        return "Oeps, er ging iets mis bij het ophalen van de weergegevens."
