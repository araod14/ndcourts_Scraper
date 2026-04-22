# ND Courts Criminal/Traffic Scraper

Scraper en Python para el portal de búsqueda pública de los tribunales de Dakota del Norte, diseñado para la sección **Criminal/Traffic**. Extrae casos por rango de fechas o por nombre de acusado, incluyendo dirección, cargos y abogado defensor desde las páginas de detalle.

Usa **Playwright** para automatización del navegador y soporta múltiples proveedores de resolución de CAPTCHA (LanAP + Cloudflare Turnstile).

## Características

- **Búsqueda por fecha (modo principal):** Extrae todos los casos de un rango de fechas sin requerir nombre; recorre automáticamente todas las páginas de resultados.
- **Búsqueda por nombre:** Modo Defendant con apellido, nombre, DOB, soundex, rango de fechas y tipo de caso.
- **Enriquecimiento de datos:** Cada resultado se enriquece automáticamente con City, State, Zip, Attorney y Charges detallados desde `CaseDetail.aspx`.
- **Soporte multi-proveedor CAPTCHA:** Compatible con 2captcha, SolveCaptcha y CapSolver.
- **Preprocesamiento de imagen CAPTCHA:** Escalado 3×, escala de grises, blur y binarización para mejorar la precisión del OCR.
- **Bypass de Cloudflare:** Maneja Turnstile Managed Challenge; espera auto-resolución (20s) y cae en CAPTCHA API como fallback.
- **Stealth multi-capa:** Soporte para camoufox (Firefox), rebrowser-playwright y playwright-stealth. Fallback a script manual mínimo.
- **Paginación automática:** Navega el GridView ASP.NET recorriendo todos los botones ">" del pager.
- **Notificaciones por email:** Envía los CSVs resultantes por Gmail SMTP al finalizar.
- **Reintentos robustos:** Reintentos internos de CAPTCHA + reintentos externos completos si se obtienen 0 resultados.
- **Anti-detección:** User-Agents aleatorios, viewports variables, movimientos de ratón Bezier, delays humanos, scroll simulado.
- **Proxy residencial:** Soporte con proxy local que pre-embebe credenciales (resuelve incompatibilidad Chromium con auth en dos pasos).
- **Depuración:** Screenshots automáticos en `debug_screenshots/` ante cualquier fallo, incluyendo imagen CAPTCHA raw y preprocesada.

## Requisitos Previos

- Python 3.10 o superior.
- API Key de un proveedor CAPTCHA soportado (2captcha, SolveCaptcha o CapSolver).
- Para email: cuenta Gmail con App Password configurada.
- Para proxy: proxies residenciales (datacenter bloqueado por Cloudflare). Webshare Rotating Residential confirmado.

## Instalación

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

Mejoras de stealth opcionales (cualquier combinación):
```bash
pip install camoufox            # Recomendado — Firefox stealth, mejor evasión Cloudflare
pip install rebrowser-playwright
pip install playwright-stealth
```

## Configuración

Crea un archivo `.env` en la raíz del proyecto:

```env
# CAPTCHA — elige un proveedor y su clave
CAPTCHA_PROVIDER=2captcha       # opciones: 2captcha | solvecaptcha | capsolver
TWOCAPTCHA_API_KEY=xxx
SOLVECAPTCHA_API_KEY=xxx
CAPSOLVER_API_KEY=CAP-xxx

# Navegador
HEADLESS=true                   # false para ver el navegador (útil para depurar)

# Proxy residencial (opcional pero recomendado)
PROXY_SERVER=http://p.webshare.io:80
PROXY_USERNAME=tu_usuario
PROXY_PASSWORD=tu_contraseña

# Email (opcional — omitir si no se necesita)
GMAIL_USER=tu_cuenta@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
EMAIL_TO=destinatario@ejemplo.com
```

## Uso

### Modo automático (misdemeanor + felony del día anterior)

```bash
python scraper.py
# o
bash run_scraper.sh
```

Ejecuta dos búsquedas por fecha (Misdemeanor y Felony para el día anterior), guarda los CSVs y los envía por email.

### Uso programático

```python
import asyncio
from scraper import NDCourtsScraper, DateFieldSearchParams, SearchParams, save_to_csv

scraper = NDCourtsScraper(api_key="xxx", provider="2captcha", headless=True)

# Búsqueda por fecha (modo principal)
params = DateFieldSearchParams(
    date_after  = "04/20/2026",
    date_before = "04/21/2026",
    case_types  = ["Misdemeanor"],   # o ["Felony"], o [] para todos
    case_status = "All",
)
results = asyncio.run(scraper.search_by_date(params))
save_to_csv(results, "output.csv")

# Búsqueda por nombre
params_name = SearchParams(
    last_name  = "Smith",
    first_name = "John",
    case_types = ["Misdemeanor"],
)
results = asyncio.run(scraper.search(params_name))
```

### Enriquecimiento retroactivo de CSV

`enrich_csv.py` es una herramienta independiente para añadir City/State/Zip/Attorney/Charges a un CSV existente. Configura `CSV_FILE` y `SEARCH_HTML` al inicio del archivo, luego:

```bash
python enrich_csv.py
```

## Estructura del Proyecto

```
scraper.py          # Scraper principal — toda la lógica de búsqueda, CAPTCHA, parseo y email
enrich_csv.py       # Herramienta de enriquecimiento retroactivo de CSVs
run_scraper.sh      # Script shell (activa venv y ejecuta scraper.py)
requirements.txt    # Dependencias Python
CLAUDE.md           # Guía técnica de arquitectura para Claude Code
GEMINI.md           # Guía técnica para Gemini
debug_screenshots/  # Screenshots de error y CAPTCHAs (generados automáticamente)
```

## Aviso Legal

Este proyecto tiene fines educativos y de investigación. El scraping de sitios web públicos debe realizarse respetando los términos de servicio del sitio objetivo y las leyes de privacidad aplicables. El autor no se hace responsable del uso indebido de esta herramienta.
