#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
局域网文件共享HTTP服务器
功能：提供文件上传服务，支持二维码访问、密码验证
"""

import os
import sys
import socket
import threading
import webbrowser
import base64
import urllib.parse
import json
import cgi
import time
import logging
from datetime import datetime
import http.server
import socketserver

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
except ImportError:
    print("需要 tkinter 库，请使用: pip install tkinter 或 conda install tkinter")
    sys.exit(1)

try:
    import qrcode
except ImportError:
    qrcode = None

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None


# ============ 配置类 ============
class Config:
    DEFAULT_PORT = 80
    DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Downloads")
    DEFAULT_IP = "0.0.0.0"
    
    def __init__(self):
        self.port = self.DEFAULT_PORT
        self.share_dir = self.DEFAULT_DIR
        self.auth_enabled = False
        self.username = "admin"
        self.password = "admin"
        self.selected_ips = []


# ============ 本次传输记录 ============
class TransferRecord:
    """记录本次服务期间传输的文件"""
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance.files = []
        return cls._instance
    
    def add_file(self, filename, size, upload_time=None, ip=""):
        with self._lock:
            if upload_time is None:
                upload_time = time.time()
            self.files.append({
                'name': filename,
                'size': size,
                'time': upload_time,
                'ip': ip
            })
    
    def get_files(self):
        with self._lock:
            return list(self.files)
    
    def clear(self):
        with self._lock:
            self.files = []
    
    def count(self):
        with self._lock:
            return len(self.files)


# ============ 获取本机IP列表 ============
def get_local_ips():
    """获取本机所有IP地址"""
    ips = []
    hostname = socket.gethostname()
    
    try:
        addrs = socket.getaddrinfo(hostname, None)
        for addr in addrs:
            ip = addr[4][0]
            if ip.startswith('192.168.') or ip.startswith('10.') or ip.startswith('172.'):
                if ip not in ips:
                    ips.append(ip)
    except:
        pass
    
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            if ip:
                ips.append(ip)
        except:
            pass
    
    ips.insert(0, '0.0.0.0')
    
    if '127.0.0.1' not in ips:
        ips.append('127.0.0.1')
    
    return ips


# ============ HTTP请求处理器 ============
class FileShareHandler(http.server.BaseHTTPRequestHandler):
    """自定义HTTP处理器"""
    
    server_version = "FileShareServer/1.0"
    
    def __init__(self, config, *args):
        self.config = config
        super().__init__(*args)
    
    def log_message(self, format, *args):
        """自定义日志格式"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {args[0]}")
    
    def log_request(self, code='-', size='-'):
        if self.path != '/favicon.ico':
            super().log_request(code, size)
    
    def do_GET(self):
        """处理GET请求"""
        if self.path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
            return
        
        rel_path = self._get_rel_path(self.path)
        if rel_path is None:
            self.send_error_page(404, "页面不存在")
            return
        
        if not self.check_auth():
            return
        
        if rel_path == '/' or rel_path == '' or rel_path == '/index.html':
            self.serve_main_page()
        elif rel_path == '/api/files':
            self.serve_file_list()
        elif rel_path.startswith('/download/'):
            filename = urllib.parse.unquote(rel_path[10:])
            self.download_file(filename)
        else:
            self.send_error_page(404, "页面不存在")
    
    def do_POST(self):
        """处理POST请求"""
        rel_path = self._get_rel_path(self.path)
        if rel_path is None:
            self.send_error_page(404, "接口不存在")
            return
        
        if not self.check_auth():
            return
        
        if rel_path == '/api/upload':
            self.handle_upload()
        else:
            self.send_error_page(404, "接口不存在")
    
    def _get_rel_path(self, path):
        """获取相对于URL前缀的路径"""
        url_path = getattr(self.config, 'url_path', '/')
        if url_path == '/' or url_path == '':
            return path
        pure_path = path.split('?')[0]
        if pure_path == url_path:
            return '/'
        if pure_path.startswith(url_path + '/'):
            return pure_path[len(url_path):]
        if pure_path.startswith(url_path + '?'):
            return '/' + pure_path[len(url_path)+1:]
        return None
    
    def check_auth(self):
        """检查认证"""
        if not self.config.auth_enabled:
            return True
        
        auth_header = self.headers.get('Authorization')
        if auth_header and auth_header.startswith('Basic '):
            try:
                encoded = auth_header[6:]
                decoded = base64.b64decode(encoded).decode('utf-8')
                username, password = decoded.split(':', 1)
                if username == self.config.username and password == self.config.password:
                    return True
            except:
                pass
        
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="FileShareServer"')
        self.send_header('Content-type', 'text/html; charset=utf-8')
        html = '''<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>需要登录</title>
<style>
body{font-family:Arial,sans-serif;background:#f0f2ff;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;}
.box{background:white;padding:40px;border-radius:12px;box-shadow:0 10px 40px rgba(0,0,0,0.1);text-align:center;}
h1{color:#667eea;margin:0 0 10px 0;}
p{color:#666;margin:0;}
</style></head>
<body><div class="box"><h1>🔐 需要身份验证</h1><p>请输入用户名和密码以访问文件传输服务器</p></div></body></html>'''.encode('utf-8')
        self.send_header('Content-Length', str(len(html)))
        self.end_headers()
        self.wfile.write(html)
        return False
    
    def serve_main_page(self):
        """提供主页面"""
        html = self.generate_html()
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(html)))
        self.end_headers()
        self.wfile.write(html)
    
    def generate_html(self):
        """生成HTML页面"""
        return '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>文件传输服务器-GXH</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 900px; margin: 0 auto; }
        .card {
            background: white;
            border-radius: 16px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
            animation: slideUp 0.5s ease-out;
        }
        @keyframes slideUp {
            from { opacity: 0; transform: translateY(30px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }
        .header h1 { font-size: 28px; margin-bottom: 10px; }
        .header p { opacity: 0.9; font-size: 14px; }
        .upload-area { padding: 30px; border-bottom: 1px solid #eee; }
        .upload-btn {
            display: block; width: 100%; padding: 40px;
            border: 3px dashed #667eea; border-radius: 12px;
            text-align: center; cursor: pointer;
            transition: all 0.3s ease; background: #f8f9ff;
        }
        .upload-btn:hover {
            border-color: #764ba2; background: #f0f2ff; transform: scale(1.02);
        }
        .upload-btn.dragover {
            border-color: #764ba2; background: #f0f2ff;
        }
        .upload-btn span { font-size: 48px; display: block; margin-bottom: 15px; }
        .upload-btn p { color: #667eea; font-size: 16px; }
        .progress-bar {
            width: 100%; height: 6px; background: #e0e0e0;
            border-radius: 3px; margin-top: 15px; overflow: hidden; display: none;
        }
        .progress-fill {
            height: 100%; background: linear-gradient(90deg, #667eea, #764ba2);
            width: 0%; transition: width 0.3s;
        }
        .file-list { padding: 20px 30px; }
        .file-list h3 { color: #333; margin-bottom: 15px; font-size: 18px; }
        .file-item {
            display: grid;
            grid-template-columns: 50px 1fr 100px 120px;
            align-items: center;
            padding: 12px 15px; border-radius: 8px;
            margin-bottom: 8px; background: #f8f9fa;
            transition: all 0.2s ease;
        }
        .file-item:hover { background: #e8eaff; transform: translateX(5px); }
        .file-icon { font-size: 24px; text-align: center; }
        .file-name {
            color: #333; font-weight: 500;
            overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding-right: 10px;
        }
        .file-type { color: #667eea; font-size: 12px; text-transform: uppercase; }
        .file-size { color: #888; font-size: 13px; text-align: right; }
        .empty { text-align: center; padding: 40px; color: #888; }
        .empty span { font-size: 48px; display: block; margin-bottom: 10px; }
        .hidden-input { display: none; }
        .file-count {
            padding: 15px 30px; background: #f8f9fa; color: #666;
            font-size: 13px; border-top: 1px solid #eee;
        }
        .toast {
            position: fixed; top: 20px; right: 20px;
            padding: 15px 25px; background: #333; color: white;
            border-radius: 8px; z-index: 9999;
            transform: translateX(400px); transition: transform 0.3s;
        }
        .toast.show { transform: translateX(0); }
        .toast.success { background: #4caf50; }
        .toast.error { background: #f44336; }
    </style>
</head>
<body>
    <div class="toast" id="toast"></div>
    <div class="container">
        <div class="card">
            <div class="header">
                <h1>📁 文件传输服务器-GXH</h1>
                <p>拖拽文件到此处或点击选择文件上传（支持批量上传）</p>
            </div>
            <div class="upload-area" id="uploadArea">
                <div class="upload-btn" id="uploadBtn" onclick="document.getElementById('fileInput').click()">
                    <span>☁️</span>
                    <p>点击选择文件 或 拖拽文件到此处</p>
                </div>
                <div class="progress-bar" id="progressBar"><div class="progress-fill" id="progressFill"></div></div>
                <input type="file" id="fileInput" class="hidden-input" multiple onchange="uploadFiles(this.files)">
            </div>
            <div class="file-list">
                <h3>📂 已上传文件</h3>
                <div id="fileList"></div>
            </div>
            <div class="file-count" id="fileCount">共 0 个文件</div>
        </div>
    </div>

    <script>
        const basePath = (function() {
            const path = window.location.pathname;
            if (path === '/' || path === '') return '';
            if (path.endsWith('/')) return path.slice(0, -1);
            return path;
        })();
        
        function getFileIcon(filename) {
            const ext = filename.split('.').pop().toLowerCase();
            const icons = {
                'jpg': '🖼️', 'jpeg': '🖼️', 'png': '🖼️', 'gif': '🖼️', 'bmp': '🖼️', 'webp': '🖼️', 'svg': '🖼️',
                'mp4': '🎬', 'avi': '🎬', 'mov': '🎬', 'mkv': '🎬', 'wmv': '🎬', 'flv': '🎬',
                'mp3': '🎵', 'wav': '🎵', 'flac': '🎵', 'aac': '🎵', 'ogg': '🎵',
                'pdf': '📕',
                'zip': '📦', 'rar': '📦', '7z': '📦', 'tar': '📦', 'gz': '📦',
                'doc': '📄', 'docx': '📄', 'xls': '📊', 'xlsx': '📊', 'ppt': '📽️', 'pptx': '📽️',
                'txt': '📝', 'rtf': '📝',
                'html': '🌐', 'css': '🎨', 'js': '⚡', 'json': '📋',
                'exe': '⚙️', 'msi': '⚙️', 'dmg': '⚙️', 'apk': '📱'
            };
            return icons[ext] || '📄';
        }

        function formatSize(bytes) {
            if (bytes === 0) return '0 B';
            const k = 1024;
            const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
        }

        function showToast(msg, type='success') {
            const toast = document.getElementById('toast');
            toast.textContent = msg;
            toast.className = 'toast show ' + type;
            setTimeout(() => { toast.className = 'toast'; }, 3000);
        }

        async function loadFiles() {
            try {
                const resp = await fetch(basePath + '/api/files');
                if (!resp.ok) throw new Error('加载失败');
                const data = await resp.json();
                renderFiles(data.files);
            } catch (e) {
                console.error('加载文件列表失败:', e);
            }
        }

        function renderFiles(files) {
            const list = document.getElementById('fileList');
            const count = document.getElementById('fileCount');
            count.textContent = '共 ' + files.length + ' 个文件';
            
            if (files.length === 0) {
                list.innerHTML = '<div class="empty"><span>📭</span><p>暂无上传文件</p></div>';
                return;
            }
            
            list.innerHTML = files.map((f, i) =>
                '<div class="file-item">' +
                    '<div class="file-icon">' + getFileIcon(f.name) + '</div>' +
                    '<div class="file-name" title="' + f.name + '">' + f.name + '</div>' +
                    '<div class="file-type">.' + (f.ext || '') + '</div>' +
                    '<div class="file-size">' + formatSize(f.size) + '</div>' +
                '</div>'
            ).join('');
        }

        async function uploadFiles(files) {
            if (!files || files.length === 0) return;
            
            const progressBar = document.getElementById('progressBar');
            const progressFill = document.getElementById('progressFill');
            const totalFiles = files.length;
            let completed = 0;
            
            progressBar.style.display = 'block';
            progressFill.style.width = '0%';
            
            let successCount = 0;
            let failCount = 0;
            
            for (const file of files) {
                const formData = new FormData();
                formData.append('file', file);
                
                try {
                    const resp = await fetch(basePath + '/api/upload', {
                        method: 'POST',
                        body: formData
                    });
                    
                    if (!resp.ok) throw new Error('上传失败');
                    successCount++;
                } catch (e) {
                    failCount++;
                    console.error('上传失败:', file.name, e);
                }
                
                completed++;
                progressFill.style.width = (completed / totalFiles * 100) + '%';
            }
            
            setTimeout(() => {
                progressBar.style.display = 'none';
            }, 1000);
            
            loadFiles();
            document.getElementById('fileInput').value = '';
            
            if (successCount > 0) {
                showToast('成功上传 ' + successCount + ' 个文件', 'success');
            }
            if (failCount > 0) {
                showToast(failCount + ' 个文件上传失败', 'error');
            }
        }

        const uploadArea = document.getElementById('uploadArea');
        const uploadBtn = document.getElementById('uploadBtn');
        
        uploadArea.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadBtn.classList.add('dragover');
        });
        
        uploadArea.addEventListener('dragleave', (e) => {
            e.preventDefault();
            uploadBtn.classList.remove('dragover');
        });
        
        uploadArea.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadBtn.classList.remove('dragover');
            uploadFiles(e.dataTransfer.files);
        });

        loadFiles();
        setInterval(loadFiles, 5000);
    </script>
</body>
</html>'''.encode('utf-8')
    
    def get_file_list(self):
        """获取本次上传的文件列表"""
        record = TransferRecord()
        files = record.get_files()
        result = []
        for f in files:
            name = f['name']
            ext = name.split('.')[-1].lower() if '.' in name else ''
            result.append({
                'name': name,
                'size': f['size'],
                'mtime': f['time'],
                'ext': ext
            })
        return result
    
    def serve_file_list(self):
        """提供文件列表API"""
        files = self.get_file_list()
        data = json.dumps({'files': files}, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    
    def download_file(self, filename):
        """下载文件"""
        fpath = os.path.join(self.config.share_dir, filename)
        
        if not os.path.exists(fpath) or not os.path.isfile(fpath):
            self.send_error_page(404, "文件不存在")
            return
        
        try:
            with open(fpath, 'rb') as f:
                data = f.read()
            
            content_type = 'application/octet-stream'
            ext = filename.split('.')[-1].lower() if '.' in filename else ''
            
            mime_types = {
                'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
                'gif': 'image/gif', 'webp': 'image/webp', 'bmp': 'image/bmp',
                'mp4': 'video/mp4', 'avi': 'video/x-msvideo', 'mov': 'video/quicktime',
                'mkv': 'video/x-matroska', 'wmv': 'video/x-ms-wmv',
                'mp3': 'audio/mpeg', 'wav': 'audio/wav', 'flac': 'audio/flac',
                'pdf': 'application/pdf',
                'zip': 'application/zip', 'rar': 'application/vnd.rar',
                '7z': 'application/x-7z-compressed', 'tar': 'application/x-tar',
                'txt': 'text/plain', 'html': 'text/html', 'css': 'text/css',
                'js': 'application/javascript', 'json': 'application/json',
                'doc': 'application/msword', 'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                'xls': 'application/vnd.ms-excel', 'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            }
            
            content_type = mime_types.get(ext, 'application/octet-stream')
            
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Disposition', 'attachment; filename*=UTF-8\'\'' + urllib.parse.quote(filename))
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_error_page(500, str(e))
    
    def send_error_page(self, code, message):
        """发送错误页面"""
        html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>{code} - {message}</title>
<style>
body{{font-family:Arial,sans-serif;background:#f0f2ff;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;}}
.box{{background:white;padding:40px;border-radius:12px;box-shadow:0 10px 40px rgba(0,0,0,0.1);text-align:center;}}
h1{{color:#f44336;margin:0 0 10px 0;}}
p{{color:#666;margin:0;}}
</style></head>
<body><div class="box"><h1>❌ {code}</h1><p>{message}</p></div></body></html>'''.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(html)))
        self.end_headers()
        self.wfile.write(html)
    
    def handle_upload(self):
        """处理文件上传"""
        client_ip = self.client_address[0]
        try:
            content_type = self.headers.get('Content-Type')
            if content_type is None or 'multipart/form-data' not in content_type:
                self.send_error_page(400, "需要multipart/form-data")
                return
            
            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={
                    'REQUEST_METHOD': 'POST',
                    'CONTENT_TYPE': content_type,
                }
            )
            
            if 'file' not in form:
                self.send_error_page(400, "没有上传文件")
                return
            
            file_item = form['file']
            if isinstance(file_item, list):
                file_item = file_item[0]
            
            filename = file_item.filename
            if not filename:
                self.send_error_page(400, "没有选择文件")
                return
            
            filename = os.path.basename(filename)
            filename = filename.replace('\\', '_').replace('..', '_').replace('/', '_')
            
            fpath = os.path.join(self.config.share_dir, filename)
            
            if os.path.exists(fpath):
                name, ext = os.path.splitext(filename)
                filename = f"{name}_{datetime.now().strftime('%Y%m%d%H%M%S')}{ext}"
                fpath = os.path.join(self.config.share_dir, filename)
            
            file_data = file_item.file.read()
            with open(fpath, 'wb') as f:
                f.write(file_data)
            
            file_size = len(file_data)
            
            record = TransferRecord()
            record.add_file(filename, file_size, ip=client_ip)
            
            data = json.dumps({'success': True, 'filename': filename, 'size': file_size}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            
        except Exception as e:
            error_data = json.dumps({'success': False, 'error': str(e)}).encode('utf-8')
            self.send_response(500)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(error_data)))
            self.end_headers()
            self.wfile.write(error_data)


# ============ HTTP服务器线程 ============
class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """支持多线程的HTTP服务器"""
    allow_reuse_address = True
    daemon_threads = True
    
    def __init__(self, config, server_address, HandlerClass):
        self.config = config
        super().__init__(server_address, HandlerClass)


# ============ GUI应用 ============
class FileShareApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("文件传输服务器-GXH")
        self.root.geometry("850x700")
        self.root.resizable(True, True)
        
        self.config = Config()
        self.server = None
        self.server_thread = None
        self.is_running = False
        self.start_time = None
        self.url_path = self.generate_url_path()
        
        self.setup_ui()
        self.refresh_ips()
        self.load_config()
        self.start_refresh_timer()
    
    def setup_ui(self):
        """设置UI"""
        main_frame = ttk.Frame(self.root, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        title_label = ttk.Label(main_frame, text="📁 文件传输服务器-GXH", 
                                font=("Microsoft YaHei", 20, "bold"))
        title_label.pack(pady=(0, 20))
        
        config_frame = ttk.LabelFrame(main_frame, text="服务器配置", padding="15")
        config_frame.pack(fill=tk.X, pady=(0, 15))
        
        ip_frame = ttk.Frame(config_frame)
        ip_frame.pack(fill=tk.X, pady=5)
        ttk.Label(ip_frame, text="监听地址:").pack(side=tk.LEFT)
        self.ip_var = tk.StringVar()
        self.ip_combo = ttk.Combobox(ip_frame, textvariable=self.ip_var, state='readonly', width=20)
        self.ip_combo.pack(side=tk.LEFT, padx=10)
        
        ttk.Label(ip_frame, text="端口:").pack(side=tk.LEFT, padx=(20, 5))
        self.port_var = tk.StringVar(value=str(self.config.DEFAULT_PORT))
        port_entry = ttk.Entry(ip_frame, textvariable=self.port_var, width=10)
        port_entry.pack(side=tk.LEFT)
        
        dir_frame = ttk.Frame(config_frame)
        dir_frame.pack(fill=tk.X, pady=5)
        ttk.Label(dir_frame, text="存放目录:").pack(side=tk.LEFT)
        self.dir_var = tk.StringVar(value=self.config.share_dir)
        dir_entry = ttk.Entry(dir_frame, textvariable=self.dir_var, width=50)
        dir_entry.pack(side=tk.LEFT, padx=10, fill=tk.X, expand=True)
        ttk.Button(dir_frame, text="浏览...", command=self.browse_dir).pack(side=tk.LEFT)
        
        info_frame = ttk.LabelFrame(main_frame, text="访问信息", padding="15")
        info_frame.pack(fill=tk.X, pady=(0, 15))
        
        link_frame = ttk.Frame(info_frame)
        link_frame.pack(fill=tk.X, pady=5)
        ttk.Label(link_frame, text="访问链接:").pack(side=tk.LEFT)
        self.link_var = tk.StringVar(value="未启动服务")
        link_entry = ttk.Entry(link_frame, textvariable=self.link_var, width=50, state='readonly')
        link_entry.pack(side=tk.LEFT, padx=10, fill=tk.X, expand=True)
        ttk.Button(link_frame, text="复制", command=self.copy_link, width=8).pack(side=tk.LEFT)
        ttk.Button(link_frame, text="打开浏览器", command=self.open_browser, width=12).pack(side=tk.LEFT, padx=5)
        
        url_frame = ttk.Frame(info_frame)
        url_frame.pack(fill=tk.X, pady=5)
        ttk.Label(url_frame, text="访问路径:").pack(side=tk.LEFT)
        self.url_path_var = tk.StringVar(value=self.url_path)
        self.url_path_entry = ttk.Entry(url_frame, textvariable=self.url_path_var, width=20)
        self.url_path_entry.pack(side=tk.LEFT, padx=10)
        ttk.Button(url_frame, text="🔄 刷新路径", command=self.refresh_url_path, width=12).pack(side=tk.LEFT)
        
        qr_frame = ttk.Frame(info_frame)
        qr_frame.pack(fill=tk.X, pady=10)
        ttk.Label(qr_frame, text="二维码:").pack(side=tk.LEFT, anchor=tk.N)
        self.qr_label = ttk.Label(qr_frame, text="(启动服务后显示二维码)")
        self.qr_label.pack(side=tk.LEFT, padx=10)
        
        auth_frame = ttk.LabelFrame(main_frame, text="访问密码", padding="15")
        auth_frame.pack(fill=tk.X, pady=(0, 15))
        
        auth_top_frame = ttk.Frame(auth_frame)
        auth_top_frame.pack(fill=tk.X, pady=5)
        self.auth_var = tk.BooleanVar(value=False)
        auth_check = ttk.Checkbutton(auth_top_frame, text="启用密码验证", 
                                      variable=self.auth_var, command=self.on_auth_toggle)
        auth_check.pack(side=tk.LEFT)
        
        auth_creds_frame = ttk.Frame(auth_frame)
        auth_creds_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(auth_creds_frame, text="账号:").pack(side=tk.LEFT)
        self.username_var = tk.StringVar(value=self.config.username)
        self.username_entry = ttk.Entry(auth_creds_frame, textvariable=self.username_var, width=20, state='disabled')
        self.username_entry.pack(side=tk.LEFT, padx=(5, 20))
        
        ttk.Label(auth_creds_frame, text="密码:").pack(side=tk.LEFT)
        self.password_var = tk.StringVar(value=self.config.password)
        self.password_entry = ttk.Entry(auth_creds_frame, textvariable=self.password_var, width=20, show="*", state='disabled')
        self.password_entry.pack(side=tk.LEFT, padx=5)
        
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(0, 15))
        
        self.start_btn = tk.Button(btn_frame, text="▶ 启动服务", command=self.toggle_server,
                                   font=("Microsoft YaHei", 14, "bold"),
                                   bg="#4CAF50", fg="white",
                                   activebackground="#45a049", activeforeground="white",
                                   relief=tk.RAISED, bd=3, padx=30, pady=12,
                                   cursor="hand2")
        self.start_btn.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(btn_frame, text="📂 打开目录", command=self.open_share_dir).pack(side=tk.LEFT, padx=10)
        
        list_frame = ttk.LabelFrame(main_frame, text="本次已接收文件", padding="15")
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        columns = ("序号", "文件名", "类型", "大小", "上传时间", "来源IP")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=12)
        self.tree.heading("序号", text="序号")
        self.tree.heading("文件名", text="文件名")
        self.tree.heading("类型", text="类型")
        self.tree.heading("大小", text="大小")
        self.tree.heading("上传时间", text="上传时间")
        self.tree.heading("来源IP", text="来源IP")
        
        self.tree.column("序号", width=50, anchor=tk.CENTER)
        self.tree.column("文件名", width=200, anchor=tk.W)
        self.tree.column("类型", width=60, anchor=tk.CENTER)
        self.tree.column("大小", width=80, anchor=tk.E)
        self.tree.column("上传时间", width=130, anchor=tk.CENTER)
        self.tree.column("来源IP", width=100, anchor=tk.CENTER)
        
        tree_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="打开文件", command=self.open_file)
        self.context_menu.add_command(label="打开所在目录", command=self.open_file_dir)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="刷新列表", command=self.refresh_file_list)
        self.tree.bind("<Button-3>", self.show_context_menu)
        
        self.status_var = tk.StringVar(value="就绪")
        status_label = ttk.Label(main_frame, textvariable=self.status_var, 
                                  font=("Microsoft YaHei", 9), foreground="#666")
        status_label.pack()
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
    
    def start_refresh_timer(self):
        self._refresh_file_list_gui()
        self.root.after(2000, self.start_refresh_timer)
    
    def refresh_ips(self):
        ips = get_local_ips()
        self.ip_combo['values'] = ips
        if ips:
            self.ip_var.set(Config.DEFAULT_IP)
    
    def generate_url_path(self):
        import random
        import string
        return '/' + ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    
    def refresh_url_path(self):
        if self.is_running:
            messagebox.showinfo("提示", "服务运行中无法修改路径，请先停止服务")
            return
        self.url_path = self.generate_url_path()
        self.url_path_var.set(self.url_path)
    
    def browse_dir(self):
        dir_path = filedialog.askdirectory(initialdir=self.dir_var.get())
        if dir_path:
            self.dir_var.set(dir_path)
    
    def on_auth_toggle(self):
        if self.auth_var.get():
            self.username_entry.config(state='normal')
            self.password_entry.config(state='normal')
        else:
            self.username_entry.config(state='disabled')
            self.password_entry.config(state='disabled')
    
    def toggle_server(self):
        if self.is_running:
            self.stop_server()
        else:
            self.start_server()
    
    def start_server(self):
        try:
            ip = self.ip_var.get()
            port = int(self.port_var.get())
            share_dir = self.dir_var.get()
            
            if not share_dir:
                messagebox.showwarning("警告", "请选择文件存放目录")
                return
            
            if not os.path.exists(share_dir):
                try:
                    os.makedirs(share_dir)
                except Exception as e:
                    messagebox.showerror("错误", f"无法创建目录: {e}")
                    return
            
            self.config.port = port
            self.config.share_dir = share_dir
            self.config.auth_enabled = self.auth_var.get()
            self.config.username = self.username_var.get()
            self.config.password = self.password_var.get()
            self.config.selected_ips = [ip]
            
            path_input = self.url_path_var.get().strip()
            if not path_input.startswith('/'):
                path_input = '/' + path_input
            self.url_path = path_input
            self.config.url_path = self.url_path
            
            record = TransferRecord()
            record.clear()
            self.start_time = time.time()
            
            for item in self.tree.get_children():
                self.tree.delete(item)
            
            server_address = (ip, port)
            
            def handler_class(*args):
                FileShareHandler(self.config, *args)
            
            self.server = ThreadedHTTPServer(self.config, server_address, handler_class)
            
            self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.server_thread.start()
            
            self.is_running = True
            self.start_btn.config(text="■ 停止服务", bg="#f44336", activebackground="#d32f2f")
            
            display_ip = ip
            if ip == '0.0.0.0':
                local_ips = get_local_ips()
                for lip in local_ips:
                    if lip != '0.0.0.0' and lip != '127.0.0.1' and not lip.startswith('127.'):
                        display_ip = lip
                        break
                if display_ip == '0.0.0.0':
                    display_ip = '127.0.0.1'
            
            url = f"http://{display_ip}:{port}{self.url_path}"
            self.link_var.set(url)
            
            self.generate_qr(url)
            
            self.refresh_file_list()
            
            self.status_var.set(f"✅ 服务已启动 | 监听: {ip}:{port}")
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 服务已启动: {ip}:{port}")
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 访问地址: {url}")
            
        except PermissionError:
            messagebox.showerror("错误", f"端口 {port} 需要管理员权限，请更换端口或以管理员身份运行")
            self.is_running = False
        except OSError as e:
            if '10048' in str(e) or 'Address already in use' in str(e):
                messagebox.showerror("错误", f"端口 {port} 已被占用，请更换端口")
            else:
                messagebox.showerror("错误", f"启动服务失败: {e}")
            self.is_running = False
        except Exception as e:
            messagebox.showerror("错误", f"启动服务失败: {e}")
            self.is_running = False
    
    def stop_server(self):
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            except:
                pass
            self.server = None
        
        self.is_running = False
        self.start_btn.config(text="▶ 启动服务", bg="#4CAF50", activebackground="#45a049")
        self.link_var.set("服务已停止")
        self.qr_label.config(image='', text="(启动服务后显示二维码)")
        self.status_var.set("服务已停止")
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 服务已停止")
    
    def generate_qr(self, url):
        if qrcode is None or Image is None or ImageTk is None:
            self.qr_label.config(text="(需安装 qrcode 和 pillow)")
            return
        
        try:
            qr = qrcode.QRCode(version=1, box_size=10, border=2)
            qr.add_data(url)
            qr.make(fit=True)
            
            img = qr.make_image(fill_color="black", back_color="white")
            img = img.resize((150, 150), Image.LANCZOS)
            
            self.qr_image = ImageTk.PhotoImage(img)
            self.qr_label.config(image=self.qr_image, text="")
        except Exception as e:
            self.qr_label.config(text=f"二维码生成失败: {e}")
    
    def copy_link(self):
        link = self.link_var.get()
        if link and link != "未启动服务" and link != "服务已停止":
            self.root.clipboard_clear()
            self.root.clipboard_append(link)
            self.status_var.set("链接已复制到剪贴板")
    
    def open_browser(self):
        link = self.link_var.get()
        if link and link.startswith("http"):
            webbrowser.open(link)
    
    def open_share_dir(self):
        share_dir = self.dir_var.get()
        if os.path.exists(share_dir):
            os.startfile(share_dir)
    
    def _refresh_file_list_gui(self):
        if not self.is_running:
            return
        
        try:
            record = TransferRecord()
            files = record.get_files()
            
            current_items = self.tree.get_children()
            current_count = len(current_items)
            
            if len(files) != current_count:
                for item in current_items:
                    self.tree.delete(item)
                
                for i, f in enumerate(files, 1):
                    name = f['name']
                    ext = name.split('.')[-1].lower() if '.' in name else ''
                    size_str = self.format_size(f['size'])
                    time_str = datetime.fromtimestamp(f['time']).strftime('%Y-%m-%d %H:%M:%S')
                    ip = f.get('ip', '')
                    self.tree.insert("", "end", values=(i, name, ext, size_str, time_str, ip))
                
                self.status_var.set(f"✅ 运行中 | 已接收 {len(files)} 个文件")
        except:
            pass
    
    def refresh_file_list(self):
        self._refresh_file_list_gui()
    
    def format_size(self, size):
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size/1024:.1f} KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size/1024/1024:.1f} MB"
        else:
            return f"{size/1024/1024/1024:.2f} GB"
    
    def show_context_menu(self, event):
        selection = self.tree.selection()
        if selection:
            self.context_menu.post(event.x_root, event.y_root)
    
    def _get_selected_filename(self):
        selection = self.tree.selection()
        if selection:
            item = selection[0]
            values = self.tree.item(item, "values")
            if values and len(values) >= 2:
                return values[1]
        return None
    
    def open_file(self):
        filename = self._get_selected_filename()
        if filename:
            fpath = os.path.join(self.config.share_dir, filename)
            if os.path.exists(fpath):
                os.startfile(fpath)
    
    def open_file_dir(self):
        filename = self._get_selected_filename()
        if filename:
            fpath = os.path.join(self.config.share_dir, filename)
            if os.path.exists(fpath):
                os.startfile(os.path.dirname(fpath))
    
    def load_config(self):
        self.port_var.set(str(self.config.port))
        self.dir_var.set(self.config.share_dir)
        self.auth_var.set(self.config.auth_enabled)
        self.username_var.set(self.config.username)
        self.password_var.set(self.config.password)
        self.on_auth_toggle()
    
    def save_transfer_log(self):
        record = TransferRecord()
        files = record.get_files()
        
        if not files:
            return
        
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        
        log_filename = f"transfer_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        log_path = os.path.join(log_dir, log_filename)
        
        try:
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write("=" * 60 + "\n")
                f.write(f"文件传输记录\n")
                f.write(f"启动时间: {datetime.fromtimestamp(self.start_time).strftime('%Y-%m-%d %H:%M:%S') if self.start_time else 'N/A'}\n")
                f.write(f"结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"存放目录: {self.config.share_dir}\n")
                f.write(f"监听地址: {self.config.selected_ips[0] if self.config.selected_ips else ''}:{self.config.port}\n")
                f.write(f"文件总数: {len(files)}\n")
                total_size = sum(f['size'] for f in files)
                f.write(f"总大小: {self.format_size(total_size)}\n")
                f.write("=" * 60 + "\n\n")
                f.write(f"{'序号':<6}{'文件名':<35}{'类型':<8}{'大小':<12}{'上传时间':<20}{'来源IP':<15}\n")
                f.write("-" * 96 + "\n")
                
                for i, f_ in enumerate(files, 1):
                    name = f_['name']
                    if len(name) > 33:
                        name = name[:30] + "..."
                    ext = name.split('.')[-1].lower() if '.' in name else ''
                    size_str = self.format_size(f_['size'])
                    time_str = datetime.fromtimestamp(f_['time']).strftime('%Y-%m-%d %H:%M:%S')
                    ip = f_.get('ip', '')
                    f.write(f"{i:<6}{name:<35}{ext:<8}{size_str:<12}{time_str:<20}{ip:<15}\n")
            
            print(f"[日志] 传输记录已保存到: {log_path}")
            return log_path
        except Exception as e:
            print(f"[日志] 保存失败: {e}")
            return None
    
    def on_close(self):
        if self.is_running:
            self.save_transfer_log()
            self.stop_server()
        self.root.destroy()


# ============ 主程序 ============
def main():
    missing = []
    if qrcode is None:
        missing.append("qrcode")
    if Image is None:
        missing.append("Pillow")
    
    if missing:
        print(f"提示: 缺少库: {', '.join(missing)}，二维码功能将不可用")
        print(f"安装命令: pip install {' '.join(missing)}")
        print()
    
    app = FileShareApp()
    app.root.mainloop()


if __name__ == "__main__":
    main()
