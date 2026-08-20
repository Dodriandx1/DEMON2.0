# Telegram Downloader Bot

## Ejecutar como Worker en Contabo o Hostinger (VPS)

Este bot es un proceso persistente de Telegram. En un VPS debe crearse como
**Worker**, **Background Process** o **Supervisor Process**, no como sitio web.
No necesita dominio, servidor web ni puerto público.

Comando de inicio del Worker:

```bash
python -u bot/main.py
```

Variables recomendadas para un Worker:

```text
KEEP_ALIVE=false
```

`KEEP_ALIVE=false` evita abrir el puerto HTTP que solo es útil para previews de
Replit. El bot seguirá funcionando normalmente porque Telegram usa su conexión
de red saliente.

### Opción Docker Worker

El `docker-compose.yml` incluido ya configura `KEEP_ALIVE=false` y reinicia el
contenedor automáticamente:

1. Instala Docker y Docker Compose en el VPS.
2. Clona el repositorio:

   ```bash
   git clone https://github.com/TU_USUARIO/TU_REPO.git
   cd TU_REPO
   cp .env.example .env
   nano .env
   ```

3. Completa `API_ID`, `API_HASH`, `BOT_TOKEN` y `ADMIN_IDS`.
4. Si YouTube solicita autenticación, exporta las cookies de YouTube en formato
   Netscape y guárdalas como `data/cookies.txt`:

   ```bash
   mkdir -p data
   chmod 600 data/cookies.txt
   ```

   No subas `.env` ni `data/cookies.txt` a GitHub.
5. Arranca el Worker:

   ```bash
   docker compose up -d --build
   docker compose logs -f telegram-bot
   ```

El volumen `bot-data` conserva usuarios autorizados y cookies al recrear el
contenedor. Para actualizar desde GitHub:

```bash
git pull
docker compose up -d --build
```

## YouTube

YouTube puede bloquear la IP de un VPS y pedir autenticación. El bot busca
cookies en `YOUTUBE_COOKIES_PATH`, `bot/cookies.txt` y `cookies.txt`. También
admite `YOUTUBE_COOKIES_B64` para proveedores que prefieren inyectarlas como
variable protegida. Desde Telegram, un administrador puede enviar el archivo y
usar `/cookies`.

## Páginas de cómics y galerías

- Enviar directamente un enlace de ToonX o JAV Guru.
- Usar `/comic <url>`.

El scraper extrae las imágenes en el orden HTML original y las envía en
álbumes de Telegram. Si una página no expone imágenes o requiere JavaScript,
devolverá un error explícito en vez de entregar thumbnails incorrectas.