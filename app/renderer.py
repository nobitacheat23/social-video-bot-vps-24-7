"""
Multi-Threaded Pixel-Perfect UI Card Renderer for Threads, TikTok, and Facebook.
Uses thread-local Headless Chrome instances with Retina 2x Device Scale Factor
and self-contained CSS templates to render crisp PNG comment caps in ~0.12s per card.
"""

import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from jinja2 import Environment, FileSystemLoader

root_dir = str(Path(__file__).resolve().parent.parent)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By


class CardRenderer:
    def __init__(
        self,
        templates_dir: Optional[str] = None,
        output_dir: Optional[str] = None,
        max_workers: int = 3,
    ):
        base_path = Path(__file__).resolve().parent.parent
        self.templates_dir = Path(templates_dir or (base_path / "templates"))
        self.output_dir = Path(output_dir or (base_path / "output" / "cards"))
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.jinja_env = Environment(loader=FileSystemLoader(str(self.templates_dir)))
        self._local = threading.local()
        self._drivers_lock = threading.Lock()
        self._all_drivers: List[webdriver.Chrome] = []
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def _get_driver(self) -> webdriver.Chrome:
        """Returns a thread-local headless Chrome instance for safe parallel card rendering."""
        drv = getattr(self._local, "driver", None)
        if drv is None:
            opts = Options()
            opts.add_argument("--headless=new")
            opts.add_argument("--disable-gpu")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_argument("--hide-scrollbars")
            opts.add_argument("--window-size=1200,1400")
            opts.add_argument("--force-device-scale-factor=2")
            opts.page_load_strategy = "eager"
            drv = webdriver.Chrome(options=opts)
            drv.set_page_load_timeout(8)
            self._local.driver = drv
            with self._drivers_lock:
                self._all_drivers.append(drv)
        return drv

    def close(self) -> None:
        """Closes all thread-local headless browsers cleanly."""
        with self._drivers_lock:
            drivers = list(self._all_drivers)
            self._all_drivers.clear()
        for d in drivers:
            try:
                d.quit()
            except Exception:
                pass
        try:
            self._executor.shutdown(wait=False)
        except Exception:
            pass

    @staticmethod
    def _wait_for_images(driver: webdriver.Chrome, timeout_sec: float = 1.2) -> None:
        """Waits up to timeout_sec for any avatar/attached images to finish loading."""
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            try:
                all_loaded = driver.execute_script(
                    "return Array.from(document.images).every(img => img.complete);"
                )
                if all_loaded:
                    return
            except Exception:
                return
            time.sleep(0.05)

    def render_card(
        self,
        item_id: str,
        platform: str = "threads",
        username: str = "nguoiquaduong",
        content: str = "Nội dung bình luận mẫu...",
        likes: str = "1,5K",
        replies: int = 12,
        reposts: str = "0",
        shares: str = "0",
        time_ago: str = "",
        is_op: bool = False,
        verified: bool = False,
        dark_mode: bool = False,
        avatar_url: Optional[str] = None,
        image_url: Optional[str] = None,
        parent_post: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> str:
        """
        Renders an HTML template to a crisp 2x Retina PNG card file (thread-safe).
        """
        template_name = f"{platform.lower()}_card.html"
        try:
            template = self.jinja_env.get_template(template_name)
        except Exception:
            template = self.jinja_env.get_template("threads_card.html")

        # Resolve local asset relative paths like "/assets/..." to file:// URIs so Chrome loads instantly
        resolved_image_url = image_url
        if resolved_image_url and resolved_image_url.startswith("/assets/"):
            local_asset = Path(root_dir) / resolved_image_url.lstrip("/")
            if local_asset.exists():
                resolved_image_url = local_asset.as_uri()
        elif resolved_image_url and os.path.exists(resolved_image_url):
            resolved_image_url = Path(resolved_image_url).resolve().as_uri()

        resolved_avatar = avatar_url
        if resolved_avatar and resolved_avatar.startswith("/assets/"):
            local_av = Path(root_dir) / resolved_avatar.lstrip("/")
            if local_av.exists():
                resolved_avatar = local_av.as_uri()
        elif resolved_avatar and os.path.exists(resolved_avatar):
            resolved_avatar = Path(resolved_avatar).resolve().as_uri()

        rendered_html = template.render(
            username=username,
            content=content,
            likes=likes,
            replies=replies,
            reposts=reposts,
            shares=shares,
            is_op=is_op,
            verified=verified,
            dark_mode=dark_mode,
            avatar_url=resolved_avatar,
            image_url=resolved_image_url,
            parent_post=parent_post,
            **kwargs,
        )

        tid = threading.get_ident()
        temp_html_path = self.output_dir / f"temp_{tid}_{item_id}.html"
        png_path = self.output_dir / f"card_{item_id}.png"

        with open(temp_html_path, "w", encoding="utf-8") as f:
            f.write(rendered_html)

        try:
            driver = self._get_driver()
            driver.get(temp_html_path.as_uri())
            self._wait_for_images(driver, timeout_sec=1.2)
            card_elem = driver.find_element(By.ID, "card")
            card_elem.screenshot(str(png_path))
        except Exception:
            # Self-heal if Chrome tab crashed: recreate driver once
            self._local.driver = None
            driver = self._get_driver()
            driver.get(temp_html_path.as_uri())
            self._wait_for_images(driver, timeout_sec=1.2)
            card_elem = driver.find_element(By.ID, "card")
            card_elem.screenshot(str(png_path))
        finally:
            if temp_html_path.exists():
                try:
                    temp_html_path.unlink()
                except Exception:
                    pass

        return str(png_path)

    def render_cards_parallel(
        self,
        specs: List[Dict[str, Any]],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[str]:
        """Renders multiple cards concurrently across worker threads while preserving order."""
        total = len(specs)
        if total == 0:
            return []

        results: List[Optional[str]] = [None] * total
        done_lock = threading.Lock()
        done_count = 0

        def _task(idx: int, spec: Dict[str, Any]) -> None:
            nonlocal done_count
            path = self.render_card(**spec)
            results[idx] = path
            with done_lock:
                done_count += 1
                cur = done_count
            if progress_callback:
                try:
                    progress_callback(cur, total)
                except Exception:
                    pass

        futures = [
            self._executor.submit(_task, idx, spec) for idx, spec in enumerate(specs)
        ]
        for fut in futures:
            fut.result()

        return [str(p) for p in results]
