import errno
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import os
import sys
from urllib.parse import parse_qs, urlparse

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("mock_scores_api")

HOST = "localhost"
PORT = 8000
# Fallback fake key for local testing only
EXPECTED_API_KEY = os.environ.get("API_KEY", "secret123")

# Sample student dataset with varied scores, string score, extra fields, duplicate name, and bad record
STUDENTS_DATABASE = [
    {"id": 101, "name": "Alice Johnson", "score": 94.5, "class": "Physics A", "email": "alice@school.edu"},
    {"id": 102, "name": "Bob Smith", "score": "85", "class": "Physics A", "email": "bob@school.edu"},
    {"id": 103, "name": "Charlie Brown", "score": 52.0, "class": "Physics B", "email": "charlie@school.edu"},
    {"id": 104, "name": "Diana Prince", "score": 78.5, "class": "Physics A", "email": "diana@school.edu"},
    {"id": 105, "name": "Evan Wright", "score": 63.0, "class": "Physics B", "email": "evan@school.edu"},
    {"id": 106, "name": "Fiona Gallagher", "score": 91.0, "class": "Physics A", "email": "fiona@school.edu"},
    {"id": 107, "name": "George Clark", "score": 44.0, "class": "Physics B", "email": "george@school.edu"},
    {"id": 108, "name": "Hannah Abbott", "score": 88.5, "class": "Physics A", "email": "hannah@school.edu"},
    {"id": 109, "name": "Ian Malcolm", "score": 72.0, "class": "Physics B", "email": "ian@school.edu"},
    {"id": 110, "name": "Julia Roberts", "score": 96.0, "class": "Physics A", "email": "julia@school.edu"},
    # Duplicate name to verify duplicate warning in client
    {"id": 111, "name": "Julia Roberts", "score": 67.5, "class": "Physics B", "email": "julia.r@school.edu"},
    # Out of range score to verify validation skipping
    {"id": 112, "name": "Bad Data Student", "score": 150.0, "class": "Physics B", "email": "bad@school.edu"},
]

PAGE_SIZE = 4
RATE_LIMIT_HIT = False


class MockScoresHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        # Route standard request logs through logging module
        logger.info("%s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:
        global RATE_LIMIT_HIT
        parsed_url = urlparse(self.path)

        if parsed_url.path != "/students":
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "Not Found"}')
            return

        # Validate API key
        received_key = self.headers.get("X-API-Key")
        if not received_key or received_key != EXPECTED_API_KEY:
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "Unauthorized: Invalid or missing API key."}')
            return

        query_params = parse_qs(parsed_url.query)

        # Optional rate limit simulation via header, query param, or env var
        simulate_rate_limit = (
            "simulate_rate_limit" in query_params
            or self.headers.get("X-Simulate-Rate-Limit") == "1"
            or os.environ.get("SIMULATE_RATE_LIMIT") == "1"
        )
        if simulate_rate_limit and not RATE_LIMIT_HIT:
            RATE_LIMIT_HIT = True
            logger.info("Simulating HTTP 429 Rate Limit for test.")
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", "2")
            self.end_headers()
            self.wfile.write(b'{"error": "Too Many Requests - rate limit exceeded."}')
            return

        # Pagination logic
        page_param = query_params.get("page", ["1"])[0]
        try:
            page = max(1, int(page_param))
        except ValueError:
            page = 1

        start_index = (page - 1) * PAGE_SIZE
        end_index = start_index + PAGE_SIZE
        page_items = STUDENTS_DATABASE[start_index:end_index]

        next_url = None
        if end_index < len(STUDENTS_DATABASE):
            next_url = f"http://{HOST}:{PORT}/students?page={page + 1}"

        response_body = {
            "page": page,
            "page_size": PAGE_SIZE,
            "total_items": len(STUDENTS_DATABASE),
            "data": page_items,
            "next": next_url,
        }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response_body).encode("utf-8"))


def run_server() -> None:
    server_address = (HOST, PORT)
    try:
        # Prevent address reuse on Windows so port collisions raise an error
        HTTPServer.allow_reuse_address = False
        httpd = HTTPServer(server_address, MockScoresHandler)
    except OSError as err:
        if err.errno == errno.EADDRINUSE or "10048" in str(err) or "Address already in use" in str(err):
            print(f"Error: Port {PORT} is already in use. Please stop the existing process or free port {PORT}.")
        else:
            print(f"Error starting mock server: {err}")
        sys.exit(1)

    logger.info("Mock Scores API running at http://%s:%d/students", HOST, PORT)
    logger.info("API Key required via API_KEY env var (validated from X-API-Key header)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Mock Scores API stopping...")
        httpd.server_close()


if __name__ == "__main__":
    run_server()
