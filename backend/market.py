"""Public Kraken EUR quotes converted to SEK with dated ECB reference rates.

No private Kraken API, API key or live order submission exists.
"""
import json
import math
import time
from datetime import date
from urllib.request import urlopen
from xml.etree import ElementTree


class MarketError(Exception):
    pass


class Kraken:
    url = 'https://api.kraken.com/0/public/Ticker?pair=XBTEUR,ETHEUR'
    fx_url = 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml'

    def __init__(self, opener=urlopen):
        self.opener = opener
        self.fx = None
        self.fx_checked = 0
        self.metadata = {}

    def exchange_rate(self):
        if self.fx and time.monotonic()-self.fx_checked < 3600:
            return self.fx
        with self.opener(self.fx_url, timeout=10) as response:
            root = ElementTree.fromstring(response.read())
        dated = [node for node in root.iter() if 'time' in node.attrib]
        if len(dated) != 1:
            raise ValueError('Missing ECB date')
        day = date.fromisoformat(dated[0].attrib['time'])
        if not 0 <= (date.today()-day).days <= 7:
            raise ValueError('ECB reference rate is stale')
        rates = [float(node.attrib['rate']) for node in dated[0] if node.attrib.get('currency') == 'SEK']
        if len(rates) != 1 or not math.isfinite(rates[0]) or rates[0] <= 0:
            raise ValueError('Invalid ECB SEK rate')
        self.fx, self.fx_checked = (rates[0], day.isoformat()), time.monotonic()
        return self.fx

    def fetch(self):
        try:
            rate, day = self.exchange_rate()
            with self.opener(self.url, timeout=10) as response:
                payload = json.load(response)
            if payload.get('error'):
                raise ValueError('Kraken returned an API error')
            prices = {}
            for symbol, prefix in [('BTC-SIM', 'XBT'), ('ETH-SIM', 'ETH')]:
                matches = [v for k, v in payload['result'].items()
                           if k.replace('XXBT', 'XBT').replace('XETH', 'ETH')
                           .replace('ZEUR', 'EUR') == prefix + 'EUR']
                if len(matches) != 1:
                    raise ValueError('Missing or ambiguous EUR ticker')
                price = float(matches[0]['c'][0])*rate
                if not math.isfinite(price) or price <= 0:
                    raise ValueError('Invalid ticker price')
                prices[symbol] = price
            self.metadata = dict(fxSource='ECB daily reference', eurSek=rate, fxDate=day,
                                 quoteCurrency='EUR', accountCurrency='SEK')
            return prices
        except Exception as exc:
            raise MarketError('Kraken/ECB market data unavailable') from exc
