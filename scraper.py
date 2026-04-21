"""
ND Courts Criminal/Traffic scraper
- Playwright para navegación y sesión
- Soporte multi-proveedor de CAPTCHA: 2captcha, SolveCaptcha, CapSolver
- LanAP Captcha (Tyler Technologies) + Cloudflare Turnstile
- Técnicas anti-detección de bot integradas
"""

import asyncio
import base64
import logging
import os
import random
import re
import smtplib
import time
from email.message import EmailMessage
from urllib.parse import urljoin
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Literal

import io
import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from PIL import Image, ImageEnhance, ImageFilter
try:
    from rebrowser_playwright.async_api import async_playwright, Page, BrowserContext, ConsoleMessage
except ImportError:
    from playwright.async_api import async_playwright, Page, BrowserContext, ConsoleMessage

# Cargar variables de entorno desde .env
load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("ndcourts")


def setup_logging(level: int = logging.DEBUG, log_file: Optional[str] = None) -> None:
    """
    Configura el logger raíz del scraper.

    Parámetros
    ----------
    level    : nivel mínimo de log (logging.DEBUG / INFO / WARNING / ERROR)
    log_file : ruta opcional a un archivo donde también escribir los logs
    """
    fmt = "%(asctime)s.%(msecs)03d [%(levelname)-5s] %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(level=level, format=fmt, datefmt=datefmt, handlers=handlers)
    # Silenciar logs verbosos de librerías externas
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)
    log.debug("Logging configurado — nivel=%s archivo=%s", logging.getLevelName(level), log_file or "—")

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

# Claves de API (prioridad a .env)
TWOCAPTCHA_API_KEY   = os.getenv("TWOCAPTCHA_API_KEY", "734770a08e22f490b9c016f297e1e4be")
SOLVECAPTCHA_API_KEY = os.getenv("SOLVECAPTCHA_API_KEY", "")
CAPSOLVER_API_KEY    = os.getenv("CAPSOLVER_API_KEY", "")

BASE_URL           = "https://publicsearch.ndcourts.gov"
SEARCH_URL         = f"{BASE_URL}/Search.aspx?ID=100"
HOME_URL           = f"{BASE_URL}/default.aspx"

CAPTCHA_POLL_INTERVAL = 5
CAPTCHA_MAX_WAIT      = 120

# Sitekey de Cloudflare Turnstile para publicsearch.ndcourts.gov
# Extraído de: https://challenges.cloudflare.com/cdn-cgi/challenge-platform/.../0x4AAAAAAADnPIDROrmt1Wwj/...
CF_TURNSTILE_SITEKEY = "0x4AAAAAAADnPIDROrmt1Wwj"

# Directorio donde se guardan screenshots de error para depuración
SCREENSHOTS_DIR = Path("debug_screenshots")

# Proveedor de CAPTCHA por defecto
CaptchaProvider = Literal["2captcha", "solvecaptcha", "capsolver"]


# ---------------------------------------------------------------------------
# Anti-detección — constantes
# ---------------------------------------------------------------------------

# Pool de User-Agents reales de Chrome en Windows/Mac (2025-2026, versiones 134-136)
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
]

# Resoluciones de pantalla comunes
_VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1280, "height": 800},
]

# Script de stealth minimalista.
# Solo corrige lo que Playwright expone de forma obvia sin crear inconsistencias
# en el fingerprint. Cloudflare Turnstile usa canvas/WebGL/plataforma para
# verificación — sobreescribir esas propiedades con valores falsos crea
# inconsistencias detectables (ej: platform='Win32' en Linux).
# El flag navigator.webdriver lo elimina --disable-blink-features=AutomationControlled.
_STEALTH_SCRIPT = """
// Objeto window.chrome completo — ausente en Chromium sin perfil de usuario,
// su ausencia es el indicador más básico de browser headless/automatizado.
if (!window.chrome) {
    window.chrome = {
        app: { isInstalled: false },
        runtime: {},
        loadTimes: function() {},
        csi: function() {},
    };
}
"""


# ---------------------------------------------------------------------------
# Parámetros de búsqueda
# ---------------------------------------------------------------------------

@dataclass
class SearchParams:
    """Parámetros para búsqueda de tipo Defendant (por nombre)."""
    last_name:         str
    first_name:        str       = ""
    middle_name:       str       = ""
    date_of_birth:     str       = ""      # MM/DD/YYYY
    case_status:       str       = "All"   # All | Open | Closed
    date_filed_after:  str       = ""      # MM/DD/YYYY
    date_filed_before: str       = ""      # MM/DD/YYYY
    case_types:        list[str] = field(default_factory=list)
    sort_by:           str       = "Filed Date"
    use_soundex:       bool      = False


@dataclass
class DateFieldSearchParams:
    """Parámetros para búsqueda por Date Field (sin nombre requerido)."""
    date_after:  str             # MM/DD/YYYY — "on or after"
    date_before: str             # MM/DD/YYYY — "on or before"
    case_types:  list[str] = field(default_factory=list)
    case_status: str       = "All"   # All | Open | Closed
    sort_by:     str       = "Filed Date"


# ---------------------------------------------------------------------------
# Interfaz base para proveedores de CAPTCHA
# ---------------------------------------------------------------------------

class CaptchaSolverBase(ABC):
    """Interfaz común para todos los proveedores de CAPTCHA."""

    @abstractmethod
    async def solve(self, image_bytes: bytes) -> str:
        """Resuelve un CAPTCHA de imagen y devuelve el texto."""
        ...

    @abstractmethod
    async def solve_turnstile(self, sitekey: str, pageurl: str) -> str:
        """Resuelve un Cloudflare Turnstile y devuelve el token."""
        ...

    @abstractmethod
    async def report_bad(self) -> None:
        """Reporta el último CAPTCHA como mal resuelto (para reembolso de crédito)."""
        ...


# ---------------------------------------------------------------------------
# Proveedor: 2captcha / SolveCaptcha (API compatible)
# ---------------------------------------------------------------------------

class TwoCaptchaClient(CaptchaSolverBase):
    """
    Cliente para 2captcha y servicios con API compatible (SolveCaptcha).

    - API clásica (in.php / res.php): para imágenes CAPTCHA
    - Task API (createTask / getTaskResult): para Cloudflare Turnstile

    Parámetros
    ----------
    api_key      : clave de API del proveedor
    provider     : "2captcha" | "solvecaptcha"
    """

    _PROVIDER_URLS: dict[str, dict[str, str]] = {
        "2captcha": {
            "classic_submit": "https://2captcha.com/in.php",
            "classic_result": "https://2captcha.com/res.php",
            "task_base":      "https://api.2captcha.com",
        },
        "solvecaptcha": {
            "classic_submit": "https://api.solvecaptcha.com/in.php",
            "classic_result": "https://api.solvecaptcha.com/res.php",
            "task_base":      "https://api.solvecaptcha.com",
        },
    }

    def __init__(self, api_key: str, provider: str = "2captcha"):
        if provider not in self._PROVIDER_URLS:
            raise ValueError(f"Proveedor desconocido '{provider}'. Usa: {list(self._PROVIDER_URLS)}")
        urls = self._PROVIDER_URLS[provider]
        self.api_key        = api_key
        self.provider       = provider
        self._submit_url    = urls["classic_submit"]
        self._result_url    = urls["classic_result"]
        self._task_base     = urls["task_base"]
        self._last_id: Optional[str] = None
        self._log           = logging.getLogger(f"ndcourts.{provider}")

    async def solve(self, image_bytes: bytes) -> str:
        """Envía la imagen y devuelve el texto resuelto (API clásica base64)."""
        image_b64 = base64.b64encode(image_bytes).decode()
        t_start   = time.monotonic()
        self._log.debug("Enviando imagen — tamaño=%d bytes  proveedor=%s",
                        len(image_bytes), self.provider)

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(self._submit_url, data={
                "key":    self.api_key,
                "method": "base64",
                "body":   image_b64,
                "json":   1,
            })
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != 1:
                self._log.error("%s rechazó el envío: %s", self.provider, data)
                raise RuntimeError(f"{self.provider} error al enviar: {data}")

            self._last_id = data["request"]
            self._log.info("CAPTCHA enviado → ID %s", self._last_id)

            polls    = 0
            deadline = time.monotonic() + CAPTCHA_MAX_WAIT
            while time.monotonic() < deadline:
                await asyncio.sleep(CAPTCHA_POLL_INTERVAL)
                polls += 1
                res = await client.get(self._result_url, params={
                    "key":    self.api_key,
                    "action": "get",
                    "id":     self._last_id,
                    "json":   1,
                })
                res.raise_for_status()
                result = res.json()

                if result.get("status") == 1:
                    solution = result["request"]
                    elapsed  = time.monotonic() - t_start
                    self._log.info(
                        "CAPTCHA resuelto → '%s'  (polls=%d, tiempo=%.1fs)",
                        solution, polls, elapsed,
                    )
                    return solution

                if result.get("request") != "CAPCHA_NOT_READY":
                    self._log.error("Error inesperado en polling: %s", result)
                    raise RuntimeError(f"{self.provider} error en polling: {result}")

                self._log.debug("Poll #%d — no listo aún", polls)

            raise RuntimeError(f"{self.provider}: tiempo de espera agotado")

    async def solve_turnstile(self, sitekey: str, pageurl: str) -> str:
        """Resuelve Cloudflare Turnstile via Task API."""
        t_start = time.monotonic()
        self._log.info("Enviando Cloudflare Turnstile a %s — sitekey=%s", self.provider, sitekey)

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{self._task_base}/createTask", json={
                "clientKey": self.api_key,
                "task": {
                    "type":       "TurnstileTaskProxyless",
                    "websiteURL": pageurl,
                    "websiteKey": sitekey,
                },
            })
            resp.raise_for_status()
            data = resp.json()

            if data.get("errorId", 0) != 0:
                self._log.error("%s rechazó Turnstile: %s", self.provider, data)
                raise RuntimeError(f"{self.provider} Turnstile createTask error: {data}")

            task_id = data["taskId"]
            self._log.info("Turnstile task creada → taskId=%s", task_id)

            polls    = 0
            deadline = time.monotonic() + CAPTCHA_MAX_WAIT
            while time.monotonic() < deadline:
                await asyncio.sleep(CAPTCHA_POLL_INTERVAL)
                polls += 1
                res = await client.post(f"{self._task_base}/getTaskResult", json={
                    "clientKey": self.api_key,
                    "taskId":    task_id,
                })
                res.raise_for_status()
                result = res.json()

                if result.get("errorId", 0) != 0:
                    self._log.error("Error en polling Turnstile: %s", result)
                    raise RuntimeError(f"{self.provider} Turnstile getTaskResult error: {result}")

                if result.get("status") == "ready":
                    token   = result["solution"]["token"]
                    elapsed = time.monotonic() - t_start
                    self._log.info(
                        "Turnstile resuelto (polls=%d, tiempo=%.1fs) token=%s…",
                        polls, elapsed, token[:40],
                    )
                    return token

                self._log.debug("Turnstile poll #%d — status=%s", polls, result.get("status"))

            raise RuntimeError(f"{self.provider} Turnstile: tiempo de espera agotado")

    async def report_bad(self) -> None:
        """Reporta el último CAPTCHA como mal resuelto."""
        if not self._last_id:
            return
        async with httpx.AsyncClient(timeout=10) as client:
            await client.get(self._result_url, params={
                "key":    self.api_key,
                "action": "reportbad",
                "id":     self._last_id,
            })
        self._log.warning("CAPTCHA reportado como incorrecto → ID %s", self._last_id)


# ---------------------------------------------------------------------------
# Proveedor: CapSolver (API propia)
# ---------------------------------------------------------------------------

class CapSolverClient(CaptchaSolverBase):
    """
    Cliente para CapSolver (api.capsolver.com).

    Usa la API JSON nativa de CapSolver, distinta de la API de 2captcha.
    - Imagen CAPTCHA: tipo ImageToTextTask
    - Cloudflare Turnstile: tipo AntiTurnstileTaskProxyless

    Parámetros
    ----------
    api_key : Client Key de CapSolver
    """

    BASE_URL = "https://api.capsolver.com"

    def __init__(self, api_key: str):
        self.api_key      = api_key
        self._last_task_id: Optional[str] = None
        self._log         = logging.getLogger("ndcourts.capsolver")

    async def _create_task(self, client: httpx.AsyncClient, task: dict) -> dict:
        """Crea una tarea en CapSolver y devuelve el diccionario de respuesta completo."""
        resp = await client.post(f"{self.BASE_URL}/createTask", json={
            "clientKey": self.api_key,
            "task":      task,
        })
        
        if resp.is_error:
            self._log.error("CapSolver createTask falló (HTTP %d): %s", resp.status_code, resp.text)
            resp.raise_for_status()

        data = resp.json()
        if data.get("errorId", 0) != 0:
            self._log.error("CapSolver createTask error de negocio: %s", data)
            raise RuntimeError(f"CapSolver error: {data.get('errorDescription', data)}")

        task_id = data.get("taskId")
        if task_id:
            self._last_task_id = task_id
            self._log.info("CapSolver task creada → taskId=%s", task_id)
        
        return data

    async def _poll_task(self, client: httpx.AsyncClient, task_id: str) -> dict:
        """Hace polling hasta que la tarea esté lista. Devuelve el dict 'solution'."""
        polls    = 0
        deadline = time.monotonic() + CAPTCHA_MAX_WAIT
        while time.monotonic() < deadline:
            await asyncio.sleep(CAPTCHA_POLL_INTERVAL)
            polls += 1
            
            payload = {
                "clientKey": self.api_key,
                "taskId":    task_id,
            }
            res = await client.post(f"{self.BASE_URL}/getTaskResult", json=payload)
            
            if res.is_error:
                self._log.error(
                    "CapSolver getTaskResult falló (HTTP %d) - ID: %s - Body: %s",
                    res.status_code, task_id, res.text
                )
                res.raise_for_status()

            result = res.json()
            if result.get("errorId", 0) != 0:
                self._log.error("CapSolver getTaskResult error de negocio: %s", result)
                raise RuntimeError(f"CapSolver error: {result.get('errorDescription', result)}")

            status = result.get("status")
            if status == "ready":
                self._log.debug("CapSolver tarea lista en poll #%d", polls)
                return result["solution"]

            self._log.debug("CapSolver poll #%d — status=%s", polls, status)

        raise RuntimeError("CapSolver: tiempo de espera agotado")

    async def solve(self, image_bytes: bytes) -> str:
        """Resuelve un CAPTCHA de imagen con CapSolver (ImageToTextTask)."""
        image_b64 = base64.b64encode(image_bytes).decode()
        t_start   = time.monotonic()
        self._log.debug("Enviando imagen a CapSolver — tamaño=%d bytes", len(image_bytes))

        async with httpx.AsyncClient(timeout=30) as client:
            data = await self._create_task(client, {
                "type": "ImageToTextTask",
                "body": image_b64,
            })
            
            # ImageToTextTask suele resolverse inmediatamente
            if data.get("status") == "ready":
                solution = data.get("solution", {})
            else:
                task_id  = data.get("taskId")
                if not task_id:
                    raise RuntimeError(f"CapSolver no devolvió taskId ni solución: {data}")
                solution = await self._poll_task(client, task_id)
            
            text     = solution.get("text", "")
            elapsed  = time.monotonic() - t_start
            self._log.info("CapSolver CAPTCHA resuelto → '%s'  (tiempo=%.1fs)", text, elapsed)
            return text

    async def solve_turnstile(self, sitekey: str, pageurl: str) -> str:
        """Resuelve Cloudflare Turnstile con CapSolver (AntiTurnstileTaskProxyless)."""
        t_start = time.monotonic()
        self._log.info("Enviando Cloudflare Turnstile a CapSolver — sitekey=%s", sitekey)

        async with httpx.AsyncClient(timeout=30) as client:
            data = await self._create_task(client, {
                "type":       "AntiTurnstileTaskProxyless",
                "websiteURL": pageurl,
                "websiteKey": sitekey,
            })
            
            if data.get("status") == "ready":
                solution = data.get("solution", {})
            else:
                task_id  = data.get("taskId")
                if not task_id:
                    raise RuntimeError(f"CapSolver no devolvió taskId ni solución: {data}")
                solution = await self._poll_task(client, task_id)
                
            token    = solution.get("token", "")
            elapsed  = time.monotonic() - t_start
            self._log.info(
                "CapSolver Turnstile resuelto (tiempo=%.1fs) token=%s…",
                elapsed, token[:40],
            )

            return token

    async def report_bad(self) -> None:
        """Reporta la última tarea como incorrecta (feedbackTask)."""
        if not self._last_task_id:
            return
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{self.BASE_URL}/feedbackTask", json={
                "clientKey": self.api_key,
                "taskId":    self._last_task_id,
                "invalid":   True,
            })
            # No lanzar error si el feedback falla — es best-effort
            self._log.warning(
                "CapSolver tarea reportada como incorrecta → taskId=%s  status=%d",
                self._last_task_id, resp.status_code,
            )


# ---------------------------------------------------------------------------
# Factory: crea el solver según el proveedor elegido
# ---------------------------------------------------------------------------

def create_captcha_solver(
    provider: CaptchaProvider,
    api_key:  str,
) -> CaptchaSolverBase:
    """
    Crea y devuelve el cliente de CAPTCHA para el proveedor indicado.

    Parámetros
    ----------
    provider : "2captcha" | "solvecaptcha" | "capsolver"
    api_key  : clave de API del proveedor

    Ejemplo
    -------
    solver = create_captcha_solver("capsolver", "CAP-xxx")
    solver = create_captcha_solver("2captcha",  "abc123")
    """
    if provider in ("2captcha", "solvecaptcha"):
        return TwoCaptchaClient(api_key, provider=provider)
    if provider == "capsolver":
        return CapSolverClient(api_key)
    raise ValueError(f"Proveedor desconocido: '{provider}'. Usa: 2captcha, solvecaptcha, capsolver")


# ---------------------------------------------------------------------------
# Helpers de comportamiento humano
# ---------------------------------------------------------------------------

async def _random_delay(min_s: float = 0.5, max_s: float = 1.8) -> None:
    """Pausa aleatoria para simular tiempo de reacción humano."""
    await asyncio.sleep(random.uniform(min_s, max_s))


def _preprocess_captcha(image_bytes: bytes) -> bytes:
    """
    Preprocesa la imagen CAPTCHA de LanAP para mejorar la precisión del solver.

    LanAP genera dos estilos observados:
      - Texto naranja/rojo sobre fondo blanco
      - Texto negro sobre fondo en damero blanco/negro

    Estrategia: escalar 3x, convertir a escala de grises con blur ligero para
    suavizar el patrón de fondo, luego binarizar con umbral adaptativo alto
    (el texto siempre es más oscuro que el fondo promediado).
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    # Escalar 3x para dar más resolución al OCR
    img = img.resize((w * 3, h * 3), Image.LANCZOS)
    # Escala de grises
    gray = img.convert("L")
    # Blur ligero: suaviza el patrón damero (fondo) sin borrar el texto
    gray = gray.filter(ImageFilter.GaussianBlur(radius=1))
    # Binarizar: umbral 180 → texto (≤180) negro, fondo (>180) blanco
    # Funciona para texto naranja (L≈135) y texto negro (L≈0) sobre fondos claros
    bw = gray.point(lambda p: 0 if p <= 180 else 255, "L").convert("RGB")
    buf = io.BytesIO()
    bw.save(buf, format="PNG")
    return buf.getvalue()


async def _human_type(page: Page, selector: str, text: str) -> None:
    """
    Escribe texto carácter a carácter con velocidad variable (30–130 ms/char).
    Limpia el campo primero (triple-click selecciona todo) para evitar que texto
    residual de intentos anteriores o autofill se acumule con el nuevo valor.
    Ocasionalmente hace una pausa más larga, como cuando un humano duda.
    """
    await page.click(selector, click_count=3)  # triple-click → selecciona todo
    await _random_delay(0.1, 0.3)
    await page.keyboard.press("Delete")        # borra selección
    await _random_delay(0.1, 0.2)
    for char in text:
        await page.keyboard.type(char, delay=random.uniform(30, 130))
        # Pausa de "duda" con probabilidad 5 %
        if random.random() < 0.05:
            await asyncio.sleep(random.uniform(0.3, 0.7))


def _bezier_points(x0: float, y0: float, x1: float, y1: float, steps: int) -> list[tuple[float, float]]:
    """Genera puntos de una curva Bezier cuadrática entre (x0,y0) y (x1,y1)."""
    cx = random.uniform(min(x0, x1), max(x0, x1))
    cy = random.uniform(min(y0, y1) - 60, max(y0, y1) + 60)
    pts = []
    for i in range(1, steps + 1):
        t = i / steps
        bx = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t ** 2 * x1
        by = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t ** 2 * y1
        pts.append((bx, by))
    return pts


async def _bezier_move(page: Page, tx: float, ty: float) -> None:
    """Mueve el mouse desde la posición actual hasta (tx, ty) con trayectoria Bezier."""
    pos = await page.evaluate("() => ({x: window._mouseX ?? 300, y: window._mouseY ?? 300})")
    steps = random.randint(18, 35)
    for bx, by in _bezier_points(pos["x"], pos["y"], tx, ty, steps):
        await page.mouse.move(bx, by)
        await asyncio.sleep(random.uniform(0.005, 0.018))
    await asyncio.sleep(random.uniform(0.06, 0.18))


async def _human_click(page: Page, selector: str) -> None:
    """Mueve el ratón en curva Bezier hasta el elemento y hace clic."""
    element = await page.query_selector(selector)
    if not element:
        return
    box = await element.bounding_box()
    if not box:
        await page.click(selector)
        return

    target_x = box["x"] + box["width"]  / 2 + random.uniform(-4, 4)
    target_y = box["y"] + box["height"] / 2 + random.uniform(-4, 4)
    await _bezier_move(page, target_x, target_y)
    await page.mouse.click(target_x, target_y)


async def _human_click_element(page: Page, element) -> None:
    """
    Igual que _human_click pero recibe un ElementHandle directamente
    (útil para links de paginación encontrados con query_selector_all).
    """
    box = await element.bounding_box()
    if not box:
        await element.click()
        return
    target_x = box["x"] + box["width"]  / 2 + random.uniform(-4, 4)
    target_y = box["y"] + box["height"] / 2 + random.uniform(-4, 4)
    await _bezier_move(page, target_x, target_y)
    await asyncio.sleep(random.uniform(0.05, 0.15))
    await page.mouse.click(target_x, target_y)


async def _wait_for_launch_search(page: Page, timeout: float = 10.0) -> None:
    """Espera a que window.LaunchSearch esté definido (Firefox puede tardar más que Chrome)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        defined = await page.evaluate("typeof LaunchSearch === 'function'")
        if defined:
            return
        await asyncio.sleep(0.25)
    raise RuntimeError("LaunchSearch no se definió en la página tras %.0fs" % timeout)


async def _random_scroll(page: Page) -> None:
    """Scroll irregular en varios pasos simulando lectura de la página."""
    total = random.randint(80, 280)
    chunks = random.randint(2, 5)
    for _ in range(chunks):
        dy = total // chunks + random.randint(-15, 15)
        await page.mouse.wheel(0, dy)
        await asyncio.sleep(random.uniform(0.15, 0.45))
    await asyncio.sleep(random.uniform(0.4, 1.2))
    await page.mouse.wheel(0, -total)


async def _human_idle(page: Page, min_s: float = 1.5, max_s: float = 4.0) -> None:
    """Simula al usuario leyendo la página: mueve el mouse aleatoriamente y espera."""
    vp = page.viewport_size or {"width": 1280, "height": 800}
    moves = random.randint(2, 5)
    for _ in range(moves):
        mx = random.uniform(vp["width"] * 0.1, vp["width"] * 0.9)
        my = random.uniform(vp["height"] * 0.1, vp["height"] * 0.7)
        await _bezier_move(page, mx, my)
        await asyncio.sleep(random.uniform(0.2, 0.6))
    await asyncio.sleep(random.uniform(min_s, max_s))


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Proxy local — resuelve incompatibilidad Chromium ↔ proxies HTTP con auth
# ---------------------------------------------------------------------------

class _LocalProxyServer:
    """
    Proxy HTTP local (127.0.0.1) que reenvía a un upstream con autenticación
    pre-embebida. Resuelve el problema de Chromium con proxies que no soportan
    el flujo de auth de dos pasos (CONNECT → 407 → CONNECT+auth).
    """

    def __init__(self, upstream: dict, host: str = "127.0.0.1", port: int = 0):
        from urllib.parse import urlparse
        parsed = urlparse(upstream["server"])
        self._up_host = parsed.hostname
        self._up_port = parsed.port or 80
        self._auth    = base64.b64encode(
            f"{upstream.get('username', '')}:{upstream.get('password', '')}".encode()
        ).decode()
        self._host    = host
        self._port    = port          # 0 = asignar automáticamente
        self._server  = None
        self._log     = logging.getLogger("ndcourts.localproxy")

    @property
    def proxy_dict(self) -> dict:
        return {"server": f"http://{self._host}:{self._port}"}

    async def start(self):
        self._server = await asyncio.start_server(
            self._handle, self._host, self._port
        )
        self._port = self._server.sockets[0].getsockname()[1]
        self._log.info("Proxy local iniciado en %s:%d → %s:%d",
                       self._host, self._port, self._up_host, self._up_port)

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter):
        try:
            first = await client_r.readline()
            if not first:
                return
            headers = []
            while True:
                line = await client_r.readline()
                headers.append(line)
                if line in (b"\r\n", b"\n", b""):
                    break

            parts = first.decode(errors="replace").split()
            if len(parts) < 2 or parts[0].upper() != "CONNECT":
                client_w.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
                await client_w.drain()
                return

            target = parts[1]   # host:port
            up_r, up_w = await asyncio.open_connection(self._up_host, self._up_port)
            connect_req = (
                f"CONNECT {target} HTTP/1.1\r\n"
                f"Host: {target}\r\n"
                f"Proxy-Authorization: Basic {self._auth}\r\n"
                f"\r\n"
            )
            up_w.write(connect_req.encode())
            await up_w.drain()

            # Leer respuesta del upstream
            resp_line = await up_r.readline()
            while True:
                hdr = await up_r.readline()
                if hdr in (b"\r\n", b"\n", b""):
                    break

            if b"200" not in resp_line:
                self._log.warning("Upstream rechazó CONNECT a %s: %s", target, resp_line.decode().strip())
                client_w.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                await client_w.drain()
                return

            client_w.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await client_w.drain()

            await asyncio.gather(
                self._pipe(client_r, up_w),
                self._pipe(up_r, client_w),
            )
        except (ConnectionResetError, asyncio.IncompleteReadError, BrokenPipeError):
            pass
        except Exception as exc:
            self._log.debug("LocalProxy error: %s", exc)
        finally:
            client_w.close()

    @staticmethod
    async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass


# Scraper principal
# ---------------------------------------------------------------------------

class NDCourtsScraper:

    def __init__(
        self,
        api_key:  str,
        headless: bool                    = True,
        proxy:    Optional[dict]          = None,
        provider: CaptchaProvider         = "2captcha",
        solver:   Optional[CaptchaSolverBase] = None,
    ):
        """
        Parámetros
        ----------
        api_key  : clave de API del proveedor de CAPTCHA
        headless : True = sin ventana visible
        proxy    : dict con 'server', y opcionalmente 'username'/'password'
                   Ejemplo: {"server": "http://user:pass@host:port"}
        provider : proveedor de CAPTCHA — "2captcha" | "solvecaptcha" | "capsolver"
                   Ignorado si se pasa `solver` directamente.
        solver   : instancia de CaptchaSolverBase (TwoCaptchaClient, CapSolverClient, etc.)
                   Si se pasa, tiene prioridad sobre `provider` y `api_key`.

        Ejemplos
        --------
        # Usando 2captcha (por defecto):
        NDCourtsScraper(api_key="abc123")

        # Usando CapSolver:
        NDCourtsScraper(api_key="CAP-xxx", provider="capsolver")

        # Usando SolveCaptcha:
        NDCourtsScraper(api_key="xxx", provider="solvecaptcha")

        # Pasando solver personalizado:
        NDCourtsScraper(api_key="", solver=MiSolverCustom())
        """
        self.captcha_client: CaptchaSolverBase = (
            solver if solver is not None
            else create_captcha_solver(provider, api_key)
        )
        self.headless      = headless
        self.proxy         = proxy
        self._local_proxy  = None
        self._camoufox_cm  = None
        self._log          = logging.getLogger("ndcourts.scraper")
        if proxy:
            server = proxy.get("server", "")
            user   = proxy.get("username", "")
            self._log.info("Proxy habilitado — server=%s user=%s", server, user or "(sin auth)")
        else:
            self._log.info("Proxy deshabilitado — conexión directa")

    # ------------------------------------------------------------------
    # Configuración del browser con anti-detección
    # ------------------------------------------------------------------

    async def _build_context(self, playwright) -> tuple:
        """Lanza el browser y devuelve (browser, context) configurados con stealth.

        Intenta usar camoufox (Firefox-based, mejor evasión de Cloudflare).
        Si no está instalado, cae en Chromium con rebrowser-playwright + playwright-stealth.
        """
        viewport = random.choice(_VIEWPORTS)

        # ------------------------------------------------------------------
        # Opción A: camoufox — Firefox stealth, fingerprint completamente distinto
        # ------------------------------------------------------------------
        try:
            from camoufox.async_api import AsyncCamoufox

            camoufox_kwargs: dict = {
                "headless": self.headless,
                "os":       ("windows", "macos"),
                "locale":   ["en-US", "en"],
            }

            if self.proxy:
                proxy_cfg: dict = {"server": self.proxy.get("server", "")}
                if self.proxy.get("username"):
                    proxy_cfg["username"] = self.proxy["username"]
                    proxy_cfg["password"] = self.proxy.get("password", "")
                camoufox_kwargs["proxy"] = proxy_cfg

            self._camoufox_cm = AsyncCamoufox(**camoufox_kwargs)
            browser = await self._camoufox_cm.__aenter__()

            context = await browser.new_context(
                viewport=viewport,
                timezone_id="America/Chicago",
                color_scheme="light",
            )
            self._log.debug("camoufox activo — Firefox stealth (viewport=%dx%d)",
                            viewport["width"], viewport["height"])
            return browser, context

        except ImportError:
            self._camoufox_cm = None
            self._log.debug("camoufox no disponible — usando Chromium")

        # ------------------------------------------------------------------
        # Opción B: Chromium con rebrowser-playwright + playwright-stealth
        # ------------------------------------------------------------------
        ua = random.choice(_USER_AGENTS)
        self._log.debug("Browser config — UA='%s'  viewport=%dx%d  headless=%s",
                        ua, viewport["width"], viewport["height"], self.headless)

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
            f"--window-size={viewport['width']},{viewport['height']}",
        ]

        proxy_config = None
        self._local_proxy = None
        if self.proxy:
            self._local_proxy = _LocalProxyServer(self.proxy)
            await self._local_proxy.start()
            proxy_config = self._local_proxy.proxy_dict

        browser = await playwright.chromium.launch(
            headless=headless if (headless := self.headless) else False,
            args=launch_args,
            **({"proxy": proxy_config} if proxy_config else {}),
        )

        context = await browser.new_context(
            user_agent=ua,
            viewport=viewport,
            locale="en-US",
            timezone_id="America/Chicago",
            color_scheme="light",
        )

        try:
            from playwright_stealth import Stealth
            stealth = Stealth(
                navigator_platform_override="Win32",
                navigator_user_agent_override=ua,
                navigator_languages_override=("en-US", "en"),
            )
            await stealth.apply_stealth_async(context)
            self._log.debug("playwright-stealth aplicado al BrowserContext")
        except Exception as e:
            self._log.debug("playwright-stealth no disponible (%s) — usando script manual", e)
            await context.add_init_script(_STEALTH_SCRIPT)

        # __cf_bm se omite: expira en 30 min y una cookie vencida activa detección

        return browser, context

    # ------------------------------------------------------------------
    # Helpers internos
    # ------------------------------------------------------------------

    async def _get_captcha_image(self, page: Page, max_retries: int = 3) -> bytes:
        """
        Descarga la imagen CAPTCHA con verificaciones de integridad y reintentos.
        Si la imagen no carga o es inválida, intenta refrescarla antes de fallar.
        """
        selector = 'img[alt="CAPTCHA code image"]'
        refresh_selector = 'a:has-text("Try another code"), a:has-text("Refresh"), #LnkRefreshCaptcha'

        for attempt in range(1, max_retries + 1):
            try:
                # 1. Esperar a que el elemento sea visible
                img_el = await page.wait_for_selector(selector, state="visible", timeout=10000)
                if not img_el:
                    raise RuntimeError("Elemento CAPTCHA no encontrado en el DOM")

                # 2. Verificar dimensiones reales via JS (evita imágenes rotas)
                is_valid = await page.eval_on_selector(selector, """
                    el => el.complete && typeof el.naturalWidth !== 'undefined' && el.naturalWidth > 0
                """)
                
                if not is_valid:
                    self._log.warning("Imagen CAPTCHA detectada como 'rota' en el browser (intento %d)", attempt)
                    if attempt < max_retries:
                        refresh_link = await page.query_selector(refresh_selector)
                        if refresh_link:
                            self._log.info("Haciendo clic en 'Try another code' para refrescar...")
                            await _human_click_element(page, refresh_link)
                            await _random_delay(1.5, 3.0)
                            continue
                    raise ValueError("La imagen del CAPTCHA no se cargó correctamente (ancho 0)")

                # 3. Obtener URL y descargar con los mismos headers de sesión
                src = await img_el.get_attribute("src")
                if not src:
                    raise ValueError("Atributo 'src' del CAPTCHA está vacío")
                
                # Convertir URL relativa en absoluta
                abs_url = urljoin(page.url, src)

                self._log.debug("Descargando CAPTCHA desde: %s (intento %d)", abs_url, attempt)
                response = await page.request.get(abs_url, headers={"Referer": page.url})
                
                if not response.ok:
                    raise RuntimeError(f"Error HTTP {response.status} al descargar CAPTCHA")

                body = await response.body()
                content_type = response.headers.get("content-type", "").lower()

                # 4. Validaciones de contenido
                if len(body) < 100:
                    raise ValueError(f"Imagen CAPTCHA demasiado pequeña ({len(body)} bytes)")
                
                if "text/html" in content_type or body.startswith(b"<"):
                    raise ValueError("La URL del CAPTCHA devolvió HTML (posible sesión expirada)")

                # Verificar magic bytes básicos (PNG, JPEG, GIF)
                if not (body.startswith(b"\x89PNG") or body.startswith(b"\xff\xd8") or body.startswith(b"GIF8")):
                    self._log.warning("Formato de imagen CAPTCHA desconocido (bytes: %s)", body[:8].hex())

                self._log.debug("CAPTCHA cargado con éxito (%d bytes)", len(body))
                # Guardar imagen original y preprocesada para diagnóstico
                try:
                    Path("debug_screenshots").mkdir(exist_ok=True)
                    Path(f"debug_screenshots/captcha_raw_{attempt}.png").write_bytes(body)
                except Exception:
                    pass
                # Preprocesar para mejorar OCR del solver
                try:
                    body = _preprocess_captcha(body)
                    Path(f"debug_screenshots/captcha_processed_{attempt}.png").write_bytes(body)
                    self._log.debug("CAPTCHA preprocesado — nuevo tamaño: %d bytes", len(body))
                except Exception as prep_err:
                    self._log.warning("Preprocesamiento CAPTCHA falló, usando imagen original: %s", prep_err)
                return body

            except Exception as e:
                self._log.warning("Fallo en _get_captcha_image (intento %d/%d): %s", attempt, max_retries, e)
                if attempt == max_retries:
                    raise
                
                # Intentar refrescar la imagen si existe el link, sino esperar
                refresh_link = await page.query_selector(refresh_selector)
                if refresh_link:
                    await _human_click_element(page, refresh_link)
                await _random_delay(2.0, 4.0)

        raise RuntimeError("No se pudo obtener una imagen CAPTCHA válida tras varios intentos")

    async def _fill_and_submit(self, page: Page, params: SearchParams, captcha_text: str) -> None:
        """Rellena el formulario con comportamiento humano y hace submit."""
        self._log.debug("Rellenando formulario — last_name='%s' first_name='%s'",
                        params.last_name, params.first_name)

        # Simular lectura de la página antes de interactuar
        await _human_idle(page, min_s=1.5, max_s=3.5)
        await _random_scroll(page)
        await _random_delay(0.4, 1.0)

        # CAPTCHA — escribir carácter a carácter
        self._log.debug("Escribiendo texto CAPTCHA: '%s'", captcha_text)
        await _human_type(page, "#CodeTextBox", captcha_text)
        await _random_delay(0.3, 0.8)

        # Tipo de búsqueda → Defendant
        self._log.debug("Seleccionando tipo de búsqueda: Defendant")
        await _human_click(page, "#Party")
        await _random_delay(0.2, 0.6)

        # Apellido (obligatorio)
        self._log.debug("Campo LastName: '%s'", params.last_name)
        await _human_type(page, "#LastName", params.last_name)
        await _random_delay(0.2, 0.5)

        if params.first_name:
            self._log.debug("Campo FirstName: '%s'", params.first_name)
            await _human_type(page, "#FirstName", params.first_name)
            await _random_delay(0.2, 0.5)

        if params.middle_name:
            self._log.debug("Campo MiddleName: '%s'", params.middle_name)
            await _human_type(page, "#MiddleName", params.middle_name)
            await _random_delay(0.2, 0.5)

        if params.date_of_birth:
            self._log.debug("Campo DateOfBirth: '%s'", params.date_of_birth)
            await _human_type(page, "#DateOfBirth", params.date_of_birth)
            await _random_delay(0.2, 0.5)

        if params.use_soundex:
            self._log.debug("Activando Soundex")
            await _human_click(page, "#chkSoundex")
            await _random_delay(0.2, 0.5)

        # Estado del caso
        self._log.debug("CaseStatus: '%s'", params.case_status)
        status_map = {"All": "#AllOption", "Open": "#OpenOption", "Closed": "#ClosedOption"}
        await _human_click(page, status_map.get(params.case_status, "#AllOption"))
        await _random_delay(0.2, 0.6)

        if params.date_filed_after:
            self._log.debug("DateFiledOnAfter: '%s'", params.date_filed_after)
            await _human_type(page, "#DateFiledOnAfter", params.date_filed_after)
            await _random_delay(0.2, 0.5)

        if params.date_filed_before:
            self._log.debug("DateFiledOnBefore: '%s'", params.date_filed_before)
            await _human_type(page, "#DateFiledOnBefore", params.date_filed_before)
            await _random_delay(0.2, 0.5)

        if params.case_types:
            self._log.debug("CaseTypes: %s", params.case_types)
            await page.select_option('select[name="CaseTypeID"]', label=params.case_types)
            await _random_delay(0.3, 0.7)

        self._log.debug("SortBy: '%s'", params.sort_by)
        await page.select_option('select[name="SortBy"]', label=params.sort_by)
        await _random_delay(0.5, 1.2)

        self._log.debug("Enviando formulario (click en SearchSubmit)")
        await _human_click(page, "#SearchSubmit")
        # ASP.NET WebForms usa UpdatePanel (AJAX parcial) — esperar resultados directamente
        try:
            await page.wait_for_selector(
                "tr:has(td > a[href*='CaseDetail']), "
                "span.ErrorMessages, #lblError, "
                "span[style*='color:red'], "
                "td[colspan]:has-text('no')",
                timeout=60000,
            )
        except Exception:
            self._log.warning("Timeout esperando respuesta del servidor tras submit — continuando")
        self._log.debug("Post-submit listo — URL: %s", page.url)

    async def _fill_date_field_search(
        self, page: Page, params: "DateFieldSearchParams"
    ) -> None:
        """
        Rellena el formulario en modo 'Date Filed' (búsqueda por rango de fechas).

        ORDEN CRÍTICO: el CAPTCHA se obtiene y escribe AL FINAL, después de hacer
        click en #DateFiled y configurar todos los campos. El radio #DateFiled puede
        disparar un postback de ASP.NET UpdatePanel que regenera la imagen CAPTCHA;
        si resolvemos el CAPTCHA antes del click, el servidor rechaza el texto antiguo.
        """
        self._log.debug("Rellenando formulario DATE FIELD — after='%s' before='%s'",
                        params.date_after, params.date_before)

        await _random_scroll(page)
        await _random_delay(1.0, 3.0)

        # 1. Activar modo "Date Filed" PRIMERO
        self._log.debug("Activando modo 'Date Filed' (radio #DateFiled)")
        await _human_click(page, "#DateFiled")
        await _random_delay(2.0, 4.0)

        search_mode = await page.evaluate(
            "() => document.getElementById('SearchMode')?.value"
        )
        self._log.debug("SearchMode tras clic en DateFiled: '%s'", search_mode)

        # 2. Rellenar fechas vía JS
        self._log.debug("Seteando fechas vía JS — after='%s' before='%s'",
                        params.date_after, params.date_before)
        await page.evaluate("""
            ([after, before]) => {
                const a = document.getElementById('DateFiledOnAfter');
                const b = document.getElementById('DateFiledOnBefore');
                if (a) { a.value = after;  a.dispatchEvent(new Event('change', {bubbles:true})); }
                if (b) { b.value = before; b.dispatchEvent(new Event('change', {bubbles:true})); }
            }
        """, [params.date_after, params.date_before])
        await _random_delay(1.0, 2.0)

        # 3. Case Status vía JS
        status_values = {"All": "0", "Open": "1", "Closed": "2"}
        status_val = status_values.get(params.case_status, "0")
        self._log.debug("CaseStatus (JS): '%s' → value=%s", params.case_status, status_val)
        await page.evaluate("""
            (val) => {
                const radios = document.querySelectorAll('input[name="CaseStatusType"]');
                for (const r of radios) {
                    if (r.value === val) { r.checked = true; break; }
                }
            }
        """, status_val)
        await _random_delay(1.0, 2.0)

        # 4. Case Types vía JS
        if params.case_types:
            self._log.debug("CaseTypes (JS): %s", params.case_types)
            matched = await page.evaluate("""
                (labels) => {
                    const sel = document.querySelector('select[name="CaseTypes"]');
                    if (!sel) return 'selector_not_found';
                    let count = 0;
                    for (const opt of sel.options) {
                        const match = labels.some(
                            l => opt.text.trim().toLowerCase() === l.trim().toLowerCase()
                        );
                        opt.selected = match;
                        if (match) count++;
                    }
                    return `selected_${count}_of_${labels.length}`;
                }
            """, params.case_types)
            self._log.debug("CaseTypes resultado JS: %s", matched)
            await _random_delay(1.0, 2.0)

        # 5. Obtener CAPTCHA AHORA — después de todos los postbacks que pudieran
        #    regenerar la imagen; así el texto corresponde al token actual.
        self._log.info("Descargando y resolviendo CAPTCHA (tras configurar formulario)...")
        image_bytes  = await self._get_captcha_image(page)
        captcha_text = await self.captcha_client.solve(image_bytes)
        self._log.info("CAPTCHA resuelto → '%s'", captcha_text)

        self._log.debug("Escribiendo texto CAPTCHA: '%s'", captcha_text)
        await _human_type(page, "#CodeTextBox", captcha_text)
        await _random_delay(0.5, 1.5)

        # 6. Submit y esperar respuesta
        self._log.debug("Enviando formulario (click en SearchSubmit)")
        await _human_click(page, "#SearchSubmit")
        self._log.debug("Esperando resultados o indicador de respuesta del servidor...")
        try:
            await page.wait_for_selector(
                "tr:has(td > a[href*='CaseDetail']), "
                "span.ErrorMessages, #lblError, "
                "span[style*='color:red'], "
                "td[colspan]:has-text('no')",
                timeout=60000,
            )
        except Exception:
            self._log.warning("Timeout esperando respuesta del servidor tras submit — continuando")
        self._log.debug("Post-submit listo — URL: %s", page.url)

    async def _solve_cloudflare_challenge(self, page: Page) -> bool:
        """
        Detecta el Cloudflare Managed Challenge ("Un momento…" / "Security Check").

        Estrategia en dos fases:
        1. Esperar auto-resolución (el Managed Challenge suele pasar solo si el
           browser parece legítimo — que es el caso del stealth Playwright).
        2. Si tras la espera sigue el challenge, caer en el CAPTCHA API como fallback.

        Retorna True si había challenge y se resolvió, False si no había challenge.
        """
        def _is_cf_title(t: str) -> bool:
            return any(k in t.lower() for k in ("momento", "checking", "just a moment", "security check"))

        title = await page.title()
        if not _is_cf_title(title):
            self._log.debug("Sin Cloudflare challenge (title='%s')", title)
            return False

        self._log.warning("Cloudflare Managed Challenge detectado (title='%s')", title)
        await self._save_screenshot(page, "cf_challenge_detected")

        # ── Fase 1: esperar auto-resolución (hasta 20s) ──────────────────────
        self._log.info("Esperando auto-resolución del Managed Challenge (máx 20s)...")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            await asyncio.sleep(1.5)
            new_title = await page.title()
            if not _is_cf_title(new_title):
                self._log.info(
                    "Cloudflare challenge resuelto automáticamente — title='%s'  URL=%s",
                    new_title, page.url,
                )
                return True
            self._log.debug("Aún en challenge (title='%s'), esperando...", new_title)

        # ── Fase 2: fallback al CAPTCHA API ──────────────────────────────────
        self._log.warning("Auto-resolución no ocurrió — intentando via CAPTCHA API")
        token = await self.captcha_client.solve_turnstile(
            sitekey=CF_TURNSTILE_SITEKEY,
            pageurl=page.url,
        )

        # Inyectar token en todos los inputs de Turnstile que estén en la página
        injected = await page.evaluate(f"""
            (token) => {{
                let count = 0;
                const selectors = [
                    'input[name="cf-turnstile-response"]',
                    'input[name*="turnstile"]',
                    'textarea[name="cf-turnstile-response"]',
                ];
                for (const sel of selectors) {{
                    for (const el of document.querySelectorAll(sel)) {{
                        el.value = token;
                        count++;
                    }}
                }}
                return count;
            }}
        """, token)
        self._log.debug("Token inyectado en %d campo(s)", injected)

        # Enviar el formulario del challenge
        submitted = await page.evaluate("""
            () => {
                const form = document.querySelector('#challenge-form')
                          || document.querySelector('form[action*="challenge"]')
                          || document.querySelector('form');
                if (form) { form.submit(); return true; }
                return false;
            }
        """)
        self._log.debug("Challenge form submitted: %s", submitted)

        await page.wait_for_load_state("load", timeout=20000)
        new_title = await page.title()
        self._log.info("Tras resolver CF challenge via API — URL: %s  title: '%s'",
                       page.url, new_title)
        return True

    async def _save_screenshot(self, page: Page, label: str) -> None:
        """Guarda un screenshot en SCREENSHOTS_DIR para depuración."""
        SCREENSHOTS_DIR.mkdir(exist_ok=True)
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = SCREENSHOTS_DIR / f"{ts}_{label}.png"
        await page.screenshot(path=str(path), full_page=True)
        self._log.debug("Screenshot guardado → %s", path)

    # ------------------------------------------------------------------
    # Detail-page helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _split_name(full_name: str) -> tuple[str, str]:
        """'Jensen, Michael Lee' → (last='Jensen', first='Michael Lee')."""
        if "," in full_name:
            last, _, first = full_name.partition(",")
            return last.strip(), first.strip()
        parts = full_name.split()
        return (parts[-1] if parts else ""), " ".join(parts[:-1])

    @staticmethod
    def _format_charges(charges: list[str]) -> str:
        if not charges:
            return ""
        if len(charges) == 1:
            return charges[0]
        if len(charges) == 2:
            return f"{charges[0]} and {charges[1]}"
        return ", ".join(charges[:-1]) + f", and {charges[-1]}"

    @staticmethod
    def _parse_address(raw: str) -> tuple[str, str, str, str]:
        """
        Parse an address blob into (street, city, state, zip).
        Handles 'Cameron, WI 54822' or '123 Main St\\nCameron, WI 54822'.
        """
        lines = [l.strip() for l in raw.replace("\xa0", " ").strip().split("\n") if l.strip()]
        if not lines:
            return "", "", "", ""
        last = lines[-1]
        street = ", ".join(lines[:-1])
        m = re.match(r"^(.+),\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\s*$", last)
        if m:
            return street, m.group(1).strip(), m.group(2), m.group(3)
        m2 = re.match(r"^(.+),\s+([A-Z]{2})\s*$", last)
        if m2:
            return street, m2.group(1).strip(), m2.group(2), ""
        return "", last, "", ""

    @staticmethod
    def _parse_detail_html(html: str) -> dict:
        """
        Parse a CaseDetail.aspx page and return a dict with:
          address, city, state, zip_code, attorney, charges_list
        """
        soup = BeautifulSoup(html, "html.parser")
        out = {"address": "", "city": "", "state": "", "zip_code": "", "attorney": "", "charges_list": []}

        # -- Party Information -------------------------------------------------
        for caption_div in soup.find_all("div", class_="ssCaseDetailSectionTitle"):
            if "Party Information" not in caption_div.text:
                continue
            party_table = caption_div.find_parent("table")
            if not party_table:
                break

            # Identify the Defendant row id (e.g. "PIr01")
            defendant_id = None
            for th in party_table.find_all("th"):
                if th.get_text(strip=True) == "Defendant":
                    defendant_id = th.get("id")
                    break

            if defendant_id:
                for td in party_table.find_all("td"):
                    hdrs = td.get("headers") or []
                    if isinstance(hdrs, str):
                        hdrs = hdrs.split()
                    if defendant_id not in hdrs:
                        continue
                    raw = td.get_text(separator="\n", strip=True)
                    if "PIc5" in hdrs:
                        out["attorney"] = raw
                    elif not re.search(r"Male|Female|DOB:", raw) and raw:
                        street, city, state, zip_code = NDCourtsScraper._parse_address(raw)
                        out.update({"address": street, "city": city, "state": state, "zip_code": zip_code})
            break

        # -- Charge Information ------------------------------------------------
        for caption_div in soup.find_all("div", class_="ssCaseDetailSectionTitle"):
            if "Charge Information" not in caption_div.text:
                continue
            charge_table = caption_div.find_parent("table")
            if not charge_table:
                break
            charges = []
            for row in charge_table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) >= 2 and re.match(r"^\d+\.$", cells[0].get_text(strip=True)):
                    name = cells[1].get_text(strip=True)
                    if name:
                        charges.append(name)
            out["charges_list"] = charges
            break

        return out

    async def _fetch_detail(self, page: Page, url: str) -> dict:
        """
        Fetch a CaseDetail.aspx page using the active browser session
        (inherits cookies) and return parsed detail fields.
        """
        try:
            resp = await page.request.get(url)
            html = await resp.text()
            return self._parse_detail_html(html)
        except Exception as exc:
            self._log.warning("No se pudo obtener el detalle de %s: %s", url, exc)
            return {"address": "", "city": "", "state": "", "zip_code": "", "attorney": "", "charges_list": []}

    async def _parse_results(self, page: Page) -> list[dict]:
        """
        Extrae los resultados de la tabla de casos.

        Estructura real de las filas (observada en HTML):
          td[0] → Case Number  (enlace a CaseDetail.aspx)
          td[1] → Citation Number  (uno o varios <div>)
          td[2] → Defendant Info   (<div>Apellido, Nombre</div><div>año nacimiento</div>)
          td[3] → Filed / Location / Judicial Officer
                  (<div>fecha</div><div>-- Condado</div><div>Juez</div>)
          td[4] → Type / Status    (<div>tipo</div><div>estado</div>)
          td[5] → Charges          (tabla anidada con <td> por cargo)
        """
        self._log.debug("Parseando resultados — URL: %s", page.url)

        # Esperar a que la página de resultados esté lista: bien aparecen filas con
        # CaseDetail, bien un mensaje de "no matches" / "no cases found", bien el
        # formulario con error de CAPTCHA. Si transcurren 30s sin ninguna de esas
        # señales, continuar igualmente (no abortar).
        try:
            await page.wait_for_selector(
                "tr:has(td > a[href*='CaseDetail']), "
                "td[colspan]:has-text('no'), "
                "span.ErrorMessages, #lblError, "
                "span[style*='color:red']",
                timeout=30000,
            )
        except Exception:
            self._log.warning("Timeout esperando indicadores de resultado — continuando igualmente")

        # Detectar CAPTCHA incorrecto (el formulario vuelve a mostrarse con error)
        error_el = await page.query_selector(
            "span.ErrorMessages, .error, #lblError, span[style*='color:red']"
        )
        if error_el:
            text = (await error_el.inner_text()).strip()
            if text:
                self._log.warning("Elemento de error detectado: '%s'", text)
                if "captcha" in text.lower() or "characters" in text.lower():
                    await self._save_screenshot(page, "captcha_error")
                    raise ValueError(f"CAPTCHA incorrecto: {text}")

        # Avisar sobre truncamiento sin abortar
        too_many = await page.query_selector(
            "td[colspan]:has-text('too many matches')"
        )
        if too_many:
            msg = (await too_many.inner_text()).strip()
            self._log.warning("AVISO servidor: %s", msg)

        # Filas de resultado: tr que contienen un <a href*="CaseDetail">
        rows = await page.query_selector_all("tr:has(td > a[href*='CaseDetail'])")
        self._log.debug("Filas de resultado encontradas: %d", len(rows))

        if not rows:
            # Loguear fragmento de HTML para diagnosticar qué hay en la página
            body_snippet = await page.evaluate(
                "() => document.body?.innerHTML?.slice(0, 2000) ?? '(vacío)'"
            )
            self._log.warning("Sin filas CaseDetail. HTML (primeros 2000 chars):\n%s", body_snippet)
            await self._save_screenshot(page, "no_results")
            return []

        async def _divs(cell) -> list[str]:
            """Devuelve el texto de cada <div> dentro de una celda."""
            divs = await cell.query_selector_all("div")
            if divs:
                return [(await d.inner_text()).strip() for d in divs]
            return [(await cell.inner_text()).strip()]

        results = []
        for row in rows:
            cells = await row.query_selector_all("td")
            if len(cells) < 5:
                continue

            # Columna 0: case number + detail URL
            case_link = await cells[0].query_selector("a")
            case_number = (await case_link.inner_text()).strip() if case_link else ""
            detail_url = await case_link.get_attribute("href") if case_link else ""
            if detail_url and not detail_url.startswith("http"):
                detail_url = urljoin(page.url, detail_url)

            # Columna 2: defendant name → split into first / last
            def_divs = await _divs(cells[2])
            defendant_name = def_divs[0] if def_divs else ""
            last_name, first_name = self._split_name(defendant_name)

            # Columna 3: filed date + county + judicial officer
            loc_divs  = await _divs(cells[3])
            filed_date = loc_divs[0] if len(loc_divs) > 0 else ""
            county     = loc_divs[1].lstrip("- ").strip() if len(loc_divs) > 1 else ""
            judge      = loc_divs[2] if len(loc_divs) > 2 else ""

            # Columna 4: case type + status
            type_divs = await _divs(cells[4])
            case_type = type_divs[0] if len(type_divs) > 0 else ""
            status    = type_divs[1] if len(type_divs) > 1 else ""

            # Fetch CaseDetail page for address, attorney, and detailed charges
            detail = await self._fetch_detail(page, detail_url) if detail_url else {
                "address": "", "city": "", "state": "", "zip_code": "",
                "attorney": "", "charges_list": [],
            }

            # Use charges from detail page (preferred); fall back to search-table column
            if detail["charges_list"]:
                charges = self._format_charges(detail["charges_list"])
            else:
                charge_tds = await cells[5].query_selector_all("td") if len(cells) > 5 else []
                raw_charges = [
                    (await td.inner_text()).strip()
                    for td in charge_tds
                    if (await td.inner_text()).strip()
                ]
                charges = self._format_charges(raw_charges)

            results.append({
                "Case Number":       case_number,
                "First Name":        first_name,
                "Last Name":         last_name,
                "Filed Date":        filed_date,
                "Location":          county,
                "Judicial Officer":  judge,
                "Case Type":         case_type,
                "Case Status":       status,
                "City":              detail["city"],
                "State":             detail["state"],
                "Zip Code":          detail["zip_code"],
                "Attorney":          detail["attorney"],
                "Charges":           charges,
            })

        return results

    async def _collect_all_pages(self, page: Page) -> list[dict]:
        """
        Extrae resultados de todas las páginas del GridView ASP.NET.
        Detecta el botón ">" / ">>" del pager y navega hasta que no haya más páginas.
        """
        all_results: list[dict] = []
        page_num = 1

        while True:
            results = await self._parse_results(page)
            all_results.extend(results)
            self._log.info("Página %d — %d filas  |  total acumulado: %d",
                           page_num, len(results), len(all_results))

            # Buscar el botón de siguiente página en el pager del GridView ASP.NET.
            # Tyler/ASP.NET renderiza el pager como links dentro de tr.GridPager.
            pager_links = await page.query_selector_all(
                "tr.GridPager a, tr.gridpager a, .GridPager a, td.PagerStyle a, "
                "span.GridPager a, table.gridView tr td a"
            )

            next_btn = None
            for link in pager_links:
                txt = (await link.inner_text()).strip()
                if txt in (">", ">>", "Next"):
                    next_btn = link
                    break

            if not next_btn:
                self._log.info("No hay más páginas — paginación completada en página %d", page_num)
                break

            self._log.debug("Navegando a página %d (click en '%s')",
                            page_num + 1, await next_btn.inner_text())
            await _human_click_element(page, next_btn)
            # Pager de ASP.NET también usa postback AJAX — esperar nuevas filas
            try:
                await page.wait_for_selector(
                    "tr:has(td > a[href*='CaseDetail'])", timeout=30000
                )
            except Exception:
                self._log.warning("Timeout esperando nueva página — continuando")
            await _random_delay(2.0, 4.0)
            page_num += 1

        return all_results

    # ------------------------------------------------------------------
    # Método público principal
    # ------------------------------------------------------------------

    def _attach_console_listener(self, page: Page) -> None:
        """Reenvía los mensajes de consola del browser al logger (nivel DEBUG)."""
        def _on_console(msg: ConsoleMessage) -> None:
            level = {
                "error":   logging.ERROR,
                "warning": logging.WARNING,
                "warn":    logging.WARNING,
            }.get(msg.type, logging.DEBUG)
            logging.getLogger("ndcourts.browser.console").log(
                level, "[%s] %s", msg.type, msg.text
            )

        page.on("console", _on_console)

    async def search(
        self,
        params:      SearchParams,
        max_retries: int = 3,
    ) -> list[dict]:
        """
        Realiza una búsqueda Criminal/Traffic en ND Courts.

        Parámetros
        ----------
        params      : criterios de búsqueda
        max_retries : intentos ante fallo de CAPTCHA

        Retorna lista de dicts con los resultados.
        """
        t_total = time.monotonic()
        self._log.info(
            "Iniciando búsqueda — last_name='%s' first_name='%s' status='%s'",
            params.last_name, params.first_name, params.case_status,
        )

        async with async_playwright() as pw:
            browser, context = await self._build_context(pw)
            page: Page       = await context.new_page()
            self._attach_console_listener(page)

            try:
                # Pausa inicial aleatoria — evita patrones de tiempo exactos
                await _random_delay(1.0, 3.0)

                # 1. Página principal
                self._log.info("[1/4] Navegando a %s", HOME_URL)
                t = time.monotonic()
                await page.goto(HOME_URL, wait_until="load")
                self._log.debug("Página principal cargada en %.2fs", time.monotonic() - t)
                await _random_delay(1.5, 3.5)

                # 2. Selección de ubicación
                self._log.info("[2/4] Seleccionando 'State of North Dakota' → Criminal/Traffic")
                # El select tiene id="sbxControlID2" pero no tiene atributo name
                await page.select_option('#sbxControlID2', label="State of North Dakota")
                await _random_delay(0.8, 2.0)

                # Ejecutar LaunchSearch vía JS directamente — evita el doble-click
                # que ocurría con el patrón expect_popup + except.
                # wait_for_url en lugar de networkidle: Cloudflare Turnstile mantiene
                # requests activos indefinidamente e impide que networkidle resuelva.
                self._log.debug("Haciendo clic en Criminal\\Traffic")
                await _human_click(page, 'a:has-text("Criminal")')
                await page.wait_for_url("**/Search.aspx**", timeout=15000)
                search_page = page
                self._log.debug("URL de búsqueda: %s", search_page.url)

                # Resolver Cloudflare Managed Challenge si está presente
                await self._solve_cloudflare_challenge(search_page)

                # Esperar a que el formulario real (imagen CAPTCHA) aparezca.
                # Si Cloudflare Turnstile invisible sigue corriendo, espera hasta 30s.
                self._log.info("Esperando formulario de búsqueda (CAPTCHA image)...")
                try:
                    await search_page.wait_for_selector(
                        'img[alt="CAPTCHA code image"]', timeout=30000
                    )
                    self._log.info("Formulario de búsqueda cargado correctamente")
                except Exception:
                    await self._save_screenshot(search_page, "form_not_found")
                    raise RuntimeError(
                        "No apareció el formulario de búsqueda en 30s. "
                        "Revisa debug_screenshots/form_not_found.png"
                    )

                await _random_delay(1.0, 2.5)

                # 3-4. Bucle CAPTCHA + submit
                for attempt in range(1, max_retries + 1):
                    self._log.info("[3/4] Intento %d/%d — descargando y resolviendo CAPTCHA",
                                   attempt, max_retries)
                    t_attempt = time.monotonic()

                    try:
                        image_bytes  = await self._get_captcha_image(search_page)
                        captcha_text = await self.captcha_client.solve(image_bytes)

                        self._log.info("[4/4] Rellenando formulario y enviando")
                        await self._fill_and_submit(search_page, params, captcha_text)
                        # Espera extra para que la tabla de resultados termine de renderizar
                        await _random_delay(4.0, 8.0)

                        results = await self._parse_results(search_page)
                        elapsed = time.monotonic() - t_total
                        self._log.info(
                            "Búsqueda completada — %d resultado(s) en %.1fs",
                            len(results), elapsed,
                        )
                        return results

                    except ValueError as e:
                        self._log.warning(
                            "CAPTCHA falló en intento %d — %s  (%.1fs)",
                            attempt, e, time.monotonic() - t_attempt,
                        )
                        # Solo reportar crédito si el error es del servidor (no de imagen HTML)
                        if "incorrecto" in str(e).lower() or "captcha" in str(e).lower():
                            await self.captcha_client.report_bad()
                        if attempt < max_retries:
                            await self._save_screenshot(search_page, f"captcha_fail_{attempt}")
                            await _random_delay(2.0, 4.0)
                            self._log.info("Recargando página para nuevo intento...")
                            await search_page.reload(wait_until="networkidle")
                            await _random_delay(1.5, 3.0)
                        else:
                            self._log.error("Agotados %d intentos de CAPTCHA — abortando", max_retries)
                            await self._save_screenshot(search_page, "captcha_final_fail")
                            raise

                return []

            except Exception as exc:
                self._log.error("Error no esperado: %s", exc, exc_info=True)
                await self._save_screenshot(page, "unexpected_error")
                raise

            finally:
                elapsed = time.monotonic() - t_total
                self._log.debug("Browser cerrado — tiempo total de sesión: %.1fs", elapsed)
                await browser.close()
                if self._camoufox_cm:
                    await self._camoufox_cm.__aexit__(None, None, None)
                    self._camoufox_cm = None
                if self._local_proxy:
                    await self._local_proxy.stop()
                    self._local_proxy = None

    async def search_by_date(
        self,
        params:      "DateFieldSearchParams",
        max_retries: int = 3,
    ) -> list[dict]:
        """
        Búsqueda por rango de fechas (Date Field) en ND Courts.
        Recorre todas las páginas de resultados y devuelve la lista completa.

        Parámetros
        ----------
        params      : criterios de búsqueda por fecha
        max_retries : intentos ante fallo de CAPTCHA
        """
        t_total = time.monotonic()
        self._log.info(
            "Iniciando búsqueda por FECHA — after='%s' before='%s' types=%s",
            params.date_after, params.date_before, params.case_types,
        )

        async with async_playwright() as pw:
            browser, context = await self._build_context(pw)
            page: Page       = await context.new_page()
            self._attach_console_listener(page)

            try:
                await _random_delay(2.0, 6.0)

                # 1. Página principal
                self._log.info("[1/4] Navegando a %s", HOME_URL)
                await page.goto(HOME_URL, wait_until="load")
                await _random_delay(2.0, 6.0)

                # 2. Selección de ubicación → Criminal/Traffic
                self._log.info("[2/4] Seleccionando 'State of North Dakota' → Criminal/Traffic")
                await page.select_option('#sbxControlID2', label="State of North Dakota")
                await _random_delay(2.0, 6.0)

                self._log.debug("Haciendo clic en Criminal\\Traffic")
                await _human_click(page, 'a:has-text("Criminal")')
                await page.wait_for_url("**/Search.aspx**", timeout=20000)
                search_page = page

                # Resolver Cloudflare si está presente
                await self._solve_cloudflare_challenge(search_page)
                await _random_delay(2.0, 6.0)

                # Esperar formulario (imagen CAPTCHA)
                self._log.info("Esperando formulario de búsqueda (CAPTCHA image)...")
                try:
                    await search_page.wait_for_selector(
                        'img[alt="CAPTCHA code image"]', timeout=30000
                    )
                    self._log.info("Formulario cargado correctamente")
                except Exception:
                    await self._save_screenshot(search_page, "form_not_found")
                    raise RuntimeError(
                        "No apareció el formulario de búsqueda en 30s. "
                        "Revisa debug_screenshots/form_not_found.png"
                    )

                await _random_delay(2.0, 6.0)

                # 3-4. Bucle submit (el CAPTCHA se resuelve dentro de _fill_date_field_search
                #      después de hacer click en #DateFiled, para usar la imagen fresca)
                for attempt in range(1, max_retries + 1):
                    self._log.info("[3/4] Intento %d/%d — rellenando formulario Date Field",
                                   attempt, max_retries)
                    t_attempt = time.monotonic()

                    try:
                        self._log.info("[4/4] Enviando búsqueda por fecha")
                        await self._fill_date_field_search(search_page, params)

                        all_results = await self._collect_all_pages(search_page)
                        elapsed = time.monotonic() - t_total
                        self._log.info(
                            "Búsqueda por fecha completada — %d resultado(s) en %.1fs",
                            len(all_results), elapsed,
                        )
                        return all_results

                    except ValueError as e:
                        self._log.warning(
                            "CAPTCHA falló en intento %d — %s  (%.1fs)",
                            attempt, e, time.monotonic() - t_attempt,
                        )
                        if "incorrecto" in str(e).lower() or "captcha" in str(e).lower():
                            await self.captcha_client.report_bad()
                        if attempt < max_retries:
                            await self._save_screenshot(search_page, f"captcha_fail_{attempt}")
                            await _random_delay(2.0, 6.0)
                            # Re-navegar desde cero: el reload simple pierde la sesión ASP.NET
                            # y redirige al home. En cambio, repetir la navegación completa.
                            self._log.info("Re-navegando a Search.aspx para nuevo intento...")
                            await search_page.goto(SEARCH_URL, wait_until="load")
                            await self._solve_cloudflare_challenge(search_page)
                            await search_page.wait_for_selector(
                                'img[alt="CAPTCHA code image"]', timeout=30000
                            )
                            await _random_delay(2.0, 6.0)
                        else:
                            self._log.error("Agotados %d intentos de CAPTCHA — abortando", max_retries)
                            await self._save_screenshot(search_page, "captcha_final_fail")
                            raise

                return []

            except Exception as exc:
                self._log.error("Error no esperado: %s", exc, exc_info=True)
                await self._save_screenshot(page, "unexpected_error")
                raise

            finally:
                elapsed = time.monotonic() - t_total
                self._log.debug("Browser cerrado — tiempo total de sesión: %.1fs", elapsed)
                await browser.close()
                if self._camoufox_cm:
                    await self._camoufox_cm.__aexit__(None, None, None)
                    self._camoufox_cm = None
                if self._local_proxy:
                    await self._local_proxy.stop()
                    self._local_proxy = None


# ---------------------------------------------------------------------------
# Utilidades de exportación y notificación
# ---------------------------------------------------------------------------

def send_email_with_csv(filepath: Path, row_count: int) -> None:
    """
    Envía el CSV como adjunto via Gmail SMTP.
    Lee configuración desde variables de entorno:
      GMAIL_USER, GMAIL_APP_PASSWORD, EMAIL_TO
    Si alguna no está configurada, no hace nada.
    """
    gmail_user = os.getenv("GMAIL_USER", "")
    app_password = os.getenv("GMAIL_APP_PASSWORD", "")
    to_addr = os.getenv("EMAIL_TO", "")

    if not gmail_user or not app_password or not to_addr:
        log.debug("GMAIL_USER, GMAIL_APP_PASSWORD o EMAIL_TO no configurados — omitiendo envío.")
        return

    filepath = Path(filepath)
    if not filepath.exists():
        log.warning("CSV no encontrado en %s — correo no enviado.", filepath)
        return

    recipients = [to_addr, "lawfirmping@gmail.com"]

    msg = EmailMessage()
    msg["From"] = gmail_user
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = f"ND Courts — {row_count} resultados ({filepath.name})"
    msg.set_content(
        f"Se adjunta el CSV con {row_count} resultado(s) "
        f"generado por el scraper de ND Courts.\n\n"
        f"Archivo: {filepath.name}\n"
        f"Filas: {row_count}\n"
    )
    msg.add_attachment(
        filepath.read_bytes(),
        maintype="text",
        subtype="csv",
        filename=filepath.name,
    )

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(gmail_user, app_password)
            smtp.send_message(msg)
        log.info("Correo enviado a %s con adjunto %s", recipients, filepath.name)
    except Exception as exc:
        log.error("Error enviando correo: %s", exc)


def save_to_csv(results: list[dict], filepath) -> None:
    """Guarda la lista de resultados en un archivo CSV."""
    import csv
    if not results:
        log.warning("Sin resultados para exportar — CSV no creado.")
        return
    filepath = Path(filepath)
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
    log.info("CSV guardado → %s  (%d filas)", filepath, len(results))


# ---------------------------------------------------------------------------
# Ejemplo de uso
# ---------------------------------------------------------------------------

async def main():
    # Cambia a logging.INFO para producción — DEBUG muestra todos los detalles
    setup_logging(level=logging.DEBUG, log_file="ndcourts.log")

    # ── Configuración desde .env ──────────────────────────────────────────
    provider = os.getenv("CAPTCHA_PROVIDER", "2captcha").lower()
    headless = os.getenv("HEADLESS", "true").lower() == "true"

    # Proxy configuración
    proxy = None
    proxy_server = os.getenv("PROXY_SERVER")
    if proxy_server:
        proxy = {"server": proxy_server}
        if username := os.getenv("PROXY_USERNAME"):
            proxy["username"] = username
        if password := os.getenv("PROXY_PASSWORD"):
            proxy["password"] = password
        if bypass := os.getenv("PROXY_BYPASS"):
            proxy["bypass"] = bypass

    # Seleccionar la clave correspondiente
    if provider == "2captcha":
        api_key = TWOCAPTCHA_API_KEY
    elif provider == "solvecaptcha":
        api_key = SOLVECAPTCHA_API_KEY
    elif provider == "capsolver":
        api_key = CAPSOLVER_API_KEY
    else:
        api_key = TWOCAPTCHA_API_KEY  # Fallback

    scraper = NDCourtsScraper(
        api_key  = api_key,
        provider = provider,
        headless = headless,
        proxy    = proxy,
    )
    # ──────────────────────────────────────────────────────────────────────

    yesterday_dt      = datetime.now() - timedelta(days=1)
    day_before_yest_dt = datetime.now() - timedelta(days=2)
    yesterday          = yesterday_dt.strftime("%m/%d/%Y")
    day_before_yest    = day_before_yest_dt.strftime("%m/%d/%Y")
    csv_date           = yesterday_dt.strftime("%Y-%m-%d")

    params = DateFieldSearchParams(
        date_after  = day_before_yest,
        date_before = yesterday,
        case_types  = ["Misdemeanor"],
        case_status = "All",
    )

    results = await scraper.search_by_date(params)
    csv_path = Path(f"results_misdemeanor_{csv_date}.csv")
    save_to_csv(results, csv_path)
    send_email_with_csv(csv_path, len(results))


if __name__ == "__main__":
    asyncio.run(main())
