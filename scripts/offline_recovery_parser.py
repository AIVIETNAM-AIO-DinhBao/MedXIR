"""Offline source adapters. No network calls and no changes to the live crawler."""
import gzip,hashlib,io,json,re,zlib
from urllib.parse import urlsplit,unquote
import xml.etree.ElementTree as ET
import zstandard
import content_parser as base
from text_clean import clean_text

RECOVERY_VERSION='offline-recovery-v5'
base.RULES['laodong.vn']=['.art-body', 'article .art-content', 'article .article-content']
base.COMMON_REMOVE += ', article .stories-box-horizontal, article.detail .keywords-box'
base.RULES['www.familydoctor.com.cn']=['#viewContent']
base.RULES['baidianfeng.familydoctor.com.cn']=['#viewContent']
base.RULES['zysjonline.com']=['#content', '.r0 > .rn']
base.RULES['article.iiyi.com']=['.article_cont .cont']
base.RULES['vietnamnet.vn'] += ['.maincontent.main-content', '.maincontent-emg']
base.COMMON_REMOVE += ', .article_cont .cont > .sp, .article_cont .cont > .b-mh-bto, .article_cont .cont > div:has(> span:-soup-contains("版权声明"))'
MAX_RAW=8*1024*1024

def known_mirror(original, target):
    source, landed=urlsplit(original),urlsplit(target)
    if source.hostname!='zysjonline.com' or landed.hostname not in {'www.zysj.com.cn','zysj.com.cn'}:return False
    a=unquote(source.path).strip('/').split('/')
    b=unquote(landed.path).strip('/').split('/')
    if len(a)==3 and a[:2]==['articles','misc']:
        return bool(b and b[0]=='zaji' and b[-1]==a[-1]+'.html')
    if len(a)==3 and a[0]=='books':
        return len(b)==3 and b[:2]==['lilunshuji',a[1]] and b[-1].endswith('.html') and b[-1]!='index.html'
    if len(a)==2 and a[0] in {'formulas','herbs'}:
        category='zhongyaofang' if a[0]=='formulas' else 'zhongyaocai'
        return bool(b and b[0]==category and b[-1]==a[-1]+'.html')
    return False

def effective_url(final_url, original, host):
    final_url=final_url or original
    parts=urlsplit(final_url)
    if parts.hostname in {'web.archive.org','www.web.archive.org'}:
        match=re.match(r'^/web/[0-9]{4,14}[a-z_]*/(https?://.+)$',parts.path)
        if not match:raise ValueError('archive_capture_unrecognized')
        inner=match.group(1)
        if parts.query:inner+='?'+parts.query
        if urlsplit(inner).hostname != host and not known_mirror(original,inner):raise ValueError('archive_source_host_mismatch')
        return inner
    return final_url

def pubmed_xml(result,raw,final_url):
    expected=re.fullmatch(r'/(\d+)/?',urlsplit(result['url']).path)
    if expected is None:raise ValueError('pubmed_source_url_invalid')
    root=ET.fromstring(raw)
    articles=[root] if root.tag=='PubmedArticle' else root.findall('.//PubmedArticle')
    matching=[a for a in articles if a.findtext('./MedlineCitation/PMID')==expected[1]]
    if len(matching)!=1:raise ValueError('pubmed_pmid_mismatch')
    article=matching[0].find('./MedlineCitation/Article')
    title_node=article.find('ArticleTitle')
    title=''.join(title_node.itertext()).strip() if title_node is not None else ''
    sections=[]
    for node in article.findall('./Abstract/AbstractText'):
        text=''.join(node.itertext()).strip()
        if text:sections.append((node.get('Label')+': ' if node.get('Label') else '')+text)
    if not title or not sections:
        result.update(status='empty_pubmed_abstract');return result
    text=title+'\n\n'+'\n\n'.join(sections)
    result.update(accepted=True,status='extracted_unreviewed',warnings=[],text=text,title=title,
                  text_chars=len(text),text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                  parser_version=base.VERSION+'+'+RECOVERY_VERSION+'+pubmed-xml',source='snute_offline_recovery',
                  source_final_url=final_url,final_url=final_url,source_html_sha256=hashlib.sha256(raw).hexdigest(),
                  page_type='article',complete_qa=False)
    return result

def classify(record,raw):
    sample=raw[:8000].decode('utf-8',errors='ignore').lower()
    if not raw:return 'empty_response'
    if ('denied by http_custom' in sample or 'verify you are human' in sample
        or 'document.cookie' in sample and 'location.reload' in sample
        or 'captcha' in sample and len(raw)<10000):return 'access_challenge'
    if record.get('content_type') and 'html' not in record['content_type']:return 'non_html'
    if urlsplit(record['url']).path not in ('','/') and urlsplit(record['final_url']).path in ('','/'):return 'redirected_home'
    return 'html_response'

def parse_html(job):
    doc_id,url,host,lang,final_url,ctype,blob,source_path=job
    result=dict(doc_id=doc_id,url=url,host=host,lang=lang,source_path=source_path,accepted=False)
    try:
        if blob[:4]==bytes.fromhex('28b52ffd'):
            with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(blob)) as reader:raw=reader.read(MAX_RAW+1)
        elif blob[:2]==bytes.fromhex('1f8b'):
            with gzip.GzipFile(fileobj=io.BytesIO(blob)) as reader:raw=reader.read(MAX_RAW+1)
        else:raw=bytes(blob)
        if len(raw)>MAX_RAW:raise ValueError('html_size_limit')
        if host=='pubmed.ncbi.nlm.nih.gov' and ('xml' in (ctype or '').lower() or raw.lstrip().startswith(b'<PubmedArticle')):
            return pubmed_xml(result,raw,final_url)
        landed=effective_url(final_url,url,host)
        if host=='zysjonline.com' and urlsplit(landed).hostname!=host and not known_mirror(url,landed):raise ValueError('mirror_source_route_mismatch')
        record=dict(doc_id=doc_id,url=url,host=host,final_url=landed,content_type=(ctype or 'text/html').lower(),sha256=hashlib.sha256(raw).hexdigest())
        record['state']=classify(record,raw)
        document=base.parse_page(record,raw)
        assert document['doc_id']==doc_id and document['url']==url
        text=document.get('text') or '\n\n'.join(b['text'] for b in document.get('blocks',[]) if b.get('text'))
        warnings=document.get('warnings',[])
        complete_qa=(document.get('page_type')=='qa' and any(b.get('role')=='question' and b.get('text','').strip() for b in document.get('blocks',[])) and any(a.get('text','').strip() for a in document.get('answers',[])))
        result.update(status=document['status'],warnings=warnings,text_chars=len(text),title=document.get('title',''),page_type=document.get('page_type',''),complete_qa=complete_qa,source_final_url=final_url,final_url=landed,source_html_sha256=record['sha256'])
        critical={'structured_source_url_mismatch','block_text_mismatch','encoded_content'}
        cleaned=clean_text(text,lang)
        if record['state']!='html_response' or document['status'] not in {'extracted_unreviewed','needs_review'} or not cleaned or len(cleaned)<30 and not complete_qa or critical.intersection(warnings):return result
        result.update(accepted=True,text=text,text_sha256=hashlib.sha256(text.encode()).hexdigest(),parser_version=base.VERSION+'+'+RECOVERY_VERSION,source='snute_offline_recovery')
    except Exception as exc:
        reason=str(exc)
        result.update(status='parser_error',error_type=type(exc).__name__)
        if reason in {'archive_capture_unrecognized','archive_source_host_mismatch','html_size_limit',
                      'mirror_source_route_mismatch','pubmed_source_url_invalid','pubmed_pmid_mismatch'}:
            result['error_reason']=reason
    return result
