import os, json, re, time, smtplib, requests, xml.etree.ElementTree as ET
from datetime import datetime, date, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from urllib.parse import quote

# CONFIG
GMAIL_USER = os.environ.get('GMAIL_USER', '')
GMAIL_PASS = os.environ.get('GMAIL_PASS', '')
RECIPIENTS = [r.strip() for r in os.environ.get('EMAIL_RECIPIENTS', '').split(',') if r.strip()]

print('=== Morning Brief Generator ===')
print('Gmail: ' + (GMAIL_USER if GMAIL_USER else 'not set'))
print('Recipients: ' + str(RECIPIENTS))

HEADERS   = {'User-Agent': 'Mozilla/5.0 (compatible; MorningBrief/1.0)'}
CACHE_FILE = 'news_cache.json'
MAX_DAYS   = 30


# ── FOREX ──────────────────────────────────────
FX_KEYS = ['eur_chf', 'usd_chf']


def _fx_frankfurter():
    """EZB-Referenzkurse. Ein Request, letzte 10 Kalendertage.
    Liefert ausschliesslich Handelstage - damit stimmt der Vortag
    auch am Montag und nach Feiertagen."""
    end   = date.today()
    start = end - timedelta(days=10)
    url = ('https://api.frankfurter.dev/v1/' + start.isoformat() + '..'
           + end.isoformat() + '?base=EUR&symbols=CHF,USD')
    d     = requests.get(url, headers=HEADERS, timeout=15).json()
    rates = d.get('rates', {})
    days  = sorted(rates.keys())
    if len(days) < 2:
        raise ValueError('nur ' + str(len(days)) + ' Handelstage geliefert')
    snap = {}
    for slot, day in (('now', days[-1]), ('prev', days[-2])):
        eur_chf = float(rates[day]['CHF'])
        eur_usd = float(rates[day]['USD'])
        snap[slot] = {'date': day,
                      'eur_chf': eur_chf,
                      'usd_chf': eur_chf / eur_usd}
    return snap


def _fx_currency_api():
    """Fallback: currency-api ueber den aktiven Cloudflare-Host
    (NICHT ueber cdn.jsdelivr.net - dieser Mirror ist toter Stand)."""
    def grab(host):
        url = 'https://' + host + '.currency-api.pages.dev/v1/currencies/eur.json'
        d   = requests.get(url, headers=HEADERS, timeout=10).json()
        e   = d['eur']
        eur_chf = float(e['chf'])
        eur_usd = float(e['usd'])
        return {'date': str(d.get('date', '')),
                'eur_chf': eur_chf,
                'usd_chf': eur_chf / eur_usd}

    now  = grab('latest')
    d0   = datetime.strptime(now['date'], '%Y-%m-%d').date()
    prev = None
    for back in range(1, 6):
        try:
            prev = grab((d0 - timedelta(days=back)).isoformat())
            break
        except Exception:
            continue
    if prev is None:
        raise ValueError('kein Vortageskurs gefunden')
    return {'now': now, 'prev': prev}


def fetch_forex():
    snap = None
    for name, fn in [('Frankfurter/EZB', _fx_frankfurter),
                     ('currency-api',    _fx_currency_api)]:
        try:
            snap = fn()
            print('Forex-Quelle: ' + name + ' ('
                  + snap['now']['date'] + ' vs. ' + snap['prev']['date'] + ')')
            break
        except Exception as e:
            print('Forex-Quelle ' + name + ' fehlgeschlagen: ' + str(e))

    if not snap:
        print('Forex: KEINE Quelle erreichbar')
        return {k: {'rate': None, 'change': None, 'changePct': None}
                for k in FX_KEYS}

    # Plausibilitaetspruefung: erkennt eine eingefrorene Quelle sofort
    try:
        age = (date.today()
               - datetime.strptime(snap['now']['date'], '%Y-%m-%d').date()).days
        if age > 4:
            print('WARNUNG: Forex-Daten sind ' + str(age)
                  + ' Tage alt - Quelle vermutlich eingefroren')
    except Exception:
        pass
    if snap['now']['date'] == snap['prev']['date']:
        print('WARNUNG: Vortag == aktueller Tag, Veraenderung waere immer 0')

    result = {}
    for key in FX_KEYS:
        rate = snap['now'][key]
        prev = snap['prev'][key]
        chg  = rate - prev
        pct  = (chg / prev * 100) if prev else 0
        result[key] = {'rate': rate, 'change': chg, 'changePct': pct}
        print(key + ': ' + str(round(rate, 4)) + ' ('
              + ('+' if pct >= 0 else '') + str(round(pct, 2)) + '%)')
    return result


# ── BITCOIN ────────────────────────────────────
def fetch_bitcoin():
    try:
        url = 'https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true'
        d = requests.get(url, timeout=10).json()['bitcoin']
        print('BTC: $' + str(int(d['usd'])) + ' (' + ('+' if d['usd_24h_change']>=0 else '') + str(round(d['usd_24h_change'],2)) + '%)')
        return {'price': d['usd'], 'changePct': d['usd_24h_change']}
    except Exception as e:
        print('Bitcoin error: ' + str(e))
        return {'price': None, 'changePct': None}


# ── STOCKS – actual OHLCV closing prices, volume-filtered (no adjustments) ──
def fetch_stocks():
    tickers = [('ESTA','Establishment Labs'), ('APYX','Apyx Medical'), ('IART','Integra LifeSciences')]
    result  = []
    for ticker, name in tickers:
        price, change, pct = None, None, None
        try:
            # Primary: actual daily OHLCV bars, last two days with real volume
            # includePrePost=false + volume filter = only complete regular sessions
            url  = ('https://query1.finance.yahoo.com/v8/finance/chart/'
                    + ticker + '?range=10d&interval=1d&includePrePost=false')
            r    = requests.get(url, headers=HEADERS, timeout=10).json()
            res  = r['chart']['result'][0]
            q    = res['indicators']['quote'][0]
            raw_closes  = q.get('close',  [])
            raw_volumes = q.get('volume', [])
            raw_times   = res.get('timestamp', [])
            # Filter: only complete sessions (volume > 0, close not None)
            valid = []
            for ts, cl, vol in zip(raw_times, raw_closes, raw_volumes):
                if cl is not None and vol is not None and int(vol) > 0:
                    valid.append((ts, cl))
            print(ticker + ': ' + str(len(valid)) + ' valid sessions')
            if len(valid) >= 2:
                price  = round(valid[-1][1], 4)
                prev   = round(valid[-2][1], 4)
                change = round(price - prev, 4)
                pct    = round((change / prev * 100), 4) if prev else 0
                print(ticker + ': close=' + str(round(price,2))
                      + ' prev=' + str(round(prev,2))
                      + ' chg=' + ('+' if pct>=0 else '') + str(round(pct,2)) + '%')
            else:
                print(ticker + ': not enough data')
        except Exception as e:
            print(ticker + ' error: ' + str(e))
        result.append({'ticker':ticker,'name':name,
                       'price':price,'change':change,'changePct':pct})
    return result


# ── NEWS – persistent cache ────────────────────
# Kurze, thematisch getrennte Abfragen. Lange OR-Ketten liefern bei
# Google News nur eine kleine, relevanz-sortierte Trefferliste voller
# alter Evergreen-Seiten - deshalb pro Gruppe eine eigene Abfrage,
# jeweils mit Zeitfenster (when:) und, wo sinnvoll, in beiden Editionen.
# prio 1 = eigene Partner, Produkte und Regulatorik
# prio 2 = Wettbewerb, Kliniken, Marktumfeld
ED_DE = 'hl=de&gl=CH&ceid=CH:de'
ED_EN = 'hl=en-US&gl=US&ceid=US:en'

NEWS_GROUPS = [
    {'name': 'Motiva / Establishment Labs', 'prio': 1, 'when': '30d',
     'q': '"Establishment Labs" OR "Motiva implant" OR "Motiva implants" OR "Mia Femtech"',
     'ed': [ED_DE, ED_EN]},
    {'name': 'Apyx / Renuvion', 'prio': 1, 'when': '30d',
     'q': '"Apyx Medical" OR "Renuvion"',
     'ed': [ED_DE, ED_EN]},
    {'name': 'Integra / Lipoelastic / Absorbest', 'prio': 1, 'when': '30d',
     'q': '"Integra LifeSciences" OR "Lipoelastic" OR "Absorbest"',
     'ed': [ED_DE, ED_EN]},
    {'name': 'Filler & Regenerativ', 'prio': 1, 'when': '30d',
     'q': '"Revanesse" OR "Prollenium" OR "SoftFil" OR "Sunekos" OR "Regen Lab"',
     'ed': [ED_DE, ED_EN]},
    {'name': 'Skincare & Geraete', 'prio': 1, 'when': '30d',
     'q': '"pHformula" OR "Solta Medical" OR "Thermage"',
     'ed': [ED_DE, ED_EN]},
    {'name': 'Regulatorik Schweiz', 'prio': 1, 'when': '30d',
     'q': '"Swissmedic" OR "swissdamed" OR "Medizinprodukteverordnung"',
     'ed': [ED_DE]},
    {'name': 'Wettbewerb', 'prio': 2, 'when': '14d',
     'q': '"Merz Aesthetics" OR "Galderma" OR "Allergan Aesthetics" OR "Mentor implant" OR "InMode"',
     'ed': [ED_DE, ED_EN]},
    {'name': 'Kliniken Schweiz', 'prio': 2, 'when': '14d',
     'q': '"Hirslanden" OR "Insel Gruppe" OR "clinic utoquai" OR "Klinik Pyramide"',
     'ed': [ED_DE]},
    {'name': 'Markt & Kongresse', 'prio': 2, 'when': '14d',
     'q': '"breast implant" OR "body contouring" OR "IMCAS"',
     'ed': [ED_EN]},
    {'name': 'Branche Schweiz', 'prio': 2, 'when': '14d',
     'q': '"Brustimplantat" OR "Aesthetische Medizin" OR "Schoenheitschirurgie"',
     'ed': [ED_DE]},
]

MAX_CACHE  = 600   # Obergrenze Cache-Eintraege
MAX_SHOW   = 20    # Meldungen im Brief
MAX_MARKET = 8     # davon hoechstens aus prio 2


def load_cache():
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []

def save_cache(items):
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

def parse_pub_date(pub_str):
    """Parse RSS pubDate string to UTC ISO string."""
    try:
        dt = parsedate_to_datetime(pub_str)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()

def ago_str(iso_str):
    """Convert stored ISO date to human-readable age string."""
    try:
        dt   = datetime.fromisoformat(iso_str)
        diff = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
        secs = diff.total_seconds()
        if secs < 3600:    return str(int(secs // 60)) + ' Min.'
        if secs < 86400:   return str(int(secs // 3600)) + ' Std.'
        days = int(secs // 86400)
        return str(days) + (' Tag' if days == 1 else ' Tage')
    except Exception:
        return ''

def sort_ts(item):
    """pubDate als vergleichbarer Zeitstempel, robust gegen Fehlwerte."""
    try:
        return datetime.fromisoformat(item.get('pubDate', '')).astimezone(timezone.utc)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)

def parse_rss(xml_text):
    items = []
    try:
        root = ET.fromstring(xml_text)
        for item in root.iter('item'):
            title_el = item.find('title')
            pub_el   = item.find('pubDate')
            src_el   = item.find('source')
            link_el  = item.find('link')
            title = (title_el.text or '') if title_el is not None else ''
            pub   = (pub_el.text or '')   if pub_el  is not None else ''
            src   = (src_el.text or '')   if src_el  is not None else ''

            link = link_el.text.strip() if link_el is not None and link_el.text else ''
            if not link:
                guid = item.find('guid')
                link = guid.text.strip() if guid is not None and guid.text else ''

            # Clean title
            title = re.sub(r'\s+-\s+\S.{2,40}$', '', title).strip()
            for ent, ch in [('&amp;','&'),('&lt;','<'),('&gt;','>'),('&quot;','"'),('&#39;',"'"),('&nbsp;',' ')]:
                title = title.replace(ent, ch)
            if len(title) < 10 or not link:
                continue

            items.append({
                'title':   title,
                'url':     link,
                'source':  src,
                'pubDate': parse_pub_date(pub),
            })
    except Exception as e:
        print('RSS parse error: ' + str(e))
    return items

def fetch_news():
    cache = load_cache()
    cached_urls = set(i.get('url', '') for i in cache)
    print('Cache geladen: ' + str(len(cache)) + ' Eintraege')

    new_count = 0
    empty_groups = []

    for group in NEWS_GROUPS:
        query = group['q'] + ' when:' + group['when']
        for edition in group['ed']:
            lang  = 'de' if edition == ED_DE else 'en'
            label = group['name'] + ' [' + lang + ']'
            try:
                rss_url = ('https://news.google.com/rss/search?q='
                           + quote(query) + '&' + edition)
                r     = requests.get(rss_url, headers=HEADERS, timeout=15)
                items = parse_rss(r.text)
                added = 0
                for item in items:
                    if item['url'] in cached_urls:
                        continue
                    item['group'] = group['name']
                    item['prio']  = group['prio']
                    cache.append(item)
                    cached_urls.add(item['url'])
                    added     += 1
                    new_count += 1
                print(label + ': ' + str(len(items)) + ' Treffer, '
                      + str(added) + ' neu')
                if not items:
                    empty_groups.append(label)
                time.sleep(1)
            except Exception as e:
                print(label + ': FEHLER ' + str(e))
                empty_groups.append(label + ' (Fehler)')

    print('Neue Meldungen insgesamt: ' + str(new_count))
    if empty_groups:
        print('WARNUNG: keine Treffer bei: ' + ', '.join(empty_groups))
    if new_count == 0:
        print('WARNUNG: kein einziger neuer Treffer - Abfragen pruefen')

    # Filter: nur die letzten MAX_DAYS Tage
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_DAYS)
    def is_recent(item):
        try:
            dt = datetime.fromisoformat(item.get('pubDate', ''))
            return dt.astimezone(timezone.utc) >= cutoff
        except Exception:
            return True  # behalten, wenn Datum unbekannt

    cache = [i for i in cache if is_recent(i)]
    cache.sort(key=sort_ts, reverse=True)
    cache = cache[:MAX_CACHE]
    save_cache(cache)
    print('Cache gespeichert: ' + str(len(cache)) + ' Eintraege')

    # Anzeige: feste Quote, damit weder aeltere Partnermeldungen noch das
    # Marktumfeld die Liste allein fuellen. Zum Schluss nach Datum sortiert.
    core   = [i for i in cache if int(i.get('prio', 2)) == 1]
    market = [i for i in cache if int(i.get('prio', 2)) != 1]
    shown  = core[:MAX_SHOW - MAX_MARKET] + market[:MAX_MARKET]
    if len(shown) < MAX_SHOW:
        seen  = set(id(i) for i in shown)
        rest  = [i for i in cache if id(i) not in seen]
        shown += rest[:MAX_SHOW - len(shown)]
    shown.sort(key=sort_ts, reverse=True)
    print('Im Brief: ' + str(len(shown)) + ' Meldungen ('
          + str(len([i for i in shown if int(i.get('prio', 2)) == 1])) + ' Partner)')

    result = []
    for item in shown:
        result.append({
            'title':   item['title'],
            'url':     item['url'],
            'source':  item.get('source', ''),
            'ago':     ago_str(item['pubDate']),
            'group':   item.get('group', ''),
            'summary': item.get('summary', ''),
        })
    return result


# ── GENERATE ───────────────────────────────────
def generate():
    data = {
        'generated': datetime.utcnow().isoformat() + 'Z',
        'forex':   fetch_forex(),
        'bitcoin': fetch_bitcoin(),
        'stocks':  fetch_stocks(),
        'news':    fetch_news(),
    }
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print('data.json: ' + str(len(data['stocks'])) + ' stocks, ' + str(len(data['news'])) + ' news')
    return data


# ── EMAIL HELPERS ──────────────────────────────
def fmt(n, dec=2):
    if n is None: return '-'
    s = ('{:.' + str(dec) + 'f}').format(abs(float(n)))
    parts = s.split('.')
    parts[0] = '{:,}'.format(int(parts[0])).replace(',', "'")
    if dec == 0: return parts[0]
    return parts[0] + '.' + parts[1]

def chg_span(n):
    if n is None: return '-'
    color = '#4ade80' if float(n) >= 0 else '#f87171'
    sign  = '+' if float(n) >= 0 else ''
    return '<span style="color:' + color + '">' + sign + '{:.2f}'.format(float(n)) + '%</span>'

def yf_url(ticker):
    return 'https://finance.yahoo.com/quote/' + ticker + '/'


# ── EMAIL ──────────────────────────────────────
def send_email(data):
    if not GMAIL_USER or not GMAIL_PASS or not RECIPIENTS:
        print('Email not configured - skipping.')
        return

    now    = datetime.now()
    days   = ['Sonntag','Montag','Dienstag','Mittwoch','Donnerstag','Freitag','Samstag']
    months = ['Januar','Februar','Maerz','April','Mai','Juni','Juli',
              'August','September','Oktober','November','Dezember']
    date_str = days[now.weekday()] + ', ' + str(now.day) + '. ' + months[now.month-1] + ' ' + str(now.year)

    # Forex
    forex_items = [
        ('EUR / CHF', 'eur_chf', 4, 'https://finance.yahoo.com/quote/EURCHF=X/'),
        ('USD / CHF', 'usd_chf', 4, 'https://finance.yahoo.com/quote/USDCHF=X/'),
    ]
    forex_rows = ''
    for label, key, dec, link in forex_items:
        fx = data.get('forex', {}).get(key, {})
        forex_rows += (
            '<tr style="border-bottom:1px solid #334155">'
            '<td colspan="3" style="padding:0">'
            '<a href="' + link + '" style="text-decoration:none;display:block">'
            '<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            '<td style="padding:12px 16px;font-size:13px;color:#94a3b8;width:35%">' + label + '</td>'
            '<td style="padding:12px 16px;font-size:17px;font-weight:900;color:#f1f5f9">' + fmt(fx.get('rate'), dec) + '</td>'
            '<td style="padding:12px 16px;font-size:12px;text-align:right">' + chg_span(fx.get('changePct')) + ' <span style="color:#475569">(24h)</span></td>'
            '</tr></table></a></td></tr>'
        )
    btc = data.get('bitcoin', {})
    forex_rows += (
        '<tr><td colspan="3" style="padding:0">'
        '<a href="https://finance.yahoo.com/quote/BTC-USD/" style="text-decoration:none;display:block">'
        '<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
        '<td style="padding:12px 16px;font-size:13px;color:#94a3b8;width:35%">Bitcoin / USD</td>'
        '<td style="padding:12px 16px;font-size:17px;font-weight:900;color:#f1f5f9">$ ' + fmt(btc.get('price'), 0) + '</td>'
        '<td style="padding:12px 16px;font-size:12px;text-align:right">' + chg_span(btc.get('changePct')) + ' <span style="color:#475569">(24h)</span></td>'
        '</tr></table></a></td></tr>'
    )

    # Stocks
    stock_cells = ''
    for s in data.get('stocks', []):
        pct    = float(s.get('changePct') or 0)
        color  = '#4ade80' if pct >= 0 else '#f87171'
        sign   = '+' if pct >= 0 else ''
        price  = '$' + fmt(s.get('price'), 2) if s.get('price') else '-'
        ticker = str(s.get('ticker', ''))
        name   = str(s.get('name', ''))
        stock_cells += (
            '<td style="width:33%;padding:0 4px;vertical-align:top">'
            '<a href="' + yf_url(ticker) + '" style="text-decoration:none;display:block">'
            '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="background:#1e293b;border:1px solid #334155;border-radius:8px">'
            '<tr><td height="14"></td></tr>'
            '<tr><td align="center" height="16" style="font-size:11px;font-weight:800;color:#64748b;letter-spacing:2px">' + ticker + '</td></tr>'
            '<tr><td align="center" height="28" style="font-size:10px;color:#475569;padding:0 6px;line-height:1.3">' + name + '</td></tr>'
            '<tr><td align="center" height="28" style="font-size:20px;font-weight:900;color:#f1f5f9">' + price + '</td></tr>'
            '<tr><td align="center" height="22" style="font-size:12px;font-weight:700;color:' + color + '">' + sign + '{:.2f}'.format(pct) + '%</td></tr>'
            '<tr><td height="14"></td></tr>'
            '</table></a></td>'
        )

    # News
    news_rows = ''
    for item in data.get('news', [])[:20]:
        title  = str(item.get('title', ''))
        url    = str(item.get('url', '#'))
        source = str(item.get('source', ''))
        ago    = str(item.get('ago', ''))
        group  = str(item.get('group', ''))
        meta   = ' &nbsp;&middot;&nbsp; '.join(
                     [p for p in [group, source, ago] if p.strip()])
        news_rows += (
            '<tr><td style="padding:11px 0;border-bottom:1px solid #1e293b">'
            '<a href="' + url + '" style="text-decoration:none;display:block">'
            + ('<div style="font-size:10px;color:#475569;margin-bottom:3px">' + meta + '</div>' if meta.strip() else '') +
            '<div style="font-size:13px;font-weight:700;color:#e2e8f0;line-height:1.45">' + title + '</div>'
            '</a></td></tr>'
        )
    if not news_rows:
        news_rows = '<tr><td style="padding:16px 0;font-size:12px;color:#64748b">Keine Neuigkeiten heute.</td></tr>'

    html = (
        '<!DOCTYPE html><html><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0"></head>'
        '<body style="margin:0;padding:0;background:#0f172a;font-family:Arial,Helvetica,sans-serif">'
        '<div style="max-width:640px;margin:0 auto;padding:0 16px 40px">'
        '<table style="width:100%;border-collapse:collapse">'
        '<tr><td style="padding:28px 0 20px;text-align:center;border-bottom:2px solid #1e293b">'
        '<div style="font-size:10px;letter-spacing:3px;text-transform:uppercase;color:#475569;margin-bottom:8px">ESTHETIC MED &middot; MEDICAL ESTHETIC</div>'
        '<div style="font-size:34px;font-weight:900;color:#f1f5f9;letter-spacing:-1px;line-height:1">Morning Brief</div>'
        '<div style="font-size:13px;color:#64748b;margin-top:8px">' + date_str + '</div>'
        '</td></tr></table>'
        '<div style="font-size:10px;font-weight:800;letter-spacing:2px;text-transform:uppercase;color:#475569;margin:24px 0 10px">W&Auml;HRUNGEN &amp; MARKT</div>'
        '<table style="width:100%;border-collapse:collapse;background:#1e293b;border:1px solid #334155;border-radius:12px;overflow:hidden;margin-bottom:24px">'
        + forex_rows + '</table>'
        '<div style="font-size:10px;font-weight:800;letter-spacing:2px;text-transform:uppercase;color:#475569;margin-bottom:10px">PARTNER-AKTIEN (NASDAQ)</div>'
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;margin-bottom:28px">'
        '<tr>' + stock_cells + '</tr></table>'
        '<div style="font-size:10px;font-weight:800;letter-spacing:2px;text-transform:uppercase;color:#475569;margin-bottom:6px">NEWS &amp; RADAR</div>'
        '<table style="width:100%;border-collapse:collapse">' + news_rows + '</table>'
        '<table style="width:100%;border-collapse:collapse;margin-top:28px">'
        '<tr><td style="padding-top:20px;border-top:1px solid #1e293b;text-align:center">'
        '<a href="https://patrickheeb86.github.io/morning-brief/" style="color:#94a3b8;text-decoration:none;font-size:12px;display:block;margin-bottom:4px">Dashboard &ouml;ffnen</a>'
        '<div style="color:#475569;font-size:11px">esthetic med GmbH / medical esthetic GmbH</div>'
        '</td></tr></table>'
        '</div></body></html>'
    )

    subject = 'Morning Brief \u00b7 ' + now.strftime('%d.%m.%Y')
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = GMAIL_USER
    msg['To']      = ', '.join(RECIPIENTS)
    msg.attach(MIMEText(html, 'html', 'utf-8'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(GMAIL_USER, GMAIL_PASS)
            s.sendmail(GMAIL_USER, RECIPIENTS, msg.as_string())
        print('Email sent to: ' + ', '.join(RECIPIENTS))
    except Exception as e:
        print('Email error: ' + str(e))
        raise


if __name__ == '__main__':
    data = generate()
    send_email(data)
