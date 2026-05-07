import os, json, re, time, smtplib, requests, anthropic
from datetime import datetime, date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# CONFIG
API_KEY    = os.environ.get('ANTHROPIC_API_KEY', '')
GMAIL_USER = os.environ.get('GMAIL_USER', '')
GMAIL_PASS = os.environ.get('GMAIL_PASS', '')
RECIPIENTS = [r.strip() for r in os.environ.get('EMAIL_RECIPIENTS', '').split(',') if r.strip()]

print('=== Startup ===')
print('API key set:  ' + ('YES' if API_KEY else 'NO - MISSING!'))
print('Gmail user:   ' + (GMAIL_USER if GMAIL_USER else 'not set'))
print('Gmail pass:   ' + ('set' if GMAIL_PASS else 'not set'))
print('Recipients:   ' + str(RECIPIENTS))

if not API_KEY:
    raise ValueError('ANTHROPIC_API_KEY missing in GitHub Secrets!')

client = anthropic.Anthropic(api_key=API_KEY)
MODEL  = 'claude-sonnet-4-6'


def parse_json(text):
    text = re.sub(r'```json|```', '', text).strip()
    m = re.search(r'[\[{]', text)
    if not m:
        return None
    try:
        return json.loads(text[m.start():])
    except Exception:
        return None


# ── FOREX (direct CDN, no Claude) ───────────────
def fetch_forex():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    result = {}
    for key, base, target in [('eur_chf', 'eur', 'chf'), ('usd_chf', 'usd', 'chf')]:
        try:
            base_url = 'https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@'
            r_now  = requests.get(base_url + 'latest/v1/currencies/' + base + '.json', timeout=10).json()
            r_prev = requests.get(base_url + yesterday + '/v1/currencies/' + base + '.json', timeout=10).json()
            rate   = r_now[base][target]
            prev   = r_prev[base][target]
            chg    = rate - prev
            pct    = (chg / prev * 100) if prev else 0
            result[key] = {'rate': rate, 'change': chg, 'changePct': pct}
            print(key + ': ' + str(round(rate, 4)) + ' (' + ('+' if pct >= 0 else '') + str(round(pct, 2)) + '%)')
        except Exception as e:
            print('Forex error ' + key + ': ' + str(e))
            result[key] = {'rate': None, 'change': None, 'changePct': None}
    return result


# ── BITCOIN (CoinGecko, no Claude) ──────────────
def fetch_bitcoin():
    try:
        url = ('https://api.coingecko.com/api/v3/simple/price'
               '?ids=bitcoin&vs_currencies=usd&include_24hr_change=true')
        d = requests.get(url, timeout=10).json()['bitcoin']
        print('BTC: $' + str(int(d['usd'])) + ' (' + ('+' if d['usd_24h_change'] >= 0 else '') + str(round(d['usd_24h_change'], 2)) + '%)')
        return {'price': d['usd'], 'changePct': d['usd_24h_change']}
    except Exception as e:
        print('Bitcoin error: ' + str(e))
        return {'price': None, 'changePct': None}


# ── STOCKS (Yahoo Finance directly, no Claude) ───
def fetch_stocks():
    tickers = [
        ('ESTA', 'Establishment Labs'),
        ('APYX', 'Apyx Medical'),
        ('IART', 'Integra LifeSciences'),
    ]
    result = []
    headers = {'User-Agent': 'Mozilla/5.0'}
    for ticker, name in tickers:
        try:
            url = ('https://query1.finance.yahoo.com/v8/finance/chart/'
                   + ticker + '?range=2d&interval=1d&includePrePost=false')
            r    = requests.get(url, headers=headers, timeout=10).json()
            meta = r['chart']['result'][0]['meta']
            price = meta['regularMarketPrice']
            prev  = meta.get('previousClose') or meta.get('chartPreviousClose') or price
            chg   = price - prev
            pct   = (chg / prev * 100) if prev else 0
            result.append({
                'ticker': ticker, 'name': name,
                'price': price, 'change': chg, 'changePct': pct
            })
            print(ticker + ': $' + str(round(price, 2)) + ' (' + ('+' if pct >= 0 else '') + str(round(pct, 2)) + '%)')
        except Exception as e:
            print('Stock error ' + ticker + ': ' + str(e))
            result.append({'ticker': ticker, 'name': name, 'price': None, 'change': None, 'changePct': None})
    return result


# ── NEWS (one Claude call with web search) ───────
def fetch_news():
    print('Calling Claude for news...')
    prompt = (
        'Search for 12-15 recent news articles (last 7 days) relevant to a Swiss medical aesthetics distributor.\n\n'
        'Topics to search:\n'
        '- Products: Establishment Labs, Motiva implants, Apyx Renuvion, Humanmed body-jet, '
        'Lipoelastic, pHformula, Puregraft, Integra IDRT, RegenLab, Sunekos, Revanesse, Prollenium, Vaser\n'
        '- Competitors: Allergan Natrelle, Mentor implants, Galderma, Merz Aesthetics, InMode, GC Aesthetics\n'
        '- Swiss market: Hirslanden, Lucerne Clinic, CHUV, HUG Geneve, Insel Gruppe, aesthetic medicine Switzerland\n'
        '- Industry: Swissmedic, plastic surgery trends, IMCAS 2026, MDR regulations\n\n'
        'Return ONLY a JSON array. No explanation. No markdown:\n'
        '[{"title":"Headline","url":"https://example.com","source":"Pub","ago":"2h","summary":"1-2 sentences."}]'
    )
    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            tools=[{'type': 'web_search_20250305', 'name': 'web_search'}],
            messages=[{'role': 'user', 'content': prompt}]
        )
        text   = '\n'.join(b.text for b in msg.content if b.type == 'text')
        result = parse_json(text)
        if isinstance(result, list) and result:
            print('Got ' + str(len(result)) + ' news items')
            return result
        print('Warning: could not parse news response')
        return []
    except Exception as e:
        print('News error: ' + str(e))
        return []


# ── GENERATE data.json ───────────────────────────
def generate():
    print('\n=== Generating data.json ===')
    data = {
        'generated': datetime.utcnow().isoformat() + 'Z',
        'forex':   fetch_forex(),
        'bitcoin': fetch_bitcoin(),
        'stocks':  fetch_stocks(),   # Direct Yahoo Finance - no Claude token cost
        'news':    fetch_news(),     # Single Claude call
    }
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print('data.json: ' + str(len(data['stocks'])) + ' stocks, ' + str(len(data['news'])) + ' news')
    return data


# ── EMAIL ────────────────────────────────────────
def f(n, dec=2):
    if n is None: return '-'
    s = ('{:.' + str(dec) + 'f}').format(abs(float(n))).split('.')
    s[0] = '{:,}'.format(int(s[0])).replace(',', "'")
    return s[0] + '.' + s[1]

def chg(n):
    if n is None: return '-'
    color = '#4ade80' if float(n) >= 0 else '#f87171'
    sign  = '+' if float(n) >= 0 else ''
    return '<span style="color:' + color + '">' + sign + '{:.2f}'.format(float(n)) + '%</span>'

def send_email(data):
    if not GMAIL_USER or not GMAIL_PASS or not RECIPIENTS:
        print('Email not configured - skipping.')
        return

    now    = datetime.now()
    days   = ['Sonntag','Montag','Dienstag','Mittwoch','Donnerstag','Freitag','Samstag']
    months = ['Januar','Februar','Maerz','April','Mai','Juni','Juli',
              'August','September','Oktober','November','Dezember']
    date_str = days[now.weekday()] + ', ' + str(now.day) + '. ' + months[now.month - 1] + ' ' + str(now.year)

    # Forex
    forex_rows = ''
    for label, key, dec in [('EUR / CHF','eur_chf',4),('USD / CHF','usd_chf',4)]:
        fx = data.get('forex', {}).get(key, {})
        forex_rows += (
            '<tr style="border-bottom:1px solid #334155">'
            '<td style="padding:10px 16px;color:#94a3b8;font-size:13px;width:35%">' + label + '</td>'
            '<td style="padding:10px 16px;font-weight:900;font-size:16px;color:#f1f5f9">' + f(fx.get('rate'), dec) + '</td>'
            '<td style="padding:10px 16px;font-size:12px;text-align:right">' + chg(fx.get('changePct')) + ' (24h)</td>'
            '</tr>'
        )
    btc = data.get('bitcoin', {})
    forex_rows += (
        '<tr>'
        '<td style="padding:10px 16px;color:#94a3b8;font-size:13px">Bitcoin / USD</td>'
        '<td style="padding:10px 16px;font-weight:900;font-size:16px;color:#f1f5f9">$ ' + f(btc.get('price'), 0) + '</td>'
        '<td style="padding:10px 16px;font-size:12px;text-align:right">' + chg(btc.get('changePct')) + ' (24h)</td>'
        '</tr>'
    )

    # Stocks
    stock_cells = ''
    for s in data.get('stocks', []):
        pct   = float(s.get('changePct') or 0)
        color = '#4ade80' if pct >= 0 else '#f87171'
        sign  = '+' if pct >= 0 else ''
        price = '$' + f(s.get('price'), 2) if s.get('price') else '-'
        stock_cells += (
            '<td style="width:33%;padding:4px">'
            '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;'
            'padding:12px 8px;text-align:center">'
            '<div style="font-size:10px;color:#64748b;letter-spacing:1.5px;margin-bottom:3px">'
            + str(s.get('ticker', '')) + '</div>'
            '<div style="font-size:9px;color:#475569;margin-bottom:8px">'
            + str(s.get('name', '')) + '</div>'
            '<div style="font-size:18px;font-weight:900;color:#f1f5f9;margin-bottom:4px">' + price + '</div>'
            '<div style="font-size:12px;color:' + color + '">' + sign + '{:.2f}'.format(pct) + '%</div>'
            '</div></td>'
        )
    if not stock_cells:
        stock_cells = '<td style="padding:10px;color:#64748b;font-size:12px">Keine Daten</td>'

    # News
    news_rows = ''
    for item in data.get('news', [])[:15]:
        title   = str(item.get('title', ''))
        url     = str(item.get('url', '#'))
        source  = str(item.get('source', ''))
        ago     = str(item.get('ago', ''))
        summary = str(item.get('summary', ''))
        meta    = (source + ' &nbsp;&middot;&nbsp; ' if source else '') + ago
        news_rows += (
            '<tr><td style="padding:12px 0;border-bottom:1px solid #1e293b">'
            '<a href="' + url + '" style="text-decoration:none;display:block">'
            '<div style="font-size:10px;color:#475569;margin-bottom:4px">' + meta + '</div>'
            '<div style="font-size:14px;font-weight:700;color:#e2e8f0;line-height:1.45;margin-bottom:5px">' + title + '</div>'
            + ('<div style="font-size:12px;color:#64748b;line-height:1.6">' + summary + '</div>' if summary else '') +
            '</a></td></tr>'
        )
    if not news_rows:
        news_rows = '<tr><td style="padding:16px 0;color:#64748b;font-size:12px">Keine Neuigkeiten heute.</td></tr>'

    html = (
        '<!DOCTYPE html><html><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0"></head>'
        '<body style="margin:0;padding:0;background:#0f172a;font-family:Arial,Helvetica,sans-serif">'
        '<div style="max-width:620px;margin:0 auto;padding:20px 16px 40px">'
        '<div style="text-align:center;padding:24px 0 20px;margin-bottom:24px;border-bottom:1px solid #1e293b">'
        '<div style="font-size:10px;letter-spacing:3px;text-transform:uppercase;color:#475569;margin-bottom:8px">'
        'ESTHETIC MED &middot; MEDICAL ESTHETIC</div>'
        '<div style="font-size:32px;font-weight:900;color:#f1f5f9;letter-spacing:-1px">Morning Brief</div>'
        '<div style="font-size:13px;color:#64748b;margin-top:8px">' + date_str + '</div>'
        '</div>'
        '<div style="font-size:10px;font-weight:800;letter-spacing:2px;text-transform:uppercase;'
        'color:#475569;margin-bottom:10px">W&Auml;HRUNGEN &amp; MARKT</div>'
        '<table style="width:100%;background:#1e293b;border:1px solid #334155;border-radius:12px;'
        'border-collapse:collapse;margin-bottom:24px">' + forex_rows + '</table>'
        '<div style="font-size:10px;font-weight:800;letter-spacing:2px;text-transform:uppercase;'
        'color:#475569;margin-bottom:10px">PARTNER-AKTIEN (NASDAQ)</div>'
        '<table style="width:100%;border-collapse:collapse;margin-bottom:24px">'
        '<tr>' + stock_cells + '</tr></table>'
        '<div style="font-size:10px;font-weight:800;letter-spacing:2px;text-transform:uppercase;'
        'color:#475569;margin-bottom:10px">NEWS &amp; RADAR</div>'
        '<table style="width:100%;border-collapse:collapse">' + news_rows + '</table>'
        '<div style="text-align:center;margin-top:32px;padding-top:20px;border-top:1px solid #1e293b">'
        '<a href="https://patrickheeb86.github.io/morning-brief/" '
        'style="color:#475569;text-decoration:none;font-size:11px">Dashboard &ouml;ffnen</a>'
        '<span style="color:#334155;font-size:11px"> &nbsp;&middot;&nbsp; '
        'esthetic med GmbH / medical esthetic GmbH &middot; K&uuml;ssnacht am Rigi</span>'
        '</div></div></body></html>'
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
