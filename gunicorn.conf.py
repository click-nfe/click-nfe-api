import os


bind = f"0.0.0.0:{os.getenv('PORT', '8080')}"
workers = int(os.getenv("WEB_CONCURRENCY", "2"))
threads = int(os.getenv("GUNICORN_THREADS", "4"))
timeout = int(os.getenv("GUNICORN_TIMEOUT_SECONDS", "120"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT_SECONDS", "30"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE_SECONDS", "5"))
accesslog = "-"
errorlog = "-"
capture_output = True
