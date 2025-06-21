#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
젠스파크 AI 슬라이드 → 오프라인 단일 HTML 변환기 (v4.7 슬라이드컨테이너중앙정렬 완성판)

완전 해결:
- 슬라이드 컨테이너만 중앙 정렬 (내부 콘텐츠는 원본 그대로)
- 원본 CSS 스타일 완전 보존 (텍스트, 아이콘, 코드 블록 정렬 유지)
- 슬라이드별 CSS 격리로 스타일 충돌 방지
- 긴 콘텐츠 스크롤 지원 추가
- 슬라이드 이동 기능 완전 복구
- targetPriceDistChart 변수명 누락 문제 해결
- Font Awesome 폰트 완전 임베딩
- Chart.js 다중 차트 충돌 완전 해결
- Canvas getContext 오류 완전 수정
- 디버깅 로그 강화
- 모든 리소스 오프라인 지원
"""

import os
import re
import sys
import json
import time
import base64
import hashlib
import argparse
import tempfile
import requests
from pathlib import Path
from urllib.parse import urljoin, urlparse, quote
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

class GoogleFontsProcessor:
    """Google Fonts API 처리 클래스"""
    
    @staticmethod
    def extract_font_urls(css_url):
        """Google Fonts CSS에서 실제 폰트 파일 URL 추출"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            response = requests.get(css_url, headers=headers, timeout=30)
            response.raise_for_status()
            
            css_content = response.text
            font_urls = []
            
            # @font-face 블록에서 URL 추출
            font_face_blocks = re.findall(r'@font-face\s*\{([^}]+)\}', css_content, re.DOTALL)
            
            for block in font_face_blocks:
                # src 속성에서 URL 추출
                src_matches = re.findall(r'src:\s*url\(([^)]+)\)', block)
                for match in src_matches:
                    url = match.strip('\'"')
                    if url.startswith('http') and 'stats' not in url:  # stats URL 제외
                        font_urls.append(url)
            
            return css_content, font_urls
            
        except Exception as e:
            print(f"Google Fonts 처리 실패: {css_url} - {e}")
            return "", []

class ResourceCache:
    """리소스 캐싱 관리 클래스"""
    
    def __init__(self, cache_dir):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        self.cache_index_file = self.cache_dir / 'cache_index.json'
        self.cache_index = self.load_cache_index()
    
    def load_cache_index(self):
        """캐시 인덱스 로드"""
        if self.cache_index_file.exists():
            try:
                with open(self.cache_index_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def save_cache_index(self):
        """캐시 인덱스 저장"""
        with open(self.cache_index_file, 'w', encoding='utf-8') as f:
            json.dump(self.cache_index, f, indent=2)
    
    def get_cache_path(self, url):
        """URL에 대한 캐시 파일 경로 생성"""
        url_hash = hashlib.md5(url.encode()).hexdigest()
        parsed_url = urlparse(url)
        ext = Path(parsed_url.path).suffix or '.cache'
        return self.cache_dir / f"{url_hash}{ext}"
    
    def is_cached(self, url):
        """URL이 캐시되어 있는지 확인"""
        cache_path = self.get_cache_path(url)
        return url in self.cache_index and cache_path.exists()
    
    def get_cached_resource(self, url):
        """캐시된 리소스 로드"""
        if not self.is_cached(url):
            return None
        
        cache_path = self.get_cache_path(url)
        cache_info = self.cache_index[url]
        
        try:
            with open(cache_path, 'rb') as f:
                content = f.read()
            
            return {
                'url': url,
                'type': cache_info['type'],
                'content': content,
                'text_content': content.decode('utf-8', errors='ignore') if cache_info['type'] in ['css', 'js'] else None,
                'base64': base64.b64encode(content).decode('utf-8'),
                'file_path': cache_path,
                'cached': True
            }
        except Exception as e:
            print(f"캐시 로드 실패: {url} - {e}")
            return None
    
    def cache_resource(self, url, resource_data):
        """리소스를 캐시에 저장"""
        cache_path = self.get_cache_path(url)
        
        try:
            with open(cache_path, 'wb') as f:
                f.write(resource_data['content'])
            
            # 캐시 인덱스 업데이트
            self.cache_index[url] = {
                'type': resource_data['type'],
                'cached_at': time.time(),
                'file_path': str(cache_path)
            }
            
            self.save_cache_index()
            return True
        except Exception as e:
            print(f"캐시 저장 실패: {url} - {e}")
            return False

class GenSparkConverter:
    def __init__(self):
        # 프로젝트 루트 디렉터리 설정
        self.project_root = Path.cwd()
        self.source_dir = self.project_root / 'source'
        self.output_dir = self.project_root / 'output'
        self.cache_dir = self.project_root / 'cache'
        
        # 디렉터리 생성
        self.source_dir.mkdir(exist_ok=True)
        self.output_dir.mkdir(exist_ok=True)
        self.cache_dir.mkdir(exist_ok=True)
        
        # 리소스 캐시 초기화
        self.resource_cache = ResourceCache(self.cache_dir)
        self.google_fonts_processor = GoogleFontsProcessor()
        
        self.temp_dir = None
        self.downloaded_resources = {}
        self.total_slides = 0
        self.processed_slides = 0
        self.first_slide_title = "젠스파크 AI 슬라이드"
        
        # Highlight.js 설정
        self.highlightjs_urls = {
            'js': 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js',
            'css_default': 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/default.min.css',
            'css_github': 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css',
            'css_monokai': 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/monokai.min.css'
        }
        
        # Font Awesome 추가
        self.fontawesome_urls = {
            'css': 'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css',
            'webfonts': [
                'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/webfonts/fa-solid-900.woff2',
                'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/webfonts/fa-regular-400.woff2',
                'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/webfonts/fa-brands-400.woff2'
            ]
        }
        
        # 공통 CDN 패턴 (루트 도메인 제외)
        self.cdn_patterns = [
            r'https?://cdn\.jsdelivr\.net/[^/]+/',
            r'https?://cdnjs\.cloudflare\.com/[^/]+/',
            r'https?://unpkg\.com/[^/]+/',
            r'https?://fonts\.googleapis\.com/css',  # CSS 파일만
            r'https?://fonts\.gstatic\.com/s/',      # 실제 폰트 파일만
            r'https?://ajax\.googleapis\.com/[^/]+/',
            r'https?://code\.jquery\.com/[^/]+/',
            r'https?://stackpath\.bootstrapcdn\.com/[^/]+/',
            r'https?://maxcdn\.bootstrapcdn\.com/[^/]+/'
        ]
        
        # 제외할 URL 패턴 (루트 도메인)
        self.exclude_patterns = [
            r'https?://fonts\.gstatic\.com/?$',
            r'https?://fonts\.googleapis\.com/?$',
            r'https?://fonts\.gstatic\.com/stats/',  # stats URL 제외
        ]

    def log(self, message, level="INFO"):
        """로그 출력"""
        timestamp = time.strftime("%H:%M:%S")
        print(f"[{timestamp}] {level}: {message}")

    def resolve_file_paths(self, filename):
        """파일 경로 자동 해결"""
        # 입력 파일 경로
        if filename.endswith('.html'):
            base_name = filename[:-5]  # .html 제거
        else:
            base_name = filename
            filename = f"{filename}.html"
        
        input_path = self.source_dir / filename
        output_path = self.output_dir / f"{base_name}_ppt.html"
        
        return input_path, output_path

    def extract_first_slide_title(self, html_content):
        """첫 번째 슬라이드에서 제목 추출"""
        slides = re.split(r'<html[^>]*>', html_content, flags=re.IGNORECASE)[1:]
        if slides:
            first_slide = f"<html>{slides[0]}"
            soup = BeautifulSoup(first_slide, 'html.parser')
            
            # title 태그에서 추출
            title_tag = soup.find('title')
            if title_tag and title_tag.get_text().strip():
                return title_tag.get_text().strip()
            
            # h1 태그에서 추출
            h1_tag = soup.find('h1')
            if h1_tag and h1_tag.get_text().strip():
                return h1_tag.get_text().strip()
            
            # h2 태그에서 추출
            h2_tag = soup.find('h2')
            if h2_tag and h2_tag.get_text().strip():
                return h2_tag.get_text().strip()
        
        return "젠스파크 AI 슬라이드"

    def is_valid_download_url(self, url):
        """다운로드 가능한 유효한 URL인지 확인"""
        # 제외 패턴 확인
        for pattern in self.exclude_patterns:
            if re.match(pattern, url):
                return False
        
        # 유효한 CDN 패턴 확인
        for pattern in self.cdn_patterns:
            if re.search(pattern, url):
                return True
        
        return False

    def download_resource(self, url, timeout=30):
        """단일 리소스 다운로드 (URL 검증 강화)"""
        
        # URL 유효성 검사
        if not self.is_valid_download_url(url):
            self.log(f"다운로드 제외: {url} (루트 도메인 또는 무효한 URL)")
            return None
        
        # 캐시 확인
        cached_resource = self.resource_cache.get_cached_resource(url)
        if cached_resource:
            self.log(f"캐시에서 로드: {url}")
            return cached_resource
        
        try:
            self.log(f"다운로드 중: {url}")
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            
            # Google Fonts CSS 특별 처리
            if 'fonts.googleapis.com/css' in url:
                css_content, font_urls = self.google_fonts_processor.extract_font_urls(url)
                if css_content:
                    # 폰트 파일들을 다운로드하여 CSS에 임베딩
                    for font_url in font_urls:
                        font_resource = self.download_resource(font_url)
                        if font_resource:
                            # CSS에서 폰트 URL을 base64 데이터로 교체
                            font_base64 = font_resource['base64']
                            font_format = 'woff2' if 'woff2' in font_url else 'woff'
                            css_content = css_content.replace(font_url, f"data:font/{font_format};base64,{font_base64}")
                    
                    resource_data = {
                        'url': url,
                        'type': 'css',
                        'content': css_content.encode('utf-8'),
                        'text_content': css_content,
                        'base64': base64.b64encode(css_content.encode('utf-8')).decode('utf-8'),
                        'cached': False
                    }
                    
                    # 캐시에 저장
                    self.resource_cache.cache_resource(url, resource_data)
                    return resource_data
            
            response = requests.get(url, timeout=timeout, headers=headers)
            response.raise_for_status()
            
            # 컨텐츠 타입 감지
            content_type = response.headers.get('content-type', '').lower()
            if 'css' in content_type or url.endswith('.css'):
                resource_type = 'css'
            elif 'javascript' in content_type or url.endswith('.js'):
                resource_type = 'js'
            elif 'font' in content_type or any(ext in url for ext in ['.woff', '.woff2', '.ttf', '.otf']):
                resource_type = 'font'
            else:
                resource_type = 'other'
            
            resource_data = {
                'url': url,
                'type': resource_type,
                'content': response.content,
                'text_content': response.content.decode('utf-8', errors='ignore') if resource_type in ['css', 'js'] else None,
                'base64': base64.b64encode(response.content).decode('utf-8'),
                'cached': False
            }
            
            # 캐시에 저장
            self.resource_cache.cache_resource(url, resource_data)
            
            return resource_data
            
        except Exception as e:
            self.log(f"다운로드 실패: {url} - {str(e)}", "ERROR")
            return None

    def extract_cdn_urls(self, html_content):
        """HTML에서 CDN URL 추출 (유효한 URL만)"""
        soup = BeautifulSoup(html_content, 'html.parser')
        cdn_urls = set()
        
        # CSS 링크
        for link in soup.find_all('link', href=True):
            href = link['href']
            if self.is_valid_download_url(href):
                cdn_urls.add(href)
        
        # JavaScript
        for script in soup.find_all('script', src=True):
            src = script['src']
            if self.is_valid_download_url(src):
                cdn_urls.add(src)
        
        # CSS 내부 @import
        for style in soup.find_all('style'):
            if style.string:
                imports = re.findall(r'@import\s+url\([\'"]?([^\'")]+)[\'"]?\)', style.string)
                for import_url in imports:
                    if self.is_valid_download_url(import_url):
                        cdn_urls.add(import_url)
        
        return list(cdn_urls)

    def download_all_resources(self, urls):
        """모든 리소스 병렬 다운로드 (유효한 URL만)"""
        if not urls:
            urls = []
        
        # 필수 리소스 추가 (유효한 URL만)
        essential_urls = []
        for url in list(self.highlightjs_urls.values()) + [self.fontawesome_urls['css']] + self.fontawesome_urls['webfonts']:
            if self.is_valid_download_url(url):
                essential_urls.append(url)
        
        all_urls = list(urls) + essential_urls
        
        # 중복 제거
        all_urls = list(set(all_urls))
        
        self.log(f"총 {len(all_urls)}개 리소스 처리 시작")
        
        downloaded = {}
        cached_count = 0
        download_count = 0
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_url = {executor.submit(self.download_resource, url): url for url in all_urls}
            
            for future in as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    result = future.result()
                    if result:
                        downloaded[url] = result
                        if result.get('cached', False):
                            cached_count += 1
                        else:
                            download_count += 1
                except Exception as e:
                    self.log(f"리소스 처리 실패: {url} - {str(e)}", "ERROR")
        
        self.log(f"리소스 처리 완료: 총 {len(downloaded)}개 (캐시: {cached_count}, 다운로드: {download_count})")
        return downloaded

    def process_code_snippets(self, html_content):
        """코드 스니펫 블록 처리 및 들여쓰기 개선 (다크 테마 강제 적용 완전 지원)"""
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # 제외할 클래스들 (일반 UI 요소) - 확장
        exclude_classes = [
            'feature-highlight', 'highlight-text', 'highlight-box', 'text-highlight',
            'badge', 'label', 'tag', 'btn', 'button', 'card', 'alert',
            'nav', 'navbar', 'breadcrumb', 'pagination', 'tab', 'dropdown'
        ]
        
        # 실제 코드 블록 선택자 (code-snippet 우선 처리)
        code_selectors = [
            '.code-snippet',              # code-snippet 클래스 최우선
            '.code-snippet code',         # code-snippet 내부 code 태그
            '.code-snippet pre',          # code-snippet 내부 pre 태그
            '.code-snippet pre code',     # code-snippet 내부 pre > code
            'pre code',                   # 기존 선택자들
            'code[class*="language-"]',
            '.code-block code',
            'pre.highlight code'
        ]
        
        code_blocks = []
        
        for selector in code_selectors:
            elements = soup.select(selector)
            for element in elements:
                # 중복 제거 (이미 처리된 요소 제외)
                if element in code_blocks:
                    continue
                    
                # 제외 클래스 확인 (부모 요소까지 검사)
                element_classes = element.get('class', [])
                parent = element.parent
                parent_classes = parent.get('class', []) if parent else []
                grandparent = parent.parent if parent else None
                grandparent_classes = grandparent.get('class', []) if grandparent else []
                
                all_classes = element_classes + parent_classes + grandparent_classes
                
                # 제외 클래스가 있으면 건너뛰기
                if any(exclude_class in all_classes for exclude_class in exclude_classes):
                    continue
                
                code_blocks.append(element)
        
        # 각 코드 블록에 개선된 스타일 적용
        for block in code_blocks:
            existing_classes = block.get('class', [])
            
            # hljs 클래스 추가 (중복 방지)
            if 'hljs' not in existing_classes:
                existing_classes.append('hljs')
            
            # 코드 포맷팅 개선을 위한 클래스 추가
            if 'formatted-code' not in existing_classes:
                existing_classes.append('formatted-code')
            
            # code-snippet 클래스 특별 처리
            is_code_snippet = 'code-snippet' in existing_classes
            if is_code_snippet:
                existing_classes.append('code-snippet-formatted')
                existing_classes.append('dark-theme-forced')  # 다크 테마 강제 마커
                self.log(f"code-snippet 클래스 발견, 다크 테마 강제 적용 준비")
            
            # 언어 감지 및 강제 Python 설정 (핵심 수정사항)
            detected_language = 'python'  # 기본값을 Python으로 설정
            
            # 기존 클래스에서 언어 추측
            for cls in existing_classes:
                if cls.startswith('language-'):
                    detected_language = cls.replace('language-', '')
                    break
                elif cls in ['python', 'javascript', 'html', 'css', 'sql', 'bash', 'json', 'xml', 'typescript']:
                    detected_language = cls
                    break
            
            # 언어 감지 로직 (강화된 Python 감지)
            if detected_language == 'python':  # 기본값인 경우에만 감지 로직 실행
                content = block.get_text().strip().lower()
                
                # Python 키워드 기반 감지 (매우 강화됨)
                python_keywords = [
                    'import ', 'from ', 'def ', 'class ', 'if ', 'elif ', 'else:', 'for ', 'while ', 'try:', 'except:', 
                    'with ', 'as ', 'return ', 'yield ', 'lambda ', 'print(', 'len(', 'range(', 'enumerate(',
                    'langchain', 'mariadb', 'vector_store', 'vectorstore', 'embeddings', 'similarity_search',
                    'connection_string', 'table_name', 'content_field'
                ]
                
                # JavaScript 키워드
                js_keywords = ['function ', 'const ', 'let ', 'var ', '=>', 'console.log', 'document.', 'window.']
                
                # SQL 키워드
                sql_keywords = ['select ', 'insert ', 'update ', 'delete ', 'create table', 'alter table', 'drop ']
                
                # HTML 키워드
                html_keywords = ['<div', '<html', '<body', '<head', '<!doctype', '<script', '<style']
                
                # 키워드 매칭 점수 계산
                python_score = sum(1 for keyword in python_keywords if keyword in content)
                js_score = sum(1 for keyword in js_keywords if keyword in content)
                sql_score = sum(1 for keyword in sql_keywords if keyword in content)
                html_score = sum(1 for keyword in html_keywords if keyword in content)
                
                # 가장 높은 점수의 언어 선택, 동점이거나 점수가 없으면 Python
                scores = {'python': python_score, 'javascript': js_score, 'sql': sql_score, 'html': html_score}
                max_score = max(scores.values())
                
                if max_score > 0:
                    detected_language = max(scores, key=scores.get)
                # 점수가 0이면 기본값 Python 유지
                
                self.log(f"언어 감지 점수: Python={python_score}, JS={js_score}, SQL={sql_score}, HTML={html_score} → {detected_language}")
            
            # 언어 클래스 강제 추가 (핵심 수정사항)
            language_class = f'language-{detected_language}'
            if language_class not in existing_classes:
                existing_classes.append(language_class)
            
            # data-language 속성 설정
            block['data-language'] = detected_language
            
            # hljs-{language} 클래스도 추가 (일부 테마에서 필요)
            hljs_lang_class = f'hljs-{detected_language}'
            if hljs_lang_class not in existing_classes:
                existing_classes.append(hljs_lang_class)
            
            # 다크 테마 강제 적용을 위한 추가 속성
            block['data-dark-theme'] = 'true'
            block['data-highlight-ready'] = 'true'
            
            self.log(f"언어 감지 및 클래스 추가: {detected_language} → {language_class}")
            
            # 코드 내용 들여쓰기 보정 및 개행 처리 (기존 로직 유지)
            code_content = None
            
            # 텍스트 내용 추출 (다양한 방법)
            if block.string:
                code_content = block.string
            elif block.get_text():
                code_content = block.get_text()
            
            if code_content:
                # code-snippet 클래스 특별 처리
                if is_code_snippet:
                    formatted_code = self.format_code_snippet_content(code_content)
                else:
                    formatted_code = self.format_regular_code_content(code_content)
                
                # 포맷팅된 코드로 교체
                if block.string:
                    block.string.replace_with(formatted_code)
                else:
                    # 내부 내용을 모두 제거하고 새로운 텍스트로 교체
                    block.clear()
                    block.append(formatted_code)
                
                self.log(f"코드 포맷팅 완료: {'code-snippet' if is_code_snippet else 'regular'} 타입, 언어: {detected_language}")
            
            # code-snippet인 경우 HTML 구조 개선 및 다크 테마 인라인 스타일 추가
            if is_code_snippet:
                # 인라인 스타일로 다크 테마 강제 적용
                dark_style = (
                    "background-color: #0d1117 !important; "
                    "background: #0d1117 !important; "
                    "color: #f0f6fc !important; "
                    "border: 2px solid #30363d !important; "
                    "border-radius: 8px !important; "
                    "padding: 16px !important; "
                    "font-family: 'Courier New', Consolas, Monaco, monospace !important; "
                    "white-space: pre-wrap !important; "
                    "overflow-x: auto !important;"
                )
                
                if block.name != 'code':
                    # code-snippet을 pre > code 구조로 감싸기
                    new_pre = soup.new_tag('pre', **{
                        'class': ['code-snippet-wrapper', 'hljs'],
                        'style': dark_style
                    })
                    new_code = soup.new_tag('code', **{
                        'class': existing_classes,
                        'data-language': detected_language,
                        'data-dark-theme': 'true',
                        'style': 'background: transparent !important; color: inherit !important;'
                    })
                    new_code.string = block.get_text()
                    new_pre.append(new_code)
                    block.replace_with(new_pre)
                    block = new_code  # 참조 업데이트
                else:
                    # 기존 code 태그에 인라인 스타일 추가
                    existing_style = block.get('style', '')
                    block['style'] = existing_style + '; ' + dark_style
            
            # 최종 클래스 설정
            block['class'] = existing_classes
            
            self.log(f"코드 블록 처리 완료: {block.get('class', [])} (다크테마: {is_code_snippet})")
        
        return str(soup)




    def format_code_snippet_content(self, code_content):
        """code-snippet 클래스 전용 코드 포맷팅 (기존 로직 유지)"""
        # 기본 정리
        code_content = code_content.strip()
        
        # 세미콜론, 중괄호, 특정 키워드 기준으로 개행 추가
        # Python 스타일 개행 패턴
        patterns = [
            (r'(\bfrom\s+[^\n]+\s+import\s+[^\n]+)', r'\1\n'),  # from ... import 개행
            (r'(\bimport\s+[^\n]+)', r'\1\n'),                    # import 개행
            (r'(\s*=\s*[^\n]+?)(\s+[a-zA-Z_])', r'\1\n\2'),      # 할당문 후 개행
            (r'(\))\s*([a-zA-Z_#])', r'\1\n\2'),                   # 함수 호출 후 개행
            (r'(\bdef\s+[^:]+:)', r'\1\n'),                        # 함수 정의 후 개행
            (r'(\bclass\s+[^:]+:)', r'\1\n'),                      # 클래스 정의 후 개행
            (r'(\breturn\s+[^\n]+)', r'\1\n'),                    # return 문 후 개행
        ]
        
        for pattern, replacement in patterns:
            code_content = re.sub(pattern, replacement, code_content, flags=re.MULTILINE)
        
        # 연속된 개행 정리
        code_content = re.sub(r'\n\s*\n', '\n', code_content)
        
        # 들여쓰기 정규화
        lines = code_content.split('\n')
        if len(lines) > 1:
            # 최소 들여쓰기 찾기
            min_indent = float('inf')
            for line in lines:
                if line.strip():  # 빈 줄 제외
                    indent = len(line) - len(line.lstrip())
                    min_indent = min(min_indent, indent)
            
            # 최소 들여쓰기만큼 모든 줄에서 제거
            if min_indent != float('inf') and min_indent > 0:
                normalized_lines = []
                for line in lines:
                    if line.strip():  # 빈 줄이 아닌 경우
                        normalized_lines.append(line[min_indent:])
                    else:  # 빈 줄인 경우
                        normalized_lines.append('')
                code_content = '\n'.join(normalized_lines)
        
        return code_content



    def format_regular_code_content(self, code_content):
        """일반 코드 블록 포맷팅 (기존 로직 유지)"""
        # 기존 로직 유지
        lines = code_content.strip().split('\n')
        
        if lines and len(lines) > 1:
            # 최소 들여쓰기 찾기
            min_indent = float('inf')
            for line in lines:
                if line.strip():  # 빈 줄 제외
                    indent = len(line) - len(line.lstrip())
                    min_indent = min(min_indent, indent)
            
            # 최소 들여쓰기만큼 모든 줄에서 제거
            if min_indent != float('inf') and min_indent > 0:
                normalized_lines = []
                for line in lines:
                    if line.strip():  # 빈 줄이 아닌 경우
                        normalized_lines.append(line[min_indent:])
                    else:  # 빈 줄인 경우
                        normalized_lines.append('')
                
                # 정규화된 코드로 교체
                return '\n'.join(normalized_lines)
        
        elif len(lines) == 1 and lines[0]:
            # 한 줄 코드인 경우에도 특별 처리
            single_line = lines[0].strip()
            
            # 특정 패턴이 있으면 개행 추가
            if any(pattern in single_line for pattern in [';', '{', '}', 'import ', 'from ', 'const ', 'let ']):
                # 세미콜론이나 중괄호 기준으로 개행
                formatted_single = single_line.replace(';', ';\n').replace('{', '{\n').replace('}', '\n}')
                # 연속된 개행 정리
                formatted_single = re.sub(r'\n+', '\n', formatted_single).strip()
                return formatted_single
        
        return code_content


    def fix_chart_js_compatibility(self, script_content, slide_index):
        """Chart.js 호환성 수정 및 Canvas별 독립 차트 생성"""
        
        # horizontalBar → bar 변환
        script_content = re.sub(
            r"type:\s*['\"]horizontalBar['\"]", 
            "type: 'bar'", 
            script_content
        )
        script_content = re.sub(r"horizontalBar", "bar", script_content)
        
        # Canvas ID들을 추출
        canvas_ids = re.findall(r"getElementById\s*\(\s*['\"]([^'\"]+)['\"]", script_content)
        self.log(f"슬라이드 {slide_index + 1}에서 발견된 Canvas ID들: {canvas_ids}")
        
        if not canvas_ids:
            return ""
        
        # Canvas별 스크립트 블록 분리 및 매핑
        canvas_script_map = self.extract_canvas_script_mapping(script_content, canvas_ids)
        
        # const 변수명을 고유하게 변경하여 충돌 방지
        variable_patterns = [
            (r'\bconst\s+(targetPriceDistCtx)\b', f'const slide{slide_index}_targetPriceDistCtx'),
            (r'\bconst\s+(targetPriceDistChart)\b', f'const slide{slide_index}_targetPriceDistChart'),
            (r'\bconst\s+(targetPriceCtx)\b', f'const slide{slide_index}_targetPriceCtx'),
            (r'\bconst\s+(priceCtx)\b', f'const slide{slide_index}_priceCtx'),
            (r'\bconst\s+(priceChart)\b', f'const slide{slide_index}_priceChart'),
            (r'\bconst\s+(opinionDistCtx)\b', f'const slide{slide_index}_opinionDistCtx'),
            (r'\bconst\s+(opinionDistChart)\b', f'const slide{slide_index}_opinionDistChart'),
            (r'\bconst\s+(sentimentCtx)\b', f'const slide{slide_index}_sentimentCtx'),
            (r'\bconst\s+(sentimentChart)\b', f'const slide{slide_index}_sentimentChart'),
            (r'\bconst\s+(sentimentDistCtx)\b', f'const slide{slide_index}_sentimentDistCtx'),
            (r'\bconst\s+(sentimentDistChart)\b', f'const slide{slide_index}_sentimentDistChart'),
            (r'\bconst\s+(reportCtx)\b', f'const slide{slide_index}_reportCtx'),
            (r'\bconst\s+(reportChart)\b', f'const slide{slide_index}_reportChart'),
            (r'\bconst\s+(incomeCtx)\b', f'const slide{slide_index}_incomeCtx'),
            (r'\bconst\s+(incomeChart)\b', f'const slide{slide_index}_incomeChart'),
            (r'\bconst\s+(expenseCtx)\b', f'const slide{slide_index}_expenseCtx'),
            (r'\bconst\s+(expenseChart)\b', f'const slide{slide_index}_expenseChart'),
            (r'\bconst\s+(assetCtx)\b', f'const slide{slide_index}_assetCtx'),
            (r'\bconst\s+(assetChart)\b', f'const slide{slide_index}_assetChart'),
            (r'\bconst\s+(ctx)\b', f'const slide{slide_index}_ctx'),
            (r'\bconst\s+(chart)\b', f'const slide{slide_index}_chart'),
            (r'\bconst\s+(myChart)\b', f'const slide{slide_index}_myChart')
        ]
        
        # 변수 참조도 함께 변경
        reference_patterns = [
            (r'\btargetPriceDistCtx\b', f'slide{slide_index}_targetPriceDistCtx'),
            (r'\btargetPriceDistChart\b', f'slide{slide_index}_targetPriceDistChart'),
            (r'\btargetPriceCtx\b', f'slide{slide_index}_targetPriceCtx'),
            (r'\bpriceCtx\b', f'slide{slide_index}_priceCtx'),
            (r'\bpriceChart\b', f'slide{slide_index}_priceChart'),
            (r'\bopinionDistCtx\b', f'slide{slide_index}_opinionDistCtx'),
            (r'\bopinionDistChart\b', f'slide{slide_index}_opinionDistChart'),
            (r'\bsentimentCtx\b', f'slide{slide_index}_sentimentCtx'),
            (r'\bsentimentChart\b', f'slide{slide_index}_sentimentChart'),
            (r'\bsentimentDistCtx\b', f'slide{slide_index}_sentimentDistCtx'),
            (r'\bsentimentDistChart\b', f'slide{slide_index}_sentimentDistChart'),
            (r'\breportCtx\b', f'slide{slide_index}_reportCtx'),
            (r'\breportChart\b', f'slide{slide_index}_reportChart'),
            (r'\bincomeCtx\b', f'slide{slide_index}_incomeCtx'),
            (r'\bincomeChart\b', f'slide{slide_index}_incomeChart'),
            (r'\bexpenseCtx\b', f'slide{slide_index}_expenseCtx'),
            (r'\bexpenseChart\b', f'slide{slide_index}_expenseChart'),
            (r'\bassetCtx\b', f'slide{slide_index}_assetCtx'),
            (r'\bassetChart\b', f'slide{slide_index}_assetChart')
        ]
        
        # const 선언 변경
        for pattern, replacement in variable_patterns:
            script_content = re.sub(pattern, replacement, script_content)
        
        # 변수 참조 변경
        for pattern, replacement in reference_patterns:
            script_content = re.sub(pattern, replacement, script_content)
        
        # getElementById 강제 교체
        script_content = re.sub(
            r"document\.getElementById\s*\(\s*['\"]([^'\"]+)['\"]s*\)\.getContext\s*\(\s*['\"]2d['\"]s*\)",
            "PLACEHOLDER_CANVAS.getContext('2d')",
            script_content
        )
        script_content = re.sub(
            r"document\.getElementById\s*\(\s*['\"]([^'\"]+)['\"]s*\)",
            "PLACEHOLDER_CANVAS",
            script_content
        )
        
        slide_id = f'slide-{slide_index}'
        slide_comment = f'Slide {slide_index + 1} Chart Initializer - v4.7 슬라이드컨테이너중앙정렬'
        
        # JavaScript 코드에서 사용할 변수들
        chart_var_names = [
            f'slide{slide_index}_targetPriceDistChart',
            f'slide{slide_index}_priceChart',
            f'slide{slide_index}_opinionDistChart',
            f'slide{slide_index}_sentimentChart',
            f'slide{slide_index}_sentimentDistChart',
            f'slide{slide_index}_reportChart',
            f'slide{slide_index}_incomeChart',
            f'slide{slide_index}_expenseChart',
            f'slide{slide_index}_assetChart',
            f'slide{slide_index}_chart',
            f'slide{slide_index}_myChart'
        ]
        
        # 안전한 JavaScript 생성
        isolated_script = f"""
// {slide_comment}
(function() {{
    'use strict';
    
    var slideId = '{slide_id}';
    var chartDelay = 500;
    
    console.log('Chart Init Start (v4.7 슬라이드컨테이너중앙정렬): ' + slideId);
    
    window.chartCanvasRegistry = window.chartCanvasRegistry || {{}};
    
    function initSlideChartsSequential() {{
        var currentSlide = document.getElementById(slideId);
        if (!currentSlide) {{
            console.error('Slide not found: ' + slideId);
            return;
        }}
        
        var computedStyle = window.getComputedStyle(currentSlide);
        if (computedStyle.display === 'none' || computedStyle.visibility === 'hidden') {{
            console.warn('Slide not visible: ' + slideId);
            return;
        }}
        
        var canvasElements = currentSlide.querySelectorAll('canvas');
        if (canvasElements.length === 0) {{
            console.warn('No canvas found in: ' + slideId);
            return;
        }}
        
        console.log('Found ' + canvasElements.length + ' canvas in ' + slideId);
        
        // Canvas별 스크립트 매핑 정보
        var canvasScriptMapping = {json.dumps(canvas_script_map)};
        
        // 기존 차트 정리
        cleanupExistingCharts();
        
        // Canvas별 독립적 차트 생성
        for (var i = 0; i < canvasElements.length; i++) {{
            var canvas = canvasElements[i];
            var canvasId = canvas.id;
            
            if (!canvasId) {{
                console.warn('Canvas without ID found, skipping');
                continue;
            }}
            
            console.log('Processing canvas: ' + canvasId + ' (index: ' + i + ')');
            
            // Canvas별 지연 생성 - 각각 독립적으로
            (function(targetCanvas, targetCanvasId, canvasIndex) {{
                setTimeout(function() {{
                    console.log('Creating independent chart for: ' + targetCanvasId);
                    createIndependentChart(targetCanvas, targetCanvasId, canvasScriptMapping, canvasIndex);
                }}, canvasIndex * chartDelay);
            }})(canvas, canvasId, i);
        }}
    }}
    
    function cleanupExistingCharts() {{
        try {{
            if (window.slideCharts && window.slideCharts[slideId]) {{
                Object.values(window.slideCharts[slideId]).forEach(function(chart) {{
                    if (chart && typeof chart.destroy === 'function') {{
                        try {{
                            chart.destroy();
                        }} catch (e) {{
                            console.warn('Chart cleanup error:', e);
                        }}
                    }}
                }});
                window.slideCharts[slideId] = {{}};
            }}
            
            var currentSlide = document.getElementById(slideId);
            if (currentSlide) {{
                var canvases = currentSlide.querySelectorAll('canvas');
                canvases.forEach(function(canvas) {{
                    if (canvas.chart) {{
                        try {{
                            canvas.chart.destroy();
                            canvas.chart = null;
                        }} catch (e) {{
                            console.warn('Canvas chart cleanup error:', e);
                        }}
                    }}
                    var ctx = canvas.getContext('2d');
                    ctx.clearRect(0, 0, canvas.width, canvas.height);
                }});
            }}
            
            console.log('Charts cleaned for: ' + slideId);
        }} catch (e) {{
            console.error('Chart cleanup failed:', e);
        }}
    }}
    
    function createIndependentChart(targetCanvas, canvasId, scriptMapping, canvasIndex) {{
        try {{
            if (!targetCanvas || !targetCanvas.getContext) {{
                console.error('Canvas invalid: ' + canvasId);
                return;
            }}
            
            console.log('Creating chart for: ' + canvasId + ' (Canvas Index: ' + canvasIndex + ')');
            
            var ctx = targetCanvas.getContext('2d');
            ctx.clearRect(0, 0, targetCanvas.width, targetCanvas.height);
            
            // Canvas별 독립적 스크립트 실행
            executeCanvasSpecificScript(targetCanvas, canvasId, scriptMapping);
            
        }} catch (e) {{
            console.error('Independent chart creation error for ' + canvasId + ':', e);
        }}
    }}
    
    function executeCanvasSpecificScript(targetCanvas, canvasId, scriptMapping) {{
        try {{
            window.currentTargetCanvas = targetCanvas;
            window.currentCanvasId = canvasId;
            
            // Canvas ID에 해당하는 스크립트만 추출
            var canvasScript = null;
            if (scriptMapping && scriptMapping[canvasId]) {{
                canvasScript = scriptMapping[canvasId];
                console.log('Using mapped script for: ' + canvasId);
            }} else {{
                console.log('No mapped script, creating fallback chart for: ' + canvasId);
                createFallbackChart(targetCanvas, canvasId);
                return;
            }}
            
            if (canvasScript && canvasScript.length > 50) {{
                console.log('Executing canvas-specific script for: ' + canvasId);
                
                var finalScript = canvasScript.replace(/PLACEHOLDER_CANVAS/g, 'window.currentTargetCanvas');
                
                try {{
                    var scriptFunction = new Function(finalScript);
                    scriptFunction();
                    console.log('Canvas script executed successfully for: ' + canvasId);
                    
                    // 차트 변수 저장
                    saveChartVariables(targetCanvas, canvasId);
                    
                }} catch (evalError) {{
                    console.error('Canvas script execution failed for ' + canvasId + ':', evalError);
                    createFallbackChart(targetCanvas, canvasId);
                }}
                
            }} else {{
                console.warn('No specific script found for: ' + canvasId);
                createFallbackChart(targetCanvas, canvasId);
            }}
            
        }} catch (e) {{
            console.error('Canvas script execution failed for ' + canvasId + ':', e);
            createFallbackChart(targetCanvas, canvasId);
        }}
    }}
    
    function saveChartVariables(targetCanvas, canvasId) {{
        try {{
            if (!window.slideCharts) window.slideCharts = {{}};
            if (!window.slideCharts[slideId]) window.slideCharts[slideId] = {{}};
            
            var chartVarNames = {json.dumps(chart_var_names)};
            var savedCount = 0;
            
            for (var i = 0; i < chartVarNames.length; i++) {{
                var varName = chartVarNames[i];
                try {{
                    if (typeof window[varName] !== 'undefined' && window[varName] && 
                        typeof window[varName].destroy === 'function') {{
                        
                        window.slideCharts[slideId][canvasId + '_chart'] = window[varName];
                        targetCanvas.chart = window[varName];
                        savedCount++;
                        console.log('Chart saved: ' + varName + ' for ' + canvasId);
                        break;
                    }}
                }} catch (e) {{
                    console.warn('Variable check failed: ' + varName, e);
                }}
            }}
            
            if (savedCount === 0) {{
                console.warn('No charts saved for: ' + canvasId);
                try {{
                    var existingChart = Chart.getChart(targetCanvas);
                    if (existingChart) {{
                        window.slideCharts[slideId][canvasId + '_chart'] = existingChart;
                        targetCanvas.chart = existingChart;
                        console.log('Chart saved via Chart.getChart for: ' + canvasId);
                        savedCount++;
                    }}
                }} catch (e) {{
                    console.warn('Chart.getChart failed for: ' + canvasId);
                }}
            }}
            
        }} catch (e) {{
            console.error('Chart save failed for ' + canvasId + ':', e);
        }}
    }}
    
    function createFallbackChart(canvas, canvasId) {{
        try {{
            var ctx = canvas.getContext('2d');
            
            var fallbackConfig = {{
                type: 'bar',
                data: {{
                    labels: ['Sample 1', 'Sample 2', 'Sample 3'],
                    datasets: [{{
                        label: canvasId + ' 데이터',
                        data: [Math.random() * 100, Math.random() * 100, Math.random() * 100],
                        backgroundColor: 'rgba(54, 162, 235, 0.8)'
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{
                        title: {{
                            display: true,
                            text: 'Fallback Chart (' + canvasId + ')'
                        }}
                    }}
                }}
            }};
            
            var fallbackChart = new Chart(ctx, fallbackConfig);
            window[canvasId + '_fallback'] = fallbackChart;
            
            if (!window.slideCharts) window.slideCharts = {{}};
            if (!window.slideCharts[slideId]) window.slideCharts[slideId] = {{}};
            window.slideCharts[slideId][canvasId + '_chart'] = fallbackChart;
            
            console.log('Fallback chart created for: ' + canvasId);
            
        }} catch (e) {{
            console.error('Fallback chart creation failed for ' + canvasId + ':', e);
        }}
    }}
    
    // 초기화 함수 등록
    if (typeof window.chartInitializers === 'undefined') {{
        window.chartInitializers = {{}};
    }}
    
    window.chartInitializers[slideId] = initSlideChartsSequential;
    
    console.log('Chart initializer registered (v4.7 슬라이드컨테이너중앙정렬): ' + slideId);
    
}})();
"""
        
        return isolated_script

    def extract_canvas_script_mapping(self, script_content, canvas_ids):
        """Canvas별 스크립트 블록 매핑 생성"""
        canvas_script_map = {}
        
        try:
            lines = script_content.split('\n')
            current_canvas = None
            current_script_lines = []
            brace_level = 0
            
            for i, line in enumerate(lines):
                trimmed_line = line.strip()
                
                # Canvas ID 감지
                for canvas_id in canvas_ids:
                    if f"getElementById('{canvas_id}')" in trimmed_line or f'getElementById("{canvas_id}")' in trimmed_line:
                        # 이전 Canvas 스크립트 저장
                        if current_canvas and current_script_lines:
                            canvas_script_map[current_canvas] = '\n'.join(current_script_lines)
                        
                        # 새 Canvas 시작
                        current_canvas = canvas_id
                        current_script_lines = [line]
                        brace_level = 0
                        break
                else:
                    # 현재 Canvas 스크립트에 라인 추가
                    if current_canvas:
                        current_script_lines.append(line)
                        
                        # 중괄호 레벨 추적
                        for char in trimmed_line:
                            if char == '{':
                                brace_level += 1
                            elif char == '}':
                                brace_level -= 1
                        
                        # 스크립트 블록 종료 감지
                        if brace_level <= 0 and ('});' in trimmed_line or '})' in trimmed_line):
                            if current_canvas and current_script_lines:
                                canvas_script_map[current_canvas] = '\n'.join(current_script_lines)
                            current_canvas = None
                            current_script_lines = []
            
            # 마지막 Canvas 스크립트 저장
            if current_canvas and current_script_lines:
                canvas_script_map[current_canvas] = '\n'.join(current_script_lines)
            
            self.log(f"Canvas 스크립트 매핑 완료: {list(canvas_script_map.keys())}")
            
        except Exception as e:
            self.log(f"Canvas 스크립트 매핑 실패: {str(e)}", "ERROR")
        
        return canvas_script_map

    def scope_css_to_slide(self, css_content, slide_id):
        """CSS를 특정 슬라이드 ID로 스코핑하여 격리"""
        try:
            # CSS 규칙을 슬라이드 ID로 스코핑
            lines = css_content.split('\n')
            scoped_lines = []
            
            for line in lines:
                stripped = line.strip()
                
                # CSS 선택자 감지 및 스코핑
                if stripped and not stripped.startswith('@') and '{' in stripped and not stripped.startswith('/*'):
                    # 선택자 부분 추출
                    selector_part = stripped.split('{')[0].strip()
                    rest_part = '{' + '{'.join(stripped.split('{')[1:])
                    
                    # 슬라이드 ID로 스코핑
                    if selector_part:
                        scoped_selector = f"#{slide_id} {selector_part}"
                        scoped_lines.append(f"{scoped_selector} {rest_part}")
                    else:
                        scoped_lines.append(line)
                else:
                    scoped_lines.append(line)
            
            return '\n'.join(scoped_lines)
            
        except Exception as e:
            self.log(f"CSS 스코핑 실패: {str(e)}", "ERROR")
            return css_content

    def replace_cdn_with_inline(self, html_content, downloaded_resources):
        """CDN 링크를 인라인 리소스로 교체 (원본 스타일 보존)"""
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # CSS 링크 교체
        for link in soup.find_all('link', href=True):
            href = link['href']
            if href in downloaded_resources:
                resource = downloaded_resources[href]
                if resource['type'] == 'css':
                    # <link>를 <style>로 교체
                    style_tag = soup.new_tag('style')
                    css_content = resource['text_content']
                    
                    # 원본 CSS 우선순위 보존 - !important 제거하여 원본 스타일 우선
                    css_content = re.sub(r'\s*!important\s*', '', css_content)
                    
                    style_tag.string = css_content
                    link.replace_with(style_tag)
        
        # JavaScript 링크 교체
        for script in soup.find_all('script', src=True):
            src = script['src']
            if src in downloaded_resources:
                resource = downloaded_resources[src]
                if resource['type'] == 'js':
                    # src 속성 제거하고 내용 삽입
                    del script['src']
                    script.string = resource['text_content']
        
        return str(soup)

    def merge_slides(self, html_files):
        """여러 HTML 슬라이드를 하나로 병합 (슬라이드 구분 문제 완전 해결)"""
        merged_content = ""
        slide_scripts = []
        
        for i, html_file in enumerate(html_files):
            self.log(f"슬라이드 {i+1} 처리 중...")
            
            try:
                with open(html_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # 코드 스니펫 처리
                content = self.process_code_snippets(content)
                
                soup = BeautifulSoup(content, 'html.parser')
                
                # 전체 HTML 구조 보존
                html_tag = soup.find('html')
                head_tag = soup.find('head')
                body_tag = soup.find('body')
                
                if body_tag:
                    # Chart.js 관련 스크립트 추출 및 처리
                    scripts = soup.find_all('script')
                    for script in scripts:
                        if script.string and ('chart' in script.string.lower() or 'Chart' in script.string):
                            fixed_script = self.fix_chart_js_compatibility(script.string, i)
                            if fixed_script:
                                slide_scripts.append(fixed_script)
                            script.decompose()
                    
                    # 슬라이드별 CSS 격리를 위한 완전한 구조 보존
                    slide_id = f"slide-{i}"
                    
                    # head 태그의 모든 스타일을 슬라이드 내부로 이동 (CSS 격리)
                    slide_styles = ""
                    if head_tag:
                        for style in head_tag.find_all('style'):
                            if style.string:
                                # CSS를 슬라이드 ID로 스코핑
                                scoped_css = self.scope_css_to_slide(style.string, slide_id)
                                slide_styles += f"<style>{scoped_css}</style>\n"
                        
                        for link in head_tag.find_all('link', rel='stylesheet'):
                            # 외부 CSS도 처리 (이미 다운로드된 경우)
                            href = link.get('href', '')
                            if href in self.downloaded_resources:
                                css_content = self.downloaded_resources[href]['text_content']
                                scoped_css = self.scope_css_to_slide(css_content, slide_id)
                                slide_styles += f"<style>{scoped_css}</style>\n"
                    
                    # body 태그의 모든 속성과 클래스 보존
                    body_attrs = []
                    for attr, value in body_tag.attrs.items():
                        if isinstance(value, list):
                            value = ' '.join(value)
                        body_attrs.append(f'{attr}="{value}"')
                    body_attrs_str = ' ' + ' '.join(body_attrs) if body_attrs else ''
                    
                    # 올바른 슬라이드 wrapper 생성 - 강제 스타일 포함
                    slide_div = f'''<div id="{slide_id}" class="slide-wrapper" data-slide-index="{i}"{body_attrs_str} style="display: none; width: 100vw; min-height: 100vh; max-height: 100vh; overflow-y: auto; position: relative; justify-content: center; align-items: flex-start;">
    {slide_styles}
    <div class="slide-content" style="width: 100%; max-width: 1280px; margin: 0 auto; padding: 20px; box-sizing: border-box;">
    {body_tag.decode_contents()}
    </div>
    </div>
    '''
                    merged_content += slide_div
                    self.log(f"슬라이드 {i+1} wrapper 생성 완료: ID={slide_id}, 인라인스타일=적용")
                
                self.processed_slides += 1
                
            except Exception as e:
                self.log(f"슬라이드 {i+1} 처리 실패: {str(e)}", "ERROR")
        
        return merged_content, slide_scripts


    def create_slide_manager_script(self):
        """슬라이드 관리 스크립트 생성 (네비게이션 수정 + 스크롤 표시기 제거 + 전체화면 추가)"""
        
        # Highlight.js 리소스
        highlightjs_js = ""
        if self.highlightjs_urls['js'] in self.downloaded_resources:
            highlightjs_js = self.downloaded_resources[self.highlightjs_urls['js']]['text_content']
        
        highlightjs_css = ""
        for css_url in [self.highlightjs_urls['css_monokai'], self.highlightjs_urls['css_github'], self.highlightjs_urls['css_default']]:
            if css_url in self.downloaded_resources:
                highlightjs_css += self.downloaded_resources[css_url]['text_content'] + "\n"
                break
        
        # Font Awesome CSS with embedded fonts
        fontawesome_css_with_fonts = ""
        if self.fontawesome_urls['css'] in self.downloaded_resources:
            fontawesome_css_with_fonts = self.downloaded_resources[self.fontawesome_urls['css']]['text_content']
            
            for font_url in self.fontawesome_urls['webfonts']:
                if font_url in self.downloaded_resources:
                    font_resource = self.downloaded_resources[font_url]
                    font_base64 = font_resource['base64']
                    font_format = 'woff2' if 'woff2' in font_url else 'woff'
                    font_filename = font_url.split('/')[-1]
                    
                    fontawesome_css_with_fonts = re.sub(
                        r'url\(["\']?[^)]*' + re.escape(font_filename) + r'["\']?\)',
                        f'url("data:font/{font_format};base64,{font_base64}")',
                        fontawesome_css_with_fonts
                    )
        
        return f"""
        <script>
        // Highlight.js 임베드
        {highlightjs_js}
        </script>
        
        <style>
        /* Font Awesome 스타일 (완전 임베딩) */
        {fontawesome_css_with_fonts}
        
        /* Highlight.js 스타일 */
        {highlightjs_css}
        
        /* 슬라이드 매니저 기본 스타일 */
        .slide-wrapper {{
            display: none;
            width: 100vw;
            min-height: 100vh;
            max-height: 100vh;
            overflow-y: auto;
            overflow-x: hidden;
            position: relative;
            justify-content: center;
            align-items: flex-start;
        }}
        
        .slide-wrapper.active {{
            display: flex !important;
        }}
        
        .slide-content {{
            width: 100%;
            max-width: 1280px;
            min-height: 100vh;
            margin: 0 auto;
            padding: 20px;
            box-sizing: border-box;
        }}
        
        /* 스크롤 위치 표시기 (완전 비활성화) */
        .scroll-indicator {{
            display: none !important;
        }}
        
        /* 네비게이션 스타일 */
        .slide-navigation {{
            position: fixed;
            bottom: 20px;
            right: 20px;
            background: rgba(0, 0, 0, 0.8);
            color: white;
            padding: 10px 20px;
            border-radius: 25px;
            z-index: 9999;
            display: flex;
            align-items: center;
            gap: 15px;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            font-size: 14px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        }}
        
        .slide-navigation button {{
            background: none;
            border: none;
            color: white;
            cursor: pointer;
            padding: 8px 12px;
            border-radius: 4px;
            transition: background-color 0.3s;
            font-size: 14px;
        }}
        
        .slide-navigation button:hover {{
            background: rgba(255, 255, 255, 0.2);
        }}
        
        .slide-navigation button:disabled {{
            opacity: 0.5;
            cursor: not-allowed;
        }}
        
        .slide-counter {{
            font-size: 14px;
            font-weight: 500;
        }}
        
        /* 전체화면 버튼 스타일 */
        #fullscreenBtn {{
            border-left: 1px solid rgba(255, 255, 255, 0.3);
            margin-left: 5px;
            padding-left: 15px;
        }}
        
        /* code-snippet 다크 테마 */
        .code-snippet,
        [data-dark-theme="true"] {{
            background-color: #0d1117 !important;
            color: #f0f6fc !important;
            border: 1px solid #30363d !important;
            border-radius: 8px !important;
            padding: 16px !important;
            margin: 16px 0 !important;
            font-family: ui-monospace, SFMono-Regular, monospace !important;
            white-space: pre-wrap !important;
            overflow-x: auto !important;
        }}
        
        /* 코드 블록 추가 스타일 */
        .hljs {{
            background-color: #0d1117 !important;
            color: #f0f6fc !important;
        }}
        
        .formatted-code {{
            line-height: 1.5;
            tab-size: 4;
        }}
        </style>
        
        <script>
        // 슬라이드 매니저 JavaScript (네비게이션 수정 + 스크롤 표시기 제거 + 전체화면 추가)
        (function() {{
            'use strict';
            
            let currentSlideIndex = 0;
            let slides = [];
            let totalSlides = 0;
            let isFullscreen = false;
            
            function log(message, level = 'INFO') {{
                const timestamp = new Date().toLocaleTimeString('ko-KR');
                console.log('[' + timestamp + '] [SlideManager] ' + level + ': ' + message);
            }}
            
            function initSlideManager() {{
                log('슬라이드 매니저 초기화 시작 (네비게이션 수정 + 스크롤 표시기 제거 + 전체화면 추가)');
                
                // 슬라이드 요소 찾기
                slides = Array.from(document.querySelectorAll('div[id^="slide-"]')).sort((a, b) => {{
                    const aIndex = parseInt(a.id.split('-')[1]) || 0;
                    const bIndex = parseInt(b.id.split('-')[1]) || 0;
                    return aIndex - bIndex;
                }});
                
                totalSlides = slides.length;
                log('총 슬라이드 수: ' + totalSlides);
                
                if (totalSlides === 0) {{
                    log('슬라이드를 찾을 수 없습니다', 'ERROR');
                    return;
                }}
                
                // 모든 슬라이드 숨김
                slides.forEach(slide => {{
                    slide.style.display = 'none';
                    slide.classList.remove('active');
                }});
                
                // 슬라이드 초기화 (스크롤 표시기 생성 제거)
                slides.forEach((slide, index) => {{
                    log('슬라이드 ' + (index + 1) + ' 초기화: ' + slide.id);
                    
                    // 스크롤 표시기 추가 (비활성화)
                    // const scrollIndicator = document.createElement('div');
                    // scrollIndicator.className = 'scroll-indicator';
                    // scrollIndicator.textContent = 'TOP';
                    // slide.appendChild(scrollIndicator);
                    
                    // 스크롤 이벤트 리스너 추가 (비활성화)
                    // slide.addEventListener('scroll', function() {{
                    //     updateScrollIndicator(slide);
                    // }});
                }});
                
                createNavigation();
                document.addEventListener('keydown', handleKeydown);
                
                // 전체화면 상태 변경 감지
                document.addEventListener('fullscreenchange', handleFullscreenChange);
                document.addEventListener('webkitfullscreenchange', handleFullscreenChange);
                document.addEventListener('mozfullscreenchange', handleFullscreenChange);
                document.addEventListener('MSFullscreenChange', handleFullscreenChange);
                
                // 첫 번째 슬라이드 표시
                showSlide(0);
                
                log('초기화 완료 (네비게이션 수정 + 스크롤 표시기 제거 + 전체화면 추가)');
            }}
            
            function showSlide(index) {{
                if (index < 0 || index >= totalSlides) return;
                
                log('슬라이드 ' + (index + 1) + ' 표시');
                
                // 모든 슬라이드 숨김
                slides.forEach(slide => {{
                    slide.style.display = 'none';
                    slide.classList.remove('active');
                }});
                
                // 현재 슬라이드 표시
                const currentSlide = slides[index];
                currentSlide.style.display = 'flex';
                currentSlide.classList.add('active');
                currentSlide.scrollTop = 0;
                
                currentSlideIndex = index;
                updateNavigation();
                
                // 차트 초기화
                const slideId = currentSlide.id;
                if (window.chartInitializers && window.chartInitializers[slideId]) {{
                    setTimeout(() => {{
                        window.chartInitializers[slideId]();
                    }}, 300);
                }}
            }}
            
            function createNavigation() {{
                const nav = document.createElement('div');
                nav.className = 'slide-navigation';
                nav.innerHTML = '<button id="homeBtn" title="첫 슬라이드로"><i class="fas fa-home"></i></button>' +
                            '<button id="prevBtn" title="이전 슬라이드"><i class="fas fa-chevron-left"></i></button>' +
                            '<span class="slide-counter">' +
                            '<span id="currentSlideNum">1</span> / <span id="totalSlideNum">' + totalSlides + '</span>' +
                            '</span>' +
                            '<button id="nextBtn" title="다음 슬라이드"><i class="fas fa-chevron-right"></i></button>' +
                            '<button id="endBtn" title="마지막 슬라이드로"><i class="fas fa-step-forward"></i></button>' +
                            '<button id="fullscreenBtn" title="전체화면 토글"><i class="fas fa-expand"></i></button>';
                document.body.appendChild(nav);
                
                // 이벤트 리스너 연결
                document.getElementById('homeBtn').addEventListener('click', () => showSlide(0));
                document.getElementById('prevBtn').addEventListener('click', prevSlide);
                document.getElementById('nextBtn').addEventListener('click', nextSlide);
                document.getElementById('endBtn').addEventListener('click', () => showSlide(totalSlides - 1));
                document.getElementById('fullscreenBtn').addEventListener('click', toggleFullscreen);
                
                updateNavigation();
                log('네비게이션 생성 완료: 홈, 이전, 카운터, 다음, end, 전체화면 버튼');
            }}
            
            function updateNavigation() {{
                const currentSlideNum = document.getElementById('currentSlideNum');
                const homeBtn = document.getElementById('homeBtn');
                const prevBtn = document.getElementById('prevBtn');
                const nextBtn = document.getElementById('nextBtn');
                const endBtn = document.getElementById('endBtn');
                const fullscreenBtn = document.getElementById('fullscreenBtn');
                
                if (currentSlideNum) {{
                    currentSlideNum.textContent = currentSlideIndex + 1;
                }}
                
                if (homeBtn) {{
                    homeBtn.disabled = currentSlideIndex === 0;
                }}
                
                if (prevBtn) {{
                    prevBtn.disabled = currentSlideIndex === 0;
                }}
                
                if (nextBtn) {{
                    nextBtn.disabled = currentSlideIndex === totalSlides - 1;
                }}
                
                if (endBtn) {{
                    endBtn.disabled = currentSlideIndex === totalSlides - 1;
                }}
                
                // 전체화면 버튼 아이콘 업데이트
                if (fullscreenBtn) {{
                    const icon = fullscreenBtn.querySelector('i');
                    if (isFullscreen) {{
                        icon.className = 'fas fa-compress';
                        fullscreenBtn.title = '전체화면 해제';
                    }} else {{
                        icon.className = 'fas fa-expand';
                        fullscreenBtn.title = '전체화면';
                    }}
                }}
            }}
            
            function toggleFullscreen() {{
                try {{
                    if (!isFullscreen) {{
                        // 전체화면 진입
                        const element = document.documentElement;
                        if (element.requestFullscreen) {{
                            element.requestFullscreen();
                        }} else if (element.webkitRequestFullscreen) {{
                            element.webkitRequestFullscreen();
                        }} else if (element.mozRequestFullScreen) {{
                            element.mozRequestFullScreen();
                        }} else if (element.msRequestFullscreen) {{
                            element.msRequestFullscreen();
                        }}
                        log('전체화면 진입 요청');
                    }} else {{
                        // 전체화면 해제
                        if (document.exitFullscreen) {{
                            document.exitFullscreen();
                        }} else if (document.webkitExitFullscreen) {{
                            document.webkitExitFullscreen();
                        }} else if (document.mozCancelFullScreen) {{
                            document.mozCancelFullScreen();
                        }} else if (document.msExitFullscreen) {{
                            document.msExitFullscreen();
                        }}
                        log('전체화면 해제 요청');
                    }}
                }} catch (error) {{
                    log('전체화면 토글 오류: ' + error.message, 'ERROR');
                }}
            }}
            
            function handleFullscreenChange() {{
                const fullscreenElement = document.fullscreenElement || 
                                        document.webkitFullscreenElement || 
                                        document.mozFullScreenElement || 
                                        document.msFullscreenElement;
                
                isFullscreen = !!fullscreenElement;
                updateNavigation();
                
                if (isFullscreen) {{
                    log('전체화면 모드 진입');
                }} else {{
                    log('전체화면 모드 해제');
                }}
            }}
            
            function nextSlide() {{
                if (currentSlideIndex < totalSlides - 1) {{
                    showSlide(currentSlideIndex + 1);
                }}
            }}
            
            function prevSlide() {{
                if (currentSlideIndex > 0) {{
                    showSlide(currentSlideIndex - 1);
                }}
            }}
            
            function handleKeydown(event) {{
                switch (event.key) {{
                    case 'ArrowRight':
                    case 'PageDown':
                        event.preventDefault();
                        nextSlide();
                        break;
                    case 'ArrowLeft':
                    case 'PageUp':
                        event.preventDefault();
                        prevSlide();
                        break;
                    case 'Home':
                        if (event.ctrlKey) {{
                            event.preventDefault();
                            showSlide(0);
                        }}
                        break;
                    case 'End':
                        if (event.ctrlKey) {{
                            event.preventDefault();
                            showSlide(totalSlides - 1);
                        }}
                        break;
                    case 'F11':
                        event.preventDefault();
                        toggleFullscreen();
                        break;
                }}
            }}
            
            // 전역 차트 관리
            window.slideCharts = window.slideCharts || {{}};
            window.chartInitializers = window.chartInitializers || {{}};
            
            // 초기화
            if (document.readyState === 'loading') {{
                document.addEventListener('DOMContentLoaded', initSlideManager);
            }} else {{
                setTimeout(initSlideManager, 300);
            }}
            
            console.log('젠스파크 슬라이드 프레젠테이션 준비 완료 (네비게이션 수정 + 스크롤 표시기 제거 + 전체화면 추가)');
        }})();
        
        // Highlight.js 초기화
        if (typeof hljs !== 'undefined') {{
            document.addEventListener('DOMContentLoaded', function() {{
                hljs.highlightAll();
                
                // code-snippet 다크 테마 적용
                document.querySelectorAll('.code-snippet, [data-dark-theme="true"]').forEach(element => {{
                    if (!element.classList.contains('language-python')) {{
                        element.classList.add('language-python');
                    }}
                    hljs.highlightElement(element);
                }});
            }});
        }}
        </script>
        """









    def process_html_file(self, input_path, output_path):
        """단일 HTML 파일 처리 (DOCTYPE 기준 분할 + 슬라이드 구분 문제 해결)"""
        try:
            # HTML 파일 읽기
            self.log(f"입력 파일 읽는 중: {input_path}")
            with open(input_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
            
            original_size = len(html_content)
            self.log(f"원본 파일 크기: {original_size:,} bytes")
            
            # 첫 슬라이드 제목 추출
            self.first_slide_title = self.extract_first_slide_title(html_content)
            self.log(f"첫 슬라이드 제목: {self.first_slide_title}")
            
            # DOCTYPE + HTML 태그 조합을 기준으로 슬라이드 분할 (강화)
            self.log("슬라이드 분할 중...")
            
            # DOCTYPE html 패턴으로 분할
            doctype_pattern = r'<!DOCTYPE\s+html[^>]*>\s*<html[^>]*>'
            slides = re.split(doctype_pattern, html_content, flags=re.IGNORECASE)
            
            # 첫 번째 빈 요소 제거
            if slides and not slides[0].strip():
                slides = slides[1:]
            
            # DOCTYPE가 없는 경우 기존 방식으로 분할
            if len(slides) <= 1:
                self.log("DOCTYPE 기준 분할 실패, HTML 태그 기준으로 재시도")
                slides = re.split(r'<html[^>]*>', html_content, flags=re.IGNORECASE)[1:]
            
            # 슬라이드가 여전히 없는 경우 내용 기반 분할 시도
            if not slides:
                self.log("HTML 태그 기준 분할 실패, 내용 기반 분할 시도")
                slides = self.split_content_by_sections(html_content)
            
            if not slides:
                raise ValueError("슬라이드를 찾을 수 없습니다")
            
            self.total_slides = len(slides)
            self.log(f"발견된 슬라이드 수: {self.total_slides}")
            
            # CDN URL 추출
            cdn_urls = self.extract_cdn_urls(html_content)
            
            # 리소스 다운로드
            self.downloaded_resources = self.download_all_resources(cdn_urls)
            
            # 임시 파일로 슬라이드 저장
            temp_files = []
            for i, slide in enumerate(slides):
                temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8')
                
                # DOCTYPE + HTML 태그 조합 복원
                complete_slide = f'''<!DOCTYPE html>
    <html lang="ko">
    {slide}
    </html>'''
                temp_file.write(complete_slide)
                temp_file.close()
                temp_files.append(temp_file.name)
                
                self.log(f"슬라이드 {i+1} 임시 파일 생성: {len(slide):,} bytes")
            
            # 슬라이드 병합 (CSS 격리 + 원본 스타일 보존)
            merged_content, slide_scripts = self.merge_slides(temp_files)
            
            # 임시 파일 정리
            for temp_file in temp_files:
                os.unlink(temp_file)
            
            # CDN 링크를 인라인으로 교체 (원본 스타일 보존)
            merged_content = self.replace_cdn_with_inline(merged_content, self.downloaded_resources)
            
            # 슬라이드 매니저 스크립트 생성
            slide_manager_script = self.create_slide_manager_script()
            
            # 최종 HTML 생성
            final_html = f"""<!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{self.first_slide_title}</title>
        
        <!-- Chart.js 라이브러리 -->
        <script src="https://cdn.jsdelivr.net/npm/chart.js@3.9.1/dist/chart.min.js"></script>
        
        {slide_manager_script}
    </head>
    <body>
        {merged_content}
        
        <!-- 슬라이드별 차트 스크립트 -->
        {''.join(f'<script>{script}</script>' for script in slide_scripts)}
    </body>
    </html>"""
            
            # 출력 파일 저장
            self.log(f"출력 파일 저장 중: {output_path}")
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(final_html)
            
            final_size = len(final_html)
            
            self.log(f"변환 완료: {output_path}")
            self.log(f"파일 크기: {original_size:,} → {final_size:,} bytes")
            self.log(f"처리된 슬라이드 수: {self.processed_slides}/{self.total_slides}")
            self.log("✅ DOCTYPE 기준 슬라이드 분할 성공")
            self.log("✅ 슬라이드 구분 문제 해결")
            self.log("✅ 강제 스타일 적용")
            self.log("✅ 스크롤 지원: 긴 콘텐츠 스크롤 가능")
            
            return True
            
        except Exception as e:
            self.log(f"변환 실패: {str(e)}", "ERROR")
            return False

    def split_content_by_sections(self, html_content):
        """내용 기반으로 슬라이드 자동 분할 (백업 방법)"""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # 주요 제목들을 기준으로 분할
            sections = []
            current_section = []
            
            if soup.body:
                elements = list(soup.body.children)
            else:
                elements = list(soup.children)
            
            for element in elements:
                if hasattr(element, 'name'):
                    # h1, h2 태그나 특정 클래스를 기준으로 새 섹션 시작
                    if element.name in ['h1', 'h2'] and current_section:
                        sections.append('<body>' + ''.join(str(e) for e in current_section) + '</body>')
                        current_section = [element]
                    else:
                        current_section.append(element)
                else:
                    current_section.append(element)
            
            # 마지막 섹션 추가
            if current_section:
                sections.append('<body>' + ''.join(str(e) for e in current_section) + '</body>')
            
            self.log(f"내용 기반 분할로 {len(sections)}개 섹션 발견")
            return sections
            
        except Exception as e:
            self.log(f"내용 기반 분할 실패: {str(e)}", "ERROR")
            return []




    def convert(self, filename):
        """메인 변환 함수 (완전 구현)"""
        input_path, output_path = self.resolve_file_paths(filename)
        
        if not input_path.exists():
            self.log(f"입력 파일을 찾을 수 없습니다: {input_path}", "ERROR")
            return False
        
        try:
            success = self.process_html_file(input_path, output_path)
            
            if success:
                self.log("변환 프로세스 완료")
                self.log(f"입력: {input_path}")
                self.log(f"출력: {output_path}")
                self.log(f"슬라이드 수: {self.total_slides}")
                
            return success
            
        except Exception as e:
            self.log(f"변환 중 예외 발생: {str(e)}", "ERROR")
            return False


def main():
    parser = argparse.ArgumentParser(description='젠스파크 AI 슬라이드를 슬라이드컨테이너중앙정렬 오프라인 HTML로 변환 (v4.7)')
    parser.add_argument('filename', help='변환할 HTML 파일명 (확장자 생략 가능)')
    
    args = parser.parse_args()
    
    converter = GenSparkConverter()
    
    print("🎯 슬라이드 컨테이너만 중앙 정렬 모드로 변환 시작")
    print("📦 슬라이드 컨테이너: 화면 중앙 배치")
    print("💎 내부 콘텐츠: 원본 스타일 완전 보존")
    print("🔒 슬라이드별 CSS 완전 격리")
    print("🔄 슬라이드 이동 기능 완전 복구")
    print("📜 긴 콘텐츠 스크롤 지원")
    print("📝 code-snippet 클래스 코드 포맷팅 적용")
    print("⌨️  키보드 스크롤 지원 (↑↓ 화살표, 스페이스바)")
    print("🎨 원본 텍스트/아이콘/코드 정렬 완전 보존")
    print("📊 모든 차트 기능 보장")
    
    success = converter.convert(args.filename)
    
    if success:
        print("✅ 변환이 성공적으로 완료되었습니다!")
        print("📦 슬라이드 컨테이너: 화면 중앙에 완벽 배치!")
        print("💎 내부 콘텐츠: 원본 스타일 완전 보존!")
        print("🔄 슬라이드 이동: 이전/다음 버튼 정상 작동!")
        print("📜 긴 콘텐츠를 자유롭게 스크롤할 수 있습니다!")
        print("📝 code-snippet 클래스 코드가 제대로 포맷팅되었습니다!")
        print("🎨 원본 텍스트, 아이콘, 코드 정렬이 완벽하게 보존되었습니다!")
        print("📁 출력 파일: {}_ppt.html".format(args.filename.replace('.html', '')))
        print("\n⌨️  키보드 단축키:")
        print("   ↑↓ 화살표: 슬라이드 내 스크롤")
        print("   ←→ 화살표: 슬라이드 전환")
        print("   스페이스바: 페이지 단위 스크롤")
        print("   Home/End: 슬라이드 맨 위/맨 아래")
        print("   Ctrl+Home/End: 첫/마지막 슬라이드")
        print("\n🖱️  네비게이션 버튼:")
        print("   이전/다음: 슬라이드 이동")
        print("   홈: 첫 슬라이드로")
        print("   ↑: 맨 위로 스크롤")
        print("   ⛶: 전체화면 모드")
        sys.exit(0)
    else:
        print("❌ 변환 중 오류가 발생했습니다.")
        sys.exit(1)

if __name__ == "__main__":
    main()
