import os, json, re, smtplib, time, requests, anthropic
from datetime import datetime, date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# CONFIG
API_KEY    = os.environ.get('ANTHROPIC_API_KEY', '')
GMAIL_USER = os.environ.get('GMAIL_USER', '')
GMAIL_PASS = os.environ.get('GMAIL_PASS', '')
RECIPIENTS = [r.strip() for r in os.environ.get('EMAIL_RECIPIENTS', '').split(',') if r.strip()]

print('=== Startup ===')
print('API key set: ' + ('YES' if API_KEY else 'NO - MISSING!'))
print('Gmail user:  ' + (GMAIL_USER if GMAIL_USER else 'not set'))
print('Gmail pass:  ' + ('set' if GMAIL_PASS else 'not set'))
print('Recipients:  ' + str(RECIPIENTS))

if not API_KEY:
    raise ValueError('ANTHROPIC_API_KEY is missing! Add it in GitHub Settings -> Secrets and variables -> Actions')

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


def ask_claude(label, prompt):
    print('Calling Claude for: ' + label)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        tools=[{'type': 'web_search_20250305', 'name': 'web_search'}],
        messages=[{'role': 'user', 'content': prompt}]
    )
    parts = []
    for b in msg.content:
        if b.type == 'text':
            parts.append(b.text)
    result = '\n'.join(parts)
    print('Response length: ' + str(len(result)) + ' chars')
    return result


def fetch_forex():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    result = {}
    for key, base, target in [('eur_chf', 'eur', 'chf'), ('usd_chf', 'usd', 'chf')]:
        try:
            base_url = 'https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@'
            url_now  = base_url + 'latest/v1/currencies/' + base + '.json'
            url_prev = base_url + yesterday + '/v1/currencies/' + base + '.json'
            r_now  = requests.get(url_now,  timeout=10).json()
            r_prev = requests.get(url_prev, timeout=10).json()
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


def fetch_bitcoin():
    try:
        url = 'https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true'
        d = requests.get(url, timeout=10).json()['bitcoin']
        price = d['usd']
        pct   = d['usd_24h_change']
        print('BTC: $' + str(int(price)) + ' (' + ('+' if pct >= 0 else '') + str(round(pct, 2)) + '%)')
        return {'price': price, 'changePct': pct}
    except Exception as e:
        print('Bitcoin error: ' + str(e))
        return {'price': None, 'changePct': None}


def fetch_stocks():
    prompt = (
        'Search current NASDAQ closing prices for: ESTA (Establishment Labs), '
        'APYX (Apyx Medical), IART (Integra LifeSciences).\n'
        'Return ONLY a JSON array, no text, no markdown fences:\n'
        '[{"ticker":"ESTA","price":71.96,"change":0.97,"changePct":1.37},'
        '{"ticker":"APYX","price":1.23,"change":-0.05,"changePct":-3.9},'
        '{"ticker":"IART","price":18.50,"change":0.30,"changePct":1.65}]'
    )
    text   = ask_claude('stocks', prompt)
    result = parse_json(text)
    if isinstance(result, list) and result:
        for s in result:
            print('  ' + str(s.get('ticker')) + ': $' + str(s.get('price')))
        return result
    print('Warning: could not parse stocks')
    return []


def fetch_news():
    prompt = (
        'You are a news researcher for a Swiss medical aesthetics distributor.\n\n'
        'Search for 14-18 relevant recent news articles (last 7 days) about:\n\n'
        '1. OWN PRODUCTS: Establishment Labs, Motiva implants, Mia Femtech, '
        'Apyx Medical, Renuvion, body-jet, Humanmed, Lipoelastic, pHformula, '
        'Puregraft, Integra IDRT, RegenLab, Sunekos, Revanesse, Prollenium, '
        'Tigr Mesh, Vaser liposuction, STRIM body contouring\n\n'
        '2. COMPETITORS: Allergan Natrelle implants, Mentor implants J&J, '
        'Galderma Restylane, Merz Belotero, InMode BodyTite, GC Aesthetics, '
        'breast implant safety news\n\n'
        '3. SWISS MARKET: Albin Group Switzerland, Calista Medical BTL, '
        'aesthetic medicine Switzerland\n\n'
        '4. CUSTOMERS (Swiss clinics): Hirslanden, Lucerne Clinic, '
        'Clinique de la Source, clinic utoquai, Clinique Generale-Beaulieu, '
        'HUG Geneve, CHUV, Insel Gruppe Bern, HOCH Health, Spital Zollikerberg, '
        'Affidea Switzerland, thurmed\n\n'
        '5. INDUSTRY: Swissmedic regulations, plastic surgery Switzerland, '
        'IMCAS AMWC 2026, EU MDR, aesthetic medicine trends\n\n'
        'For each article write a 1-2 sentence summary in the article language.\n'
        'Return ONLY a JSON array, no explanation, no markdown:\n'
        '[{"title":"Headline","url":"https://example.com/article",'
        '"source":"Publication","ago":"2 hours ago","summary":"Short summary."}]\n'
        'Only real articles with real URLs. Mix German, French, English.'
    )
    text   = ask_claude('news', prompt)
    result = parse_json(text)
    if isinstance(result, list) and result:
        print('Got ' + str(len(result)) + ' news items')
        return result
    print('Warning: could not parse news')
    return []


def generate():
    print('\n=== Generating data.json ===')
    data = {
        'generated': datetime.utcnow().isoformat() + 'Z',
        'forex':     fetch_forex(),
        'bitcoin':   fetch_bitcoin(),
        'stocks':    fetch_stocks(),
    }
    print('Waiting 65s between Claude calls (rate limit)...')
    time.sleep(65)
    data['news'] = fetch_news()
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print('data.json saved: ' + str(len(data['stocks'])) + ' stocks, ' + str(len(data['news'])) + ' news')
    return data


def num_fmt(n, dec=2):
    if n is None:
        return '-'
    val  = abs(float(n))
    fmt  = ('{:,.' + str(dec) + 'f}').format(val)
    fmt  = fmt.replace(',', "'")
    return fmt


def chg_cell(n):
    if n is None:
        return '-'
    color = '#4ade80' if float(n) >= 0 else '#f87171'
    sign  = '+' if float(n) >= 0 else ''
    return '<span style="color:' + color + '">' + sign + str(round(float(n), 2)) + '%</span>'


def send_email(data):
    if not GMAIL_USER or not GMAIL_PASS or not RECIPIENTS:
        print('Email credentials not set - skipping email.')
        return

    now    = datetime.now()
    days   = ['Sonntag','Montag','Dienstag','Mittwoch','Donnerstag','Freitag','Samstag']
    months = ['Januar','Februar','Maerz','April','Mai','Juni','Juli',
              'August','September','Oktober','November','Dezember']
    date_str = days[now.weekday()] + ', ' + str(now.day) + '. ' + months[now.month - 1] + ' ' + str(now.year)

    # Forex table rows
    forex_rows = ''
    for label, key, dec in [('EUR / CHF', 'eur_chf', 4), ('USD / CHF', 'usd_chf', 4)]:
        f    = data['forex'].get(key, {})
        rate = num_fmt(f.get('rate'), dec) if f.get('rate') else '-'
        chg  = chg_cell(f.get('changePct'))
        forex_rows += (
            '<tr>'
            '<td style="padding:8px 14px;color:#94a3b8;font-size:12px">' + label + '</td>'
            '<td style="padding:8px 14px;font-weight:800;font-size:15px;color:#f1f5f9">' + rate + '</td>'
            '<td style="padding:8px 14px;font-size:11px">' + chg + ' (24h)</td>'
            '</tr>'
        )
    btc = data.get('bitcoin', {})
    btc_price = ('$ ' + num_fmt(btc.get('price'), 0)) if btc.get('price') else '-'
    forex_rows += (
        '<tr>'
        '<td style="padding:8px 14px;color:#94a3b8;font-size:12px">Bitcoin / USD</td>'
        '<td style="padding:8px 14px;font-weight:800;font-size:15px;color:#f1f5f9">' + btc_price + '</td>'
        '<td style="padding:8px 14px;font-size:11px">' + chg_cell(btc.get('changePct')) + ' (24h)</td>'
        '</tr>'
    )

    # Stock cells
    stock_cells = ''
    for s in data.get('stocks', []):
        pct   = float(s.get('changePct', 0))
        color = '#4ade80' if pct >= 0 else '#f87171'
        sign  = '+' if pct >= 0 else ''
        stock_cells += (
            '<td style="width:33%;padding:0 3px">'
            '<div style="background:#1e293b;border:1px solid #334155;border-radius:8px;'
            'padding:10px;text-align:center">'
            '<div style="font-size:9px;color:#64748b;letter-spacing:1px">' + str(s.get('ticker', '')) + '</div>'
            '<div style="font-size:16px;font-weight:900;color:#f1f5f9;margin:4px 0">$' + num_fmt(s.get('price'), 2) + '</div>'
            '<div style="font-size:11px;color:' + color + '">' + sign + str(round(pct, 2)) + '%</div>'
            '</div></td>'
        )

    # News rows
    news_rows = ''
    for item in data.get('news', [])[:15]:
        title   = str(item.get('title', ''))
        url     = str(item.get('url', '#'))
        source  = str(item.get('source', ''))
        ago     = str(item.get('ago', ''))
        summary = str(item.get('summary', ''))
        news_rows += (
            '<tr><td style="padding:10px 0;border-bottom:1px solid #334155">'
            '<a href="' + url + '" style="text-decoration:none">'
            '<div style="font-size:10px;color:#64748b;margin-bottom:3px">' + source + ' &nbsp;&#183;&nbsp; ' + ago + '</div>'
            '<div style="font-size:13px;font-weight:700;color:#f1f5f9;line-height:1.4;margin-bottom:4px">' + title + '</div>'
            '<div style="font-size:11px;color:#94a3b8;line-height:1.5">' + summary + '</div>'
            '</a></td></tr>'
        )

    dashboard_url = 'https://patrickheeb86.github.io/morning-brief/'

    html = (
        '<!DOCTYPE html><html><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        '<body style="background:#0f172a;color:#f1f5f9;font-family:Arial,sans-serif;margin:0;padding:16px">'
        '<div style="max-width:600px;margin:0 auto">'
        '<div style="text-align:center;padding:20px 0 16px;border-bottom:1px solid #334155;margin-bottom:16px">'
        '<div style="font-size:9px;letter-spacing:3px;text-transform:uppercase;color:#64748b;margin-bottom:6px">'
        'esthetic med &middot; medical esthetic</div>'
        '<div style="font-size:26px;font-weight:900;color:#f1f5f9;letter-spacing:-1px">Morning Brief</div>'
        '<div style="font-size:12px;color:#94a3b8;margin-top:4px">' + date_str + '</div>'
        '</div>'
        '<div style="font-size:9px;font-weight:800;letter-spacing:2px;text-transform:uppercase;'
        'color:#64748b;margin-bottom:8px">W&auml;hrungen &amp; Markt</div>'
        '<table style="width:100%;background:#1e293b;border:1px solid #334155;border-radius:10px;'
        'border-collapse:collapse;margin-bottom:16px">' + forex_rows + '</table>'
        '<div style="font-size:9px;font-weight:800;letter-spacing:2px;text-transform:uppercase;'
        'color:#64748b;margin-bottom:8px">Partner-Aktien (NASDAQ)</div>'
        '<table style="width:100%;border-collapse:collapse;margin-bottom:16px"><tr>' + stock_cells + '</tr></table>'
        '<div style="font-size:9px;font-weight:800;letter-spacing:2px;text-transform:uppercase;'
        'color:#64748b;margin-bottom:4px">News &amp; Radar</div>'
        '<table style="width:100%;border-collapse:collapse">' + news_rows + '</table>'
        '<div style="text-align:center;padding:20px 0 0;font-size:9px;color:#475569">'
        '<a href="' + dashboard_url + '" style="color:#64748b;text-decoration:none">Dashboard &ouml;ffnen</a>'
        ' &nbsp;&middot;&nbsp; esthetic med GmbH / medical esthetic GmbH &middot; K&uuml;ssnacht am Rigi'
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
