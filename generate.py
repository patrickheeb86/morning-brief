import os, json, re, time, smtplib, requests, xml.etree.ElementTree as ET
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo
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
def fetch_forex():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    result = {}
    for key, base, target in [('eur_chf','eur','chf'), ('usd_chf','usd','chf')]:
        try:
            base_url = 'https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@'
            r_now  = requests.get(base_url+'latest/v1/currencies/'+base+'.json', timeout=10).json()
            r_prev = requests.get(base_url+yesterday+'/v1/currencies/'+base+'.json', timeout=10).json()
            rate   = r_now[base][target]
            prev   = r_prev[base][target]
            chg    = rate - prev
            pct    = (chg / prev * 100) if prev else 0
            result[key] = {'rate': rate, 'change': chg, 'changePct': pct}
            print(key + ': ' + str(round(rate,4)) + ' (' + ('+' if pct>=0 else '') + str(round(pct,2)) + '%)')
        except Exception as e:
            print('Forex error ' + key + ': ' + str(e))
            result[key] = {'rate': None, 'change': None, 'changePct': None}
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
            url  = ('https://query1.finance.yahoo.com/v8/finance/chart/'
                    + ticker + '?range=10d&interval=1d&includePrePost=false')
            r    = requests.get(url, headers=HEADERS, timeout=10).json()
            res  = r['chart']['result'][0]
            q    = res['indicators']['quote'][0]
            raw_closes  = q.get('close',  [])
            raw_volumes = q.get('volume', [])
            raw_times   = res.get('timestamp', [])
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
NEWS_QUERIES = [
    # Gruppe 1: Kernprodukte & Hauptpartner
    '"Establishment Labs" OR "Motiva implant" OR "Apyx Medical" OR "Renuvion" OR "Integra LifeSciences" OR "Integra IDRT"',
    # Gruppe 2: Weitere Produkte & Lieferanten
    '"Lipoelastic" OR "Humanmed" OR "body-jet" OR "pHformula" OR "Regen Lab" OR "RegenLab" OR "Puregraft" OR "GC Aesthetics" OR "Novus Scientific" OR "Revanesse" OR "Prollenium"',
    # Gruppe 3: Weitere Lieferanten & Partner
    '"STRIM HC" OR "Professional Dietetics" OR "TULIP MEDICAL" OR "Absorbest" OR "Meta Cell Technology" OR "Derm-appeal" OR "Soft Medical Aesthetics"',
    # Gruppe 4: Wettbewerber & Marktumfeld
    '"Galderma" aesthetics OR "Merz Aesthetics" OR "InMode" aesthetic OR "Allergan" aesthetics OR "Mentor implant" OR "Sientra"',
    # Gruppe 5: Kunden (Kliniken & Praxen)
    '"Lucerne Clinic" OR "BRST AG" OR "CHUV" OR "Clinique Générale-Beaulieu" OR "Clinique de la Source" OR "ZANZI CLINIC" OR "Affidea Plastic Surgery" OR "clinic utoquai" OR "LIPO CLINIC" OR "Aesthetic Alliance" OR "Clinique des Grangettes"',
    # Gruppe 6: Spitäler & Institutionen
    '"Universitätsspital Zürich" OR "Universitätsspital Basel" OR "Insel Gruppe" OR "Hirslanden" OR "Hôpitaux Universitaires de Genève" OR "Kantonsspital Winterthur" OR "Spital Zollikerberg" OR "LUKS Spitalbetriebe" OR "Ospedale Lugano" OR "Hôpital du Valais" OR "HOCH Health Ostschweiz"',
    # Gruppe 7: Branchentrends & Kongresse
    '"IMCAS 2026" OR "aesthetic medicine" congress OR "breast implant" safety OR "body contouring" trend OR "minimally invasive" aesthetics OR "regenerative aesthetics" OR fillers 2026',
    # Gruppe 8: Regulatorik & Behörden
    '"Swissmedic" Medizinprodukt OR "Swissmedic" Zulassung OR "Swissmedic" Implantat OR "MDR" Medizinprodukte Schweiz',
]

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

def parse_rss(xml_text):
    items = []
    try:
        root = ET.fromstring(xml_text)
        for item in root.iter('item'):
            title_el = item.find('title')
            pub_el   = item.find('pubDate')
            src_el   = item.find('source')
            title = (title_el.text or '') if title_el is not None else ''
            pub   = (pub_el.text or '')   if pub_el  is not None else ''
            src   = (src_el.text or '')   if src_el  is not None else ''

            link = ''
            for node in item.childNodes if hasattr(item, 'childNodes') else []:
                pass
            link_el = item.find('link')
            if link_el is not None and link_el.text:
                link = link_el.text.strip()
            if not link:
                guid = item.find('guid')
                link = guid.text.strip() if guid is not None and guid.text else ''

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
    cached_urls = set(i.get('url','') for i in cache)
    print('Cache loaded: ' + str(len(cache)) + ' items')

    new_count = 0
    for i, query in enumerate(NEWS_QUERIES):
        try:
            rss_url = 'https://news.google.com/rss/search?q=' + quote(query) + '&hl=de&gl=CH&ceid=CH:de'
            r       = requests.get(rss_url, headers=HEADERS, timeout=15)
            items   = parse_rss(r.text)
            for item in items:
                if item['url'] not in cached_urls:
                    cache.append(item)
                    cached_urls.add(item['url'])
                    new_count += 1
            print('Query ' + str(i+1) + ': fetched ' + str(len(items)) + ' items')
            time.sleep(1)
        except Exception as e:
            print('RSS query ' + str(i+1) + ' error: ' + str(e))

    print('New items added: ' + str(new_count))

    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_DAYS)
    def is_recent(item):
        try:
            dt = datetime.fromisoformat(item.get('pubDate',''))
            return dt.astimezone(timezone.utc) >= cutoff
        except Exception:
            return True

    cache = [i for i in cache if is_recent(i)]

    def sort_key(item):
        try:
            return datetime.fromisoformat(item.get('pubDate','')).astimezone(timezone.utc)
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    cache.sort(key=sort_key, reverse=True)
    save_cache(cache)
    print('Cache saved: ' + str(len(cache)) + ' items')

    result = []
    for item in cache[:40]:
        result.append({
            'title':   item['title'],
            'url':     item['url'],
            'source':  item.get('source',''),
            'ago':     ago_str(item['pubDate']),
            'summary': item.get('summary',''),
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

    now    = datetime.now(ZoneInfo('Europe/Zurich'))
    days   = ['Montag','Dienstag','Mittwoch','Donnerstag','Freitag','Samstag','Sonntag']
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
            '<tr><td align="center" height="22" style="font-size:12px;font-weight:700;color:' + color + '">' + sign + '{:.2f}'.format(pct) + '% / ' + sign + '$' + fmt(s.get('change'), 2) + '</td></tr>'
            '<tr><td height="14"></td></tr>'
            '</table></a></td>'
        )

    # News
    news_rows = ''
    for item in data.get('news', [])[:40]:
        title  = str(item.get('title', ''))
        url    = str(item.get('url', '#'))
        source = str(item.get('source', ''))
        ago    = str(item.get('ago', ''))
        meta   = (source + ' &nbsp;&middot;&nbsp; ' if source else '') + ago
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
