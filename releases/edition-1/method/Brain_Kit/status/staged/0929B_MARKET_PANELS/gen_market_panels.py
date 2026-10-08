#!/usr/bin/env python3
"""gen_market_panels.py - Market panel generator from E1 dataset.
Reads Namebeam_E1_Dataset_LATEST.csv, filters by date >= 2026-09-01, non-empty market, engine != "agree".
Writes panels/<market_slug>.md (one per market), panels/INDEX.md, panels/PANELS_MANIFEST.json.
Copy law: ASCII only, no em/en dashes, no emoji, no banned words.
Proper-noun gate for non-serp engines; URL-to-host for google_serp.
Selftest mode builds a fixture CSV and validates output.
"""
import csv,json,re,collections,sys,os,tempfile,time,hashlib,unicodedata,urllib.parse

def slugify(s):
    """Convert market name to filename slug."""
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')

def asc(s):
    """Convert to ASCII only, no fancy dashes or quotes."""
    if not isinstance(s, str):
        s = str(s)
    s = s.replace('—', ', ').replace('–', ', ').replace('’', "'")
    s = s.replace('‘', "'").replace('“', '"').replace('”', '"')
    s = s.replace(' ', ' ').replace('•', ',').replace('®', '').replace('™', '')
    return ''.join(c for c in unicodedata.normalize('NFKD', s) if ord(c) < 128)

def strip_banned(s):
    """Remove banned words from string."""
    banned = ['unlock', 'supercharge', 'revolutionary', 'game-changing', 'seamless', 'effortless', 'guarantee', 'ranking', 'honest']
    for w in banned:
        s = re.sub(r'\b' + re.escape(w) + r'\b', '', s, flags=re.I)
    return re.sub(r'\s+', ' ', s).strip()

def is_proper_noun(s):
    """Check if s is a proper noun (not advice text).
    Every word must be capitalized OR in the allowed set, first word not in stoplist, no banned phrases."""
    if not s or len(s) < 4:
        return False
    
    stoplist = {'where', 'how', 'what', 'why', 'look', 'read', 'check', 'get', 'review', 'request', 
                'interview', 'ask', 'compare', 'verify', 'consider', 'use', 'key', 'avoid', 'choose', 
                'find', 'visit', 'contact', 'call', 'schedule', 'start', 'make', 'see', 'note', 'tip', 
                'tips', 'step', 'steps', 'pros', 'cons', 'best', 'top'}
    
    allowed_lowercase = {'of', 'the', 'and', 'de', 'la', 'for', 'at', 'by', 'on', 'in'}
    
    banned_phrases = {'portfolio', 'portfolios', 'quotes', 'reviews', 'credentials', 'consultation', 
                      'consultations', 'proposal', 'proposals', 'experience', 'strengths', 'weaknesses', 
                      'pricing', 'budget', 'factors', 'questions', 'options', 'services', 'process'}
    
    words = s.split()
    
    if not words:
        return False
    
    first_word = words[0].lower()
    if first_word in stoplist:
        return False
    
    s_lower = s.lower()
    for phrase in banned_phrases:
        if phrase in s_lower:
            return False
    
    for word in words:
        word_lower = word.lower()
        if word_lower not in allowed_lowercase and not word[0].isupper() and not word.isdigit() and word not in ('&', '+'):
            return False
    
    return True

def url_to_host(url):
    """Extract host from URL, strip www and scheme."""
    skip_domains = {'reddit.com', 'yelp.com', 'facebook.com', 'youtube.com', 'google.com', 'clutch.co',
                    'designrush.com', 'expertise.com', 'angi.com', 'thumbtack.com', 'houzz.com', 'bbb.org',
                    'yellowpages.com', 'mapquest.com'}
    
    if not url or not ('://' in url or url.startswith('www.')):
        return None
    
    if not url.startswith('http'):
        url = 'http://' + url
    
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc or parsed.path.split('/')[0]
        if not host:
            return None
        host = host.replace('www.', '')
        if host in skip_domains:
            return None
        return host
    except:
        return None

def is_junk(b):
    """Filter out junk business names."""
    if not b or len(b) < 4 or len(b) > 60:
        return True
    if len(b.split()) < 2:
        return True
    junk_re = re.compile(r'^(https?://|www\.)|\b(city|county|state|phone|average|cost|serves|residents|required|department|know for|repairs|certified|specialty|questions|resources|local|the|and|only)\b', re.I)
    if junk_re.search(b):
        return True
    generic_re = re.compile(r'research|red flags|ask |my |key factors|ways to|services needed|larger|market analysis|popular|for content|for analytics|campaign|built-in|referrals|google reviews|better business|yelp|angi|thumbtack|houzz|bbb|nextdoor|google maps|chamber', re.I)
    if generic_re.search(b):
        return True
    return False

def check_banned(text):
    """Check for banned words in text."""
    banned = ['unlock', 'supercharge', 'revolutionary', 'game-changing', 'seamless', 'effortless', 'guarantee', 'ranking', 'honest']
    text_lower = text.lower()
    for w in banned:
        if re.search(r'\b' + re.escape(w) + r'\b', text_lower):
            return w
    return None

def read_dataset(csv_path, since_date='2026-09-01'):
    """Read CSV and yield (market, engine, date, target, niche, businesses_named, sha256)."""
    markets = {}
    with open(csv_path, encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['engine'] == 'agree':
                continue
            if row['date'] < since_date:
                continue
            if not row['market']:
                continue
            market = asc(strip_banned(row['market']))
            if market not in markets:
                markets[market] = {
                    'dates': set(),
                    'engines': collections.defaultdict(set),
                    'engine_answers': collections.defaultdict(int),
                    'engine_biz': collections.defaultdict(lambda: collections.defaultdict(set)),
                    'niches': set(),
                    'targets': set(),
                    'businesses': collections.defaultdict(set),
                    'niche_engine_biz': collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(set))),
                    'niche_targets': collections.defaultdict(set),
                    'sha256': row.get('sha256', ''),
                }
            m = markets[market]
            m['dates'].add(row['date'])
            m['engines'][row['engine']].add(row['date'])
            m['engine_answers'][row['engine']] += 1
            niche = asc(strip_banned(row['niche']))
            if niche:
                m['niches'].add(niche)
            target = asc(strip_banned(row['target']))
            if target:
                m['targets'].add(target)
                m['niche_targets'][niche].add(target)
            neb = m['niche_engine_biz'][niche][row['engine']]
            
            for b in (row['businesses_named'] or '').split(';'):
                b = b.strip()
                
                if row['engine'] == 'google_serp':
                    # For google_serp, extract host from URL
                    host = url_to_host(b)
                    if host and len(host) >= 4:
                        m['businesses'][host].add(row['date'])
                        m['engine_biz'][row['engine']][host].add(row['date'])
                        neb[host].add(row['date'])
                else:
                    # For other engines, apply junk filter and proper-noun gate
                    if is_junk(b):
                        continue
                    if not is_proper_noun(b):
                        continue
                    b = asc(strip_banned(b))
                    if b and len(b) >= 4:
                        m['businesses'][b].add(row['date'])
                        m['engine_biz'][row['engine']][b].add(row['date'])
                        neb[b].add(row['date'])
            
            if row.get('sha256'):
                m['sha256'] = row['sha256']
    
    return markets

def format_table(headers, rows):
    """Format a markdown table."""
    if not rows:
        return ""
    
    lines = []
    lines.append('| ' + ' | '.join(headers) + ' |')
    lines.append('|' + '|'.join([' --- ' for _ in headers]) + '|')
    for row in rows:
        lines.append('| ' + ' | '.join(asc(str(x)) for x in row) + ' |')
    
    return '\n'.join(lines)

TOP_N = 10

def top_with_ties(biz_days, n=None):
    """Top n by days named, plus every row tied with the nth (a tie is never split silently).
    Returns (rows, count_not_shown)."""
    n = n or TOP_N
    ranked = sorted(biz_days.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    if len(ranked) > n:
        cut = len(ranked[n - 1][1])
        shown = [kv for kv in ranked if len(kv[1]) >= cut]
    else:
        shown = ranked
    rows = [[b, len(d), sorted(d)[0], sorted(d)[-1]] for b, d in shown]
    return rows, len(ranked) - len(shown)

def generate_panel_md(market, market_data, csv_path):
    """Generate markdown panel for a single market."""
    dates = sorted(market_data['dates'])
    first_date = dates[0]
    last_date = dates[-1]
    run_days = len(dates)
    
    sha256 = market_data['sha256'][:16] if market_data['sha256'] else 'unknown'
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())
    
    lines = []
    lines.append(f'# Market Panel: {asc(market)}')
    lines.append('')
    lines.append(f'Dated record, {first_date} to {last_date}, {run_days} run days, generated {timestamp} from Namebeam_E1_Dataset_LATEST.csv ({sha256})')
    lines.append('')
    
    # ENGINE COVERAGE table
    lines.append('## Engine Coverage')
    lines.append('')
    engine_rows = []
    for engine in sorted(market_data['engines'].keys()):
        engine_days = len(market_data['engines'][engine])
        answers = market_data['engine_answers'].get(engine, 0)
        distinct_biz = len(market_data['engine_biz'][engine])
        engine_rows.append([engine, engine_days, answers, distinct_biz])
    
    lines.append(format_table(['Engine', 'Run Days', 'Answers', 'Distinct Businesses'], engine_rows))
    lines.append('')
    
    # BUYER QUESTIONS table
    lines.append('## Buyer Questions')
    lines.append('')
    niche_rows = []
    for niche in sorted(market_data['niches']):
        if not niche:
            continue
        questions = len(market_data['niche_targets'].get(niche, set()))
        niche_rows.append([niche, questions, run_days])
    
    lines.append(format_table(['Niche', 'Questions', 'Run Days'], niche_rows))
    lines.append('')
    
    # TOP NAMED per engine. v1 prints only the two engines whose names are business-grade in the record:
    # Perplexity (names plus cited sources) and Google SERP (cited domains). The anthropic, openai and gemini
    # name fields are proper-noun harvests that still carry non-business phrases (COS audit 2026-09-29), so
    # their tables are withheld until the extractor passes a proper-noun audit; their counts stay in the manifest.
    SHOW_TOP = {'perplexity', 'google_serp'}
    withheld = [e for e in sorted(market_data['engines'].keys()) if e not in SHOW_TOP]
    for niche in sorted(n for n in market_data['niches'] if n):
        for engine in sorted(market_data['engines'].keys()):
            if engine not in SHOW_TOP:
                continue
            label = 'Top Cited Domains: Google SERP' if engine == 'google_serp' else f'Top Named: {engine.capitalize()}'
            lines.append(f'## {niche}: {label}')
            lines.append('')
            top_rows, more = top_with_ties(market_data['niche_engine_biz'][niche][engine])
            if top_rows:
                lines.append(format_table(['Business', 'Days Named', 'First Named', 'Last Named'], top_rows))
                if more:
                    lines.append('')
                    lines.append(f'{more} more named on fewer days; every row is in the dated record.')
            else:
                lines.append('(No businesses named)')
            lines.append('')
    if withheld:
        lines.append('## Withheld In This Edition')
        lines.append('')
        lines.append('Named-business tables for ' + ', '.join(withheld) + ' are withheld: the record holds their answers and dates (see Engine Coverage), but name extraction for these engines is not yet business-grade, so no table is printed until it is.')
        lines.append('')
    # METHOD section
    lines.append('## Method')
    lines.append('')
    lines.append('Same buyer questions asked to each engine once a day, every answer stored with its date and file hash. Counts computed from the record; nothing estimated.')
    lines.append('')
    
    # Footer
    lines.append('---')
    lines.append('')
    lines.append('Namebeam, Carter Enterprise LLC, 30 N Gould St, Ste 65270, Sheridan, WY 82801. Full record and method: https://proof.namebeam.ai')
    
    md = '\n'.join(lines)
    
    # Validate ASCII and banned words
    non_ascii = [c for c in md if ord(c) > 127]
    if non_ascii:
        raise ValueError(f"Non-ASCII characters in output: {non_ascii[:5]}")
    
    banned = check_banned(md)
    if banned:
        raise ValueError(f"Banned word '{banned}' in output")
    
    return md

def run_selftest():
    """Run selftest mode."""
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_file = os.path.join(tmpdir, 'test.csv')
        
        with open(csv_file, 'w') as f:
            f.write('date,kind,target,niche,market,engine,businesses_named_count,businesses_named,first_named,source_file,sha256\n')
            # Test proper-noun gate: advice phrase should be dropped
            f.write('2026-09-01,question,Where are good plumbers?,plumbing,Springfield OR,perplexity,2,ABC Plumbing Inc;Get multiple quotes,2026-09-01,test.csv,abc123def456\n')
            # Test URL-to-host: URL should become host, reddit should be skipped
            f.write('2026-09-01,question,Where are good plumbers?,plumbing,Springfield OR,google_serp,2,https://reddit.com/r/plumbing;https://example.com/plumbers,2026-09-01,test.csv,abc123def456\n')
            f.write('2026-09-01,question,Where are good plumbers?,plumbing,Springfield OR,anthropic,1,XYZ Pipes Co,2026-09-01,test.csv,abc123def456\n')
            f.write('2026-09-02,question,Where are good plumbers?,plumbing,Springfield OR,perplexity,1,Quality Plumbing LLC,2026-09-02,test.csv,abc123def456\n')
        
        markets = read_dataset(csv_file, since_date='2026-09-01')
        
        assert 'Springfield OR' in markets, "Market not found"
        m = markets['Springfield OR']
        
        perplexity_biz = list(m['engine_biz']['perplexity'].keys())
        assert 'Get multiple quotes' not in perplexity_biz, f"Advice phrase should be dropped; got {perplexity_biz}"
        assert 'ABC Plumbing Inc' in perplexity_biz, f"Proper noun should be kept; got {perplexity_biz}"
        
        google_serp_biz = list(m['engine_biz']['google_serp'].keys())
        assert 'reddit.com' not in google_serp_biz, f"Reddit domain should be skipped; got {google_serp_biz}"
        assert 'example.com' in google_serp_biz, f"example.com should be kept; got {google_serp_biz}"
        
        # REGRESSION 2026-10-03 (Tobin omission): a tie at the 10th row must not be split, and
        # one niche's domains must not crowd another niche out of its table.
        days_a = {f'a{i:02d}.com': {f'2026-09-{d:02d}' for d in range(1, 8)} for i in range(9)}
        days_a.update({f't{i}.com': {'2026-09-01', '2026-09-02'} for i in range(3)})
        days_a['zzz-tied.com'] = {'2026-09-01', '2026-09-02'}
        rows, more = top_with_ties(days_a)
        names = [r[0] for r in rows]
        assert 'zzz-tied.com' in names, f"tie at the cutoff was split: {names}"
        assert more == 0, more
        with open(csv_file, 'a') as f:
            f.write('2026-09-03,question,Who is best?,hvac,Springfield OR,google_serp,1,https://hvac-one.com/,2026-09-03,test.csv,abc\n')
            f.write('2026-09-03,question,Who handles injuries?,injury law,Springfield OR,google_serp,1,https://www.injury-firm.com/,2026-09-03,test.csv,abc\n')
        m = read_dataset(csv_file, since_date='2026-09-01')['Springfield OR']
        assert 'injury-firm.com' in m['niche_engine_biz']['injury law']['google_serp'], 'niche table missing its own domain'
        assert 'injury-firm.com' not in m['niche_engine_biz']['hvac']['google_serp'], 'niche tables bleed'
        md2 = generate_panel_md('Springfield OR', m, csv_file)
        assert '## injury law: Top Cited Domains: Google SERP' in md2 and 'injury-firm.com' in md2, 'per-niche table not printed'
        assert '| injury law | 1 |' in md2 and '| hvac | 1 |' in md2, 'per-niche question count wrong'

        panels_dir = os.path.join(tmpdir, 'panels')
        os.makedirs(panels_dir, exist_ok=True)
        
        md = generate_panel_md('Springfield OR', m, csv_file)
        
        non_ascii = [c for c in md if ord(c) > 127]
        assert not non_ascii, f"Non-ASCII in output: {non_ascii}"
        
        banned = check_banned(md)
        assert not banned, f"Banned word '{banned}' in output"
        
        print('SELFTEST GREEN')
        return 0
    
    print('SELFTEST RED')
    return 1

def main():
    if '--selftest' in sys.argv:
        sys.exit(run_selftest())
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(script_dir, '../../../corpus/namebeam/Namebeam_E1_Dataset_LATEST.csv')
    panels_dir = os.path.join(script_dir, 'panels')
    
    if not os.path.isabs(csv_path):
        csv_path = os.path.abspath(csv_path)
    
    os.makedirs(panels_dir, exist_ok=True)
    
    start_time = time.time()
    markets = read_dataset(csv_path, since_date='2026-09-01')
    
    index_rows = []
    manifest = {}
    
    for market in sorted(markets.keys()):
        market_data = markets[market]
        
        md = generate_panel_md(market, market_data, csv_path)
        
        slug = slugify(market)
        panel_path = os.path.join(panels_dir, f'{slug}.md')
        
        with open(panel_path, 'w', encoding='utf-8') as f:
            f.write(md)
        
        run_days = len(market_data['dates'])
        questions = len(market_data['targets'])
        distinct_businesses = len(market_data['businesses'])
        
        index_rows.append([
            market,
            run_days,
            questions,
            distinct_businesses,
            f'{slug}.md'
        ])
        
        manifest[market] = {
            'run_days': run_days,
            'questions': questions,
            'distinct_businesses': distinct_businesses,
            'engines': len(market_data['engines']),
            'first_date': min(market_data['dates']),
            'last_date': max(market_data['dates'])
        }
    
    # Write INDEX.md
    index_path = os.path.join(panels_dir, 'INDEX.md')
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write('# Namebeam Market Panels\n\n')
        f.write(format_table(['Market', 'Run Days', 'Questions', 'Distinct Businesses', 'Panel'], index_rows))
        f.write('\n\n')
        
        if index_rows:
            total_run_days = sum(r[1] for r in index_rows)
            total_questions = sum(r[2] for r in index_rows)
            total_businesses = sum(r[3] for r in index_rows)
            f.write(f'**Total**: {len(index_rows)} markets, {total_run_days} total run days, {total_questions} total questions, {total_businesses} total distinct businesses\n')
    
    # Write PANELS_MANIFEST.json
    manifest_path = os.path.join(panels_dir, 'PANELS_MANIFEST.json')
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)
    
    elapsed = time.time() - start_time
    
    print(f'Generated {len(markets)} market panels in {elapsed:.2f}s')
    print(f'Panels written to {panels_dir}')

if __name__ == '__main__':
    main()
