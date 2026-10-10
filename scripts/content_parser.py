"""Extract source content from the host templates observed in the pilot.

No network calls. Unknown or incomplete pages stay in the review queue.
"""
import argparse
import gzip
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup, NavigableString, Tag

VERSION = 'pilot-v1'

# Selectors are ordered fallbacks, except for the explicit multi-region cases below.
RULES = {}

def register(hosts, selectors):
    for host in hosts.split():
        RULES[host] = list(selectors)

register('suckhoecongdongonline.vn nhandan.vn baogialai.com.vn tienphong.vn baoquangninh.vn www.sggp.org.vn www.vietnamplus.vn', ['.article__body'])
register('suckhoedoisong.vn thanhnien.vn tuoitre.vn baochinhphu.vn', ['.detail-content'])
register('baohaiphong.vn baodanang.vn hanoimoi.vn', ['.entry'])
register('baonghean.vn khoahocphothong.vn', ['article.entry'])
register('phuyen.baodaklak.vn baoquangtri.vn baovinhlong.com.vn', ['.td-post-content'])
register('baolangson.vn baotayninh.vn', ['#content-detail'])
register('benhviennhitrunguong.gov.vn special.vietnamplus.vn', ['.entry-content'])
register('baby.39.net care.39.net fitness.39.net woman.39.net food.39.net fk.39.net cancer.39.net nk.39.net heart.39.net man.39.net gan.39.net tnb.39.net wei.39.net shen.39.net ek.39.net gk.39.net gc.39.net pf.39.net sj.39.net byby.39.net', ['.art_content', '#contentText.art_con'])
register('www.a-hospital.com zhongyibaodian.net', ['#bodyContent'])
RULES.update({
    'www.cnkang.com': ['#endText', '.detailc', '.zh05b'],
    'www.zydcd.com': ['div.info_txt'],
    'www.wujue.com': ['.art-text', 'td#text', '#article-main_left #art-leftbox', 'td:has(img.photoresize)'],
    'www.youlai.cn': ['[class*="textContent--"]'],
    'phunusuckhoe.giadinhonline.vn': ['#article-body'],
    'www.vinmec.com': ['#main-article'],
    'medlatec.vn': ['.block-posts-single .description', '.infrastructure-description'],
    'qihuangzhishu.com': ['.cbody'],
    'giadinhonline.vn': ['#explus-editor'],
    'vietnamnet.vn': ['#maincontent', '.maincontent.content-full-image'],
    'test.pmphai.com': ['#content.gu_det_left_con'],
    'tiemchunglongchau.com.vn': ['#content-article', 'section[aria-label="Quiz Details Body"]'],
    'baocantho.com.vn': ['#newscontents .content-fck'],
    'hellobacsi.com': ['.article-content-item.body-content'],
    'baoangiang.com.vn': ['#newscontents .content-fck'],
    'vov.vn': ['.text-long', '.article-content'],
    'jb39.com': ['.post-jibing'],
    'youmed.vn': ['section.article-content article:not([class]) > div.prose'],
    'vov2.vov.vn': ['.article-body'],
    'www.msdmanuals.cn': ['[class*="TopicMainContent_content"]', '[class*="Chapter_content"]'],
    'suckhoeviet.org.vn': ['.__MASTERCMS_CONTENT'],
    'tamanhhospital.vn': ['.post_info.box_detail_post:has(p)'],
    'www.qdnd.vn': ['.post-content'],
    'thaythuocvietnam.vn': ['.content-post.clearfix'],
    'bachmai.gov.vn': ['.content-editor-post'],
    'www.baidu.com': ['.health-dict__overview'],
    'dantri.com.vn': ['#desktop-in-article', '.e-magazine__body', '.video-container'],
    'vnexpress.net': ['article.fck_detail'],
    'health.people.com.cn': ['#rm_txt_zw', '.artDet', '.articleCont'],
    'baophutho.vn': ['#dcontent'],
    'baothanhhoa.vn': ['#dcontent', '.article__body'],
    'www.pharmacity.vn': ['#pmc-content-blog-detail'],
    'www.familydoctor.cn': ['.article-content'],
    'mega.vietnamplus.vn': ['.entry.production.longform'],
    'fk.99.com.cn': ['.wrap-left .content'],
})
RULES['nhandan.vn'].append('.Theme-Layer-BodyText--inner')

SUMMARY_SELECTORS = '.article__sapo, .detail-sapo, .detail__sapo, .post-summary, .sc-longform-header-sapo, .content-detail-sapo, .article-summary, .art_summary'
SUMMARY_HOST = {
    'giadinhonline.vn': 'h2.intro', 'medlatec.vn': '.block-posts-single .shortdescription',
    'dantri.com.vn': '[data-slot="sapo"]', 'fk.99.com.cn': '.introduct',
    'www.familydoctor.cn': 'article.article .content > .summary',
}

# Only identified UI containers; ordinary links, lists, tables and references stay.
COMMON_REMOVE = 'script, style, button, form, .ez-toc-container, #ez-toc-container, #toc, #toc-container, .sharethis-inline-share-buttons, .bdsharebuttonbox, .related-news, article.story, [type="RelatedNewsBox"], [type="RelatedOneNews"], .readmore-body-box, .article__meta, .article__source, .article__tag'
HOST_REMOVE = {
    'www.a-hospital.com': '#jump-to-nav, table.nav, table.navbox, table.msg-table, #catlinks, #fromlink, .editsection',
    'zhongyibaodian.net': '#jump-to-nav, table.nav, table.navbox, table.msg-table, #catlinks, #fromlink, .editsection',
    'www.cnkang.com': '#jkpl',
    'jb39.com': '.jb-daohang, .g160, .zz-more, .post-bm-ad, .r2, .jb-xx-jb, [class*="jb-search-"], ul.ul-ss-2.h2-ss, .jb-page, .jb-dir-page',
    'thaythuocvietnam.vn': '.post-views',
    'qihuangzhishu.com': '.book-page, .book-page2, .fhbsml',
    'fk.99.com.cn': 'ul.cont-text, .cont-tag',
    'baothanhhoa.vn': '.drelated',
    'vietnamnet.vn': '.inner-article',
    'tamanhhospital.vn': '.ggnews_wrap, .div_over.bg_xam',
    'www.baidu.com': '.health-dict__overview__navs, .health-dict__overview__extend, .health-dict__image-viewer__wrapper',
    'special.vietnamplus.vn': '.sharedaddy, #jp-relatedposts, .wp-block-buttons',
}
BLOCK_TAGS = {'p', 'div', 'section', 'article', 'li', 'ul', 'ol', 'blockquote', 'figure', 'figcaption', 'dl', 'dt', 'dd', 'tr', 'td', 'th'}

def text(node):
    return re.sub(r'\s+', ' ', node.get_text(' ', strip=True) if isinstance(node, Tag) else str(node)).replace('\x00', '').strip()

def decode(raw, content_type=''):
    probe = raw[:8192].decode('ascii', errors='ignore')
    charset = re.search(r'charset\s*=\s*["\x27]?([\w-]+)', probe, re.I)
    header = re.search(r'charset=([\w-]+)', content_type, re.I)
    preferred = charset.group(1) if charset else header.group(1) if header else 'utf-8'
    if preferred.lower() in {'gbk', 'gb2312', 'gb-2312'}:
        preferred = 'gb18030'
    for encoding in dict.fromkeys([preferred, 'utf-8', 'gb18030']):
        try:
            return raw.decode(encoding), encoding, [] if encoding == preferred else ['encoding_fallback']
        except (UnicodeDecodeError, LookupError):
            pass
    return raw.decode('utf-8', errors='replace'), 'utf-8', ['encoding_replacement']

def top_nodes(nodes):
    chosen = {id(n) for n in nodes}
    return [n for n in nodes if not any(id(p) in chosen for p in n.parents)]

def title_of(soup, host):
    selectors = {
        'www.120ask.com': '#d_askH1', 'ask.39.net': '.ask_tit',
        'www.a-hospital.com': '#firstHeading', 'zhongyibaodian.net': '#firstHeading',
        'test.pmphai.com': '.gu_det_left_top',
        'www.baidu.com': '.health-dict__disease__header__info__title',
    }
    if host in selectors and (n := soup.select_one(selectors[host])):
        return text(n)
    for selector in ['meta[property="og:title"]', 'meta[name="twitter:title"]']:
        if (n := soup.select_one(selector)) and n.get('content'):
            return n['content'].strip()
    n = soup.select_one('h1') or soup.title
    return text(n) if n else ''

def dom_path(node):
    if isinstance(node, Tag) and node.get('_source_path'):
        return node['_source_path']
    parts = []
    while isinstance(node, Tag) and node.name != '[document]':
        part = node.name
        if node.get('id'):
            parts.append(part + '#' + node['id'])
            break
        index = 1 + sum(1 for s in node.previous_siblings if isinstance(s, Tag) and s.name == node.name)
        parts.append(f'{part}:nth-of-type({index})')
        node = node.parent
    return ' > '.join(reversed(parts))

def blocks_from(root, role):
    """Walk every text node once. Nested lists/tables are not flattened twice."""
    blocks = []
    def emit(kind, value, node):
        value = text(value)
        if value:
            blocks.append({'kind': kind, 'role': role, 'text': value, 'source_path': dom_path(node)})
    def walk(node, kind='paragraph'):
        pending = []
        def flush():
            emit(kind, ''.join(pending), node)
            pending.clear()
        for child in node.children:
            if isinstance(child, NavigableString):
                # Comments are not document text.
                if child.__class__ is NavigableString:
                    pending.append(str(child))
            elif isinstance(child, Tag):
                if child.name in {'img', 'source', 'iframe'}:
                    continue
                if child.name == 'br':
                    flush()
                elif child.name == 'table':
                    flush()
                    caption = child.find('caption', recursive=False)
                    if caption:
                        emit('caption', text(caption), caption)
                    for row in child.find_all('tr'):
                        if row.find_parent('table') is child:
                            cells = row.find_all(['td', 'th'], recursive=False)
                            if any(c.find('table') for c in cells):
                                walk(row)
                            else:
                                emit('table_row', ' | '.join(text(c) for c in cells), row)
                elif re.fullmatch(r'h[1-6]', child.name) or set(child.get('class', [])) & {'gu_det_left_con_h1', 'pInterTitle', 'health-dict__overview__text__level1-tag__title', 'disease_title'}:
                    flush()
                    emit('heading', text(child), child)
                elif child.name in BLOCK_TAGS:
                    flush()
                    walk(child, 'list_item' if child.name == 'li' else 'caption' if child.name == 'figcaption' else 'paragraph')
                else:
                    # Inline tags remain in their original position; no artificial
                    # spaces are inserted inside Chinese words split by links.
                    pending.append(child.get_text('', strip=False))
        flush()
    walk(root)
    return blocks


def clean_region(root, host, role, result):
    def remove(node, reason):
        value = text(node)
        if value:
            result['removed'].append({'reason': reason, 'selector_path': dom_path(node), 'text': value})
        node.decompose()

    common = COMMON_REMOVE.replace(', article.story', '') if role == 'listing' else COMMON_REMOVE
    selectors = common + ', ' + HOST_REMOVE.get(host, '[data-parser-unused]')
    if role.startswith('answer_'):
        selectors += ', .b_anscont_more'
    for node in top_nodes(root.select(selectors)):
        remove(node, 'identified_ui')
    if host == 'qihuangzhishu.com':
        for node in list(root.select('.cbd > p')):
            if text(node).startswith(('1000余本中医古籍', '下载《', '下载所有中医书籍')):
                remove(node, 'book_download_navigation')
    if host == 'baonghean.vn':
        for node in list(root.select('table.full-img')):
            heading = node.find('th')
            if heading and text(heading) == 'TIN LIÊN QUAN':
                remove(node, 'related_articles_table')
    # A related-link label is removed only when the remaining text consists
    # entirely of links. A paragraph mentioning "相关阅读" in prose is retained.
    label_pattern = r'(?:[>»\s]*Xem thêm\s*:?[\s:]*|相关阅读(?:推荐)?[：:]?|【相关(?:文章|专题)】)'
    for node in list(root.select('p, h2, h3, blockquote, .gu_det_left_con_main')):
        if node.parent is None:
            continue
        if host.endswith('.39.net') and (text(node) in {'39健康网(www.39.net)专稿，未经书面授权请勿转载。', '未经作者允许授权，禁止转载', '（以上内容仅授权39健康网独家使用，未经版权方授权请勿转载。）'} or re.fullmatch(r'（责任编辑：[^）]+）', text(node))):
            remove(node, 'publisher_notice_or_editor_metadata')
            continue
        outside_links = ''.join(str(s) for s in node.find_all(string=True) if not s.find_parent('a')).strip()
        if re.fullmatch(label_pattern, outside_links, re.I):
            if node.select_one('a[href]'):
                remove(node, 'related_links_only')
            elif re.fullmatch(r'相关阅读(?:推荐)?[：:]?', text(node)) or text(node) == 'Xem thêm:':
                sibling = node.find_next_sibling()
                while sibling and sibling.name in {'a', 'p', 'ul', 'ol'} and (sibling.name == 'a' or sibling.select_one('a[href]')):
                    nonlink = '' if sibling.name == 'a' else ''.join(str(s) for s in sibling.find_all(string=True) if not s.find_parent('a')).strip()
                    if nonlink:
                        break
                    following = sibling.find_next_sibling()
                    remove(sibling, 'related_links_only')
                    sibling = following
                remove(node, 'related_links_label')
        elif host == 'www.vinmec.com' and text(node).startswith('Để đặt lịch khám tại viện, Quý khách') and node.select_one('a[href]'):
            remove(node, 'booking_call_to_action')
        elif host == 'suckhoedoisong.vn' and text(node) == 'Xem thêm video đang được quan tâm:':
            sibling = node.find_next_sibling()
            if sibling and sibling.get('type') == 'insertembedcode':
                remove(sibling, 'recommended_video')
            remove(node, 'recommended_video_label')
        elif host == 'test.pmphai.com' and text(node) == '此内容为收费内容':
            heading = node.find_previous_sibling()
            result['missing_sections'].append(text(heading) if heading else '')
            remove(node, 'paywall_placeholder')
    if host == 'ask.39.net' and role == 'question':
        main = root.select_one('.txt_ms')
        for repeated in list(root.select('.txt_bc')):
            span = repeated.select_one('span')
            same_question = main and span and text(main) == text(span)
            only_repeated_question = main and re.sub(r'\s+', '', text(repeated)) == '补充说明：' + re.sub(r'\s+', '', text(main))
            if same_question and only_repeated_question:
                remove(repeated, 'duplicate_question')
    for media in root.select('audio, video, iframe'):
        for string in list(media.find_all(string=True)):
            if text(string):
                result['removed'].append({'reason': 'browser_media_fallback', 'selector_path': dom_path(media), 'text': text(string)})
            string.extract()

def parse_page(record, raw):
    source, encoding, warnings = decode(raw, record.get('content_type', '')) if record['state'] == 'html_response' else ('', '', [])
    soup = BeautifulSoup(source, 'html.parser')
    host = record['host']
    result = {
        'doc_id': record['doc_id'], 'url': record['url'], 'host': host,
        'final_url': record.get('final_url'), 'raw_path': record.get('raw_path'),
        'source_sha256': record.get('sha256'),
        'parser_version': VERSION, 'encoding': encoding, 'title': title_of(soup, host),
        'status': 'needs_review', 'page_type': 'article', 'warnings': warnings,
        'blocks': [], 'answers': [], 'resources': [], 'removed': [],
        'selected_source_text': '', 'outside_headings': [], 'outside_paragraphs': [], 'selectors': [],
        'missing_sections': [], 'text': '',
        'source_links': [urljoin(record['url'], n['href']) for n in soup.select('.article__source a[href]')],
        'author_metadata': [],
    }
    if record['state'] != 'html_response':
        result['status'] = record['state']
        return result
    regions = []
    if host in {'www.120ask.com', 'ask.39.net'}:
        result['page_type'] = 'qa'
        question_selector = '#d_msCon' if host == 'www.120ask.com' else '.ask_hid'
        result['selectors'] = [question_selector]
        question = soup.select_one(question_selector)
        if question:
            regions.append((question, 'question'))
        else:
            warnings.append('question_missing')
        answer_selector = '.b_answerli:not(.zhenshiAD) .b_anscont_cont' if host == 'www.120ask.com' else '.selected .sele_all .sele_txt'
        result['selectors'].append(answer_selector)
        for index, answer in enumerate(top_nodes(soup.select(answer_selector))):
            regions.append((answer, f'answer_{index}'))
            box = answer.find_parent(class_='b_answerli' if host == 'www.120ask.com' else 'sele_all')
            doctor = box.select_one('.doc_name, .b_answertop, .b_doctor') if box else None
            timestamp = box.select_one('.doc_time, .b_anscont_time') if box else None
            date = text(timestamp) if timestamp else ''
            date_match = re.search(r'\d{4}-\d{2}-\d{2}[\s\d:]*', date)
            result['answers'].append({'index': index, 'doctor_metadata': text(doctor) if doctor else '', 'timestamp': date_match.group().strip() if date_match else date})
        if not result['answers']:
            warnings.append('no_answers_in_html')
    elif host == 'jbk.39.net':
        result['page_type'] = 'encyclopedia'
        if soup.select_one('.information_box'):
            result['selectors'] = ['.disease', '.disease_box:has(.information_box)']
            warnings.append('overview_only_linked_tabs_not_fetched')
        else:
            result['selectors'] = ['article.w740 .intro', 'article.w740 .catalogItem']
        regions = [(n, 'body') for n in top_nodes(soup.select(', '.join(result['selectors'])))]
    else:
        for selector in RULES.get(host, []):
            nodes = top_nodes(soup.select(selector))
            if host == 'www.wujue.com' and selector == '#article-main_left #art-leftbox':
                nodes = [n for n in nodes if text(n).startswith('图片简介')]
            if nodes and (any(text(n) for n in nodes) or any(n.select_one('img,iframe,audio,video') for n in nodes)):
                result['selectors'] = [selector]
                regions = [(n, 'body') for n in nodes]
                break
    if not regions:
        listing = soup.select_one('.timeline, .timeline.content-list') if host in {'nhandan.vn', 'baogialai.com.vn'} else None
        if listing:
            regions = [(listing, 'listing')]
            result['selectors'] = ['.timeline']
            result['page_type'] = 'listing'
            warnings.append('listing_not_single_article')
        elif urlsplit(record['url']).path in {'', '/'}:
            result['status'] = 'homepage'
            result['page_type'] = 'homepage'
            result['selected_source_text'] = text(soup.body or soup)
            return result
        else:
            result['status'] = 'unknown_template'
            result['selected_source_text'] = text(soup.body or soup)
            return result
    # Keep introductions outside the body, without taking summaries of sidebar cards.
    if result['page_type'] == 'article':
        body_nodes = {id(n) for root, _ in regions for n in [root, *root.descendants]}
        summaries = top_nodes(soup.select(SUMMARY_SELECTORS + ', ' + SUMMARY_HOST.get(host, '[data-parser-unused]')))
        summaries = [n for n in summaries if id(n) not in body_nodes and not n.find_parent('article', class_='story') and not n.find_parent(class_='related-news')]
        regions = [(n, 'summary') for n in summaries] + regions
    if host == 'tiemchunglongchau.com.vn' and soup.select_one('section[aria-label="Quiz Details Body"]'):
        result['page_type'] = 'quiz'
        warnings.append('quiz_questions_not_in_server_html')
    if host == 'www.vinmec.com' and soup.select_one('.box_quizz_detail'):
        result['page_type'] = 'quiz'
        warnings.append('quiz_questions_not_in_server_html')
    if host == 'tiemchunglongchau.com.vn':
        heading = next((h for h in soup.select('h2') if text(h) == 'NGUỒN THAM KHẢO'), None)
        references = heading.find_parent('section') if heading else None
        if references and not any(references is root or root in references.parents for root, _ in regions):
            regions.append((references, 'references'))
    if host == 'youmed.vn' and (references := soup.select_one('section.article-content details.sources')):
        regions.append((references, 'references'))
    if host == 'youmed.vn':
        article = soup.select_one('section.article-content article:not([class])')
        result['author_metadata'] = [text(n) for n in article.find_all(recursive=False) if text(n).startswith('Tác giả:')] if article else []
    if host == 'www.wujue.com' and ('/Photo/' in record['url'] or '/tuku/' in record['url']):
        result['page_type'] = 'gallery'
        warnings.append('image_content_not_transcribed')
    if host == 'thaythuocvietnam.vn' and 'Câu hỏi số 1' in result['title']:
        result['page_type'] = 'placeholder'
        warnings.append('placeholder_qa')
    result['selected_source_text'] = '\n\n'.join(text(n) for n, _ in regions)
    selected = {id(n) for root, _ in regions for n in [root, *root.descendants]}
    result['outside_headings'] = [{'text': text(n), 'path': dom_path(n)} for n in soup.select('h1,h2,h3') if id(n) not in selected and text(n)]
    result['outside_paragraphs'] = [{'text': text(n), 'path': dom_path(n)} for n in soup.select('p, blockquote') if id(n) not in selected and len(text(n)) >= 80 and not n.find_parent(['header', 'footer', 'nav', 'form']) and not n.find(['p', 'blockquote'])]
    if host in {'baophutho.vn', 'baothanhhoa.vn'} and any(re.fullmatch(r'[A-Za-z0-9+/=\s]{200,}', text(n)) for n, _ in regions):
        result['status'] = 'encoded_content'
        warnings.append('body_requires_decoder')
        return result
    if host == 'test.pmphai.com' and '此内容为收费内容' in result['selected_source_text']:
        warnings.append('paywalled_sections')
    if host == 'www.120ask.com' and '图片涉及用户隐私' in result['selected_source_text']:
        warnings.append('patient_image_unavailable')
    if host == 'jb39.com':
        warnings.append('section_previews_linked_details_not_fetched')
    if host == 'www.cnkang.com' and result['selectors'] == ['.zh05b']:
        result['page_type'] = 'listing'
        warnings.append('listing_description_only')
    if host == 'www.msdmanuals.cn' and 'Chapter_content' in result['selectors'][0]:
        result['page_type'] = 'listing'
        warnings.append('chapter_index')
    for root, role in regions:
        # Freeze source locations before cleanup changes sibling positions.
        for node in [root, *root.find_all()]:
            node['_source_path'] = dom_path(node)
        clean_region(root, host, role, result)
        # Resources in identified UI have already been removed.
        for node in root.select('img, audio, video, source, iframe, a[href]'):
            target = node.get('data-src') or node.get('data-original') or node.get('data-lazy-src') or node.get('src') or node.get('href')
            if not target or target.startswith(('data:', 'javascript:')):
                continue
            absolute = urljoin(record.get('final_url') or record['url'], target)
            embedded_pdf = parse_qs(urlsplit(absolute).query).get('url', [None])[0]
            if node.name == 'a' and role != 'references' and not urlsplit(absolute).path.lower().endswith('.pdf'):
                continue
            target_url = embedded_pdf or absolute
            kind = 'pdf' if urlsplit(target_url).path.lower().endswith('.pdf') else node.name
            if node.name == 'source' and node.find_parent(['audio', 'video']):
                kind = node.find_parent(['audio', 'video']).name
            result['resources'].append({'kind': kind, 'url': target_url, 'caption': node.get('alt', '')})
        blocks = blocks_from(root, role)
        # Detect accidental text loss in the DOM walker independently of cleanup.
        expected = re.sub(r'\s+', '', root.get_text()).replace('\x00', '')
        actual = re.sub(r'\s+', '', ''.join(b['text'] for b in blocks)).replace('|', '')
        if actual != expected.replace('|', ''):
            warnings.append('block_text_mismatch')
        result['blocks'].extend(blocks)
    for index, block in enumerate(result['blocks']):
        block['order'] = index
    result['text'] = '\n\n'.join(b['text'] for b in result['blocks'])
    if '[links()]' in result['text']:
        warnings.append('unrendered_publisher_marker')
    for answer in result['answers']:
        answer['text'] = '\n\n'.join(b['text'] for b in result['blocks'] if b['role'] == f"answer_{answer['index']}")
    if host == 'thaythuocvietnam.vn' and 'Câu hỏi số 1' in result['text'] and 'Câu trả lời từ chuyên gia' in result['text']:
        warnings.append('placeholder_qa')
    if not result['text']:
        warnings.append('media_only' if result['resources'] else 'empty_extraction')
    if result['resources'] and sum(len(b['text']) for b in result['blocks']) < 100 and any(r['kind'] == 'img' for r in result['resources']):
        warnings.append('image_content_not_transcribed')
    if any(r['kind'] == 'pdf' for r in result['resources']):
        warnings.append('pdf_not_parsed')
    if any(r['kind'] in {'audio', 'video'} for r in result['resources']):
        warnings.append('media_transcript_not_verified')
    if any(r['kind'] == 'iframe' for r in result['resources']):
        warnings.append('embedded_content_not_parsed')
    if host in {'www.a-hospital.com', 'zhongyibaodian.net'}:
        warnings.append('wiki_tail_requires_review')
    result['warnings'] = list(dict.fromkeys(warnings))
    result['status'] = 'needs_review' if warnings else 'extracted_unreviewed'
    return result


def write_review(documents, path):
    # Text only: original website scripts and active HTML are never embedded.
    data = json.dumps(documents, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    page = '''<!doctype html><meta charset="utf-8"><title>Parser review</title>
<style>body{font:15px system-ui;margin:24px}input,select{padding:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere}main{display:grid;grid-template-columns:1fr 1fr;gap:24px}section{border:1px solid #ddd;padding:16px}li{margin:8px 0}.warn{color:#a40}</style>
<h1>Đối chiếu parser pilot</h1><p>Các bản trích chưa được duyệt. Bên trái là vùng nguồn trước khi làm sạch; bên phải là kết quả. Xem heading ngoài vùng chọn để phát hiện nội dung bị bỏ sót.</p>
<input id="query" placeholder="Host, doc_id, trạng thái" size="45"><select id="docs"></select><div id="info"></div>
<main><section><h2>Vùng nguồn trước làm sạch</h2><pre id="source"></pre></section><section><h2>Nội dung trích xuất</h2><pre id="output"></pre></section></main>
<h2>Các vùng đã loại</h2><pre id="removed"></pre><h2>Heading ngoài vùng chọn — cần đối chiếu</h2><pre id="outside"></pre><h2>Đoạn dài ngoài vùng chọn — có thể là nội dung bị sót hoặc gợi ý</h2><pre id="paragraphs"></pre><h2>Ảnh / media / PDF thuộc vùng nội dung</h2><pre id="resources"></pre>
<script id="data" type="application/json">DATA</script><script>
const rows=JSON.parse(document.getElementById('data').textContent), sel=document.getElementById('docs');
function show(){const r=rows[Number(sel.value)];if(!r)return;document.getElementById('info').textContent=`${r.doc_id} | ${r.host} | ${r.status} | ${r.title} | ${r.url} | ${r.selectors.join(', ')} | ${r.warnings.join(', ')}`;
for(const [id,value] of Object.entries({source:r.selected_source_text,output:(r.blocks||[]).map(b=>`[${b.role}/${b.kind}] ${b.text}`).join('\\n\\n'),removed:r.removed,outside:r.outside_headings,paragraphs:r.outside_paragraphs||[],resources:r.resources}))document.getElementById(id).textContent=typeof value==='string'?value:JSON.stringify(value,null,2);}
function filter(){const q=document.getElementById('query').value.toLowerCase();sel.replaceChildren();rows.forEach((r,i)=>{if(`${r.doc_id} ${r.host} ${r.status} ${r.warnings.join(' ')}`.toLowerCase().includes(q)){const o=document.createElement('option');o.value=i;o.textContent=`${r.doc_id} | ${r.host} | ${r.status}`;sel.appendChild(o);}});show();}
sel.onchange=show;document.getElementById('query').oninput=filter;filter();</script>'''
    path.write_text(page.replace('DATA', data), encoding='utf-8')


def run_pilot(root=Path('artifacts/host_template_audit'), output=Path('artifacts/parser_review')):
    output.mkdir(parents=True, exist_ok=True)
    documents = []
    for line in (root / 'pages.jsonl').read_text().splitlines():
        record = json.loads(line)
        raw = gzip.decompress((root / record['raw_path']).read_bytes()) if record.get('raw_path') else b''
        try:
            documents.append(parse_page(record, raw))
        except Exception as error:
            documents.append({'doc_id': record['doc_id'], 'url': record['url'], 'host': record['host'], 'title': '', 'status': 'parser_error', 'warnings': [str(error)], 'blocks': [], 'answers': [], 'resources': [], 'removed': [], 'outside_headings': [], 'selectors': [], 'selected_source_text': ''})
    with (output / 'documents.jsonl').open('w') as handle:
        for document in documents:
            handle.write(json.dumps(document, ensure_ascii=False) + '\n')
    write_review(documents, output / 'review.html')
    print(json.dumps({'documents': len(documents), 'statuses': dict(Counter(d['status'] for d in documents)), 'warnings': dict(Counter(w for d in documents for w in d['warnings']))}, ensure_ascii=False, indent=2))
    return documents

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot', type=Path, default=Path('artifacts/host_template_audit'))
    parser.add_argument('--output', type=Path, default=Path('artifacts/parser_review'))
    args = parser.parse_args()
    run_pilot(args.pilot, args.output)
