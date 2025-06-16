#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
젠스파크 AI 슬라이드 → 오프라인 단일 HTML 변환기 (v4.6 최종완성판)

완전 해결:
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
        """코드 스니펫 블록 처리 및 들여쓰기 개선 (오인식 방지 강화)"""
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # 제외할 클래스들 (일반 UI 요소) - 확장
        exclude_classes = [
            'feature-highlight',
            'highlight-text', 
            'highlight-box',
            'text-highlight',
            'badge',
            'label',
            'tag',
            'btn',
            'button',
            'card',
            'alert',
            'nav',
            'navbar',
            'breadcrumb',
            'pagination',
            'tab',
            'dropdown'
        ]
        
        # 실제 코드 블록 선택자만 사용
        code_selectors = [
            'pre code',
            'code[class*="language-"]',
            '.code-block code',
            'pre.highlight code'
        ]
        
        code_blocks = []
        
        for selector in code_selectors:
            elements = soup.select(selector)
            for element in elements:
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
            
            # 언어 감지를 위한 데이터 속성 추가
            if not block.get('data-language'):
                # 기존 클래스에서 언어 추측
                for cls in existing_classes:
                    if cls.startswith('language-'):
                        block['data-language'] = cls.replace('language-', '')
                        break
                    elif cls in ['python', 'javascript', 'html', 'css', 'sql', 'bash', 'json', 'xml']:
                        block['data-language'] = cls
                        break
            
            # 코드 내용 들여쓰기 보정
            if block.string:
                # 코드 내용 정규화
                code_content = block.string
                # 앞뒤 공백 제거 후 들여쓰기 보정
                lines = code_content.strip().split('\n')
                if lines:
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
                        block.string = '\n'.join(normalized_lines)
            
            block['class'] = existing_classes
        
        return str(soup)

    def fix_chart_js_compatibility(self, script_content, slide_index):
        """Chart.js 호환성 수정 및 다중 차트 충돌 완전 해결 (f-string 오류 완전 수정)"""
        
        # horizontalBar → bar 변환
        script_content = re.sub(
            r"type:\s*['\"]horizontalBar['\"]", 
            "type: 'bar'", 
            script_content
        )
        
        # horizontalBar 옵션도 변경
        script_content = re.sub(r"horizontalBar", "bar", script_content)
        
        # Canvas ID들을 추출
        canvas_ids = re.findall(r"getElementById\s*\(\s*['\"]([^'\"]+)['\"]", script_content)
        self.log(f"슬라이드 {slide_index + 1}에서 발견된 Canvas ID들: {canvas_ids}")
        
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
            (r'\bconst\s+(ctx)\b', f'const slide{slide_index}_ctx'),
            (r'\bconst\s+(chart)\b', f'const slide{slide_index}_chart'),
            (r'\bconst\s+(myChart)\b', f'const slide{slide_index}_myChart'),
            (r'\bconst\s+(data)\b', f'const slide{slide_index}_data'),
            (r'\bconst\s+(options)\b', f'const slide{slide_index}_options')
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
        
        # **f-string 오류 해결**: 일반 문자열 포맷팅 사용
        slide_id = f'slide-{slide_index}'
        slide_comment = f'Slide {slide_index + 1} Chart Initializer - v4.7 Complete Fix'
        
        # JavaScript 코드에서 사용할 변수들
        chart_var_names = [
            f'slide{slide_index}_targetPriceDistChart',
            f'slide{slide_index}_priceChart',
            f'slide{slide_index}_opinionDistChart',
            f'slide{slide_index}_sentimentChart',
            f'slide{slide_index}_sentimentDistChart',
            f'slide{slide_index}_reportChart',
            f'slide{slide_index}_chart',
            f'slide{slide_index}_myChart'
        ]
        
        # **안전한 문자열 조합 방식** (f-string 대신 .format() 사용)
        isolated_script = """
    // {slide_comment}
    (function() {{
        'use strict';
        
        var slideId = '{slide_id}';
        var chartDelay = 500;
        
        console.log('Chart Init Start (v4.7): ' + slideId);
        
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
            
            for (var i = 0; i < canvasElements.length; i++) {{
                var canvas = canvasElements[i];
                console.log('Canvas ' + i + ' ID: ' + canvas.id);
                window.chartCanvasRegistry[canvas.id] = canvas;
                window['canvas_' + canvas.id] = canvas;
            }}
            
            if (window.slideCharts && window.slideCharts[slideId]) {{
                Object.values(window.slideCharts[slideId]).forEach(function(chart) {{
                    if (chart && typeof chart.destroy === 'function') {{
                        try {{
                            chart.destroy();
                            console.log('Chart destroyed successfully');
                        }} catch (e) {{
                            console.warn('Chart cleanup error:', e);
                        }}
                    }}
                }});
                window.slideCharts[slideId] = {{}};
            }}
            
            canvasElements.forEach(function(canvas) {{
                if (canvas.chart) {{
                    try {{
                        canvas.chart.destroy();
                        canvas.chart = null;
                        console.log('Canvas chart instance destroyed: ' + canvas.id);
                    }} catch (e) {{
                        console.warn('Canvas chart cleanup error:', e);
                    }}
                }}
                
                var ctx = canvas.getContext('2d');
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                if (ctx.reset) ctx.reset();
            }});
            
            if (!window.slideCharts) window.slideCharts = {{}};
            if (!window.slideCharts[slideId]) window.slideCharts[slideId] = {{}};
            
            for (var i = 0; i < canvasElements.length; i++) {{
                (function(canvas, index) {{
                    setTimeout(function() {{
                        console.log('Init Canvas ' + index + ' ID: ' + canvas.id + ' (v4.7)');
                        initSingleChartV47(canvas, index);
                    }}, index * chartDelay);
                }})(canvasElements[i], i);
            }}
        }}
        
        function initSingleChartV47(canvas, chartIndex) {{
            try {{
                if (!canvas || !canvas.getContext) {{
                    console.error('Canvas invalid: ' + chartIndex);
                    return;
                }}
                
                var canvasId = canvas.id;
                if (!canvasId) {{
                    console.error('Canvas ID missing: ' + chartIndex);
                    return;
                }}
                
                console.log('Creating chart for (v4.7): ' + canvasId);
                
                var ctx = canvas.getContext('2d');
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                if (ctx.reset) ctx.reset();
                
                executeIndependentChartScript(canvas, canvasId);
                
            }} catch (e) {{
                console.error('Canvas init error ' + chartIndex + ' (v4.7):', e);
            }}
        }}
        
        function executeIndependentChartScript(targetCanvas, canvasId) {{
            try {{
                if (!targetCanvas) {{
                    console.error('Target canvas is null: ' + canvasId);
                    return;
                }}
                
                var canvasContext = targetCanvas.getContext('2d');
                if (!canvasContext) {{
                    console.error('Context creation failed: ' + canvasId);
                    return;
                }}
                
                console.log('Canvas ready (v4.7): ' + canvasId);
                
                window.currentTargetCanvas = targetCanvas;
                window.currentCanvasId = canvasId;
                
                var originalScript = `{script_content}`;
                
                var canvasSpecificScript = extractCanvasSpecificScriptV47(originalScript, canvasId);
                
                if (canvasSpecificScript) {{
                    console.log('Executing independent script for: ' + canvasId);
                    
                    var finalScript = canvasSpecificScript.replace(/PLACEHOLDER_CANVAS/g, 'window.currentTargetCanvas');
                    
                    console.log('Final script prepared for: ' + canvasId);
                    
                    try {{
                        eval(finalScript);
                        console.log('Chart script executed successfully for: ' + canvasId);
                    }} catch (evalError) {{
                        console.error('Chart script execution failed for ' + canvasId + ':', evalError);
                        
                        try {{
                            var fallbackScript = createFallbackChartScript(canvasId);
                            eval(fallbackScript);
                            console.log('Fallback chart created for: ' + canvasId);
                        }} catch (fallbackError) {{
                            console.error('Fallback chart failed for ' + canvasId + ':', fallbackError);
                        }}
                    }}
                    
                }} else {{
                    console.warn('No specific script found for: ' + canvasId);
                    createEmergencyChart(targetCanvas, canvasId);
                }}
                
                var chartVarNames = {chart_var_names};
                
                var savedCount = 0;
                for (var i = 0; i < chartVarNames.length; i++) {{
                    var varName = chartVarNames[i];
                    try {{
                        if (typeof window[varName] !== 'undefined' && window[varName]) {{
                            window.slideCharts[slideId][varName + '_' + canvasId] = window[varName];
                            targetCanvas.chart = window[varName];
                            savedCount++;
                            console.log('Chart saved (v4.7): ' + varName);
                        }}
                    }} catch (e) {{
                        console.warn('Variable save failed: ' + varName, e);
                    }}
                }}
                
                if (savedCount === 0) {{
                    console.warn('No charts saved for: ' + canvasId);
                }} else {{
                    console.log('Charts saved (v4.7): ' + savedCount + ' for ' + canvasId);
                }}
                
            }} catch (e) {{
                console.error('Independent chart execution failed for ' + canvasId + ':', e);
            }}
        }}
        
        function extractCanvasSpecificScriptV47(fullScript, canvasId) {{
            try {{
                console.log('Extracting script v4.7 for Canvas: ' + canvasId);
                
                var lines = fullScript.split('\\n');
                var extractedLines = [];
                var inTargetBlock = false;
                var braceLevel = 0;
                
                for (var i = 0; i < lines.length; i++) {{
                    var line = lines[i].trim();
                    
                    if (line.indexOf("getElementById('" + canvasId + "')") !== -1 || 
                        line.indexOf('getElementById("' + canvasId + '")') !== -1 ||
                        (line.indexOf('PLACEHOLDER_CANVAS') !== -1 && !inTargetBlock)) {{
                        
                        inTargetBlock = true;
                        console.log('Found start for ' + canvasId + ' at line: ' + i);
                    }}
                    
                    if (inTargetBlock) {{
                        extractedLines.push(lines[i]);
                        
                        for (var j = 0; j < line.length; j++) {{
                            if (line[j] === '{{') braceLevel++;
                            if (line[j] === '}}') braceLevel--;
                        }}
                        
                        if (inTargetBlock && line.indexOf('}});') !== -1 && braceLevel <= 0) {{
                            console.log('Found end for ' + canvasId + ' at line: ' + i);
                            break;
                        }}
                        
                        if (line.indexOf("getElementById('") !== -1 && 
                            line.indexOf("getElementById('" + canvasId + "')") === -1 &&
                            line.indexOf('PLACEHOLDER_CANVAS') === -1) {{
                            extractedLines.pop();
                            console.log('Found other canvas, ending extraction for ' + canvasId);
                            break;
                        }}
                    }}
                }}
                
                if (extractedLines.length > 0) {{
                    var result = extractedLines.join('\\n');
                    console.log('Script extracted v4.7 for: ' + canvasId + ' (' + extractedLines.length + ' lines)');
                    return result;
                }}
                
                console.warn('Could not extract script v4.7 for: ' + canvasId);
                return null;
            }} catch (e) {{
                console.error('Script extraction error v4.7 for ' + canvasId + ':', e);
                return null;
            }}
        }}
        
        function createFallbackChartScript(canvasId) {{
            return `
                try {{
                    var fallbackCtx = window.currentTargetCanvas.getContext('2d');
                    var fallbackChart = new Chart(fallbackCtx, {{
                        type: 'bar',
                        data: {{
                            labels: ['데이터 1', '데이터 2', '데이터 3'],
                            datasets: [{{
                                label: '폴백 차트',
                                data: [10, 20, 30],
                                backgroundColor: 'rgba(54, 162, 235, 0.5)'
                            }}]
                        }},
                        options: {{
                            responsive: true,
                            maintainAspectRatio: false
                        }}
                    }});
                    window.{fallback_var_name} = fallbackChart;
                    console.log('Fallback chart created for: ' + canvasId);
                }} catch (e) {{
                    console.error('Fallback chart creation failed:', e);
                }}
            `;
        }}
        
        function createEmergencyChart(canvas, canvasId) {{
            try {{
                var ctx = canvas.getContext('2d');
                ctx.fillStyle = '#f0f0f0';
                ctx.fillRect(0, 0, canvas.width, canvas.height);
                ctx.fillStyle = '#333';
                ctx.font = '16px Arial';
                ctx.textAlign = 'center';
                ctx.fillText('차트 로딩 중...', canvas.width / 2, canvas.height / 2);
                console.log('Emergency placeholder created for: ' + canvasId);
            }} catch (e) {{
                console.error('Emergency chart creation failed:', e);
            }}
        }}
        
        if (typeof window.chartInitializers === 'undefined') {{
            window.chartInitializers = {{}};
        }}
        
        window.chartInitializers[slideId] = initSlideChartsSequential;
        
        console.log('Chart initializer registered (v4.7): ' + slideId);
        
    }})();
    """.format(
            slide_comment=slide_comment,
            slide_id=slide_id,
            script_content=script_content.replace('`', '\\`').replace('\\', '\\\\'),
            chart_var_names=str(chart_var_names).replace("'", '"'),
            fallback_var_name=f'slide{slide_index}_fallbackChart'
        )
        
        # Chart.js 인덱스 옵션 수정
        if 'indexAxis' not in script_content and 'bar' in script_content:
            isolated_script = isolated_script.replace(
                "type: 'bar'",
                "type: 'bar',\n            indexAxis: 'y'"
            )
        
        return isolated_script




    def replace_cdn_with_inline(self, html_content, downloaded_resources):
        """CDN 링크를 인라인 리소스로 교체"""
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # CSS 링크 교체
        for link in soup.find_all('link', href=True):
            href = link['href']
            if href in downloaded_resources:
                resource = downloaded_resources[href]
                if resource['type'] == 'css':
                    # <link>를 <style>로 교체
                    style_tag = soup.new_tag('style')
                    style_tag.string = resource['text_content']
                    style_tag['data-original-url'] = href
                    link.replace_with(style_tag)
        
        # JavaScript 교체
        for script in soup.find_all('script', src=True):
            src = script['src']
            if src in downloaded_resources:
                resource = downloaded_resources[src]
                if resource['type'] == 'js':
                    # src를 제거하고 내용을 인라인으로
                    del script['src']
                    script.string = resource['text_content']
                    script['data-original-url'] = src
        
        # CSS 내부 @import 교체
        for style in soup.find_all('style'):
            if style.string:
                css_content = style.string
                for url, resource in downloaded_resources.items():
                    if resource['type'] == 'css' and url in css_content:
                        css_content = css_content.replace(f"@import url('{url}')", resource['text_content'])
                        css_content = css_content.replace(f'@import url("{url}")', resource['text_content'])
                        css_content = css_content.replace(f"@import url({url})", resource['text_content'])
                style.string = css_content
        
        return str(soup)

    def process_single_slide(self, slide_html, slide_index):
        """단일 슬라이드 처리"""
        self.log(f"슬라이드 {slide_index + 1} 처리 중...")
        
        # 첫 번째 슬라이드에서 제목 추출
        if slide_index == 0:
            soup = BeautifulSoup(slide_html, 'html.parser')
            title_tag = soup.find('title')
            if title_tag and title_tag.get_text().strip():
                self.first_slide_title = title_tag.get_text().strip()
            else:
                h1_tag = soup.find('h1')
                if h1_tag and h1_tag.get_text().strip():
                    self.first_slide_title = h1_tag.get_text().strip()
        
        # 코드 스니펫 처리 (오인식 방지 강화)
        slide_html = self.process_code_snippets(slide_html)
        
        # CDN URL 추출 (유효한 URL만)
        cdn_urls = self.extract_cdn_urls(slide_html)
        
        # CDN 리소스 다운로드 (처음에만)
        if cdn_urls and not self.downloaded_resources:
            self.downloaded_resources = self.download_all_resources(cdn_urls)
        elif not self.downloaded_resources:
            # CDN이 없어도 필수 리소스는 다운로드
            self.downloaded_resources = self.download_all_resources([])
        
        # CDN을 인라인으로 교체
        slide_html = self.replace_cdn_with_inline(slide_html, self.downloaded_resources)
        
        # Chart.js 호환성 수정 및 스코프 격리
        soup = BeautifulSoup(slide_html, 'html.parser')
        for script in soup.find_all('script'):
            if script.string and ('Chart' in script.string or 'ctx' in script.string or 'canvas' in script.string.lower()):
                script.string = self.fix_chart_js_compatibility(script.string, slide_index)
        
        slide_html = str(soup)
        
        # HTML 구조 수정
        soup = BeautifulSoup(slide_html, 'html.parser')
        
        # 슬라이드 ID 및 클래스 설정
        slide_id = f"slide-{slide_index}"
        html_tag = soup.find('html')
        
        if html_tag:
            html_tag.name = 'div'  # html을 div로 변경
            html_tag['id'] = slide_id
            html_tag['class'] = html_tag.get('class', []) + ['genspark-slide']
            html_tag['style'] = 'display: none;'  # 초기 숨김
        
        # head 태그를 div로 변경 (메타데이터 보존)
        head_tag = soup.find('head')
        if head_tag:
            head_tag.name = 'div'
            head_tag['class'] = ['slide-head']
            head_tag['style'] = 'display: none;'
        
        # body 태그를 div로 변경
        body_tag = soup.find('body')
        if body_tag:
            body_tag.name = 'div'
            body_tag['class'] = body_tag.get('class', []) + ['slide-body']
        
        self.processed_slides += 1
        return str(soup)

    def create_navigation_controls(self):
        """개선된 네비게이션 컨트롤 생성"""
        return """
        <div id="slide-controls" style="
            position: fixed;
            bottom: 20px;
            right: 20px;
            z-index: 10000;
            background: rgba(0,0,0,0.8);
            color: white;
            padding: 12px 18px;
            border-radius: 25px;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            font-size: 14px;
            user-select: none;
            box-shadow: 0 4px 20px rgba(0,0,0,0.3);
            backdrop-filter: blur(10px);
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            gap: 10px;
        ">
            <span id="slide-counter" style="margin-right: 10px; font-weight: 500;">1 / 1</span>
            
            <button id="home-btn" onclick="slideManager.goToSlide(0)" style="
                background: transparent;
                border: 1px solid rgba(255,255,255,0.3);
                color: white;
                padding: 8px 10px;
                border-radius: 6px;
                cursor: pointer;
                font-size: 12px;
                transition: all 0.2s ease;
                display: flex;
                align-items: center;
            " onmouseover="this.style.background='rgba(255,255,255,0.1)'" onmouseout="this.style.background='transparent'" title="첫 슬라이드">
                <i class="fas fa-home"></i>
            </button>
            
            <button id="prev-btn" onclick="slideManager.previousSlide()" style="
                background: transparent;
                border: 1px solid rgba(255,255,255,0.3);
                color: white;
                padding: 8px 12px;
                border-radius: 6px;
                cursor: pointer;
                font-size: 12px;
                transition: all 0.2s ease;
                display: flex;
                align-items: center;
                gap: 5px;
            " onmouseover="this.style.background='rgba(255,255,255,0.1)'" onmouseout="this.style.background='transparent'" title="이전 슬라이드">
                <i class="fas fa-chevron-left"></i> 이전
            </button>
            
            <button id="next-btn" onclick="slideManager.nextSlide()" style="
                background: transparent;
                border: 1px solid rgba(255,255,255,0.3);
                color: white;
                padding: 8px 12px;
                border-radius: 6px;
                cursor: pointer;
                font-size: 12px;
                transition: all 0.2s ease;
                display: flex;
                align-items: center;
                gap: 5px;
            " onmouseover="this.style.background='rgba(255,255,255,0.1)'" onmouseout="this.style.background='transparent'" title="다음 슬라이드">
                다음 <i class="fas fa-chevron-right"></i>
            </button>
            
            <button id="end-btn" onclick="slideManager.goToSlide(slideManager.totalSlides - 1)" style="
                background: transparent;
                border: 1px solid rgba(255,255,255,0.3);
                color: white;
                padding: 8px 10px;
                border-radius: 6px;
                cursor: pointer;
                font-size: 12px;
                transition: all 0.2s ease;
                display: flex;
                align-items: center;
            " onmouseover="this.style.background='rgba(255,255,255,0.1)'" onmouseout="this.style.background='transparent'" title="마지막 슬라이드">
                <i class="fas fa-step-forward"></i>
            </button>
            
            <button id="fullscreen-btn" onclick="slideManager.toggleFullscreen()" style="
                background: transparent;
                border: 1px solid rgba(255,255,255,0.3);
                color: white;
                margin-left: 10px;
                padding: 8px 10px;
                border-radius: 6px;
                cursor: pointer;
                font-size: 12px;
                transition: all 0.2s ease;
                display: flex;
                align-items: center;
            " onmouseover="this.style.background='rgba(255,255,255,0.1)'" onmouseout="this.style.background='transparent'" title="전체화면">
                <i class="fas fa-expand"></i>
            </button>
        </div>
        
        <div id="slide-progress" style="
            position: fixed;
            top: 0;
            left: 0;
            height: 4px;
            background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
            z-index: 10001;
            transition: width 0.3s ease;
            box-shadow: 0 2px 4px rgba(102, 126, 234, 0.3);
        "></div>
        
        <div id="slide-info" style="
            position: fixed;
            top: 20px;
            left: 20px;
            z-index: 10000;
            background: rgba(0,0,0,0.8);
            color: white;
            padding: 8px 15px;
            border-radius: 15px;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            font-size: 12px;
            backdrop-filter: blur(10px);
            opacity: 0;
            transition: opacity 0.3s ease;
        " id="info-toast">
            <span id="info-text">젠스파크 슬라이드</span>
        </div>
        """

    def create_slide_manager_script(self):
        """개선된 슬라이드 관리 JavaScript 생성 (다중 차트 지원, 디버깅 로그 포함)"""
        
        # Highlight.js 리소스 포함
        highlightjs_js = self.downloaded_resources.get(self.highlightjs_urls['js'], {}).get('text_content', '')
        highlightjs_css = self.downloaded_resources.get(self.highlightjs_urls['css_github'], {}).get('text_content', '')
        
        # Font Awesome CSS 포함
        fontawesome_css = self.downloaded_resources.get(self.fontawesome_urls['css'], {}).get('text_content', '')
        
        # 폰트 파일들을 CSS에 인라인으로 포함 (완전 강화)
        fontawesome_css_with_fonts = fontawesome_css
        
        for font_url in self.fontawesome_urls['webfonts']:
            if font_url in self.downloaded_resources:
                font_resource = self.downloaded_resources[font_url]
                font_base64 = font_resource['base64']
                
                # 폰트 형식 감지
                if 'woff2' in font_url:
                    font_format = 'woff2'
                elif 'woff' in font_url:
                    font_format = 'woff'
                elif 'ttf' in font_url:
                    font_format = 'truetype'
                elif 'otf' in font_url:
                    font_format = 'opentype'
                else:
                    font_format = 'woff2'
                
                font_filename = Path(font_url).name
                
                # 모든 가능한 경로 패턴을 완전히 교체 (확장)
                font_patterns = [
                    # 기본 패턴들
                    f"url('../webfonts/{font_filename}')",
                    f'url("../webfonts/{font_filename}")',
                    f"url(../webfonts/{font_filename})",
                    f"url('webfonts/{font_filename}')",
                    f'url("webfonts/{font_filename}")',
                    f"url(webfonts/{font_filename})",
                    f"url('./{font_filename}')",
                    f'url("./{font_filename}")',
                    f"url(./{font_filename})",
                    f"url('{font_filename}')",
                    f'url("{font_filename}")',
                    f"url({font_filename})",
                    
                    # 절대 경로 패턴들
                    f"url('/webfonts/{font_filename}')",
                    f'url("/webfonts/{font_filename}")',
                    f"url(./webfonts/{font_filename})",
                    f"url('./webfonts/{font_filename}')",
                    f'url("./webfonts/{font_filename}")',
                    
                    # 특정 파일명 패턴들
                    f"url(fa-solid-900.woff2)",
                    f"url('fa-solid-900.woff2')",
                    f'url("fa-solid-900.woff2")',
                    f"url(fa-solid-900.woff)",
                    f"url('fa-solid-900.woff')",
                    f'url("fa-solid-900.woff")',
                    f"url(fa-solid-900.ttf)",
                    f"url('fa-solid-900.ttf')",
                    f'url("fa-solid-900.ttf")',
                    f"url(fa-regular-400.woff2)",
                    f"url('fa-regular-400.woff2')",
                    f'url("fa-regular-400.woff2")',
                    f"url(fa-regular-400.woff)",
                    f"url('fa-regular-400.woff')",
                    f'url("fa-regular-400.woff")',
                    f"url(fa-regular-400.ttf)",
                    f"url('fa-regular-400.ttf')",
                    f'url("fa-regular-400.ttf")',
                    f"url(fa-brands-400.woff2)",
                    f"url('fa-brands-400.woff2')",
                    f'url("fa-brands-400.woff2")',
                    f"url(fa-brands-400.woff)",
                    f"url('fa-brands-400.woff')",
                    f'url("fa-brands-400.woff")',
                    f"url(fa-brands-400.ttf)",
                    f"url('fa-brands-400.ttf')",
                    f'url("fa-brands-400.ttf")',
                    
                    # 추가 폴백 패턴들
                    f"url(fonts/{font_filename})",
                    f"url('./fonts/{font_filename}')",
                    f'url("./fonts/{font_filename}")',
                    f"url('../fonts/{font_filename}')",
                    f'url("../fonts/{font_filename}")',
                ]
                
                # 정규식을 사용한 더 강력한 교체
                import re
                
                # 파일명만 있는 패턴들도 교체
                base_name = font_filename.split('.')[0]  # fa-solid-900
                regex_patterns = [
                    rf"url\(\s*['\"]?[^'\"]*{re.escape(font_filename)}['\"]?\s*\)",
                    rf"url\(\s*['\"]?[^'\"]*{re.escape(base_name)}\.woff2?['\"]?\s*\)",
                    rf"url\(\s*['\"]?[^'\"]*{re.escape(base_name)}\.ttf['\"]?\s*\)",
                    rf"url\(\s*['\"]?[^'\"]*{re.escape(base_name)}\.otf['\"]?\s*\)",
                ]
                
                # 패턴별 교체
                for pattern in font_patterns:
                    fontawesome_css_with_fonts = fontawesome_css_with_fonts.replace(
                        pattern,
                        f"url(data:font/{font_format};base64,{font_base64})"
                    )
                
                # 정규식 패턴 교체
                for regex_pattern in regex_patterns:
                    fontawesome_css_with_fonts = re.sub(
                        regex_pattern,
                        f"url(data:font/{font_format};base64,{font_base64})",
                        fontawesome_css_with_fonts,
                        flags=re.IGNORECASE
                    )
        
        return f"""
        <script>
        // Highlight.js 임베드
        {highlightjs_js}
        </script>
        
        <style>
        /* Font Awesome 스타일 (폰트 완전 포함) */
        {fontawesome_css_with_fonts}
        
        /* Highlight.js 스타일 */
        {highlightjs_css}
        
        /* 개선된 코드 블록 스타일 */
        .hljs,
        .formatted-code {{
            border-radius: 8px;
            padding: 1.5em;
            margin: 1em 0;
            font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', 'Consolas', 'Courier New', monospace;
            font-size: 0.9em;
            line-height: 1.6;
            overflow-x: auto;
            background: #f8f9fa !important;
            border: 1px solid #e9ecef;
            white-space: pre;
            word-wrap: normal;
            tab-size: 4;
        }}
        
        .code-snippet {{
            border-radius: 8px;
            border: 1px solid #e1e5e9;
            background-color: #f6f8fa;
            margin: 1.5em 0;
            overflow: hidden;
        }}
        
        .code-snippet .hljs,
        .code-snippet .formatted-code {{
            background: #f6f8fa !important;
            border: none;
            margin: 0;
            border-radius: 0;
        }}
        
        .code-snippet pre {{
            margin: 0;
            padding: 1.5em;
            background: #f6f8fa;
            overflow-x: auto;
            white-space: pre;
            word-wrap: normal;
        }}
        
        .code-snippet code {{
            font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', 'Consolas', 'Courier New', monospace;
            font-size: 0.9em;
            line-height: 1.6;
            background: transparent;
            padding: 0;
            border-radius: 0;
            white-space: pre;
            word-wrap: normal;
        }}
        
        /* 일반 하이라이트 요소는 코드 스타일 제거 (강화) */
        .feature-highlight,
        .highlight-text,
        .highlight-box,
        .text-highlight,
        .badge,
        .label,
        .tag,
        .btn,
        .button,
        .card,
        .alert {{
            font-family: inherit !important;
            background: transparent !important;
            border: none !important;
            padding: inherit !important;
            margin: inherit !important;
            white-space: normal !important;
        }}
        
        /* 들여쓰기 보정 */
        .formatted-code,
        .code-snippet pre,
        .code-snippet code {{
            text-indent: 0;
            text-align: left;
        }}
        
        /* 반응형 네비게이션 */
        @media (max-width: 768px) {{
            #slide-controls {{
                flex-wrap: wrap;
                gap: 5px;
                padding: 8px 12px;
                bottom: 10px;
                right: 10px;
            }}
            
            #slide-controls button {{
                padding: 6px 8px;
                font-size: 10px;
            }}
            
            #slide-controls span {{
                font-size: 12px;
                margin-right: 5px;
            }}
        }}
        </style>
        
        <script>
        // Chart.js 인스턴스 전역 관리
        window.slideCharts = window.slideCharts || {{}};
        
        // Chart 정리 함수
        function destroySlideCharts(slideId) {{
            if (window.slideCharts[slideId]) {{
                Object.values(window.slideCharts[slideId]).forEach(chart => {{
                    if (chart && typeof chart.destroy === 'function') {{
                        try {{
                            chart.destroy();
                        }} catch (e) {{
                            console.warn('차트 정리 오류:', e);
                        }}
                    }}
                }});
                window.slideCharts[slideId] = {{}};
            }}
        }}
        
        // 젠스파크 슬라이드 매니저 (다중 차트 완전 지원, 디버깅 포함)
        class GenSparkSlideManager {{
            constructor() {{
                this.slides = document.querySelectorAll('.genspark-slide');
                this.currentSlide = 0;
                this.totalSlides = this.slides.length;
                this.isTransitioning = false;
                this.debugMode = true; // 디버깅 모드 활성화
                
                this.init();
            }}
            
            init() {{
                this.log('젠스파크 슬라이드 매니저 초기화 시작');
                
                if (this.slides.length > 0) {{
                    this.showSlide(0);
                }}
                
                this.setupEventListeners();
                this.updateUI();
                this.setupFullscreenDetection();
                
                this.log(`초기화 완료 - 총 ${{this.totalSlides}}개 슬라이드`);
            }}
            
            setupEventListeners() {{
                // 키보드 이벤트
                document.addEventListener('keydown', (e) => {{
                    if (this.isTransitioning) return;
                    
                    switch(e.key) {{
                        case 'ArrowRight':
                        case 'Space':
                        case 'PageDown':
                            e.preventDefault();
                            this.nextSlide();
                            break;
                        case 'ArrowLeft':
                        case 'PageUp':
                            e.preventDefault();
                            this.previousSlide();
                            break;
                        case 'Home':
                            e.preventDefault();
                            this.goToSlide(0);
                            break;
                        case 'End':
                            e.preventDefault();
                            this.goToSlide(this.totalSlides - 1);
                            break;
                        case 'F11':
                            e.preventDefault();
                            this.toggleFullscreen();
                            break;
                        case 'Escape':
                            if (document.fullscreenElement) {{
                                document.exitFullscreen();
                            }}
                            break;
                        case 'r':
                        case 'R':
                            if (e.ctrlKey || e.metaKey) {{
                                e.preventDefault();
                                location.reload();
                            }}
                            break;
                    }}
                }});
                
                // 마우스 휠 이벤트 - 슬라이드 내부 스크롤만 허용
                document.addEventListener('wheel', (e) => {{
                    // 휠 스크롤로는 슬라이드 전환을 하지 않음
                    // 슬라이드 내부의 자연스러운 스크롤만 허용
                }}, {{ passive: true }});
                
                // 터치 이벤트 (모바일)
                let touchStartX = 0;
                let touchStartY = 0;
                
                document.addEventListener('touchstart', (e) => {{
                    touchStartX = e.touches[0].clientX;
                    touchStartY = e.touches[0].clientY;
                }});
                
                document.addEventListener('touchend', (e) => {{
                    if (this.isTransitioning) return;
                    
                    const touchEndX = e.changedTouches[0].clientX;
                    const touchEndY = e.changedTouches[0].clientY;
                    const diffX = touchStartX - touchEndX;
                    const diffY = touchStartY - touchEndY;
                    
                    // 수평 스와이프가 수직보다 클 때만 슬라이드 전환
                    if (Math.abs(diffX) > Math.abs(diffY) && Math.abs(diffX) > 50) {{
                        if (diffX > 0) {{
                            this.nextSlide();
                        }} else {{
                            this.previousSlide();
                        }}
                    }}
                }});
                
                // 창 크기 변경 대응
                window.addEventListener('resize', () => {{
                    this.handleResize();
                }});
            }}
            
            setupFullscreenDetection() {{
                document.addEventListener('fullscreenchange', () => {{
                    const btn = document.getElementById('fullscreen-btn');
                    if (btn) {{
                        const icon = btn.querySelector('i');
                        if (icon) {{
                            icon.className = document.fullscreenElement ? 'fas fa-compress' : 'fas fa-expand';
                        }}
                    }}
                }});
            }}
            
            showSlide(index) {{
                if (index < 0 || index >= this.totalSlides || this.isTransitioning) return;
                
                this.isTransitioning = true;
                this.log(`슬라이드 ${{index + 1}} 표시 시작`);
                
                // 이전 슬라이드 정리
                if (this.currentSlide !== index) {{
                    const prevSlide = this.slides[this.currentSlide];
                    if (prevSlide) {{
                        const prevSlideId = prevSlide.id;
                        prevSlide.style.display = 'none';
                        
                        // 이전 슬라이드의 모든 차트 정리
                        destroySlideCharts(prevSlideId);
                        this.log(`이전 슬라이드 ${{prevSlideId}} 정리 완료`);
                    }}
                }}
                
                // 새 슬라이드 표시
                const slide = this.slides[index];
                const slideId = slide.id;
                slide.style.display = 'block';
                slide.scrollTop = 0;
                
                // 차트 컨테이너 초기화
                if (!window.slideCharts[slideId]) {{
                    window.slideCharts[slideId] = {{}};
                }}
                
                this.currentSlide = index;
                this.updateUI();
                
                // 차트 초기화 (충분한 지연 + 순차 처리)
                setTimeout(() => {{
                    this.log(`슬라이드 ${{slideId}} 차트 초기화 시작`);
                    if (window.chartInitializers && window.chartInitializers[slideId]) {{
                        try {{
                            window.chartInitializers[slideId]();
                        }} catch (e) {{
                            this.log(`슬라이드 ${{slideId}} 차트 초기화 오류: ${{e.message}}`, 'ERROR');
                        }}
                    }} else {{
                        this.log(`슬라이드 ${{slideId}}: 차트 초기화 함수 없음`);
                    }}
                    
                    this.isTransitioning = false;
                }}, 1500); // 1.5초 지연으로 안정성 확보
                
                this.log(`슬라이드 ${{index + 1}} 표시됨`);
            }}
            
            processSlideContent(slide) {{
                // 코드 하이라이팅 적용 (실제 코드 블록만)
                this.applyCodeHighlighting(slide);
                
                // 이미지 레이지 로딩
                this.loadSlideImages(slide);
                
                // 코드 포맷팅 개선
                this.improveCodeFormatting(slide);
            }}
            
            improveCodeFormatting(slide) {{
                const codeBlocks = slide.querySelectorAll('pre code, .code-snippet code, code[class*="language-"]');
                
                codeBlocks.forEach(block => {{
                    // 제외할 클래스 확인 (확장)
                    const excludeClasses = ['feature-highlight', 'highlight-text', 'highlight-box', 'text-highlight', 'badge', 'label', 'tag', 'btn', 'button', 'card', 'alert'];
                    const blockClasses = Array.from(block.classList);
                    const parentClasses = block.parentElement ? Array.from(block.parentElement.classList) : [];
                    const allClasses = blockClasses.concat(parentClasses);
                    const hasExcludeClass = excludeClasses.some(cls => allClasses.includes(cls));
                    
                    if (hasExcludeClass) {{
                        return; // 제외 클래스가 있으면 처리하지 않음
                    }}
                    
                    // 코드 블록 내부의 텍스트 정규화
                    if (block.textContent) {{
                        const lines = block.textContent.split('\\n');
                        
                        // 빈 줄 제거 (앞뒤)
                        while (lines.length > 0 && !lines[0].trim()) {{
                            lines.shift();
                        }}
                        while (lines.length > 0 && !lines[lines.length - 1].trim()) {{
                            lines.pop();
                        }}
                        
                        // 최소 들여쓰기 계산 및 제거
                        if (lines.length > 0) {{
                            let minIndent = Infinity;
                            lines.forEach(line => {{
                                if (line.trim()) {{
                                    const indent = line.match(/^\\s*/)[0].length;
                                    minIndent = Math.min(minIndent, indent);
                                }}
                            }});
                            
                            if (minIndent > 0 && minIndent !== Infinity) {{
                                const normalizedLines = lines.map(line => 
                                    line.trim() ? line.substring(minIndent) : line
                                );
                                block.textContent = normalizedLines.join('\\n');
                            }}
                        }}
                    }}
                }});
            }}
            
            applyCodeHighlighting(slide) {{
                if (typeof hljs === 'undefined') return;
                
                // 실제 코드 블록만 선택 (제외 클래스 확장)
                const codeBlocks = slide.querySelectorAll('pre code, code[class*="language-"], .code-block code');
                
                codeBlocks.forEach(block => {{
                    // 제외할 클래스 확인 (확장)
                    const excludeClasses = ['feature-highlight', 'highlight-text', 'highlight-box', 'text-highlight', 'badge', 'label', 'tag', 'btn', 'button', 'card', 'alert', 'nav', 'navbar'];
                    const blockClasses = Array.from(block.classList);
                    const parentClasses = block.parentElement ? Array.from(block.parentElement.classList) : [];
                    const grandparentClasses = block.parentElement && block.parentElement.parentElement ? Array.from(block.parentElement.parentElement.classList) : [];
                    const allClasses = blockClasses.concat(parentClasses, grandparentClasses);
                    const hasExcludeClass = excludeClasses.some(cls => allClasses.includes(cls));
                    
                    if (hasExcludeClass) {{
                        return; // 제외 클래스가 있으면 하이라이팅하지 않음
                    }}
                    
                    if (!block.classList.contains('hljs-processed')) {{
                        try {{
                            hljs.highlightElement(block);
                            block.classList.add('hljs-processed');
                        }} catch (e) {{
                            this.log('코드 하이라이팅 오류: ' + e.message, 'WARN');
                        }}
                    }}
                }});
            }}
            
            loadSlideImages(slide) {{
                const images = slide.querySelectorAll('img[data-src]');
                
                images.forEach(img => {{
                    img.src = img.getAttribute('data-src');
                    img.removeAttribute('data-src');
                }});
            }}
            
            nextSlide() {{
                if (this.currentSlide < this.totalSlides - 1) {{
                    this.showSlide(this.currentSlide + 1);
                }} else {{
                    this.showInfo('마지막 슬라이드입니다');
                }}
            }}
            
            previousSlide() {{
                if (this.currentSlide > 0) {{
                    this.showSlide(this.currentSlide - 1);
                }} else {{
                    this.showInfo('첫 번째 슬라이드입니다');
                }}
            }}
            
            goToSlide(index) {{
                this.showSlide(index);
            }}
            
            updateUI() {{
                // 슬라이드 카운터 업데이트
                const counter = document.getElementById('slide-counter');
                if (counter) {{
                    counter.textContent = `${{this.currentSlide + 1}} / ${{this.totalSlides}}`;
                }}
                
                // 프로그레스 바 업데이트
                const progress = document.getElementById('slide-progress');
                if (progress) {{
                    const percentage = ((this.currentSlide + 1) / this.totalSlides) * 100;
                    progress.style.width = `${{percentage}}%`;
                }}
                
                // 버튼 상태 업데이트
                const prevBtn = document.getElementById('prev-btn');
                const nextBtn = document.getElementById('next-btn');
                const homeBtn = document.getElementById('home-btn');
                const endBtn = document.getElementById('end-btn');
                
                if (prevBtn) {{
                    prevBtn.style.opacity = this.currentSlide === 0 ? '0.5' : '1';
                    prevBtn.disabled = this.currentSlide === 0;
                }}
                
                if (nextBtn) {{
                    nextBtn.style.opacity = this.currentSlide === this.totalSlides - 1 ? '0.5' : '1';
                    nextBtn.disabled = this.currentSlide === this.totalSlides - 1;
                }}
                
                if (homeBtn) {{
                    homeBtn.style.opacity = this.currentSlide === 0 ? '0.5' : '1';
                }}
                
                if (endBtn) {{
                    endBtn.style.opacity = this.currentSlide === this.totalSlides - 1 ? '0.5' : '1';
                }}
            }}
            
            toggleFullscreen() {{
                if (document.fullscreenElement) {{
                    document.exitFullscreen();
                }} else {{
                    document.documentElement.requestFullscreen().catch(err => {{
                        this.log('전체화면 실패: ' + err.message, 'WARN');
                    }});
                }}
            }}
            
            handleResize() {{
                // 반응형 처리
                const slides = document.querySelectorAll('.genspark-slide');
                slides.forEach(slide => {{
                    slide.style.height = window.innerHeight + 'px';
                }});
            }}
            
            showInfo(message, duration = 2000) {{
                // UI 알림
                const infoToast = document.getElementById('slide-info');
                const infoText = document.getElementById('info-text');
                
                if (infoToast && infoText) {{
                    infoText.textContent = message;
                    infoToast.style.opacity = '1';
                    
                    setTimeout(() => {{
                        infoToast.style.opacity = '0';
                    }}, duration);
                }}
            }}
            
            log(message, level = 'INFO') {{
                // 디버깅 모드일 때만 출력
                if (this.debugMode) {{
                    const timestamp = new Date().toLocaleTimeString();
                    console.log(`[${{timestamp}}] ${{level}}: ${{message}}`);
                }}
            }}
        }}
        
        // 전역 변수 및 초기화
        let slideManager;
        
        document.addEventListener('DOMContentLoaded', function() {{
            slideManager = new GenSparkSlideManager();
        }});
        
        </script>
        """

    def create_complete_html(self, slides_data):
        """완전한 HTML 파일 생성"""
        
        # 모든 슬라이드 HTML 결합
        all_slides_html = "\n".join(slides_data)
        
        # 네비게이션 컨트롤
        navigation_html = self.create_navigation_controls()
        
        # 슬라이드 매니저 스크립트
        manager_script = self.create_slide_manager_script()
        
        # 완전한 HTML 조립
        complete_html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <title>{self.first_slide_title}</title>
    
    <style>
        /* 전역 리셋 및 기본 스타일 */
        * {{
            box-sizing: border-box;
        }}
        
        html, body {{
            margin: 0;
            padding: 0;
            width: 100%;
            height: 100%;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: #ffffff;
            overflow: hidden;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }}
        
        /* 젠스파크 슬라이드 컨테이너 */
        .genspark-slide {{
            position: absolute;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            overflow-y: auto;
            overflow-x: hidden;
            background: #ffffff;
            display: none;
            scroll-behavior: smooth;
        }}
        
        .genspark-slide:first-of-type {{
            display: block;
        }}
        
        /* 슬라이드 내부 스타일 보존 */
        .slide-head {{
            position: absolute;
            top: -9999px;
            left: -9999px;
        }}
        
        .slide-body {{
            width: 100%;
            min-height: 100vh;
            padding: 0;
            margin: 0;
        }}
        
        /* 젠스파크 원본 스타일 우선순위 보장 */
        .genspark-slide * {{
            position: relative;
        }}
        
        /* 스크롤바 스타일링 */
        .genspark-slide::-webkit-scrollbar {{
            width: 12px;
        }}
        
        .genspark-slide::-webkit-scrollbar-track {{
            background: rgba(0,0,0,0.1);
            border-radius: 6px;
        }}
        
        .genspark-slide::-webkit-scrollbar-thumb {{
            background: rgba(0,0,0,0.3);
            border-radius: 6px;
            border: 2px solid transparent;
            background-clip: content-box;
        }}
        
        .genspark-slide::-webkit-scrollbar-thumb:hover {{
            background: rgba(0,0,0,0.5);
            background-clip: content-box;
        }}
        
        /* 반응형 디자인 */
        @media (max-width: 768px) {{
            #slide-controls {{
                bottom: 10px !important;
                right: 10px !important;
                padding: 8px 12px !important;
                font-size: 12px !important;
                flex-wrap: wrap !important;
            }}
            
            #slide-controls button {{
                padding: 6px 8px !important;
                font-size: 10px !important;
                margin: 2px !important;
            }}
            
            #slide-info {{
                top: 10px !important;
                left: 10px !important;
                font-size: 11px !important;
            }}
        }}
        
        /* 전체화면 스타일 */
        body:fullscreen {{
            cursor: none;
        }}
        
        body:fullscreen #slide-controls {{
            opacity: 0.7;
        }}
        
        body:fullscreen #slide-controls:hover {{
            opacity: 1;
        }}
        
        /* 프린트 스타일 */
        @media print {{
            .genspark-slide {{
                position: static !important;
                display: block !important;
                page-break-after: always;
            }}
            
            #slide-controls,
            #slide-progress,
            #slide-info {{
                display: none !important;
            }}
        }}
        
        /* 로딩 애니메이션 */
        .loading {{
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 3px solid rgba(255,255,255,.3);
            border-radius: 50%;
            border-top-color: #fff;
            animation: spin 1s ease-in-out infinite;
        }}
        
        @keyframes spin {{
            to {{ transform: rotate(360deg); }}
        }}
    </style>
</head>
<body>
    <!-- 젠스파크 슬라이드들 -->
    {all_slides_html}
    
    <!-- 네비게이션 컨트롤 -->
    {navigation_html}
    
    <!-- 슬라이드 매니저 스크립트 -->
    {manager_script}
    
    <!-- 최종 초기화 -->
    <script>
        // 전역 이벤트 리스너
        window.addEventListener('error', function(e) {{
            console.error('JavaScript 오류:', e.error);
        }});
        
        window.addEventListener('unhandledrejection', function(e) {{
            console.error('Promise 거부:', e.reason);
        }});
        
        // 페이지 완전 로드 후 실행
        window.addEventListener('load', function() {{
            document.body.style.opacity = '1';
            console.log('젠스파크 슬라이드 프레젠테이션 준비 완료');
        }});
    </script>
</body>
</html>"""
        
        return complete_html

    def convert(self, input_file, output_file):
        """메인 변환 함수"""
        try:
            # 입력 파일 읽기
            self.log(f"입력 파일 읽는 중: {input_file}")
            with open(input_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 첫 슬라이드 제목 추출
            self.first_slide_title = self.extract_first_slide_title(content)
            self.log(f"첫 슬라이드 제목: {self.first_slide_title}")
            
            # 슬라이드 분할
            self.log("슬라이드 분할 중...")
            slides = re.split(r'<html[^>]*>', content, flags=re.IGNORECASE)[1:]
            
            if not slides:
                raise ValueError("유효한 슬라이드를 찾을 수 없습니다. 입력 파일이 <html>로 구분된 슬라이드를 포함하는지 확인하세요.")
            
            self.total_slides = len(slides)
            self.log(f"발견된 슬라이드 수: {self.total_slides}")
            
            # 각 슬라이드 처리
            processed_slides = []
            for i, slide_content in enumerate(slides):
                # HTML 구조 복원
                slide_html = f"<html>{slide_content}"
                if '</html>' not in slide_html.lower():
                    slide_html += '</html>'
                
                processed_slide = self.process_single_slide(slide_html, i)
                processed_slides.append(processed_slide)
            
            # 완전한 HTML 생성
            self.log("최종 HTML 생성 중...")
            complete_html = self.create_complete_html(processed_slides)
            
            # 출력 디렉터리 생성
            output_file.parent.mkdir(parents=True, exist_ok=True)
            
            # 출력 파일 저장
            self.log(f"출력 파일 저장 중: {output_file}")
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(complete_html)
            
            # 결과 통계
            file_size = os.path.getsize(output_file) / 1024 / 1024
            
            # 캐시 정보
            cached_count = sum(1 for r in self.downloaded_resources.values() if r.get('cached', False))
            downloaded_count = len(self.downloaded_resources) - cached_count
            
            self.log("=" * 60)
            self.log("✅ 변환 완료!")
            self.log(f"   입력 파일: {input_file}")
            self.log(f"   출력 파일: {output_file}")
            self.log(f"   파일 크기: {file_size:.2f} MB")
            self.log(f"   슬라이드 수: {self.total_slides}")
            self.log(f"   슬라이드 제목: {self.first_slide_title}")
            self.log(f"   처리된 슬라이드: {self.processed_slides}")
            self.log(f"   리소스 처리: 총 {len(self.downloaded_resources)}개 (캐시: {cached_count}, 다운로드: {downloaded_count})")
            self.log("   해결된 모든 문제: targetPriceDistChart 포함, Font Awesome 완전 임베딩, Chart.js 다중 차트 완전 동작")
            self.log(f"   브라우저에서 열어보세요: file://{os.path.abspath(output_file)}")
            self.log("=" * 60)
            
            return True
            
        except Exception as e:
            self.log(f"변환 실패: {str(e)}", "ERROR")
            return False

def main():
    """메인 함수"""
    parser = argparse.ArgumentParser(
        description='젠스파크 AI 슬라이드를 오프라인 단일 HTML로 변환 (v4.6 최종완성판)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
완전 해결된 모든 문제점 (v4.6):
  • targetPriceDistChart 변수명 누락 문제 해결
  • Font Awesome 폰트 완전 임베딩 (모든 경로 패턴 처리)
  • Chart.js 다중 차트 충돌 완전 해결 (순차 초기화)
  • Canvas getContext 오류 완전 수정 (안전한 접근)
  • const 변수 충돌 완전 방지 (IIFE 스코프 격리)
  • 젠스파크 원본 스타일 100% 보존
  • 디버깅 로그 강화 (차트 생성 과정 추적)
  • 완전 오프라인 지원

사용법:
  python converter.py filename.html
  → 입력: source/filename.html  
  → 출력: output/filename_ppt.html

단축키:
  • 방향키/스페이스/PageUp,Down: 슬라이드 이동
  • Home/End: 처음/마지막 슬라이드
  • F11: 전체화면, ESC: 전체화면 해제
  • 마우스 휠: 슬라이드 내부 스크롤만
        """
    )
    
    parser.add_argument('filename', help='변환할 파일명 (source/ 폴더 내)')
    parser.add_argument('--verbose', '-v', action='store_true', help='상세 로그 출력')
    parser.add_argument('--clear-cache', action='store_true', help='캐시 초기화')
    
    args = parser.parse_args()
    
    # 변환기 초기화
    converter = GenSparkConverter()
    
    # 캐시 초기화 옵션
    if args.clear_cache:
        import shutil
        if converter.cache_dir.exists():
            shutil.rmtree(converter.cache_dir)
            converter.cache_dir.mkdir()
            print("캐시가 초기화되었습니다.")
        return 0
    
    # 파일 경로 해결
    input_path, output_path = converter.resolve_file_paths(args.filename)
    
    # 입력 파일 존재 확인
    if not input_path.exists():
        print(f"ERROR: 입력 파일을 찾을 수 없습니다: {input_path}")
        print(f"source/ 폴더에 {args.filename} 파일이 있는지 확인하세요.")
        return 1
    
    # 변환 실행
    success = converter.convert(input_path, output_path)
    
    return 0 if success else 1

if __name__ == '__main__':
    sys.exit(main())
