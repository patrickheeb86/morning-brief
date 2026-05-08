import os, json, re, time, smtplib, requests, xml.etree.ElementTree as ET
from datetime import datetime, date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote

# CONFIG
GMAIL_USER = os.environ.get('GMAIL_USER', '')
GMAIL_PASS = os.environ.get('GMAIL_PASS', '')
RECIPIENTS = [r.strip() for r in os.environ.get('EMAIL_RECIPIENTS', '').split(',') if r.strip()]

print('=== Morning Brief Generator (zero API cost) ===')
print('Gmail: ' + (GMAIL_USER if GMAIL_USER else 'not set'))
print('Recipients: ' + str(RECIPIENTS))

HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; MorningBrief/1.0)'}


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


# ── STOCKS (Yahoo Finance) ─────────────────────
def fetch_stocks():
    tickers = [('ESTA','Establishment Labs'), ('APYX','Apyx Medical'), ('IART','Integra LifeSciences')]
    result = []
    for ticker, name in tickers:
        try:
            url = 'https://query1.finance.yahoo.com/v8/finance/chart/' + ticker + '?range=2d&interval=1d'
            r    = requests.get(url, headers=HEADERS, timeout=10).json()
            meta = r['chart']['result'][0]['meta']
            price = meta['regularMarketPrice']
            prev  = meta.get('previousClose') or meta.get('chartPreviousClose') or price
            chg   = price - prev
            pct   = (chg / prev * 100) if prev else 0
            result.append({'ticker':ticker,'name':name,'price':price,'change':chg,'changePct':pct})
            print(ticker + ': $' + str(round(price,2)) + ' (' + ('+' if pct>=0 else '') + str(round(pct,2)) + '%)')
        except Exception as e:
            print('Stock error ' + ticker + ': ' + str(e))
            result.append({'ticker':ticker,'name':name,'price':None,'change':None,'changePct':None})
    return result


# ── NEWS (Google News RSS – zero cost) ─────────
NEWS_QUERIES = [
    # Products & Partners
    '"Establishment Labs" OR "Motiva implant" OR "Apyx Medical" OR "Renuvion" OR "Lipoelastic" OR "pHformula" OR "Integra IDRT" OR "Vaser liposuction" OR "Revanesse" OR "Prollenium" OR "Sunekos" OR "RegenLab" OR "body-jet" OR "Puregraft"',
    # Competitors & Swiss market
    '"Allergan" aesthetics OR "Mentor implant" OR "Galderma" aesthetics OR "Merz Aesthetics" OR "InMode" aesthetic OR "breast implant" Switzerland OR "Albin Group" OR "Calista Medical" OR aesthetic medicine Switzerland',
    # Customers & Industry
    '"Hirslanden" OR "Lucerne Clinic" OR "CHUV" plastic surgery OR "Insel Gruppe" OR "Swissmedic" Medizinprodukt OR "plastic surgery" Switzerland OR "aesthetic medicine" Schweiz OR "IMCAS 2026"',
]

def parse_rss(xml_text):
    items = []
    try:
        root = ET.fromstring(xml_text)
        ns   = {'media': 'http://search.yahoo.com/mrss/'}
        for item in root.iter('item'):
            title_el = item.find('title')
            link_el  = item.find('link')
            pub_el   = item.find('pubDate')
            src_el   = item.find('source')

            title = (title_el.text or '') if title_el is not None else ''
            pub   = (pub_el.text or '')   if pub_el  is not None else ''
            src   = (src_el.text or '')   if src_el  is not None else ''

            # Google News: link is a text node after <link/>
            link = ''
            if link_el is not None:
                link = link_el.text or ''
            if not link:
                guid = item.find('guid')
                link = guid.text if guid is not None else '#'

            # Clean title: remove "- Source Name" suffix
            title = re.sub(r'\s+-\s+\S.{2,40}$', '', title).strip()
            # Decode HTML entities
            for ent, ch in [('&amp;','&'),('&lt;','<'),('&gt;','>'),('&quot;','"'),('&#39;',"'"),('&nbsp;',' ')]:
                title = title.replace(ent, ch)

            if len(title) < 10:
                continue

            # Parse age
            ago = ''
            try:
                from email.utils import parsedate_to_datetime
                dt  = parsedate_to_datetime(pub)
                diff = datetime.now(dt.tzinfo) - dt
                secs = diff.total_seconds()
                if secs < 3600:    ago = str(int(secs//60)) + ' Min.'
                elif secs < 86400: ago = str(int(secs//3600)) + ' Std.'
                else:              ago = str(int(secs//86400)) + ' Tage'
            except Exception:
                ago = ''

            items.append({'title': title, 'url': link, 'source': src, 'ago': ago, 'summary': ''})
    except Exception as e:
        print('RSS parse error: ' + str(e))
    return items

def fetch_news():
    all_items = []
    seen_titles = set()
    for i, query in enumerate(NEWS_QUERIES):
        try:
            rss_url = 'https://news.google.com/rss/search?q=' + quote(query) + '&hl=de&gl=CH&ceid=CH:de'
            r = requests.get(rss_url, headers=HEADERS, timeout=15)
            items = parse_rss(r.text)
            new_items = []
            for item in items:
                key = item['title'][:60].lower()
                if key not in seen_titles:
                    seen_titles.add(key)
                    new_items.append(item)
            all_items.extend(new_items[:6])
            print('Query ' + str(i+1) + ': ' + str(len(new_items)) + ' new items')
            time.sleep(1)
        except Exception as e:
            print('News query ' + str(i+1) + ' error: ' + str(e))

    print('Total news: ' + str(len(all_items)))
    return all_items[:18]


# ── GENERATE data.json ─────────────────────────
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
    print('data.json saved: ' + str(len(data['stocks'])) + ' stocks, ' + str(len(data['news'])) + ' news')
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

    # Forex rows - each row linked to Yahoo Finance
    forex_items = [
        ('EUR / CHF', 'eur_chf', 4, 'https://finance.yahoo.com/quote/EURCHF=X/'),
        ('USD / CHF', 'usd_chf', 4, 'https://finance.yahoo.com/quote/USDCHF=X/'),
    ]
    forex_rows = ''
    for label, key, dec, link in forex_items:
        fx = data.get('forex', {}).get(key, {})
        forex_rows += (
            '<tr style="border-bottom:1px solid #334155">'
            '<td style="padding:12px 16px;font-size:13px;color:#94a3b8;width:35%">'
            '<a href="' + link + '" style="color:#94a3b8;text-decoration:none">' + label + '</a></td>'
            '<td style="padding:12px 16px;font-size:17px;font-weight:900;color:#f1f5f9">' + fmt(fx.get('rate'), dec) + '</td>'
            '<td style="padding:12px 16px;font-size:12px;text-align:right">' + chg_span(fx.get('changePct')) + ' <span style="color:#475569">(24h)</span></td>'
            '</tr>'
        )
    btc = data.get('bitcoin', {})
    forex_rows += (
        '<tr>'
        '<td style="padding:12px 16px;font-size:13px;color:#94a3b8">'
        '<a href="https://finance.yahoo.com/quote/BTC-USD/" style="color:#94a3b8;text-decoration:none">Bitcoin / USD</a></td>'
        '<td style="padding:12px 16px;font-size:17px;font-weight:900;color:#f1f5f9">$ ' + fmt(btc.get('price'), 0) + '</td>'
        '<td style="padding:12px 16px;font-size:12px;text-align:right">' + chg_span(btc.get('changePct')) + ' <span style="color:#475569">(24h)</span></td>'
        '</tr>'
    )

    # Stock cards - Outlook-safe nested tables, fixed equal heights
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
            '<tr><td height="16"></td></tr>'
            '<tr><td align="center" height="16" '
            'style="font-size:11px;font-weight:800;color:#64748b;letter-spacing:2px">' + ticker + '</td></tr>'
            '<tr><td align="center" height="28" '
            'style="font-size:10px;color:#475569;padding:0 6px;line-height:1.3">' + name + '</td></tr>'
            '<tr><td align="center" height="28" '
            'style="font-size:20px;font-weight:900;color:#f1f5f9">' + price + '</td></tr>'
            '<tr><td align="center" height="22" '
            'style="font-size:12px;font-weight:700;color:' + color + '">' + sign + '{:.2f}'.format(pct) + '%</td></tr>'
            '<tr><td height="16"></td></tr>'
            '</table></a></td>'
        )

    # News rows
    news_rows = ''
    for item in data.get('news', []):
        title   = str(item.get('title', ''))
        url     = str(item.get('url', '#'))
        source  = str(item.get('source', ''))
        ago     = str(item.get('ago', ''))
        meta    = ''
        if source: meta += source
        if source and ago: meta += ' &nbsp;&middot;&nbsp; '
        if ago: meta += ago
        news_rows += (
            '<tr><td style="padding:11px 0;border-bottom:1px solid #1e293b">'
            '<a href="' + url + '" style="text-decoration:none;display:block">'
            + ('<div style="font-size:10px;color:#475569;margin-bottom:3px">' + meta + '</div>' if meta else '') +
            '<div style="font-size:13px;font-weight:700;color:#e2e8f0;line-height:1.45">' + title + '</div>'
            '</a></td></tr>'
        )
    if not news_rows:
        news_rows = '<tr><td style="padding:16px 0;font-size:12px;color:#64748b">Keine Neuigkeiten heute.</td></tr>'

    # Full email HTML
    html = (
        '<!DOCTYPE html><html><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0"></head>'
        '<body style="margin:0;padding:0;background:#0f172a;font-family:Arial,Helvetica,sans-serif">'
        '<div style="max-width:640px;margin:0 auto;padding:0 16px 40px">'

        # Header
        '<table style="width:100%;border-collapse:collapse">'
        '<tr><td style="padding:28px 0 20px;text-align:center;border-bottom:2px solid #1e293b">'
        '<div style="font-size:10px;letter-spacing:3px;text-transform:uppercase;color:#475569;margin-bottom:8px">'
        'ESTHETIC MED &middot; MEDICAL ESTHETIC</div>'
        '<div style="font-size:34px;font-weight:900;color:#f1f5f9;letter-spacing:-1px;line-height:1">Morning Brief</div>'
        '<div style="font-size:13px;color:#64748b;margin-top:8px">' + date_str + '</div>'
        '</td></tr></table>'

        # Forex
        '<div style="font-size:10px;font-weight:800;letter-spacing:2px;text-transform:uppercase;'
        'color:#475569;margin:24px 0 10px">W&Auml;HRUNGEN &amp; MARKT</div>'
        '<table style="width:100%;border-collapse:collapse;background:#1e293b;'
        'border:1px solid #334155;border-radius:12px;overflow:hidden;margin-bottom:24px">'
        + forex_rows + '</table>'

        # Stocks
        '<div style="font-size:10px;font-weight:800;letter-spacing:2px;text-transform:uppercase;'
        'color:#475569;margin-bottom:10px">PARTNER-AKTIEN (NASDAQ)</div>'
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;margin-bottom:28px">'
        '<tr>' + stock_cells + '</tr></table>'

        # News
        '<div style="font-size:10px;font-weight:800;letter-spacing:2px;text-transform:uppercase;'
        'color:#475569;margin-bottom:6px">NEWS &amp; RADAR</div>'
        '<table style="width:100%;border-collapse:collapse">' + news_rows + '</table>'

        # Footer
        '<table style="width:100%;border-collapse:collapse;margin-top:28px">'
        '<tr><td style="padding-top:20px;border-top:1px solid #1e293b;text-align:center">'
        '<a href="https://patrickheeb86.github.io/morning-brief/" '
        'style="color:#64748b;text-decoration:none;font-size:11px">Dashboard &ouml;ffnen</a>'
        '<span style="color:#334155;font-size:11px"> &nbsp;&middot;&nbsp; '
        'esthetic med GmbH / medical esthetic GmbH</span>'
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
