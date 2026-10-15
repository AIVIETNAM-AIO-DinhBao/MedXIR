"""Extract source content from the host templates observed in the pilot.

No network calls. Unknown or incomplete pages stay in the review queue.
"""
import argparse
import base64
import binascii
import gzip
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit, parse_qsl
from html import unescape

from bs4 import BeautifulSoup, NavigableString, Tag

VERSION = 'parser-v2.3'

# Public CMS renderer: dSecurity.b7 in dcore-2.2.0.d7.204.js and
# dcms.decode in cmsfcore.dg72.js (baophutho.vn / baothanhhoa.vn).
CMS_ALPHABET = ' 0123456789aáàạảãăắằặẳẵâấầậẩẫbcdđeéẹẻẽêếềệểễfghiíìịỉĩjklmnoóòọỏõôốồộổỗơớờợởỡpqrstuúùụủũưứừựửữvwxyýỳỵỷỹAÁÀẠẢÃĂẮẰẶẲẴÂẤẦẬẨẪBCDĐEÉẸẺẼÊẾỀỆỂỄFGHIÍÌỊỈĨJKLMNOÓÒỌỎÕÔỐỒỘỔỖƠỚỜỢỞỠPQRSTUÚÙỤỦŨƯỨỪỰỬỮVWXYÝỲỴỶỸ@#=$%*&?:;,.!()<>{}[]"'


def decode_cms(soup, result):
    """Reproduce the site's public renderer; never execute source JavaScript."""
    if result['host'] not in {'baophutho.vn', 'baothanhhoa.vn'}:
        return
    node = soup.select_one('#dcontent')
    if node is None or node.find():
        return
    payload = re.sub(r'\s+', '', node.get_text())
    scripts = '\n'.join(n.get_text() for n in soup.select('script:not([src])'))
    key = re.search(r'''dmain\.detailView\(\s*['"][^'"]*['"]\s*,\s*['"](\d+)['"]''', scripts)
    if not key or not payload:
        return
    try:
        encoded = base64.b64decode(payload, validate=True).decode('utf-8')
        shift = int(key[1])
        decoded = encoded.translate(str.maketrans(CMS_ALPHABET, ''.join(
            CMS_ALPHABET[(i - shift) % len(CMS_ALPHABET)] for i in range(len(CMS_ALPHABET)))))
        fragment = BeautifulSoup(decoded, 'html.parser')
        # Accept only the known article structure. Failed/new encodings stay queued.
        if not fragment.select_one('.pBody, .pHead, .pTitle') or '\ufffd' in decoded:
            return
    except (ValueError, UnicodeError, binascii.Error):
        return
    result['normalizations'].append(dict(reason='cms_base64_shift', source_path='#dcontent',
                                        key=shift, text=payload))
    node.clear()
    node.extend(list(fragment.contents))


def longchau_regions(soup, result):
    """Read explicit article/disease fields, including sections hidden by 'read more'."""
    script = soup.select_one('script#__NEXT_DATA__')
    try:
        props = json.loads(script.get_text())['props']['pageProps']
        disease = urlsplit(result.get('final_url') or result['url']).path.startswith('/benh/')
        data = props['data'] if disease else props['detail']['data']
        prefix = 'props.pageProps.data' if disease else 'props.pageProps.detail.data'
        if data['slug'].strip('/') != urlsplit(result.get('final_url') or result['url']).path.strip('/'):
            result['warnings'].append('structured_source_url_mismatch')
            return []
    except (AttributeError, KeyError, TypeError, ValueError):
        return []
    result['title'] = data.get('headline') or data.get('name') or result['title']
    result['page_type'] = 'encyclopedia' if disease else 'article'
    result['source_metadata'] = {k: data.get(k) for k in ('author', 'approver', 'publishedAt', 'updatedAt')}
    regions = []
    def add(field, value, role='body', heading=None):
        if not value:
            return
        if not isinstance(value, str):
            result['warnings'].append('structured_field_not_parsed')
            result['missing_sections'].append(field)
            return
        root = soup.new_tag('section')
        root['_source_path'] = f'script#__NEXT_DATA__::{prefix}.{field}'
        if heading:
            h = soup.new_tag('h2')
            h.string = heading
            root.append(h)
        root.extend(list(BeautifulSoup(value, 'html.parser').contents))
        regions.append((root, role))
        result['selectors'].append(root['_source_path'])
    add('shortDescription', data.get('shortDescription'), 'summary')
    add('description', data.get('description'))
    for field, heading in [('symptom', 'Triệu chứng'), ('aetiologies', 'Nguyên nhân'),
                           ('risk', 'Nguy cơ'), ('diagnoseAndTreaty', 'Chẩn đoán và điều trị'),
                           ('livingAndPreventive', 'Sinh hoạt và phòng ngừa')]:
        add(field, data.get(field), heading=heading)
    for index, faq in enumerate(data.get('faqs') or []):
        if isinstance(faq, dict):
            add(f'faqs[{index}].answer', faq.get('answer'), 'faq', faq.get('question'))
    add('referenceSource', data.get('referenceSource'), 'references')
    add('sourceArticle', data.get('sourceArticle'), 'references')
    for kind, value in [('audio', data.get('audio')), ('video', data.get('urlVideo'))]:
        url = value.get('url') if isinstance(value, dict) else value
        if isinstance(url, str) and url.startswith(('http://', 'https://')):
            result['resources'].append(dict(kind=kind, url=url, caption=''))
    if any((props.get('mediaHub') or {}).values()):
        result['structured_media'] = props['mediaHub']
        result['warnings'].append('embedded_content_not_parsed')
    return regions

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
    'www.cnkang.com': ['#endText', '.detailc', '#art_text', '.conmain > div:not([class])', 'td.style3:has(> p)', '.detail:has(.ysxp02c_table)', '.zh05b'],
    'www.zydcd.com': ['div.info_txt', '#booklist'],
    'www.wujue.com': ['.art-text', 'td#text', '#article-main_left #art-leftbox', 'td:has(img.photoresize)'],
    'www.youlai.cn': ['[class*="textContent--"]'],
    'phunusuckhoe.giadinhonline.vn': ['#article-body'],
    'www.vinmec.com': ['#main-article'],
    'medlatec.vn': ['.block-posts-single .description', '.infrastructure-description'],
    'qihuangzhishu.com': ['.cbody'],
    'giadinhonline.vn': ['#explus-editor'],
    'vietnamnet.vn': ['#maincontent', '.maincontent.content-full-image', '.emag-inner .sapo, .emag-inner .titles, .emag-inner .inner-contetn'],
    'test.pmphai.com': ['#content.gu_det_left_con'],
    'tiemchunglongchau.com.vn': ['#content-article', 'section[aria-label="Quiz Details Body"]'],
    'baocantho.com.vn': ['#newscontents .content-fck'],
    'hellobacsi.com': ['.article-content-item.body-content'],
    'baoangiang.com.vn': ['#newscontents .content-fck'],
    'vov.vn': ['.text-long', '.article-content', '.node-content-audio .node-body > .content', 'article.detail--video > .content', '.box-content .article-media'],
    'jb39.com': ['.post-jibing'],
    'youmed.vn': ['section.article-content article:not([class]) > div.prose'],
    'vov2.vov.vn': ['.article-body', '.article__body'],
    'www.msdmanuals.cn': ['[class*="TopicMainContent_content"]', '[class*="Chapter_content"]'],
    'suckhoeviet.org.vn': ['.__MASTERCMS_CONTENT'],
    'tamanhhospital.vn': ['.post_info.box_detail_post:has(p)'],
    'www.qdnd.vn': ['.post-content'],
    'thaythuocvietnam.vn': ['.content-post.clearfix'],
    'bachmai.gov.vn': ['.content-editor-post'],
    'www.baidu.com': ['.health-dict__overview'],
    'dantri.com.vn': ['#desktop-in-article', '#desktop-cate-in-article', '.e-magazine__body', '.video-container'],
    'vnexpress.net': ['article.fck_detail'],
    'health.people.com.cn': ['#rm_txt_zw', '.artDet', '.articleCont', '.rm_txt_con'],
    'baophutho.vn': ['#dcontent'],
    'baothanhhoa.vn': ['#dcontent', '.article__body'],
    'www.pharmacity.vn': ['#pmc-content-blog-detail'],
    'www.familydoctor.cn': ['.article-content'],
    'benhvienvietduc.org': ['.news-primary-content .edittor-content', '.news-primary-content'],
    'mega.vietnamplus.vn': ['.entry.production.longform'],
    'fk.99.com.cn': ['.wrap-left .content'],
})
RULES['nhandan.vn'].append('.Theme-Layer-BodyText--inner')
RULES['care.39.net'].append('.art > .content')
RULES['man.39.net'].append('body > p')
RULES['suckhoedoisong.vn'].append('.detail-container [data-role="video-autoplay"]')
RULES['dantri.com.vn'].append('main > .video-highlight, main > .video-grid')

SUMMARY_SELECTORS = '.article__sapo, .detail-sapo, .detail__sapo, .post-summary, .sc-longform-header-sapo, .content-detail-sapo, .article-summary, .art_summary'
SUMMARY_HOST = {
    'giadinhonline.vn': 'h2.intro', 'medlatec.vn': '.block-posts-single .shortdescription',
    'dantri.com.vn': '[data-slot="sapo"]', 'fk.99.com.cn': '.introduct',
    'www.familydoctor.cn': 'article.article .content > .summary',
    'vov2.vov.vn': '.article-description',
}

# Only identified UI containers; ordinary links, lists, tables and references stay.
COMMON_REMOVE = 'script, style, button, form, .ez-toc-container, #ez-toc-container, #toc, #toc-container, .sharethis-inline-share-buttons, .bdsharebuttonbox, .related-news, article.story, [type="RelatedNewsBox"], [type="RelatedOneNews"], .readmore-body-box, .article__meta, .article__source, .article__tag'
HOST_REMOVE = {
    'benhvienvietduc.org': '.news-details-date, .news-detail-bot, .news-detail-more, .news-primary-content > h1',
    'www.a-hospital.com': '#jump-to-nav, table.nav, table.navbox, table.msg-table, #catlinks, #fromlink, .editsection',
    'zhongyibaodian.net': '#jump-to-nav, table.nav, table.navbox, table.msg-table, #catlinks, #fromlink, .editsection',
    'www.cnkang.com': '#jkpl, .detail > h1, .detail > .detailb',
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
BLOCK_TAGS = {'p', 'div', 'section', 'article', 'li', 'ul', 'ol', 'blockquote', 'figure', 'figcaption', 'dl', 'dt', 'dd', 'tr', 'td', 'th', 'thead', 'tbody', 'tfoot', 'caption', 'table'}

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
    if host in selectors:
        for n in soup.select(selectors[host]):
            if text(n):
                return text(n)
    for selector in ['meta[property="og:title"]', 'meta[name="twitter:title"]']:
        for n in soup.select(selector):
            if n.get('content', '').strip():
                return n['content'].strip()
    for selector in ('h1', 'title'):
        for n in soup.select(selector):
            if text(n):
                return text(n)
    return ''


def normalize_markup(root, result):
    """Decode escaped formatting inside content, retaining the original text for review."""
    pattern = r'</?(?:p|div|br|img|a|strong|b|em|i|span|h[1-6]|ul|ol|li|table|tr|td|th)(?:\s[^<>]*|\s*/?)>'
    for string in list(root.find_all(string=True)):
        if type(string) is not NavigableString:
            continue
        value = str(string)
        decoded = unescape(value)
        if not re.search(pattern, decoded, re.I):
            continue
        fragment = BeautifulSoup(decoded, 'html.parser')
        if not fragment.find():
            continue
        result['normalizations'].append(dict(reason='escaped_html',
                                            source_path=dom_path(string.parent), text=value))
        result['warnings'].append('escaped_html_normalized')
        string.replace_with(*list(fragment.contents))


def find_pagination(soup, regions, url):
    candidates = soup.select('a[rel~="next"], .pagination a[href], .pages a[href], #pages a[href]')
    candidates += [link for root, _ in regions for link in root.select('a[href]')]
    source = urlsplit(url)
    def article_key(parts):
        path = re.sub(r'_\d+(?=\.(?:s?html?|htm)$)', '', parts.path, flags=re.I)
        query = sorted((key, value) for key, value in parse_qsl(parts.query)
                       if key.lower() not in {'page', 'p', 'pageno', 'pagenum', 'pageindex', 'start', 'offset'})
        return path, query
    links = {}
    for link in candidates:
        if 'next' not in link.get('rel', []) and not re.fullmatch(
                r'下一页|下页|下一頁|下一张|下一張|Trang tiếp(?: theo)?|Next(?: page)?|[>»›]+', text(link), re.I):
            continue
        target = urlsplit(urljoin(url, link.get('href', '')))
        absolute = target._replace(fragment='').geturl()
        if (target.scheme not in {'http', 'https'} or target.hostname != source.hostname
                or absolute == source._replace(fragment='').geturl()):
            continue
        same_article = article_key(target) == article_key(source)
        # Book chapters and disease detail tabs have separate URLs. Their own
        # missing-section warnings already describe these links.
        if source.hostname in {'qihuangzhishu.com', 'jb39.com'} and not same_article:
            continue
        links[absolute] = dict(url=absolute, same_article=same_article)
    return list(links.values())

def dom_path(node):
    if isinstance(node, Tag) and node.get('_source_path'):
        return node['_source_path']
    parts = []
    while isinstance(node, Tag) and node.name != '[document]':
        if node.get('_source_path'):
            parts.append(node['_source_path'])
            break
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
        if node.name == 'tr':
            cells = [cell for cell in node.find_all(['td', 'th']) if cell.find_parent('tr') is node]
            # html.parser can nest unclosed cells/rows. Walk that DOM once instead
            # of flattening an ancestor row and then emitting its descendants again.
            if cells and not any(c.find('table') for c in cells):
                values = [' '.join(str(string) for string in cell.find_all(string=True)
                                   if type(string) is NavigableString
                                   and string.find_parent(['td', 'th']) is cell) for cell in cells]
                emit('table_row', ' | '.join(values), node)
                for row in node.find_all('tr'):
                    if row.find_parent('tr') is node:
                        walk(row)
                return
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
                    walk(child)
                elif re.fullmatch(r'h[1-6]', child.name) or set(child.get('class', [])) & {'gu_det_left_con_h1', 'pInterTitle', 'health-dict__overview__text__level1-tag__title', 'disease_title'}:
                    flush()
                    emit('heading', text(child), child)
                elif child.name in BLOCK_TAGS:
                    flush()
                    walk(child, 'list_item' if child.name == 'li' else 'caption' if child.name in {'figcaption', 'caption'} else 'paragraph')
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
            result['removed'].append({'reason': reason, 'selector_path': dom_path(node), 'text': value,
                                      'links': [dict(text=text(a), url=urljoin(result['url'], a['href']))
                                                for a in node.select('a[href]')]})
        node.decompose()

    common = COMMON_REMOVE.replace(', article.story', '') if role == 'listing' else COMMON_REMOVE
    selectors = common + ', ' + HOST_REMOVE.get(host, '[data-parser-unused]')
    if role.startswith('answer_'):
        selectors += ', .b_anscont_more'
    for node in top_nodes(root.select(selectors)):
        remove(node, 'identified_ui')
    if host.endswith('.39.net'):
        notices = {'39健康网(www.39.net)专稿，未经书面授权请勿转载。', '未经作者允许授权，禁止转载',
                   '（以上内容仅授权39健康网独家使用，未经版权方授权请勿转载。）'}
        notices = {re.sub(r'\s+', '', value) for value in notices}
        candidates = [node for node in root.select('p, div, span, strong, b')
                      if re.sub(r'\s+', '', text(node)) in notices
                      or re.fullmatch(r'（(?:(?:实习)?编辑|责任编辑)：[^）]+）', text(node))]
        for node in top_nodes(candidates):
            remove(node, 'publisher_notice_or_editor_metadata')
    if host == 'medlatec.vn':
        # Styled store cards: keep ordinary clinical links to drugs in the article.
        candidates = [node for node in root.select('div[style]')
                      if 'max-width' in node.get('style', '') and node.select_one('a[href*="store.medlatec.vn"]')
                      and ('border' in node.get('style', '') or 'background' in node.get('style', ''))]
        for node in top_nodes(candidates):
            remove(node, 'store_promotion_card')
        for node in list(root.select('p')):
            if text(node).startswith('Để được tư vấn hoặc đặt lịch khám với bác sĩ chuyên khoa') and node.select_one('a[href^="tel:"]'):
                remove(node, 'booking_call_to_action')
    if host == 'youmed.vn':
        for node in list(root.select('p')):
            if node.parent is None:
                continue
            value = text(node)
            if (('tính năng Chat với bác sĩ trên ứng dụng YouMed' in value and node.select_one('a[href]'))
                    or (value.startswith('Bạn có thể tải ứng dụng YouMed') and node.select_one('a[href]'))):
                sibling = node.find_next_sibling()
                if sibling and sibling.name in {'ul', 'ol'} and all(
                        text(item).startswith('Bước ') for item in sibling.find_all('li', recursive=False)):
                    remove(sibling, 'app_promotion_steps')
                    sibling = node.find_next_sibling()
                if sibling and sibling.name == 'figure' and 'ứng dụng YouMed' in text(sibling):
                    remove(sibling, 'app_promotion_figure')
                remove(node, 'app_promotion_paragraph')
    if host in {'www.a-hospital.com', 'zhongyibaodian.net'}:
        for heading in list(root.select('h2, h3')):
            if text(heading) not in {'更多医学百科条目', '相关页面'}:
                continue
            sibling = heading.find_next_sibling()
            if sibling and sibling.name in {'ul', 'ol', 'div', 'table'} and sibling.select_one('a[href]'):
                remove(sibling, 'wiki_related_navigation')
                remove(heading, 'wiki_related_navigation_label')
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
    label_pattern = r'(?:[>»\s]*Xem thêm\s*:?[\s:]*|(?:相关阅读(?:推荐)?|相关推荐|编辑推荐|编辑热荐)[：:]?|【相关(?:文章|专题)】)'
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
            elif re.fullmatch(label_pattern, text(node), re.I):
                sibling = node.find_next_sibling()
                while sibling and sibling.name in {'a', 'p', 'ul', 'ol'} and (sibling.name == 'a' or sibling.select_one('a[href]')):
                    nonlink = '' if sibling.name == 'a' else ''.join(str(s) for s in sibling.find_all(string=True) if not s.find_parent('a')).strip()
                    if re.sub(r'[\s:：、，,。.!！?？·—-]', '', nonlink):
                        break
                    following = sibling.find_next_sibling()
                    remove(sibling, 'related_links_only')
                    sibling = following
                remove(node, 'related_links_label')
        elif host == 'www.vinmec.com' and text(node).startswith(('Để đặt lịch khám tại viện, Quý khách', 'Quý khách vui lòng bấm số')) and node.select_one('a[href]'):
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
    if host.endswith('.39.net'):
        for node in list(root.select('i[title="img"], i[title="timg"]')):
            target = text(node)
            if target.startswith(('http://', 'https://')):
                result['resources'].append(dict(kind='img', url=target, caption=''))
                remove(node, 'gallery_image_url_metadata')

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
        'normalizations': [], 'pagination_links': [],
    }
    if record['state'] != 'html_response':
        result['status'] = record['state']
        return result
    decode_cms(soup, result)
    if not result['title']:
        warnings.append('title_missing')
    body_text = text(soup.body or soup)
    fatal = host == 'www.cnkang.com' and body_text.startswith('Fatal error') and len(body_text) < 2000
    soft404 = (urlsplit(record.get('final_url') or record['url']).path == '/web/404.html'
               or result['title'] == '抱歉，您访问的页面不存在')
    main_article = soup.select_one('#main-article') if host == 'www.vinmec.com' else None
    missing_data = host == 'www.vinmec.com' and not soup.select_one('.box_quizz_detail') and (
        main_article is None or not text(main_article) and not main_article.select_one('img,audio,video,iframe')) and any(
        text(node).startswith('Không tìm thấy dữ liệu bạn yêu cầu') for node in soup.select('#modal-api-error p'))
    if fatal or soft404 or missing_data:
        result.update(status='source_error', page_type='error', selected_source_text=body_text)
        warnings.append('source_error_in_html')
        return result
    regions = []
    if host == 'nhathuoclongchau.com.vn':
        regions = longchau_regions(soup, result)
    elif host in {'www.120ask.com', 'ask.39.net'}:
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
            if nodes and (any(text(n) for n in nodes) or any(n.select_one('img,iframe,audio,video,[data-url],[data-vid]') for n in nodes)):
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
            candidate_exists = any(soup.select_one(selector) for selector in RULES.get(host, []))
            result['status'] = 'empty_content' if candidate_exists else 'unknown_template'
            if candidate_exists:
                warnings.append('content_region_empty')
            result['selected_source_text'] = text(soup.body or soup)
            return result
    if result['selectors'] == ['#booklist'] or (host == 'dantri.com.vn' and '/video/' in record['url']
                                               and result['selectors'] == ['main > .video-highlight, main > .video-grid']):
        result['page_type'] = 'listing'
        warnings.append('listing_not_single_article')
    if host == 'www.cnkang.com' and result['selectors'] == ['.detail:has(.ysxp02c_table)']:
        result['page_type'] = 'recipe'
    if host in {'www.a-hospital.com', 'zhongyibaodian.net'} and soup.select_one('#filehistory'):
        result['page_type'] = 'attachment'
        warnings.append('attachment_page_requires_review')
    result['pagination_links'] = find_pagination(soup, regions, record.get('final_url') or record['url'])
    if result['pagination_links']:
        warnings.append('pagination_not_fetched' if any(p['same_article'] for p in result['pagination_links'])
                        else 'pagination_link_requires_review')
    # Keep introductions outside the body, without taking summaries of sidebar cards.
    if result['page_type'] == 'article' and host != 'nhathuoclongchau.com.vn':
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
    if (host in {'vov.vn', 'vov2.vov.vn'} and 'infographic' in result['title'].lower()
            or 'infographic' in urlsplit(record['url']).path.lower()):
        result['page_type'] = 'infographic'
        warnings.append('image_content_not_transcribed')
    if host.endswith('.39.net') and soup.select_one('[id^="modePhoto"]'):
        result['page_type'] = 'gallery'
        warnings.append('image_content_not_transcribed')
    if host == 'thaythuocvietnam.vn' and 'Câu hỏi số 1' in result['title']:
        result['page_type'] = 'placeholder'
        warnings.append('placeholder_qa')
    result['selected_source_text'] = '\n\n'.join(text(n) for n, _ in regions)
    selected = {id(n) for root, _ in regions for n in [root, *root.descendants]}
    result['outside_headings'] = [{'text': text(n), 'path': dom_path(n)} for n in soup.select('h1,h2,h3') if id(n) not in selected and text(n)]
    result['outside_paragraphs'] = [{'text': text(n), 'path': dom_path(n)} for n in soup.select('p, blockquote') if id(n) not in selected and len(text(n)) >= 80 and not n.find_parent(['header', 'footer', 'nav', 'form']) and not n.find(['p', 'blockquote'])]
    if host == 'nhathuoclongchau.com.vn':
        # SSR duplicates of the same JSON fields are already covered.
        covered = {text(n) for root, _ in regions for n in root.select('h1,h2,h3,p,blockquote')}
        result['outside_headings'] = [n for n in result['outside_headings'] if n['text'] not in covered]
        result['outside_paragraphs'] = [n for n in result['outside_paragraphs'] if n['text'] not in covered]
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
        normalize_markup(root, result)
        # Freeze source locations before cleanup changes sibling positions.
        for node in [root, *root.find_all()]:
            node['_source_path'] = dom_path(node)
        clean_region(root, host, role, result)
        pager_urls = {p['url'] for p in result['pagination_links']}
        pager_nodes = [node for node in root.select('a[href]') if urlsplit(urljoin(
            record.get('final_url') or record['url'], node['href']))._replace(fragment='').geturl() in pager_urls]
        for node in pager_nodes:
            result['removed'].append(dict(reason='pagination_control', selector_path=dom_path(node),
                                          text=text(node), url=node['href']))
            node.decompose()
        # Resources in identified UI have already been removed.
        for node in root.select('img, audio, video, source, iframe, a[href], [data-url], [data-src], [data-vid]'):
            target = node.get('data-url') or node.get('data-vid') or node.get('data-src') or node.get('data-original') or node.get('data-lazy-src') or node.get('src') or node.get('href')
            if not target or target.startswith(('data:', 'javascript:')):
                continue
            if node.get('data-vid') and not urlsplit(target).scheme and '/' in target and '.' in target.split('/')[0]:
                target = 'https://' + target
            absolute = urljoin(record.get('final_url') or record['url'], target)
            embedded_pdf = parse_qs(urlsplit(absolute).query).get('url', [None])[0]
            if node.name == 'a' and role != 'references' and not urlsplit(absolute).path.lower().endswith('.pdf'):
                continue
            target_url = embedded_pdf or absolute
            suffix = urlsplit(target_url).path.lower()
            kind = ('pdf' if suffix.endswith('.pdf') else 'video' if suffix.endswith(('.mp4', '.webm', '.m3u8'))
                    else 'audio' if suffix.endswith(('.mp3', '.wav', '.m4a')) else node.name)
            if node.name == 'source' and node.find_parent(['audio', 'video']):
                kind = node.find_parent(['audio', 'video']).name
            if node.name not in {'img', 'audio', 'video', 'source', 'iframe', 'a'} and kind == node.name:
                kind = 'embedded'
            resource = {'kind': kind, 'url': target_url, 'caption': node.get('alt') or node.get('data-title', '')}
            if resource not in result['resources']:
                result['resources'].append(resource)
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
    text_length = sum(len(b['text']) for b in result['blocks'])
    image_count = sum(r['kind'] == 'img' for r in result['resources'])
    if image_count and (text_length < 100 or image_count >= 2 and text_length < 500):
        warnings.append('image_content_not_transcribed')
    if result['text'] and text_length < 200:
        warnings.append('short_content_requires_review')
    if any(r['kind'] == 'pdf' for r in result['resources']):
        warnings.append('pdf_not_parsed')
    if any(r['kind'] in {'audio', 'video'} for r in result['resources']):
        warnings.append('media_transcript_not_verified')
    if any(r['kind'] in {'iframe', 'embedded'} for r in result['resources']):
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
<h2>Các vùng đã loại</h2><pre id="removed"></pre><h2>HTML escape đã chuyển thành DOM</h2><pre id="normalizations"></pre><h2>Trang tiếp theo chưa tải</h2><pre id="pagination"></pre><h2>Heading ngoài vùng chọn — cần đối chiếu</h2><pre id="outside"></pre><h2>Đoạn dài ngoài vùng chọn — có thể là nội dung bị sót hoặc gợi ý</h2><pre id="paragraphs"></pre><h2>Ảnh / media / PDF thuộc vùng nội dung</h2><pre id="resources"></pre>
<script id="data" type="application/json">DATA</script><script>
const rows=JSON.parse(document.getElementById('data').textContent), sel=document.getElementById('docs');
function show(){const r=rows[Number(sel.value)];if(!r)return;document.getElementById('info').textContent=`${r.doc_id} | ${r.host} | ${r.status} | ${r.title} | ${r.url} | ${r.selectors.join(', ')} | ${r.warnings.join(', ')}`;
for(const [id,value] of Object.entries({source:r.selected_source_text,output:(r.blocks||[]).map(b=>`[${b.role}/${b.kind}] ${b.text}`).join('\\n\\n'),removed:r.removed,normalizations:r.normalizations||[],pagination:r.pagination_links||[],outside:r.outside_headings,paragraphs:r.outside_paragraphs||[],resources:r.resources}))document.getElementById(id).textContent=typeof value==='string'?value:JSON.stringify(value,null,2);}
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
