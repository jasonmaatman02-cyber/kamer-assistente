import asyncio
import colorsys
import random
import time
from tapo import ApiClient


def hex_to_hsl(hex_color):
    hex_color = hex_color.lstrip('#')
    rgb = tuple(int(hex_color[i:i+2], 16) / 255.0 for i in (0, 2, 4))
    hsl = colorsys.rgb_to_hls(*rgb)
    return hsl

kleuren = {
    "rood": (0, 100),
    "oranje": (30, 100),
    "geel": (60, 100),
    "groen": (120, 100),
    "blauw": (180, 100),
    "paars": (270, 100),
    "roze": (330, 100),
    "wit": (0, 1),  
}

class SlimmeLamp:
    def __init__(self, email, wachtwoord, ip_adres):
        self.email = email
        self.wachtwoord = wachtwoord
        self.ip = ip_adres
        self.client = None
        self.lamp = None

    async def connect(self):
        self.client = ApiClient(self.email, self.wachtwoord)
        self.lamp = await self.client.l530(self.ip)
        return "Verbonden met lamp"

    async def aan(self):
        await self.lamp.on()
        await self.lamp.set_brightness(100)
        return "Lamp is aan"

    async def uit(self):
        await self.lamp.off()
        return "Lamp is uit"
    async def zet_kleur_temp(self, kleur_temp):
        
        await self.lamp.set_color_temperature(kleur_temp)
        return f"Kleurtemperatuur ingesteld op {kleur_temp}K"
        
    async def zet_kleur(self, kleur_naam):
        if "#" in kleur_naam:
            h, l, s = hex_to_hsl(kleur_naam)
            hue = int(h * 360)
            saturation = int(s * 100)
        elif kleur_naam in kleuren:
            hue, saturation = kleuren[kleur_naam]
        else:
            return f"Kleur '{kleur_naam}' niet gevonden! Kies uit: {list(kleuren.keys())}"

        await self.lamp.set_hue_saturation(hue, saturation)
  
        return f"Kleur gezet naar {kleur_naam}"

    async def zet_helderheid(self, doel_helderheid, stappen=20, vertraging=0.05):
        await self.lamp.set_brightness(round(doel_helderheid))
        return f"Heldereheid ingesteld op {doel_helderheid}%"

    async def party(self, duur=10, interval=0.1):
        x = await self.lamp.get_device_info()
        kleuren_lijst = list(kleuren.values())
        einde = time.monotonic() + duur
        await self.lamp.set_brightness(100)

        while time.monotonic() < einde:
            hue, saturation = random.choice(kleuren_lijst)
            await self.lamp.set_hue_saturation(hue, saturation)
            await asyncio.sleep(interval)

        # Herstel naar vorige waarden
        await self.lamp.set_brightness(x.brightness)
        await self.lamp.set_hue_saturation(x.hue, x.saturation)
        return "Party-modus afgerond"

    async def bureau(self):
        await self.lamp.set_hue_saturation(60, 40)  # zacht geel
        await self.lamp.set_brightness(60)
        return "Bureau-stand ingesteld (zacht geel, 60%)"

    async def normaal(self):
        await self.lamp.set_hue_saturation(0, 1)  # wit
        await self.lamp.set_brightness(100)
        return "Normale stand ingesteld (wit, 100%)"

    async def status(self):
        info = await self.lamp.get_device_info()

        def g(name, default=None):
            if isinstance(info, dict):
                return info.get(name, default)
            return getattr(info, name, default)

        return {
            "on": bool(g("device_on", g("on", False))),
            "brightness": g("brightness"),
            "hue": g("hue"),
            "saturation": g("saturation"),
            "color_temp": g("color_temp"),
        }

if __name__ == "__main__":
    from keys.API_keys import tapo_user, tapo_password
    lamp = SlimmeLamp(tapo_user, tapo_password, "192.168.2.15")
    asyncio.run(lamp.connect())
    asyncio.run(lamp.uit())