"""Servidor de protocolo descartável: não carrega modelos nem acessa serviços externos."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/catalog-denied/models":
            self.reply({"error": "Catálogo indisponível no servidor de teste."}, status=403)
            return
        self.reply({"data": [{"id": "modelo-controlado"}]})

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        if size > 1048576:
            self.send_error(413)
            return
        json.loads(self.rfile.read(size))
        self.reply(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Resposta controlada: o container preserva seu contexto.",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 12, "total_tokens": 22},
            }
        )

    def reply(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


ThreadingHTTPServer(("127.0.0.1", 11434), Handler).serve_forever()
