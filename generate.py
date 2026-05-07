import os, json, re, smtplib, requests, anthropic
from datetime import datetime, date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ── CONFIG ──────────────────────────────────────
API_KEY    = os.environ['ANTHROPIC_API_KEY']
GMAIL_USER = os.environ.get('GMAIL_USER', '')
GMAIL_PASS = os.environ.get('GMAIL_PASS', '')
RECIPIENTS = [r.strip() for r in os.environ.get('EMAIL_RECIPIENTS', '').split(',') if r.strip()]

client = anthropic.Anthropic(api_key=API_KEY)

# ── HELPERS ─────────────────────────────────────
def parse_json(text):
    text = re.sub(r'```json|```', '', text).strip()
    m = re.search(r'[\[{]', text)
    if not m: return None
    try: return json.loads(text[m.start():])
    except: return None

def ask_claude(prompt):
    msg = client.messages.create(
        model='claude-sonnet-4-5-20251001',
        max_tokens=2000,
        tools=[{'type': 'web_search_20250305', 'name': 'web_search'}],
        messages=[{'role': 'user', 'content': prompt}]
    )
    return '\n'.join(b.text for b in msg.content if b.type == 'text')

# ── FOREX ────────────────────────────────────────
def fetch_forex():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    result = {}
    for key, base, target in [('eur_chf','eur','chf'), ('usd_chf','usd','chf')]:
        try:
            BASE = 'https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@'
            r_now  = requests.get(BASE+'latest/v1/currencies/'+base+'.json', timeout=10).json()
            r_yest = requests.get(BASE+yesterday+'/v1/currencies/'+base+'.json', timeout=10).json()
            rate  = r_now[base][target]
            prev  = r_yest[base][target]
            chg   = rate - prev
            chgPct = (chg / prev * 100) if prev else 0
            result[key] = {'rate': rate, 'change': chg, 'changePct': chgPct}
            print(f'{key}: {rate:.4f} ({chgPct:+.2f}%)')
        except Exception as e:
            print(f'Forex {key} error: {e}')
            result[key] = {'rate': None, 'change': None, 'changePct': None}
    return result

# ── BITCOIN ──────────────────────────────────────
def fetch_bitcoin():
    try:
        d = requests.get(
            'https://api.coingecko.com/api/v3/simple/price'
            '?ids=bitcoin&vs_currencies=usd&include_24hr_change=true',
            timeout=10
        ).json()['bitcoin']
        print(f"BTC: ${d['usd']:,.0f} ({d['usd_24h_change']:+.2f}%)")
        return {'price': d['usd'], 'changePct': d['usd_24h_change']}
    except Exception as e:
        print(f'Bitcoin error: {e}')
        return {'price': None, 'changePct': None}

# ── STOCKS ───────────────────────────────────────
def fetch_stocks():
    print('Fetching stocks via Claude...')
    text = ask_claude(
        'Search current NASDAQ closing prices for: ESTA (Establishment Labs), '
        'APYX (Apyx Medical), IART (Integra LifeSciences).\n'
        'Return ONLY JSON array, no text:\n'
        '[{"ticker":"ESTA","price":71.96,"change":0.97,"changePct":1.37},'
        '{"ticker":"APYX","price":1.23,"change":-0.05,"changePct":-3.9},'
        '{"ticker":"IART","price":18.50,"change":0.30,"changePct":1.65}]'
    )
    result = parse_json(text)
    if isinstance(result, list) and result:
        for s in result:
            print(f"  {s['ticker']}: ${s.get('price','?')} ({s.get('changePct','?'):+.2f}%)" if isinstance(s.get('changePct'), (int,float)) else f"  {s.get('ticker')}: raw")
        return result
    print('  Warning: could not parse stock data')
    return []

# ── NEWS ─────────────────────────────────────────
def fetch_news():
    print('Fetching news via Claude...')
    text = ask_claude(
        'You are a news researcher for a Swiss medical aesthetics distributor '
        '(esthetic med GmbH / medical esthetic GmbH, Küssnacht am Rigi).\n\n'
        'Search for the 14-18 most relevant recent news articles (last 7 days):\n\n'
        '1. OWN PRODUCTS/PARTNERS: Establishment Labs, Motiva implants, Mia Femtech, '
        'Apyx Medical Renuvion, body-jet Humanmed liposuction, Lipoelastic compression, '
        'pHformula skincare, Puregraft, Integra IDRT, RegenLab PRP, Sunekos injectables, '
        'Revanesse Prollenium filler, Tigr Mesh, Vaser liposuction, STRIM body contouring\n\n'
        '2. COMPETITORS: Allergan Natrelle breast implants, Mentor implants J&J, '
        'Galderma Restylane, Merz Belotero, InMode BodyTite, GC Aesthetics LunaXT, '
        'breast implant safety news\n\n'
        '3. SWISS MARKET: Albin Group Switzerland, Calista Medical BTL, '
        'aesthetic medicine Switzerland, medical aesthetics Switzerland\n\n'
        '4. CUSTOMERS: Hirslanden, Lucerne Clinic, Clinique de la Source, '
        'clinic utoquai Zurich, Clinique Générale-Beaulieu Geneva, HUG Genève, '
        'CHUV, Insel Gruppe Bern, HOCH Health Ostschweiz, Spital Zollikerberg, '
        'Affidea Switzerland, thurmed\n\n'
        '5. INDUSTRY: Swissmedic regulations, plastic surgery Switzerland, '
        'IMCAS AMWC 2026, EU MDR updates, aesthetic medicine trends\n\n'
        'For each article, provide a 1-2 sentence summary in the same language as the article.\n'
        'Return ONLY a JSON array, no explanation, no markdown:\n'
        '[{"title":"Headline","url":"https://real-url.com/article","source":"Publication",'
        '"ago":"2 hours ago","summary":"1-2 sentence summary."}]\n\n'
        'Use only real articles with real URLs. Mix German, French and English.'
    )
    result = parse_json(text)
    if isinstance(result, list) and result:
        print(f'  Got {len(result)} news items')
        return result
    print('  Warning: could not parse news')
    return []

# ── GENERATE data.json ───────────────────────────
def generate():
    print('=== Morning Brief Generator ===')
    print(f'Date: {datetime.now().strftime("%d.%m.%Y %H:%M")} CET\n')

    data = {
        'generated': datetime.utcnow().isoformat() + 'Z',
        'forex':   fetch_forex(),
        'bitcoin': fetch_bitcoin(),
        'stocks':  fetch_stocks(),
        'news':    fetch_news(),
    }

    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f'\ndata.json saved ({len(data["stocks"])} stocks, {len(data["news"])} news)')
    return data

# ── EMAIL ────────────────────────────────────────
def fmt(n, dec=2, prefix=''):
    if n is None: return '–'
    s = f'{abs(float(n)):.{dec}f}'.split('.')
    s[0] = f'{int(s[0]):,}'.replace(',', "'")
    return prefix + s[0] + '.' + s[1]

def chg_html(n):
    if n is None: return ''
    c = '#4ade80' if float(n) >= 0 else '#f87171'
    return f'<span style="color:{c}">{"+" if float(n)>=0 else ""}{float(n):.2f}%</span>'

def send_email(data):
    if not GMAIL_USER or not GMAIL_PASS or not RECIPIENTS:
        print('Email not configured – skipping.')
        return

    now = datetime.now()
    D = ['Sonntag','Montag','Dienstag','Mittwoch','Donnerstag','Freitag','Samstag']
    M = ['Januar','Februar','März','April','Mai','Juni','Juli','August','September','Oktober','November','Dezember']
    date_str = f"{D[now.weekday()]}, {now.day}. {M[now.month-1]} {now.year}"

    # Forex rows
    forex_rows = ''
    for label, key, dec in [('EUR / CHF','eur_chf',4),('USD / CHF','usd_chf',4)]:
        f = data['forex'].get(key, {})
        forex_rows += (
            f'<tr>'
            f'<td style="padding:8px 14px;color:#94a3b8;font-size:12px">{label}</td>'
            f'<td style="padding:8px 14px;font-weight:800;font-size:15px;color:#f1f5f9">{fmt(f.get("rate"),dec)}</td>'
            f'<td style="padding:8px 14px;font-size:11px">{chg_html(f.get("changePct"))} (24h)</td>'
            f'</tr>'
        )
    btc = data.get('bitcoin', {})
    forex_rows += (
        f'<tr>'
        f'<td style="padding:8px 14px;color:#94a3b8;font-size:12px">Bitcoin / USD</td>'
        f'<td style="padding:8px 14px;font-weight:800;font-size:15px;color:#f1f5f9">{fmt(btc.get("price"),0,"$\u00a0")}</td>'
        f'<td style="padding:8px 14px;font-size:11px">{chg_html(btc.get("changePct"))} (24h)</td>'
        f'</tr>'
    )

    # Stock cells
    stock_cells = ''
    for s in data.get('stocks', []):
        c = '#4ade80' if float(s.get('changePct',0)) >= 0 else '#f87171'
        sign = '+' if float(s.get('changePct',0)) >= 0 else ''
        stock_cells += (
            f'<td style="width:33%;padding:0 3px">'
            f'<div style="background:#1e293b;border:1px solid #334155;border-radius:8px;padding:10px;text-align:center">'
            f'<div style="font-size:9px;color:#64748b;letter-spacing:1px">{s.get("ticker","")}</div>'
            f'<div style="font-size:16px;font-weight:900;color:#f1f5f9;margin:4px 0">${fmt(s.get("price"),2)}</div>'
            f'<div style="font-size:11px;color:{c}">{sign}{float(s.get("changePct",0)):.2f}%</div>'
            f'</div></td>'
        )

    # News items
    news_rows = ''
    for item in data.get('news', [])[:15]:
        news_rows += (
            f'<tr><td style="padding:10px 0;border-bottom:1px solid #334155">'
            f'<a href="{item.get("url","#")}" style="text-decoration:none">'
            f'<div style="font-size:10px;color:#64748b;margin-bottom:3px">'
            f'{item.get("source","")} &nbsp;·&nbsp; {item.get("ago","")}</div>'
            f'<div style="font-size:13px;font-weight:700;color:#f1f5f9;line-height:1.4;margin-bottom:4px">'
            f'{item.get("title","")}</div>'
            f'<div style="font-size:11px;color:#94a3b8;line-height:1.5">'
            f'{item.get("summary","")}</div>'
            f'</a></td></tr>'
        )

    html = f'''<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="background:#0f172a;color:#f1f5f9;font-family:Arial,sans-serif;margin:0;padding:16px">
<div style="max-width:600px;margin:0 auto">
  <div style="text-align:center;padding:20px 0 16px;border-bottom:1px solid #334155;margin-bottom:16px">
    <div style="font-size:9px;letter-spacing:3px;text-transform:uppercase;color:#64748b;margin-bottom:6px">esthetic med · medical esthetic</div>
    <div style="font-size:26px;font-weight:900;color:#f1f5f9;letter-spacing:-1px">Morning Brief</div>
    <div style="font-size:12px;color:#94a3b8;margin-top:4px">{date_str}</div>
  </div>
  <div style="font-size:9px;font-weight:800;letter-spacing:2px;text-transform:uppercase;color:#64748b;margin-bottom:8px">Währungen &amp; Markt</div>
  <table style="width:100%;background:#1e293b;border:1px solid #334155;border-radius:10px;border-collapse:collapse;margin-bottom:16px">
    {forex_rows}
  </table>
  <div style="font-size:9px;font-weight:800;letter-spacing:2px;text-transform:uppercase;color:#64748b;margin-bottom:8px">Partner-Aktien (NASDAQ)</div>
  <table style="width:100%;border-collapse:collapse;margin-bottom:16px"><tr>{stock_cells}</tr></table>
  <div style="font-size:9px;font-weight:800;letter-spacing:2px;text-transform:uppercase;color:#64748b;margin-bottom:4px">News &amp; Radar</div>
  <table style="width:100%;border-collapse:collapse">{news_rows}</table>
  <div style="text-align:center;padding:20px 0 0;font-size:9px;color:#475569">
    <a href="https://patrickheeb86.github.io/morning-brief/" style="color:#64748b;text-decoration:none">Dashboard öffnen</a>
    &nbsp;·&nbsp; esthetic med GmbH / medical esthetic GmbH · Küssnacht am Rigi
  </div>
</div></body></html>'''

    msg = MIMEMultipart('alternative')
    msg['Subject'] = f'Morning Brief · {now.strftime("%d.%m.%Y")}'
    msg['From']    = GMAIL_USER
    msg['To']      = ', '.join(RECIPIENTS)
    msg.attach(MIMEText(html, 'html', 'utf-8'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(GMAIL_USER, GMAIL_PASS)
            s.sendmail(GMAIL_USER, RECIPIENTS, msg.as_string())
        print(f'Email sent to: {", ".join(RECIPIENTS)}')
    except Exception as e:
        print(f'Email error: {e}')
        raise

if __name__ == '__main__':
    data = generate()
    send_email(data)
