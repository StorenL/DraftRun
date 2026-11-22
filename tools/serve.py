#!/usr/bin/env python3
"""本地预览服务器。

行为尽量贴近线上：把 /posts/xxx 映射到 public/posts/xxx.html，
这样站内链接（不带 .html 后缀）在本地也能点通。

用法：
    python tools/serve.py            # 默认 8765 端口
    python tools/serve.py 8080       # 指定端口
"""

import http.server
import os
import socketserver
import sys
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"


class Handler(http.server.SimpleHTTPRequestHandler):
    """请求 /a/b 时，若磁盘上只有 a/b.html，就返回 a/b.html。"""

    def translate_path(self, path):
        candidate = super().translate_path(path)
        if os.path.isdir(candidate):
            index = os.path.join(candidate, "index.html")
            if os.path.exists(index):
                return index
            return candidate
        if not os.path.exists(candidate) and os.path.exists(candidate + ".html"):
            return candidate + ".html"
        return candidate

    def send_head(self):
        # 目录且没有 index.html 时不要列目录，直接 404
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            self.send_error(404, "Not found")
            return None
        return super().send_head()

    def end_headers(self):
        # 本地开发禁用缓存，改完刷新就能看到
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    if not PUBLIC.exists():
        raise SystemExit("public/ 不存在，先运行 python tools/build.py")

    handler = partial(Handler, directory=str(PUBLIC))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        print("潦草地 · 本地预览")
        print(f"  首页    http://localhost:{port}/")
        print(f"  文章页  http://localhost:{port}/posts/android-app-launch-flow")
        print("  Ctrl+C 停止")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止")


if __name__ == "__main__":
    main()
