# Bot Descargador de Videos de Telegram

Bot de Telegram que descarga videos de redes sociales (YouTube, TikTok, Instagram, Twitter/X, Facebook, Mega.nz y más), los codifica con FFmpeg y los sube directamente a Telegram con barras de progreso en tiempo real.

## Run & Operate

- `python bot/main.py` — ejecutar el bot de Telegram
- `pnpm --filter @workspace/api-server run dev` — ejecutar el servidor API (puerto 5000)

## Stack

- Python 3.11
- Pyrogram — cliente de Telegram (bot)
- yt-dlp — descargador de videos (YouTube, TikTok, Instagram, Twitter/X, etc.)
- megatools — descargador de Mega.nz
- FFmpeg — codificación de video y marcas de agua
- psutil — estadísticas del sistema
- pymongo — base de datos de usuarios (opcional)
- Node.js 24 / pnpm — monorepo API server

## Where things live

- `bot/main.py` — código completo del bot
- `bot/requirements.txt` — dependencias Python
- `lib/api-spec/openapi.yaml` — spec de la API REST

## Comandos del Bot

- Enviar enlace — descarga el video con barra de progreso
- `/start` — presentación del bot
- `/stat` — panel de estadísticas (RAM, CPU, disco, uptime)
- `/reset` — reiniciar estadísticas y cancelar todas las descargas
- `/cancel` o `/cancel_<id>` — cancelar descarga activa
- `/encode` — convertir video adjunto (.mkv/.avi) a MP4
- Enviar enlace + `-lat` — añade subtítulos en español

## Funcionalidades

- Barras de progreso en tiempo real (descarga → codificación → subida)
- Un solo mensaje editado en tiempo real por cada etapa
- Soporte de carrusel de fotos TikTok
- Marcas de agua personalizables (texto, posición, tamaño, contorno)
- Soporte de subtítulos en español con flag `-lat`
- Cancelación efectiva de descargas con `/cancel`
- Descarga desde Mega.nz (megatools)
- Video entregado como video con carátula (no documento)

## Secrets requeridos

- `API_ID` — ID de la app de Telegram (my.telegram.org)
- `API_HASH` — Hash de la app de Telegram
- `BOT_TOKEN` — Token del bot (@BotFather)
- `ADMIN_IDS` — IDs de Telegram de admins, separados por comas
- `MONGO_URI` — (Opcional) Cadena de conexión MongoDB

## User preferences

- Créditos: `✪ Bot By → @The_canst & @Ryota_YT`
- Videos entregados como video con carátula, nunca como documento
- Barras de progreso con caracteres ⬢ y ◉/◌

## Gotchas

- El bot usa un cliente de bot Pyrogram. Para subir archivos >2GB se necesita sesión de usuario
- `megatools` debe estar instalado en el sistema para descargas de Mega.nz
- FFmpeg preset `veryfast` para minimizar tiempo de codificación
- El `/cancel` mata el proceso del sistema operativo directamente para garantizar parada inmediata

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
