import os

# ✅ Server socket — Render sets PORT env variable
bind = f"0.0.0.0:{os.environ.get('PORT', 5000)}"

workers = 3
threads = 2
timeout = 120
accesslog = "-"
errorlog = "-"
loglevel = "info"
preload_app = True
