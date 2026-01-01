import os
import sys
import json
import time
import math
import signal
import redis
import subprocess

# === KONFIGURACIJA ===
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
QUEUE_NAME = os.getenv("QUEUE_NAME", "task_queue")

MIN_WORKERS = 2
MAX_WORKERS = 10
TASKS_PER_WORKER = 5
MAX_STEP_UP = 2
MAX_STEP_DOWN = 1
COOLDOWN = 60
CHECK_INTERVAL = 20
HYSTERESIS = 0.3  # 30% dead zone - sprječava oscilacije

# === GRACEFUL SHUTDOWN ===
_shutdown = False

def _handle_signal(sig, frame):
    global _shutdown
    log("info", "shutdown", {"signal": sig})
    _shutdown = True

signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def log(level, event, data=None):
    """JSON structured log."""
    print(json.dumps({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "level": level,
        "event": event,
        **(data or {})
    }), flush=True)


def get_current_workers():
    """Broj aktivnih worker kontejnera."""
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "label=com.docker.compose.service=worker", "-q"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return MIN_WORKERS
        return len([l for l in result.stdout.strip().split('\n') if l])
    except subprocess.TimeoutExpired:
        log("error", "docker_timeout")
        return MIN_WORKERS
    except Exception as e:
        log("error", "docker_error", {"error": str(e)})
        return MIN_WORKERS


def scale_to(count):
    """Skaliraj workere."""
    try:
        result = subprocess.run(
            ["docker", "compose", "up", "-d", "--scale", f"worker={count}", "--no-recreate"],
            capture_output=True, text=True, timeout=120
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        log("error", "scale_timeout", {"target": count})
        return False


# === MAIN ===
try:
    r = redis.from_url(REDIS_URL, socket_connect_timeout=10, socket_timeout=10)
    r.ping()
    log("info", "started", {"queue": QUEUE_NAME, "workers": f"{MIN_WORKERS}-{MAX_WORKERS}"})
except redis.RedisError as e:
    log("error", "redis_failed", {"error": str(e)})
    sys.exit(1)

last_scale_time = 0

while not _shutdown:
    try:
        q_len = r.llen(QUEUE_NAME)
        current_w = get_current_workers()
        now = time.time()

        # Idealni broj workera
        ideal_w = math.ceil(q_len / TASKS_PER_WORKER)
        ideal_w = max(MIN_WORKERS, min(MAX_WORKERS, ideal_w))

        # Hysteresis provjera - scale samo ako smo izvan dead zone
        current_capacity = current_w * TASKS_PER_WORKER
        upper_bound = current_capacity * (1 + HYSTERESIS)
        lower_bound = current_capacity * (1 - HYSTERESIS)

        should_scale = False
        new_count = current_w

        if q_len > upper_bound and current_w < MAX_WORKERS:
            # Scale UP
            new_count = min(current_w + MAX_STEP_UP, ideal_w)
            should_scale = True
        elif q_len < lower_bound and current_w > MIN_WORKERS:
            # Scale DOWN
            new_count = max(current_w - MAX_STEP_DOWN, MIN_WORKERS)
            should_scale = True

        # Cooldown + execute
        if should_scale and (now - last_scale_time) > COOLDOWN:
            direction = "up" if new_count > current_w else "down"
            log("info", f"scale_{direction}", {
                "queue": q_len, "from": current_w, "to": new_count
            })
            if scale_to(new_count):
                last_scale_time = now

    except redis.RedisError as e:
        log("error", "redis_error", {"error": str(e)})
    except Exception as e:
        log("error", "unexpected", {"error": str(e)})

    # Sleep s prekidom za shutdown
    for _ in range(CHECK_INTERVAL):
        if _shutdown:
            break
        time.sleep(1)
    
log("info", "stopped")
