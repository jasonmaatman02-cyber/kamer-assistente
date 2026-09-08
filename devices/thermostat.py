"""Thermostat.

There is no hardware integration yet, so the target temperature is just
persisted in ``settings.json`` via :mod:`config`. When a real API (Honeywell,
Tado, …) is added, only ``_apply`` needs to talk to it.
"""
import config

DEFAULTS = {"target": 20, "min": 15, "max": 30}


class ThermostatController:
    def _cfg(self, key):
        return config.get(f"devices.thermostat.{key}", DEFAULTS[key])

    def get_temperature(self):
        return self._cfg("target")

    def set_temperature(self, temperature):
        t = max(self._cfg("min"), min(self._cfg("max"), int(round(float(temperature)))))
        config.set("devices.thermostat.target", t)
        self._apply(t)
        return t

    def _apply(self, t):
        # TODO: echte thermostaat-API aanroepen
        print(f"Thermostaat doeltemperatuur: {t}°C")

    def state(self):
        return {
            "target": self._cfg("target"),
            "min": self._cfg("min"),
            "max": self._cfg("max"),
        }
