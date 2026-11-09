"""
One-Click Launcher for Social Video Automation Studio.
"""

import os
import sys
import webbrowser
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.sfx_generator import ensure_default_assets

def main():
    print("=" * 60)
    print("🚀 SOCIAL VIDEO AUTOMATION STUDIO (Shorts/Reels/TikTok)")
    print("=" * 60)
    
    print("-> Checking and initializing default sound effects and sample assets...")
    assets = ensure_default_assets(str(PROJECT_ROOT))
    print(f"   [OK] Assets verified ({len(assets)} files).")

    host = "127.0.0.1"
    port = 8000
    url = f"http://{host}:{port}"

    print(f"-> Starting Web Studio on: {url}")
    print("   Open your browser to start generating short videos!")
    print("=" * 60)

    if not os.environ.get("NO_BROWSER"):
        import threading
        def open_browser():
            import time
            time.sleep(1.5)
            webbrowser.open(url)
        
        threading.Thread(target=open_browser, daemon=True).start()

    import uvicorn
    use_reload = os.environ.get("DEV_RELOAD", "0") == "1"
    uvicorn.run("app.main:app", host=host, port=port, reload=use_reload)

if __name__ == "__main__":
    main()
