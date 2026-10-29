#!/usr/bin/env python3
"""Cloud NHL report: live data, mobile HTML, verified SMTP delivery, independent Pages publish."""
import argparse, concurrent.futures, datetime as dt, email, html, imaplib, json, os
import re, smtplib, ssl, sys, time, urllib.parse, urllib.request
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo
PT = ZoneInfo('America/Los_Angeles')
NHL = 'https://api-web.nhle.com/v1/'
ESPN = 'https://site.api.espn.com/apis/site/'
AUDIT = []

def fetch(url, attempts=3):
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={'User-Agent':'NHL-Daily/1.0', 'Accept':'application/json'})
            with urllib.request.urlopen(req, timeout=20) as r:
                value = json.load(r)
            AUDIT.append({'url':url,'retrieved':dt.datetime.now(dt.timezone.utc).isoformat()})
            return value
        except Exception:
            if attempt + 1 == attempts: raise
            time.sleep(2 ** attempt)

def optional(url, default=None):
    try: return fetch(url)
    except Exception: return default

def text(v):
    return v.get('default','') if isinstance(v,dict) else str(v or '')

def esc(v): return html.escape(str(v), quote=True)

def short(name):
    words = name.split()
    return words[0][0]+'. '+' '.join(words[1:]) if len(words)>1 else name

def iso(v): return dt.datetime.fromisoformat(v.replace('Z','+00:00'))

def season_for(day):
    start=day.year if day.month >= 7 else day.year-1
    return int(f'{start}{start+1}')

def score(day):
    d=fetch(ESPN+'v2/sports/hockey/nhl/scoreboard?dates='+day.strftime('%Y%m%d')+'&limit=100')
    if 'events' not in d or not d.get('leagues'): raise ValueError('Schedule response missing events/leagues')
    return d

def team(c):
    t=dict(c['team']);t['logo']=t.get('logo') or (t.get('logos') or [{}])[0].get('href','')
    return t

def teams_of(event):
    c=event['competitions'][0]['competitors']
    return next(x for x in c if x['homeAway']=='away'),next(x for x in c if x['homeAway']=='home')

def get_records(day, season):
    d=optional(NHL+'standings/'+day.isoformat())
    if d and d.get('standings'):
        return {text(t['teamAbbrev']):(t['wins'],t['losses'],t['otLosses']) for t in d['standings']}
    d=optional('https://site.api.espn.com/apis/v2/sports/hockey/nhl/standings?season='+str(season%10000),{})
    records={}
    def walk(node):
        standing=node.get('standings',{})
        if standing.get('season')==season%10000 and standing.get('seasonType')==2:
            for row in standing.get('entries',[]):
                s={x['name']:x.get('value') for x in row['stats']}
                if all(s.get(k) is not None for k in ['wins','losses','otLosses']):
                    records[row['team']['abbreviation']]=tuple(int(s[k]) for k in ['wins','losses','otLosses'])
        for child in node.get('children',[]): walk(child)
    walk(d)
    return records

def record(t, records):
    r=records.get(t['abbreviation'])
    return '-'.join(map(str,r)) if r is not None else 'Record unavailable'

def leaders(season):
    d=optional(NHL+f'skater-stats-leaders/{season}/2?categories=points,goals,assists&limit=100',{})
    result={}
    for key in ['points','goals','assists']:
        rows=d.get(key,[])
        result[key]=[{'name':short(text(p['firstName'])+' '+text(p['lastName'])), 'value':p['value'], 'team':{'abbreviation':p.get('teamAbbrev',''),'displayName':p.get('teamAbbrev','')}} for p in rows]
    if not all(result.values()):
        e=optional(ESPN+f'v3/sports/hockey/nhl/leaders?season={season%10000}&seasontype=2',{})
        if e.get('requestedSeason',{}).get('year')==season%10000:
            for cat in e.get('leaders',{}).get('categories',[]):
                if cat['name'] in result and not result[cat['name']]:
                    result[cat['name']]=[{'name':short(p['athlete']['displayName']), 'value':p['value'], 'team':p.get('team') or p['athlete'].get('team',{})} for p in cat['leaders']]
    return result

def team_leaders(t, season):
    d=optional(NHL+f"club-stats/{t['abbreviation']}/{season}/2",{})
    rows=d.get('skaters',[])
    result={}
    for key in ['goals','assists']:
        if rows:
            best=max(p.get(key,0) for p in rows)
            names=[short(text(p['firstName'])+' '+text(p['lastName'])) for p in rows if p.get(key,0)==best]
            result[key]=', '.join(names)+f' — {best}'+(' each (tied)' if len(names)>1 else '')
        else: result[key]='Stats unavailable'
    if not rows:
        core=optional(f'https://sports.core.api.espn.com/v2/sports/hockey/leagues/nhl/seasons/{season%10000}/types/2/teams/{t["id"]}/leaders',{})
        for cat in core.get('categories',[]):
            if cat['name'] not in result: continue
            players=cat.get('leaders',[])
            if not players: continue
            best=max(p['value'] for p in players)
            tied=[p for p in players if p['value']==best]
            names=[]
            for player in tied:
                ref=player.get('athlete',{}).get('$ref','').replace('http:','https:')
                if not ref.startswith('https://sports.core.api.espn.com/'): continue
                athlete=optional(ref,{})
                if athlete.get('displayName'): names.append(short(athlete['displayName']))
            if len(names)==len(tied) and len(players)>len(tied):
                result[cat['name']]=', '.join(names)+f' — {best:g}'+(' each (tied)' if len(names)>1 else '')
    return result

def goalie(c):
    for p in c.get('probables',[]):
        if p.get('name')=='probableStartingGoalie' and p.get('athlete'):
            status=p.get('status',{}).get('name','').lower()
            label='Confirmed' if status=='confirmed' else 'Projected'
            return short(p['athlete']['displayName'])+' — '+label
    return 'Not yet announced'

def moneyline(summary,event):
    if event.get('status',{}).get('type',{}).get('state')!='pre': return 'Odds not available'
    away,home=teams_of(event)
    for book in summary.get('pickcenter',[]):
        # ESPN's full-game two-way NHL moneyline. Never use spreads, regulation or mixed books.
        if not book.get('provider',{}).get('name'): continue
        ml=book.get('moneyline',{})
        vals=[]
        for side in ['away','home']:
            raw=ml.get(side,{}).get('current',{}).get('odds') or ml.get(side,{}).get('close',{}).get('odds')
            if raw is None: raw=book.get(side+'TeamOdds',{}).get('moneyLine')
            try:
                n=int(str(raw).replace('−','-'))
                if n==0: raise ValueError()
                vals.append(n)
            except (TypeError,ValueError): break
        if len(vals)!=2: continue
        implied=lambda n: -n/(-n+100) if n<0 else 100/(n+100)
        if implied(vals[0])==implied(vals[1]): return 'Pick’em'
        i=0 if implied(vals[0])>implied(vals[1]) else 1
        geographic=team([away,home][i]).get('location','')
        return f'{geographic} ({vals[i]:+d})'
    return 'Odds not available'

def logo(t):
    ab=t.get('abbreviation','').lower(); ab={'utah':'uta'}.get(ab,ab)
    src=t.get('logo') or (t.get('logos') or [{}])[0].get('href') or f'https://a.espncdn.com/i/teamlogos/nhl/500/{ab}.png'
    if not re.match(r'^https://a\.espncdn\.com/.+\.png$',src): return esc(t.get('displayName',ab))
    return f'<img src="{esc(src)}" width="28" height="28" alt="{esc(t.get("displayName",ab))}" style="width:28px;height:28px;object-fit:contain;vertical-align:middle">'

TRIVIA=[
 ('Wayne Gretzky scored 50 goals in his first 39 games of the 1981–82 season.', 'https://www.nhl.com/news/nhl-greatest-moments-wayne-gretzky-50-goals-in-39-games-292533102'),
 ('The Stanley Cup was first awarded in 1893, before the NHL existed.', 'https://records.nhl.com/awards/stanley-cup/history-of-the-trophy'),
 ('Detroit and the Montreal Maroons played six overtime periods in a 1936 playoff game.', 'https://www.nhl.com/news/this-date-in-nhl-history-march-24-279805016'),
 ('Only NHL teams have competed for the Stanley Cup since the 1926–27 season.', 'https://records.nhl.com/awards/stanley-cup/history-of-the-trophy')]

def historical(day):
    # Verified exact-date entries take precedence. Fallback facts are explicitly undated trivia.
    exact={
      '12-30':('1981: Wayne Gretzky reached 50 goals in his 39th game of the season.','https://www.nhl.com/news/this-date-in-nhl-history-december-30-285055510'),
      '03-24':('1936: Detroit and the Montreal Maroons began a playoff game that needed six overtime periods.','https://www.nhl.com/news/this-date-in-nhl-history-march-24-279805016')}
    key=day.strftime('%m-%d')
    if key in exact: return '🗓️ ON THIS DAY IN NHL HISTORY...',*exact[key]
    # Exact-date coverage is expanded in history.json without changing code.
    entries=json.loads(Path(__file__).with_name('history.json').read_text()) if Path(__file__).with_name('history.json').exists() else {}
    if key in entries:
        entry=entries[key]
        return '🗓️ ON THIS DAY IN NHL HISTORY...',entry['text'],entry['source']
    fact,url=TRIVIA[day.toordinal()%len(TRIVIA)]
    return '🗓️ FUN NHL TRIVIA...',fact,url

CSS='''body{margin:0;color:#202124;font-family:Arial,sans-serif}table{box-sizing:border-box}td{overflow-wrap:break-word}.card{display:inline-table;vertical-align:top;width:100%;max-width:232px;margin:0 4px 8px 0}.section{padding:16px 24px}.game{padding:20px 24px}.info{width:100%;table-layout:fixed;border-collapse:collapse;font-size:13px}.info td,.info th{padding:8px 4px;text-align:left;vertical-align:top;border-bottom:1px solid #ddd}.info th{font-size:11px}h2{font-size:20px;margin:0 0 10px}h3{font-size:18px;line-height:1.5;margin:0 0 8px}@media(max-width:600px){.section,.game{padding-left:16px!important;padding-right:16px!important}.card{display:table!important;width:100%!important;max-width:none!important;table-layout:fixed}.info{font-size:12px}.info th{font-size:10px}.shell{width:100%!important}}'''

def card(inner): return '<table class="card" role="presentation" cellpadding="8" cellspacing="0" bgcolor="#F5F5F5" style="display:inline-table;vertical-align:top;width:100%;max-width:232px;font-size:14px"><tbody>'+inner+'</tbody></table>'

def render(day,today,yesterday,records,league,details,stats):
    out=[]
    def section(s): out.append('<tr><td class="section" style="padding:16px 24px">'+s+'</td></tr>')
    season=season_for(day); season_label=f'{season//10000}–{str(season%10000)[-2:]}'
    section(f'<h1 style="margin:0 0 8px">NHL Daily</h1>{day.strftime("%A, %B")} {day.day}, {day.year}<p style="font-size:12px;color:#666">{season_label} regular season</p>')
    recap='<h2>YESTERDAY’S SCORES</h2><p>'+esc((day-dt.timedelta(days=1)).strftime('%B %d, %Y'))+'</p>'
    for e in yesterday:
        a,h=teams_of(e); status=e['status']['type']; badge=status.get('shortDetail',status.get('description','Unfinished'))
        if status.get('completed'):
            period=e['status'].get('period',3);badge='FINAL/SO' if period>=5 else 'FINAL/OT' if period==4 else 'FINAL'
        body=f'<tr><th colspan="3" align="left">{esc(badge)}</th></tr>'
        for c in [a,h]:
            t=team(c);body+=f'<tr><td width="34">{logo(t)}</td><td><b>{esc(t["name"])}</b><br><span style="font-size:12px;color:#666">({esc(record(t,records))})</span></td><td align="right" style="font-size:26px;font-weight:bold">{esc(c.get("score","—"))}</td></tr>'
        recap+=card(body)
    section(recap if yesterday else recap+'No games were played.')
    boards=f'<h2>SCORING LEADERS</h2><p style="font-size:12px">{season_label} regular season · Top five per category · T = tied rank</p>'
    for key in ['points','goals','assists']:
        entries=league.get(key,[]);body=f'<tr><th colspan="4" align="left">{key.upper()}</th></tr>'
        if len(entries)<5: body+='<tr><td colspan="4">Stats unavailable</td></tr>'
        for i,p in enumerate(entries[:5]):
            rank=next(j+1 for j,x in enumerate(entries) if x['value']==p['value']);tied=sum(x['value']==p['value'] for x in entries)>1
            body+=f'<tr><td width="24">{"T" if tied else ""}{rank}</td><td width="32">{logo(p["team"])}</td><td>{esc(p["name"])}</td><td align="right"><b>{p["value"]:g}</b></td></tr>'
        boards+=card(body)
    section(boards)
    out.append('<tr><td class="section" style="padding:12px 24px 4px"><h2 style="margin:0">TODAY’S MATCH UPS</h2></td></tr>')
    for i,e in enumerate(today):
        a,h=teams_of(e);bg='#FFFFFF' if i%2==0 else '#F5F5F5';start=iso(e['date']).astimezone(PT)
        heading=' @ '.join(logo(team(c))+' ('+esc(record(team(c),records))+')' for c in [a,h])
        body=f'<h3>{heading}</h3><p style="font-weight:bold;margin:0 0 5px">Puck drop: {start.strftime("%-I:%M %p")} PT</p><p style="font-weight:bold;margin:0 0 12px">FAVORITE: {esc(moneyline(details.get(e["id"],{}),e))}</p>'
        if e.get('season',{}).get('type')!=2: body+='<p>Preseason / playoff context: '+esc(e.get('season',{}).get('slug','Unknown'))+'</p>'
        body+='<table class="info" cellpadding="8" cellspacing="0" style="width:100%;table-layout:fixed"><colgroup><col style="width:10%"><col style="width:32%"><col style="width:32%"><col style="width:26%"></colgroup><tr>'+''.join('<th>'+x+'</th>' for x in ['TEAM','GOALS LEADER','ASSISTS LEADER','GOALIE'])+'</tr>'
        for c in [a,h]:
            t=team(c);s=stats.get(t['abbreviation'],{});body+='<tr><td>'+logo(t)+'</td>'+''.join('<td>'+esc(v)+'</td>' for v in [s.get('goals','Stats unavailable'),s.get('assists','Stats unavailable'),goalie(c)])+'</tr>'
        body+='</table>'
        out.append(f'<tr><td class="game" bgcolor="{bg}" style="background:{bg};padding:{8 if i==0 else 20}px 24px 20px">{body}</td></tr>')
    if not today: section('No NHL games are scheduled today.')
    title,fact,url=historical(day);section(f'<h3>{esc(title)}</h3><p>{esc(fact)}</p><a href="{esc(url)}">Historical source</a>')
    return '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NHL Daily</title><style>'+CSS+'</style></head><body><table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center"><table class="shell" role="presentation" width="100%" style="max-width:760px" cellspacing="0" cellpadding="0">'+''.join(out)+'</table></td></tr></table></body></html>'

def sent_exists(user,password,subject):
    with imaplib.IMAP4_SSL('imap.gmail.com',ssl_context=ssl.create_default_context()) as im:
        im.login(user,password)
        status,boxes=im.list()
        sent=next((re.search(rb'"([^"]+)"$',x).group(1).decode() for x in boxes if b'\\Sent' in x and re.search(rb'"([^"]+)"$',x)),None)
        if not sent: raise RuntimeError('Gmail Sent folder unavailable; refusing unchecked send')
        if im.select('"'+sent+'"',readonly=True)[0]!='OK': raise RuntimeError('Cannot read Sent')
        # UTF-8 subject uses a literal to preserve the em dash.
        im.literal=subject.encode('utf-8')
        status,ids=im.search('UTF-8','SUBJECT')
        if status!='OK': raise RuntimeError('Sent search failed')
        for message_id in (ids[0].split() if ids and ids[0] else []):
            status,parts=im.fetch(message_id,'(BODY.PEEK[HEADER.FIELDS (SUBJECT TO)])')
            if status!='OK': raise RuntimeError('Cannot verify Sent message headers')
            raw=next((p[1] for p in parts if isinstance(p,tuple)),b'')
            message=email.message_from_bytes(raw,policy=email.policy.default)
            if str(message['Subject'])==subject and os.environ.get('EMAIL_TO','').lower() in str(message['To']).lower(): return True
        return False

def send_report(day,body):
    user=os.environ['SMTP_USERNAME'];password=os.environ['GMAIL_APP_PASSWORD'];recipient=os.environ['EMAIL_TO']
    subject=f'NHL Daily Email — {day.strftime("%B")} {day.day}, {day.year}'
    if sent_exists(user,password,subject):
        print('Daily email already present in Gmail Sent; skipped');return
    m=EmailMessage();m['From']=user;m['To']=recipient;m['Subject']=subject
    m['Message-ID']=f'<nhl-daily-{day.isoformat()}@nhl-daily.local>'
    m.set_content('Your NHL Daily HTML report is included.');m.add_alternative(body,subtype='html')
    # Do not retry SMTP after an ambiguous acceptance. The next run checks Sent first.
    with smtplib.SMTP_SSL('smtp.gmail.com',465,context=ssl.create_default_context(),timeout=45) as smtp:
        smtp.login(user,password);smtp.send_message(m)
    for _ in range(6):
        time.sleep(5)
        if sent_exists(user,password,subject): print('Email sent and verified in Gmail Sent');return
    raise RuntimeError('SMTP accepted message, but Sent verification is pending; inspect before resending')

def publish(body):
    token=os.environ['GH_TOKEN'];repo=os.environ['GITHUB_REPOSITORY']
    import base64
    url=f'https://api.github.com/repos/{repo}/contents/index.html'
    headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','User-Agent':'NHL-Daily'}
    with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=30) as r: old=json.load(r)
    if base64.b64decode(old['content']).decode()!=body:
        data=json.dumps({'message':'Update NHL Daily report','sha':old['sha'],'branch':'main','content':base64.b64encode(body.encode()).decode()}).encode()
        with urllib.request.urlopen(urllib.request.Request(url,data=data,headers=headers,method='PUT'),timeout=30) as r: result=json.load(r)
        print('Saved report commit '+result['commit']['sha'])
    # GITHUB_TOKEN commits don't automatically trigger Pages; request a build explicitly.
    build_url=f'https://api.github.com/repos/{repo}/pages/builds'
    with urllib.request.urlopen(urllib.request.Request(build_url,data=b'{}',headers=headers,method='POST'),timeout=30) as r: json.load(r)
    public=f'https://{repo.split("/")[0]}.github.io/{repo.split("/")[1]}/'
    for attempt in range(24):
        try:
            with urllib.request.urlopen(public+'?check='+str(int(time.time())),timeout=20) as r: live=r.read().decode()
            if live==body: print('Live GitHub Pages report verified');return
        except Exception: pass
        time.sleep(10)
    raise RuntimeError('Report committed, but live Pages deployment not verified yet')

def main():
    p=argparse.ArgumentParser();p.add_argument('--date');p.add_argument('--preview',action='store_true');args=p.parse_args()
    day=dt.date.fromisoformat(args.date) if args.date else dt.datetime.now(PT).date()
    season=season_for(day)
    current=score(day);prev=score(day-dt.timedelta(days=1))
    today=[e for e in current['events'] if iso(e['date']).astimezone(PT).date()==day]
    yesterday=[e for e in prev['events'] if iso(e['date']).astimezone(PT).date()==day-dt.timedelta(days=1)]
    today.sort(key=lambda e:(not any(team(c)['abbreviation']=='CHI' for c in teams_of(e)),e['date']))
    records=get_records(day,season);league=leaders(season);details={};stats={}
    allteams={team(c)['abbreviation']:team(c) for e in today for c in teams_of(e)}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        fs={pool.submit(team_leaders,t,season):k for k,t in allteams.items()}
        for f,k in fs.items(): stats[k]=f.result()
        fs={pool.submit(optional,ESPN+'v2/sports/hockey/nhl/summary?event='+e['id'],{}):e['id'] for e in today}
        for f,k in fs.items(): details[k]=f.result()
    body=render(day,today,yesterday,records,league,details,stats)
    Path('build').mkdir(exist_ok=True);Path('build/report.html').write_text(body)
    Path('build/research.json').write_text(json.dumps({'date':day.isoformat(),'sources':AUDIT},indent=2))
    print(f'Report built: {len(yesterday)} recap games, {len(today)} matchups')
    if args.preview: return
    errors=[]
    try: send_report(day,body)
    except Exception as e: errors.append('Email delivery failed ('+type(e).__name__+'); check mail authorization and service status')
    try: publish(body)
    except Exception as e: errors.append('Website publishing failed ('+type(e).__name__+')')
    for error in errors: print('::error::'+error)
    if errors: sys.exit(1)
if __name__=='__main__': main()
