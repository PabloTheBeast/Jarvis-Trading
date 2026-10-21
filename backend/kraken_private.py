"""Kraken Spot REST signing. No automatic retries of private requests.

API secrets are loaded only from the local process or an owner-only file; never
returned to the browser, copied into the database, or included in error messages.
"""
import base64
import hashlib
import hmac
import json
import os
import stat
import threading
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ExchangeError(Exception):
    """A definitive exchange rejection, safe to display without request headers."""


class ExchangeUnknown(Exception):
    """Transport/parse failure: a mutating request may have reached the exchange."""
    def __init__(self,message,possible_order=False):
        super().__init__(message)
        self.possible_order=possible_order


class CredentialsMissing(ExchangeError):
    pass


SAFE_ERRORS={
    'EGeneral:Permission denied':'API-nyckeln saknar behörighet för anropet. Kontrollera att du ändrat samma nyckel som JARVIS använder.',
    'EGeneral:Invalid arguments':'Kraken avvisade anropets parametrar.',
    'EAPI:Invalid key':'Kraken känner inte igen API-nyckeln. Kontrollera att den är aktiv och korrekt inmatad.',
    'EAPI:Invalid signature':'Signaturen stämmer inte. Kontrollera att API key och API secret hör till samma nyckel.',
    'EAPI:Invalid nonce':'Nonce avvisades. Använd inte samma API-nyckel i flera installationer och återställ inte en äldre databas.',
    'EAPI:Rate limit exceeded':'För många API-anrop. Vänta innan en ny kontokontroll.',
    'EGeneral:Temporary lockout':'Kraken har tillfälligt spärrat anropen. Vänta innan nästa försök.',
    'EOrder:Insufficient funds':'Tillgängligt saldo räcker inte för ordern.',
    'EService:Unavailable':'Krakens tjänst är tillfälligt otillgänglig.',
}


def rejection_message(method,errors):
    # Never echo arbitrary response text: it may contain sensitive request data.
    known=list(dict.fromkeys(value for value in errors if isinstance(value,str) and value in SAFE_ERRORS))
    if not known:
        return 'Kraken avvisade '+method+' (okänd felkod). Inga råa svarsdata visas.'
    return 'Kraken avvisade '+method+': '+'; '.join(value+' — '+SAFE_ERRORS[value] for value in known)


def load_credentials(database):
    key,secret=os.environ.get('KRAKEN_API_KEY'),os.environ.get('KRAKEN_API_SECRET')
    if key or secret:
        if not key or not secret:
            raise CredentialsMissing('Både KRAKEN_API_KEY och KRAKEN_API_SECRET måste finnas från samma källa.')
    else:
        path=Path(database).parent/'kraken.credentials.json'
        try:
            info=path.lstat()
        except FileNotFoundError:
            raise CredentialsMissing('Kraken är inte konfigurerad. Kör python3 -m backend.ops configure-kraken lokalt.') from None
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode & 0o077:
            raise CredentialsMissing('Kraken-filen måste ägas av aktuell användare och ha filrättighet 0600.')
        try:
            data=json.loads(path.read_text())
            key,secret=data['apiKey'],data['apiSecret']
        except Exception:
            raise CredentialsMissing('Kraken-filen har ogiltigt format.') from None
    try:
        decoded=base64.b64decode(secret,validate=True)
        if not isinstance(key,str) or not key.strip() or len(decoded)<32:
            raise ValueError()
    except Exception:
        raise CredentialsMissing('Kraken-nycklarna måste vara råa lokala värden; en proxyplatshållare kan inte HMAC-signeras.') from None
    return key,decoded


def api_signature(secret,nonce,path,body):
    digest=hashlib.sha256(str(nonce).encode()+body).digest()
    return base64.b64encode(hmac.new(secret,path.encode()+digest,hashlib.sha512).digest()).decode()


class KrakenPrivate:
    def __init__(self,database,nonce,opener=urlopen,credentials=load_credentials):
        self.database=database
        self.next_nonce=nonce
        self.opener=opener
        self.credentials=credentials
        self.lock=threading.Lock()

    def call(self,method,parameters=None):
        if method not in {'BalanceEx','OpenOrders','ClosedOrders','QueryOrders','QueryTrades','AddOrder','CancelOrder'}:
            raise ExchangeError('Unsupported private operation')
        with self.lock:
            key,secret=self.credentials(self.database)
            nonce=self.next_nonce()
            path='/0/private/'+method
            values={'nonce':str(nonce),**(parameters or {})}
            body=urlencode(values).encode()
            request=Request('https://api.kraken.com'+path,data=body,headers={
                'API-Key':key,'API-Sign':api_signature(secret,nonce,path,body),
                'Content-Type':'application/x-www-form-urlencoded','User-Agent':'JARVIS/0.6'})
            try:
                with self.opener(request,timeout=10) as response:
                    payload=json.load(response)
            except Exception:
                raise ExchangeUnknown('Kraken-svar saknas. Utfallet måste stämmas av; ordern skickas inte igen automatiskt.') from None
            if not isinstance(payload,dict) or not isinstance(payload.get('error'),list):
                raise ExchangeUnknown('Ogiltigt Kraken-svar; kontrollera börsens orderhistorik.') from None
            if payload['error'] and isinstance(payload.get('result'),dict) and payload['result'].get('txid'):
                raise ExchangeUnknown('Kraken returnerade både fel och order-ID; avstämning krävs.',True)
            if payload['error']:
                # Internal/service failures may occur after acceptance. Only explicit
                # parameter/auth/funding rejections prove an AddOrder was rejected.
                if method=='AddOrder' and any(not str(value).startswith((
                    'EOrder:', 'EAPI:', 'EAuth:', 'EGeneral:Invalid arguments',
                    'EGeneral:Permission denied')) for value in payload['error']):
                    raise ExchangeUnknown('Kraken rapporterade ett tjänstefel; stäm av ordern innan fler försök.',True)
                raise ExchangeError(rejection_message(method,payload['error']))
            if not isinstance(payload.get('result'),dict):
                raise ExchangeUnknown('Kraken returnerade inget verifierbart resultat.')
            return payload['result']
