"""
صيانة شهرية لبيانات «راديو العالم».
يسحب كل المحطات الشغالة من Radio Browser، ويبني ملفات data/ من جديد:
  arabic.json · professional.json · countries.json · languages.json · world/XX.json · sections/*.json
ويفحص روابط المحطات العربية فعليًا، ويستبعد إسرائيل واللغة العبرية،
ثم يحدّث رقم نسخة البيانات DATA_V في index.html عشان المتصفحات تجيب النسخة الجديدة.

التشغيل:  python scripts/refresh_data.py            (من الإنترنت)
          python scripts/refresh_data.py --input dump.json --no-check   (تجربة محلية)
"""
import json, re, os, sys, glob, time, collections, datetime, urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from names import AR, LANG  # noqa: E402

DATA = os.path.join(ROOT, 'data')
UA = 'RadioAlAlam/1.0 (ashrafabdelrazek.github.io/radio)'
SERVERS = ['https://de1.api.radio-browser.info', 'https://de2.api.radio-browser.info',
           'https://nl1.api.radio-browser.info', 'https://at1.api.radio-browser.info',
           'https://fi1.api.radio-browser.info']
ARAB = set(list(AR)[:22])
EXCLUDE_CC = {'IL'}
EXCLUDE_LANG = re.compile(r'hebrew', re.I)


def get(url, timeout=60):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def fetch_all():
    for srv in SERVERS:
        try:
            allst, off, L = [], 0, 10000
            while True:
                a = get(f'{srv}/json/stations?hidebroken=true&limit={L}&offset={off}&order=stationuuid', 120)
                allst += a
                off += L
                if len(a) < L:
                    break
            if len(allst) > 20000:
                print('fetched', len(allst), 'from', srv)
                return allst
        except Exception as e:
            print('server failed', srv, e)
    raise SystemExit('كل سيرفرات Radio Browser فشلت — مفيش تحديث المرة دي')


def alive(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA, 'Range': 'bytes=0-1', 'Icy-MetaData': '0'})
        with urllib.request.urlopen(req, timeout=8) as r:
            r.read(1)
            return r.status < 400
    except Exception:
        return False


def check(s):
    u = s['url']
    if u.startswith('https') and alive(u):
        return 'ok', u
    if u.startswith('http:'):
        hu = 'https://' + u[7:]
        if alive(hu):
            return 'ok_https', hu
    return 'unverified', u


def slim(s):
    return {'id': s['stationuuid'], 'name': re.sub(r'\s+', ' ', s['name']).strip(), 'url': s['url_resolved'],
            'home': s.get('homepage', ''), 'logo': s.get('favicon', ''), 'tags': s.get('tags', ''), 'cc': s['countrycode'],
            'country_ar': AR.get(s['countrycode'], s.get('country', '')), 'lang': s.get('language', ''),
            'votes': s.get('votes', 0), 'clicks': s.get('clickcount', 0), 'codec': s.get('codec', ''),
            'bitrate': s.get('bitrate', 0), 'https': s['url_resolved'].startswith('https')}


def cat(s):
    t = (s.get('tags') or '').lower() + ' ' + s['name'].lower()
    if re.search(r'quran|qur|koran|islam|relig|reciter|afasi|afasy|sudais|minshawi|husary|قرآن|قران|تلاوة|dua|hadith|اذاعة القر|إذاعة القر', t): return 'قرآن وديني'
    if re.search(r'news|اخبار|أخبار|talk|politic|bbc|sawa|monte carlo|mc doualiya|الحوار', t): return 'أخبار وحوار'
    if re.search(r'classic|tarab|طرب|oldies|umm kulthum|ام كلثوم|fairouz|فيروز|zaman|زمان|nostalg|اغاني زمان|أغاني زمان', t): return 'طرب وكلاسيكيات'
    if re.search(r'kids|children|اطفال|أطفال', t): return 'أطفال'
    if re.search(r'sport|رياض|football|كورة|كوره', t): return 'رياضة'
    if re.search(r'educat|learn|تعليم|university|جامعة|english', t): return 'تعليمي'
    return 'عام ومنوعات'


SEC = {
    'learn':   r'educat|universit|lecture|science|knowledge|school|campus|college|academ|تعليم|جامع|ثقاف|معرف|podcast',
    'lang':    r'learn(ing)? (english|arabic|french|spanish|german)|language learn|esl|langue|idioma|تعلم (الانجليزية|اللغة|الإنجليزية|لغة)|slow news|easy english|bbc learning',
    'kids':    r'\bkids?\b|children|child|family|kinder|enfants|niñ|bambini|أطفال|اطفال|عائل|اسرة|أسرة|nursery|lullab',
    'zaman':   r'oldies|\b[5-7]0s\b|nostalg|retro|golden|vintage|swing|big band|crooner|tarab|طرب|زمان|classic arabic|أم كلثوم|ام كلثوم|عبد الحليم|فيروز|old songs|evergreen',
    'drama':   r'drama|theat(re|er)|stor(y|ies)|storytell|audiobook|audio book|book|مسرح|قص(ص|ة)|حكاي|old time radio|\botr\b|radio play|hörspiel|comedy',
    'spirit':  r"quran|qur'?an|koran|islam|relig|christian|gospel|church|bible|قرآن|قران|اسلام|إسلام|اذكار|أذكار|دعاء|تلاوة|reciter|hadith|sermon|spiritual",
    'biz':     r'\bnews\b|business|financ|econom|market|talk|politic|أخبار|اخبار|اقتصاد|أعمال|current affairs|public radio',
    'sport':   r'sport|football|soccer|basketball|رياض|كورة|كوره|espn|match',
    'relax':   r'relax|chill|ambient|sleep|meditat|nature|rain|lofi|lo-fi|calm|spa|yoga|zen|هدوء|استرخاء|new age|binaural',
    'classic': r'classical|piano|instrumental|opera|symphon|baroque|orchestr|chamber|كلاسيك|focus|study',
    'health':  r'health|wellness|medical|doctor|fitness|lifestyle|صحة|طب|cooking|food',
}
SEC_META = {'learn': ('🎓', 'تعليم وثقافة'), 'lang': ('🗣', 'تعلّم لغات'), 'kids': ('🧒', 'أطفال وأسرة'), 'zaman': ('🕰', 'زمن الفن الجميل'),
            'drama': ('🎭', 'أرشيف ومسرح وقصص'), 'spirit': ('🕌', 'روحانيات'), 'biz': ('💼', 'أعمال وأخبار'), 'sport': ('⚽', 'رياضة'),
            'relax': ('🌊', 'استرخاء ونوم'), 'classic': ('🎼', 'كلاسيك وتركيز'), 'health': ('🩺', 'صحة وحياة')}
LANGS = {'arabic': 'عربي', 'english': 'إنجليزي', 'french': 'فرنسي', 'spanish': 'إسباني', 'german': 'ألماني', 'turkish': 'تركي', 'urdu': 'أوردو',
         'italian': 'إيطالي', 'russian': 'روسي', 'persian': 'فارسي', 'farsi': 'فارسي', 'hindi': 'هندي', 'indonesian': 'إندونيسي', 'malay': 'ماليزي'}


def lang_key(s):
    l = (s.get('language') or '').lower()
    for k in LANGS:
        if k in l:
            return k
    return ''


def dump(path, obj, indent=None):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)


def main():
    args = sys.argv[1:]
    if '--input' in args:
        d = json.load(open(args[args.index('--input') + 1], encoding='utf-8'))
    else:
        d = fetch_all()
    do_check = '--no-check' not in args

    # تنظيف: شغال فقط + استبعاد إسرائيل والعبرية + حذف التكرار بالرابط (الأعلى تصويتًا يكسب)
    d = [s for s in d if s.get('lastcheckok', 1) == 1 and s.get('url_resolved')
         and s.get('countrycode') not in EXCLUDE_CC and not EXCLUDE_LANG.search(s.get('language') or '')]
    seen, D = set(), []
    for s in sorted(d, key=lambda x: -(x.get('votes') or 0)):
        u = s['url_resolved'].strip().rstrip('/')
        if u and u not in seen:
            seen.add(u)
            D.append(s)
    if len(D) < 15000:
        raise SystemExit(f'عدد المحطات قليل بشكل مريب ({len(D)}) — مش هنكتب فوق البيانات الحالية')
    print('stations after cleanup:', len(D))

    # 1) العربي + فحص الروابط
    def is_arabic(s):
        lang = (s.get('language') or '').lower()
        if 'arab' in lang: return True
        if s['countrycode'] == 'AE': return False
        return s['countrycode'] in ARAB
    ar = [slim(s) for s in D if is_arabic(s)]
    for s in ar:
        s['cat'] = cat(s)
    if do_check:
        with ThreadPoolExecutor(32) as ex:
            res = list(ex.map(check, ar))
        for s, (st, u) in zip(ar, res):
            s['status'] = st
            if st == 'ok_https':
                s['url'] = u
                s['https'] = True
    else:
        for s in ar:
            s['status'] = 'ok' if s['https'] else 'unverified'
    ar.sort(key=lambda s: ({'ok': 0, 'ok_https': 0, 'unverified': 2}.get(s['status'], 1), -s['votes']))
    dump(os.path.join(DATA, 'arabic.json'), ar, 0)
    print('arabic', len(ar), collections.Counter(s['status'] for s in ar))

    # 2) المهني
    pro_re = re.compile(r'\b(news|business|finance|economy|economics|talk|education|science|university|lecture|technology|npr|bbc world|bloomberg|cnbc|monocle|financial)\b')
    pro = [slim(s) for s in D if pro_re.search((s.get('tags') or '').lower() + ' ' + s['name'].lower()) and (s.get('votes') or 0) >= 50 and s['url_resolved'].startswith('https')]
    pro.sort(key=lambda x: -x['votes'])
    dump(os.path.join(DATA, 'professional.json'), pro[:600], 0)

    # 3) الدول واللغات
    https = [s for s in D if s['url_resolved'].startswith('https') and s.get('countrycode')]
    cnt = collections.Counter(s['countrycode'] for s in https)
    en = {}
    for s in D:
        en.setdefault(s['countrycode'], s.get('country', ''))
    dump(os.path.join(DATA, 'countries.json'), [{'cc': c, 'ar': AR.get(c, ''), 'en': en.get(c, c), 'n': n} for c, n in cnt.most_common()], 0)
    lc = collections.Counter()
    for s in D:
        for l in (s.get('language') or '').lower().split(','):
            l = l.strip()
            if l and not EXCLUDE_LANG.search(l):
                lc[l] += 1
    dump(os.path.join(DATA, 'languages.json'), [{'code': l, 'ar': LANG.get(l, ''), 'n': n} for l, n in lc.most_common(150)], 0)

    # 4) ملف لكل دولة
    wdir = os.path.join(DATA, 'world')
    os.makedirs(wdir, exist_ok=True)
    for f in glob.glob(os.path.join(wdir, '*.json')):
        os.remove(f)
    world = {}
    for s in https:
        lst = world.setdefault(s['countrycode'], [])
        if len(lst) < 100:
            lst.append(slim(s))
    for c, l in world.items():
        dump(os.path.join(wdir, f'{c}.json'), l)

    # 5) الأقسام الموضوعية
    sdir = os.path.join(DATA, 'sections')
    os.makedirs(sdir, exist_ok=True)
    out = {k: [] for k in SEC}
    for s in D:
        u = s['url_resolved'].strip()
        if not u.startswith('https'):
            continue
        t = ((s.get('tags') or '') + ' | ' + (s.get('name') or '')).lower()
        for k, rx in SEC.items():
            if len(out[k]) < 300 and re.search(rx, t):
                out[k].append({'id': s['stationuuid'], 'name': re.sub(r'\s+', ' ', s['name']).strip(), 'url': u, 'logo': s.get('favicon', ''),
                               'tags': (s.get('tags') or '')[:60], 'cc': s['countrycode'], 'country_ar': AR.get(s['countrycode'], s.get('country', '')),
                               'lang': lang_key(s), 'votes': s.get('votes', 0), 'bitrate': s.get('bitrate', 0)})
    idx = []
    for k, arr in out.items():
        arr.sort(key=lambda s: (0 if s['lang'] == 'arabic' or s['cc'] in ARAB else 1, -s['votes']))
        dump(os.path.join(sdir, f'{k}.json'), arr)
        langs = collections.Counter(s['lang'] for s in arr if s['lang'])
        ic, name = SEC_META[k]
        idx.append({'key': k, 'ic': ic, 'name': name, 'n': len(arr), 'ar': sum(s['lang'] == 'arabic' for s in arr),
                    'langs': [[l, LANGS[l], n] for l, n in langs.most_common(6)]})
    dump(os.path.join(sdir, 'index.json'), idx)

    # 6) رقم نسخة جديد للبيانات عشان المتصفحات تتجاوز الكاش
    ver = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d')
    p = os.path.join(ROOT, 'index.html')
    h = open(p, encoding='utf-8').read()
    h2 = re.sub(r"const DATA_V='[^']*'", f"const DATA_V='{ver}'", h, count=1)
    if h2 != h:
        open(p, 'w', encoding='utf-8').write(h2)
    print('done · DATA_V =', ver)


if __name__ == '__main__':
    main()
