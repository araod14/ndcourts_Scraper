# ND Courts Criminal/Traffic Scraper

Este es un scraper robusto desarrollado en Python para el portal de búsqueda pública de los tribunales de Dakota del Norte (North Dakota Courts), específicamente diseñado para la sección de casos **Criminal/Traffic**.

Utiliza **Playwright** para la automatización del navegador y soporta múltiples proveedores de servicios de resolución de CAPTCHA para sortear las protecciones de bot (LanAP Captcha y Cloudflare Turnstile).

## 🚀 Características

- **Automatización con Playwright:** Navegación real en navegador Chromium.
- **Soporte Multi-Proveedor CAPTCHA:** Compatible con 2captcha, SolveCaptcha y CapSolver.
- **Bypass de Cloudflare:** Maneja desafíos de Turnstile mediante integración con solvers.
- **Técnicas Anti-Detección:** Uso de scripts de sigilo (stealth), User-Agents aleatorios y simulación de comportamiento humano (tiempos de espera y movimientos de ratón).
- **Depuración:** Captura automática de capturas de pantalla en caso de error en la carpeta `debug_screenshots/`.

## 📋 Requisitos Previos

- Python 3.10 o superior.
- Una cuenta y API Key en uno de los proveedores soportados (2captcha, SolveCaptcha o CapSolver).

## 🛠️ Instalación

1.  **Clonar el repositorio** (o descargar los archivos).
2.  **Crear y activar un entorno virtual:**
    ```bash
    python -m venv venv
    source venv/bin/activate  # En Windows: venv\Scripts\activate
    ```
3.  **Instalar las dependencias:**
    ```bash
    pip install -r requirements.txt
    ```
4.  **Instalar el navegador necesario (Chromium):**
    ```bash
    playwright install chromium
    ```

## 📖 Uso

1.  **Configurar las variables de entorno:**
    Copia el archivo de ejemplo y edítalo con tus claves:
    ```bash
    cp .env.example .env
    ```
    Edita `.env` y configura tu API Key y el proveedor preferido:
    - `TWOCAPTCHA_API_KEY`
    - `SOLVECAPTCHA_API_KEY`
    - `CAPSOLVER_API_KEY`
    - `CAPTCHA_PROVIDER` (opciones: `2captcha`, `solvecaptcha`, `capsolver`)
    - `HEADLESS` (opcional: `true` para ejecución en segundo plano, `false` para ver el navegador)

3.  **Configuración de Proxy (Opcional):**
    Si necesitas usar un proxy, configura las siguientes variables en `.env`:
    - `PROXY_SERVER`: Dirección del servidor (ej: `http://mi-proxy:8080` o `socks5://host:1080`)
    - `PROXY_USERNAME`: Usuario del proxy (si requiere autenticación)
    - `PROXY_PASSWORD`: Contraseña del proxy
    - `PROXY_BYPASS`: Dominios que no deben usar el proxy (separados por comas)

4.  **Ejecutar el scraper:**
    ```bash
    python scraper.py
    ```

### Configuración avanzada
Puedes seguir pasando parámetros directamente al constructor si lo prefieres, pero el script `main()` ahora prioriza lo definido en el archivo `.env`.

## 📂 Estructura del Proyecto

- `scraper.py`: Archivo principal que contiene toda la lógica del scraper y los clientes de CAPTCHA.
- `requirements.txt`: Librerías de Python necesarias.
- `CLAUDE.md` / `GEMINI.md`: Guías técnicas y de arquitectura para asistentes de IA.
- `debug_screenshots/`: Directorio donde se guardan evidencias de errores durante la ejecución.

## ⚠️ Aviso Legal

Este proyecto tiene fines educativos y de investigación. El scraping de sitios web públicos debe realizarse respetando los términos de servicio del sitio objetivo y las leyes de privacidad aplicables. El autor no se hace responsable del uso indebido de esta herramienta.
